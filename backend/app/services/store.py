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
            self.has_cropland = bool(con.execute(
                "SELECT count(*) FROM information_schema.tables WHERE table_name = 'cropland'").fetchone()[0])
            return
        self._con = duckdb.connect()
        self.available = Path(settings.coverage_path).exists()
        if self.available:
            # Views can't take prepared parameters in DuckDB, so quote the path literal.
            path = settings.hotspots_glob.replace("'", "''")
            self._con.execute(
                f"CREATE VIEW hotspots AS SELECT * FROM read_parquet('{path}', hive_partitioning=true)"
            )
        self.has_cropland = False
        crop = settings.data_dir / "processed" / "cropland_cells.parquet"
        if crop.exists():
            # Small (one row per agricultural fire cell): load into memory for fast anti-joins.
            self._con.execute("CREATE TABLE cropland AS SELECT * FROM read_parquet(?)", [str(crop)])
            self.has_cropland = True

    @contextmanager
    def _session(self, aoi: AOI | None):
        """A fresh cursor per query (no result state can leak between requests); when `aoi` is a
        polygon, the cursor gets an `in_aoi(lat, lon)` function."""
        with self._lock:
            cur = self._con.cursor()
            registered = False
            try:
                if aoi is not None and aoi.kind == "polygon":
                    geom = aoi.geometry

                    def in_aoi(lat: pa.Array, lon: pa.Array) -> pa.Array:
                        return pa.array(shapely.contains_xy(geom, lon.to_numpy(), lat.to_numpy()))

                    cur.create_function(
                        "in_aoi", in_aoi, [duckdb.sqltype("DOUBLE")] * 2, duckdb.sqltype("BOOLEAN"), type="arrow"
                    )
                    registered = True
                yield cur
            finally:
                if registered:
                    cur.remove_function("in_aoi")  # functions are shared by all cursors of the connection
                cur.close()

    def query(self, sql: str, params: list | None = None, aoi: AOI | None = None) -> list[dict]:
        with self._session(aoi) as con:
            cur = con.execute(sql, params or [])
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]

    def copy_csv(self, sql: str, params: list, path: str, aoi: AOI | None = None) -> int:
        """Write a query result to a CSV file with DuckDB COPY. Returns the row count."""
        safe = path.replace("'", "''")
        with self._session(aoi) as con:
            con.execute(f"COPY ({sql}) TO '{safe}' (HEADER, DELIMITER ',')", params).fetchall()
            return con.execute(f"SELECT count(*) FROM read_csv('{safe}', header=true)").fetchall()[0][0]

    def coverage(self) -> dict:
        return json.loads(Path(self.settings.coverage_path).read_text())
