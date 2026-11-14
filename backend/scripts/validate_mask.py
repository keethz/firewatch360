"""Check the NRT static-source mask on months where FIRMS `type` is known.

Usage:  backend/.venv/bin/python backend/scripts/validate_mask.py [--grid 0.01]

For standard-processing rows (which have `type`), compares, per sensor and month:
  truth  = unique cell-days with type == 0
  masked = unique cell-days in cells never flagged type 1/2/3 in the archive (what NRT uses)
  raw    = unique cell-days with no type filter
Prints percent errors. Update MASK_VALIDATION in app/api/routes.py if they change.
"""
import argparse
import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.config import get_settings  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--grid", type=float, default=0.01)
    g = ap.parse_args().grid
    glob = get_settings().hotspots_glob.replace("'", "''")
    con = duckdb.connect()
    con.execute(f"""CREATE VIEW h AS SELECT *, CAST(floor(latitude / {g} + 1e-9) AS BIGINT) gy,
                    CAST(floor(longitude / {g} + 1e-9) AS BIGINT) gx
                    FROM read_parquet('{glob}', hive_partitioning=true)""")
    con.execute("CREATE TABLE mask AS SELECT DISTINCT gy, gx FROM h WHERE processing_level = 'archive' AND type IN (1, 2, 3)")
    rows = con.execute("""
        WITH d AS (SELECT DISTINCT sensor, acq_date, gy, gx, type FROM h
                   WHERE processing_level = 'standard' AND confidence_norm IN ('nominal', 'high'))
        SELECT sensor, strftime(acq_date, '%Y-%m') ym,
               count(DISTINCT (acq_date, gy, gx)) FILTER (WHERE type = 0) truth,
               count(DISTINCT (acq_date, gy, gx)) FILTER (WHERE NOT EXISTS
                     (SELECT 1 FROM mask m WHERE m.gy = d.gy AND m.gx = d.gx)) masked,
               count(DISTINCT (acq_date, gy, gx)) raw
        FROM d GROUP BY ALL ORDER BY ALL""").fetchall()
    print("sensor  month    truth  masked  mask_err%  raw_err%")
    for s, m, t, mk, r in rows:
        print(f"{s:6}  {m}  {t:6}  {mk:6}  {(mk - t) / t * 100:8.1f}  {(r - t) / t * 100:7.1f}")


if __name__ == "__main__":
    main()
