"""Fetch FIRMS hotspots for the period after the yearly archive (2026-01-01 -> latest) via the area API.

Usage:  backend/.venv/bin/python backend/scripts/fetch_2026.py [--start 2026-01-01]

- Queries the FIRMS availability endpoint, then for each source fetches 10-day chunks over
  three US bounding boxes (CONUS, Alaska, Hawaii). Standard-processing (SP) data is used
  where available, NRT after it.
- Raw responses are cached unchanged in data/raw/api/<SOURCE>/<box>_<start>_<days>.csv
  (existing files are skipped, so the script can be rerun safely).
- Clipping to the US boundary happens in prepare_data.py, not here.
"""
from __future__ import annotations

import argparse
import csv
import io
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.config import get_settings  # noqa: E402

API = "https://firms.modaps.eosdis.nasa.gov/api"
# Non-overlapping boxes (split at lon -129) that together cover the 50 US states.
BOXES = {
    "conus": "-129,24,-66,50",
    "alaska": "-180,50,-129,72",
    "hawaii": "-161,18,-154,23",
}
# Sensor -> (standard source, near-real-time source)
SENSORS = {"MODIS": ("MODIS_SP", "MODIS_NRT"), "VIIRS": ("VIIRS_SNPP_SP", "VIIRS_SNPP_NRT")}
MAX_DAYS = 5  # FIRMS area API rejects day ranges above 5 ("Expects [1..5]")


def availability(client: httpx.Client, key: str) -> dict[str, tuple[date, date]]:
    r = client.get(f"{API}/data_availability/csv/{key}/all")
    r.raise_for_status()
    out = {}
    for row in csv.DictReader(io.StringIO(r.text)):
        out[row["data_id"]] = (date.fromisoformat(row["min_date"]), date.fromisoformat(row["max_date"]))
    return out


def fetch(client: httpx.Client, url: str) -> str:
    for attempt in range(4):
        try:
            r = client.get(url)
            if r.status_code == 200 and r.text.startswith("latitude"):
                return r.text
            msg = r.text[:200]
            if r.status_code == 400:  # bad request: retrying won't help
                break
        except httpx.HTTPError as e:
            msg = str(e)
        time.sleep(2 ** attempt * 2)
    raise RuntimeError(f"FIRMS request failed: {msg}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="2026-01-01")
    args = p.parse_args()
    s = get_settings()
    if not s.firms_map_key:
        raise SystemExit("FIRMS_MAP_KEY not set in .env")
    out_root = Path(s.data_dir) / "raw" / "api"
    start = date.fromisoformat(args.start)

    with httpx.Client(timeout=120) as client:
        avail = availability(client, s.firms_map_key)
        for sensor, (sp, nrt) in SENSORS.items():
            sp_end = avail[sp][1]
            nrt_start, nrt_end = avail[nrt]
            plan = [(sp, start, sp_end), (nrt, max(nrt_start, sp_end + timedelta(days=1)), nrt_end)]
            for source, a, b in plan:
                if a > b:
                    continue
                print(f"{sensor}: {source} {a} -> {b}")
                (out_root / source).mkdir(parents=True, exist_ok=True)
                d = a
                while d <= b:
                    days = min(MAX_DAYS, (b - d).days + 1)
                    for box, bbox in BOXES.items():
                        f = out_root / source / f"{box}_{d}_{days}.csv"
                        if f.exists():
                            continue
                        url = f"{API}/area/csv/{s.firms_map_key}/{source}/{bbox}/{days}/{d}"
                        f.write_text(fetch(client, url))
                        time.sleep(0.3)
                    d += timedelta(days=days)
    print("done")


if __name__ == "__main__":
    main()
