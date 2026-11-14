"""Read-only access to the processed hotspot Parquet dataset via DuckDB."""
from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from pathlib import Path

import duckdb
import pyarrow as pa
import shapely

from app.core.config import Settings
from app.services.aoi import AOI


class HotspotStore:
    def __init__(self, settings: Settings | None, con: duckdb.DuckDBPyConnection | None = None):
        self.settings = settings
        self._lock = threading.Lock()
        if con is not None:  # injected connection with a `hotspots` table/view (tests)
            self._con = con
            self.available = True
            return
        self._con = duckdb.connect()
        self.available = Path(settings.coverage_path).exists()
        if self.available:
            # Views can't take prepared parameters in DuckDB, so quote the path literal.
            path = settings.hotspots_glob.replace("'", "''")
            self._con.execute(
                f"CREATE VIEW hotspots AS SELECT * FROM read_parquet('{path}', hive_partitioning=true)"
            )

    @contextmanager
    def _session(self, aoi: AOI | None):
        """Hold the connection lock; when `aoi` is a polygon, expose `in_aoi(lat, lon)`."""
        with self._lock:
            registered = False
            if aoi is not None and aoi.kind == "polygon":
                geom = aoi.geometry

                def in_aoi(lat: pa.Array, lon: pa.Array) -> pa.Array:
                    return pa.array(shapely.contains_xy(geom, lon.to_numpy(), lat.to_numpy()))

                self._con.create_function(
                    "in_aoi", in_aoi, [duckdb.sqltype("DOUBLE")] * 2, duckdb.sqltype("BOOLEAN"), type="arrow"
                )
                registered = True
            try:
                yield self._con
            finally:
                if registered:
                    self._con.remove_function("in_aoi")

    def query(self, sql: str, params: list | None = None, aoi: AOI | None = None) -> list[dict]:
        with self._session(aoi) as con:
            cur = con.execute(sql, params or [])
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]

    def copy_csv(self, sql: str, params: list, path: str, aoi: AOI | None = None) -> int:
        """Write a query result to a CSV file with DuckDB COPY. Returns the row count."""
        safe = path.replace("'", "''")
        with self._session(aoi) as con:
            con.execute(f"COPY ({sql}) TO '{safe}' (HEADER, DELIMITER ',')", params)
            return con.execute(f"SELECT count(*) FROM read_csv('{safe}', header=true)").fetchone()[0]

    def coverage(self) -> dict:
        return json.loads(Path(self.settings.coverage_path).read_text())
