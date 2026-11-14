"""Build the normalized, partitioned Parquet hotspot dataset.

Usage:  backend/.venv/bin/python backend/scripts/prepare_data.py [--data-dir data]

Inputs
  data/raw/modis/*.csv, data/raw/viirs-snpp/*.csv   FIRMS yearly country archive (US only)
  data/raw/api/<SOURCE>/*.csv                        FIRMS area API, 2026 onward (fetch_2026.py)
  data/reference/ne_10m_admin_0_countries.geojson    US boundary used to clip API rows

Output
  data/processed/hotspots/sensor=<MODIS|VIIRS>/year=<YYYY>/*.parquet
  data/processed/coverage.json

No values are invented: every output row comes from one input row. Original source
columns are kept alongside normalized ones. `processing_level` records provenance:
  archive  - FIRMS yearly country file
  standard - FIRMS API standard processing (MODIS_SP / VIIRS_SNPP_SP)
  nrt      - FIRMS API near-real-time (provisional)
"""
from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import shapely
from shapely.geometry import shape

# Columns common to every FIRMS CSV we read. `type` is absent from NRT files.
BASE = {
    "latitude": "DOUBLE", "longitude": "DOUBLE", "scan": "DOUBLE", "track": "DOUBLE",
    "acq_date": "DATE", "acq_time": "VARCHAR", "satellite": "VARCHAR", "instrument": "VARCHAR",
    "confidence": "VARCHAR", "version": "VARCHAR", "frp": "DOUBLE", "daynight": "VARCHAR",
}
SENSOR_COLS = {
    "MODIS": {"brightness": "DOUBLE", "bright_t31": "DOUBLE"},
    "VIIRS": {"bright_ti4": "DOUBLE", "bright_ti5": "DOUBLE"},
}
BRIGHT = {"MODIS": ("brightness", "bright_t31"), "VIIRS": ("bright_ti4", "bright_ti5")}

# acq_time is HHMM in UTC; leading zeros are dropped in some files.
DATETIME_SQL = (
    "CAST(acq_date AS TIMESTAMP) "
    "+ to_hours(CAST(substr(lpad(acq_time, 4, '0'), 1, 2) AS INTEGER)) "
    "+ to_minutes(CAST(substr(lpad(acq_time, 4, '0'), 3, 2) AS INTEGER))"
)
# MODIS confidence 0-100 -> low <30, nominal 30-79, high >=80 (FIRMS guidance).
# VIIRS confidence l / n / h.
CONF_SQL = {
    "MODIS": """CASE WHEN TRY_CAST(confidence AS INTEGER) IS NULL THEN NULL
                     WHEN TRY_CAST(confidence AS INTEGER) < 30 THEN 'low'
                     WHEN TRY_CAST(confidence AS INTEGER) < 80 THEN 'nominal'
                     ELSE 'high' END""",
    "VIIRS": """CASE lower(confidence) WHEN 'l' THEN 'low' WHEN 'n' THEN 'nominal'
                                       WHEN 'h' THEN 'high' ELSE NULL END""",
}
API_SOURCES = {
    "MODIS_SP": ("MODIS", "standard"), "MODIS_NRT": ("MODIS", "nrt"),
    "VIIRS_SNPP_SP": ("VIIRS", "standard"), "VIIRS_SNPP_NRT": ("VIIRS", "nrt"),
}
US_BUFFER_DEG = 0.02  # ~2 km; keeps coastal detections (validated on 2025 archive: 99.996% retained)


def sql_list(paths: list[str]) -> str:
    return "[" + ", ".join("'" + p.replace("'", "''") + "'" for p in paths) + "]"


def select_sql(sensor: str, level: str, files: list[str], has_type: bool) -> str:
    b1, b2 = BRIGHT[sensor]
    # DuckDB maps `columns` by position, so keep FIRMS file order.
    order = ["latitude", "longitude", b1, "scan", "track", "acq_date", "acq_time", "satellite",
             "instrument", "confidence", "version", b2, "frp", "daynight"] + (["type"] if has_type else [])
    types = {**BASE, **SENSOR_COLS[sensor], "type": "INTEGER"}
    cols = {c: types[c] for c in order}
    type_expr = "type" if has_type else "CAST(NULL AS INTEGER)"
    # repr() of the column dict is valid DuckDB struct syntax.
    return f"""
SELECT
  '{sensor}' AS sensor, '{level}' AS processing_level,
  latitude, longitude,
  {DATETIME_SQL} AS acq_datetime_utc,
  acq_date, year(acq_date) AS year, month(acq_date) AS month,
  confidence AS confidence_raw, {CONF_SQL[sensor]} AS confidence_norm,
  frp, daynight, {type_expr} AS type, satellite, instrument, version, scan, track,
  {b1} AS brightness_k, {b2} AS brightness_2_k, acq_time AS acq_time_raw
FROM read_csv({sql_list(files)}, header=true, columns={cols!r})"""


