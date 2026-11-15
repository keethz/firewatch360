"""Build the cropland mask used by the "exclude agricultural burning" filter.

For every 0.01 deg grid cell that has ever had a FIRMS detection in the conterminous US, estimate the
fraction of the cell that is cropland or pasture/hay in NLCD 2021 (classes 82 and 81), by sampling a
4 x 4 grid of points inside the cell on an ~90 m NLCD raster fetched from the MRLC WCS in 2 x 2 deg tiles.

Output: data/processed/cropland_cells.parquet with columns gy, gx (0.01 deg cell index, same formula as
the harmonizer: floor(lat / 0.01 + 1e-9)) and crop_pct (0-100, cropland + pasture), crops_pct (class 82 only).
Only cells with any agricultural land are written. Takes a few minutes; tiles are cached in
data/cache/cropland so an interrupted run resumes.

    # from the firewatch360 folder
    backend/.venv/bin/python backend/scripts/prepare_cropland.py
"""
from __future__ import annotations

import io
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import duckdb
import httpx
import numpy as np
import pandas as pd
import tifffile
from pyproj import Transformer

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
OUT = DATA / "processed" / "cropland_cells.parquet"
CACHE = DATA / "cache" / "cropland"
NLCD_URL = "https://www.mrlc.gov/geoserver/ows"
COVERAGE = "mrlc_download__NLCD_2021_Land_Cover_L48"
CELL = 0.01
TILE = 200  # cells per tile side (2 deg)
SCALE = 1 / 3  # 30 m -> 90 m pixels
SAMPLES = 4  # per cell side
logging.getLogger("tifffile").setLevel(logging.ERROR)
to5070 = Transformer.from_crs(4326, 5070, always_xy=True)


def fire_cells() -> pd.DataFrame:
    glob = (DATA / "processed" / "hotspots" / "**" / "*.parquet").as_posix()
    return duckdb.sql(f"""
        SELECT DISTINCT CAST(floor(latitude / {CELL} + 1e-9) AS BIGINT) AS gy,
                        CAST(floor(longitude / {CELL} + 1e-9) AS BIGINT) AS gx
        FROM read_parquet('{glob}', hive_partitioning = true)
        WHERE latitude BETWEEN 24 AND 49.5 AND longitude BETWEEN -125 AND -66.5""").df()


def fetch_tile(client: httpx.Client, ty: int, tx: int) -> tuple[np.ndarray, tuple] | None:
    """NLCD raster covering tile (ty, tx), in EPSG:5070, with its bounds. Cached as .npz."""
    path = CACHE / f"{ty}_{tx}.npz"
    if path.exists():
        z = np.load(path)
        return z["arr"], tuple(z["bounds"])
    lat0, lon0 = ty * TILE * CELL, tx * TILE * CELL
    lons = np.linspace(lon0, lon0 + TILE * CELL, 9)
    lats = np.linspace(lat0, lat0 + TILE * CELL, 9)
    X, Y = to5070.transform(*np.meshgrid(lons, lats))
    x0, x1, y0, y1 = X.min() - 300, X.max() + 300, Y.min() - 300, Y.max() + 300
    for attempt in range(4):
        try:
            r = client.get(NLCD_URL, params=[
                ("service", "WCS"), ("version", "2.0.1"), ("request", "GetCoverage"), ("coverageId", COVERAGE),
                ("subset", f"X({x0},{x1})"), ("subset", f"Y({y0},{y1})"), ("scaleFactor", f"{SCALE:.6f}"),
                ("format", "image/geotiff")])
            if r.status_code == 200 and r.headers.get("content-type", "").startswith("image"):
                arr = tifffile.imread(io.BytesIO(r.content)).astype(np.uint8)
                bounds = (x0, y0, x1, y1)
                np.savez_compressed(path, arr=arr, bounds=np.array(bounds))
                return arr, bounds
            if r.status_code == 200:  # XML exception: tile outside the coverage (e.g. ocean)
                return None
        except httpx.HTTPError:
            pass
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"tile {ty},{tx} failed")


def tile_fractions(arr: np.ndarray, bounds: tuple, cells: pd.DataFrame) -> pd.DataFrame:
    x0, y0, x1, y1 = bounds
    h, w = arr.shape[:2]
    off = (np.arange(SAMPLES) + 0.5) / SAMPLES
    oy, ox = [a.ravel() for a in np.meshgrid(off, off)]
    lat = ((cells["gy"].to_numpy()[:, None] + oy) * CELL).ravel()
    lon = ((cells["gx"].to_numpy()[:, None] + ox) * CELL).ravel()
    X, Y = to5070.transform(lon, lat)
    col = np.clip(((X - x0) / (x1 - x0) * w).astype(int), 0, w - 1)
    row = np.clip(((y1 - Y) / (y1 - y0) * h).astype(int), 0, h - 1)
    cls = arr[row, col].reshape(len(cells), SAMPLES * SAMPLES)
    valid = (cls > 0).sum(1)
    ag = np.isin(cls, (81, 82)).sum(1)
    crops = (cls == 82).sum(1)
    with np.errstate(invalid="ignore", divide="ignore"):
        out = cells.assign(crop_pct=np.where(valid > 0, ag * 100 // np.maximum(valid, 1), 0).astype(np.uint8),
                           crops_pct=np.where(valid > 0, crops * 100 // np.maximum(valid, 1), 0).astype(np.uint8))
    return out[out["crop_pct"] > 0]


def main() -> int:
    CACHE.mkdir(parents=True, exist_ok=True)
    cells = fire_cells()
    cells["ty"], cells["tx"] = cells["gy"] // TILE, cells["gx"] // TILE
    groups = {k: g[["gy", "gx"]] for k, g in cells.groupby(["ty", "tx"])}
    print(f"{len(cells):,} fire cells in {len(groups)} tiles", flush=True)
    client = httpx.Client(timeout=180, headers={"User-Agent": "firewatch360-prototype"})
    parts, done, t0 = [], 0, time.time()

    def work(key):
        res = fetch_tile(client, *key)
        return None if res is None else tile_fractions(*res, groups[key])

    with ThreadPoolExecutor(6) as ex:
        futs = {ex.submit(work, k): k for k in groups}
        for f in as_completed(futs):
            part = f.result()
            if part is not None and len(part):
                parts.append(part)
            done += 1
            if done % 20 == 0 or done == len(groups):
                print(f"  {done}/{len(groups)} tiles, {time.time() - t0:.0f} s", flush=True)
    out = pd.concat(parts, ignore_index=True)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(OUT, index=False)
    share = (out["crop_pct"] >= 50).sum() / len(cells) * 100
    print(f"wrote {OUT} ({len(out):,} cells with agriculture; {share:.1f}% of fire cells are >= 50% agricultural)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