def api_has_type(files: list[str]) -> bool:
    header = Path(files[0]).read_text().splitlines()[0].split(",")
    return "type" in header


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default=str(Path(__file__).resolve().parents[2] / "data"))
    args = parser.parse_args()
    data_dir = Path(args.data_dir)
    raw = data_dir / "raw"
    out = data_dir / "processed" / "hotspots"

    archive = {
        "MODIS": sorted(str(p) for p in (raw / "modis").glob("*.csv")),
        "VIIRS": sorted(str(p) for p in (raw / "viirs-snpp").glob("*.csv")),
    }
    if not all(archive.values()):
        raise SystemExit(f"Missing archive CSVs under {raw}")

    con = duckdb.connect()
    parts = [select_sql(s, "archive", f, has_type=True) for s, f in archive.items()]
    expected = {s: con.execute(f"SELECT count(*) FROM ({select_sql(s, 'archive', f, True)})").fetchone()[0]
                for s, f in archive.items()}

    # API rows: read, then clip to the US boundary so they match the country archive.
    api_report = {}
    api_parts = []
    for source, (sensor, level) in API_SOURCES.items():
        files = sorted(str(p) for p in (raw / "api" / source).glob("*.csv")
                       if len(p.read_text().splitlines()) > 1)
        if not files:
            continue
        api_parts.append(select_sql(sensor, level, files, api_has_type(files)))
    if api_parts:
        boundary = data_dir / "reference" / "ne_10m_admin_0_countries.geojson"
        feats = json.loads(boundary.read_text())["features"]
        us = shape(next(f for f in feats if f["properties"]["ADM0_A3"] == "USA")["geometry"])
        us = us.buffer(US_BUFFER_DEG)
        shapely.prepare(us)
        api_df = con.execute(" UNION ALL ".join(api_parts)).df()
        inside = shapely.contains_xy(us, api_df["longitude"].values, api_df["latitude"].values)
        for (sensor, level), g in api_df.groupby(["sensor", "processing_level"]):
            mask = inside[g.index]
            api_report[f"{sensor}/{level}"] = {"fetched": int(len(g)), "kept_in_us": int(mask.sum())}
        api_us = api_df[inside].reset_index(drop=True)  # noqa: F841 (used by DuckDB below)
        # pandas turns DATE into a timestamp; cast back so all partitions share one schema.
        parts.append("SELECT * REPLACE (CAST(acq_date AS DATE) AS acq_date) FROM api_us")
        for sensor in expected:
            expected[sensor] += int((api_us["sensor"] == sensor).sum())

    if out.exists():
        shutil.rmtree(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    con.execute(
        f"COPY ({' UNION ALL '.join(parts)}) TO '{out}' "
        "(FORMAT parquet, PARTITION_BY (sensor, year), COMPRESSION zstd)"
    )

    glob = f"{out}/**/*.parquet"
    per_year = con.execute(
        f"""SELECT sensor, year, count(*) AS rows,
                   count(*) FILTER (WHERE type = 0) AS vegetation_rows,
                   count(*) FILTER (WHERE type IS NULL) AS untyped_rows,
                   string_agg(DISTINCT processing_level, ',' ORDER BY processing_level) AS levels,
                   min(acq_date)::VARCHAR AS first_date, max(acq_date)::VARCHAR AS last_date,
                   count(*) FILTER (WHERE acq_datetime_utc IS NULL) AS bad_time_rows
            FROM read_parquet('{glob}', hive_partitioning=true)
            GROUP BY sensor, year ORDER BY sensor, year"""
    ).fetchall()
    cols = ["sensor", "year", "rows", "vegetation_rows", "untyped_rows", "levels",
            "first_date", "last_date", "bad_time_rows"]
    years = [dict(zip(cols, r)) for r in per_year]
    levels = con.execute(
        f"""SELECT sensor, processing_level, count(*), min(acq_date)::VARCHAR, max(acq_date)::VARCHAR
            FROM read_parquet('{glob}', hive_partitioning=true) GROUP BY 1, 2 ORDER BY 1, 4"""
    ).fetchall()

    sensors = {}
    for s in ("MODIS", "VIIRS"):
        ys = [y for y in years if y["sensor"] == s]
        sensors[s] = {
            "rows": sum(y["rows"] for y in ys),
            "first_date": min(y["first_date"] for y in ys),
            "last_date": max(y["last_date"] for y in ys),
            "years": len(ys),
            "by_processing_level": [
                {"level": l, "rows": n, "first_date": a, "last_date": b}
                for (ss, l, n, a, b) in levels if ss == s
            ],
        }
        if sensors[s]["rows"] != expected[s]:
            raise SystemExit(f"Row count mismatch for {s}: expected={expected[s]} parquet={sensors[s]['rows']}")

    coverage = {
        "region": "United States",
        "source": "NASA FIRMS yearly country files (https://firms.modaps.eosdis.nasa.gov/data/country/) "
                  "and FIRMS area API for 2026 onward",
        "products": {"MODIS": "MODIS Collection 6.1 (Terra + Aqua), 1 km",
                     "VIIRS": "VIIRS S-NPP 375 m"},
        "api_clip": {"boundary": "Natural Earth 10m admin-0 (USA)", "buffer_deg": US_BUFFER_DEG,
                     "rows": api_report},
        "processed_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sensors": sensors,
        "per_year": years,
    }
    (data_dir / "processed" / "coverage.json").write_text(json.dumps(coverage, indent=2))
    for s, v in sensors.items():
        print(f"{s}: {v['rows']:,} rows, {v['first_date']} -> {v['last_date']}")
        for lv in v["by_processing_level"]:
            print(f"   {lv['level']:<8} {lv['rows']:>10,}  {lv['first_date']} -> {lv['last_date']}")
    print("API clip:", api_report)


if __name__ == "__main__":
    main()
