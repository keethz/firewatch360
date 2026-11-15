"""Spatial anomaly map: which places had unusual fire activity in a given month.

Method (see docs/SCIENTIFIC_METHOD.md, "Anomaly map"):
 1. Count fire cell-days (0.01 deg cell x UTC day, the same unit as the harmonized series) and
    group them into coarse map cells (default 0.25 deg, about 25 km).
 2. For the selected calendar month, build each map cell's value in every year with the same
    harmonization as the series: k*c*Terra before Aqua, k*MODIS before VIIRS, observed VIIRS after.
    k and c are the factors of the selected area (or the US-wide factors).
 3. Baseline = that calendar month in the baseline years (target year excluded).
    Robust z = (value - median) / scale, with scale = max(1.4826 * MAD, sqrt(max(median, 1))).
    The floor stops cells that were usually zero from scoring huge z values on a handful of
    detections; it is roughly the Poisson standard deviation of the median count.
 4. A cell is reported if it had detections in the target month or its baseline median > 0.
    It is flagged when z >= threshold AND value >= min_value cell-days.

Prototype parameters, not a validated product.
"""
from __future__ import annotations

import numpy as np

from app.services.aoi import AOI
from app.services.harmonize import CONFIDENCE_LEVELS, PROCESSING_LEVELS, HarmonizationParams
from app.services.store import HotspotStore


def cell_month_counts(store: HotspotStore, aoi: AOI, p: HarmonizationParams, month: int, cell_deg: float,
                      levels: tuple[str, ...], years: tuple[int, int]) -> dict[tuple[str, int, int, int], int]:
    """Fire cell-days per (sensor key, year, map-cell y, map-cell x) for one calendar month."""
    where, params = aoi.sql()
    conf = CONFIDENCE_LEVELS[p.min_confidence]
    filt = [where, f"processing_level IN ({','.join('?' * len(levels))})",
            f"confidence_norm IN ({','.join('?' * len(conf))})",
            "month(acq_date) = ?", "year(acq_date) BETWEEN ? AND ?"]
    params = [*params, *levels, *conf, month, years[0], years[1]]
    use_mask = p.vegetation_only and "nrt" in levels
    mask_cte, mask_params, veg = "", [], ""
    if p.vegetation_only:
        veg = ("WHERE type = 0 OR (processing_level = 'nrt' AND NOT EXISTS "
               "(SELECT 1 FROM static_mask m WHERE m.gy = g.gy AND m.gx = g.gx))") if use_mask else "WHERE type = 0"
    if use_mask:
        mask_cte = """static_mask AS (
            SELECT DISTINCT CAST(floor(latitude / ? + 1e-9) AS BIGINT) AS gy,
                            CAST(floor(longitude / ? + 1e-9) AS BIGINT) AS gx
            FROM hotspots WHERE processing_level = 'archive' AND type IN (1, 2, 3)
        ),"""
        mask_params = [p.grid_deg, p.grid_deg]
    sql = f"""
        WITH {mask_cte}
        g AS (
            SELECT sensor, satellite, acq_date, type, processing_level,
                   CAST(floor(latitude / ? + 1e-9) AS BIGINT) AS gy,
                   CAST(floor(longitude / ? + 1e-9) AS BIGINT) AS gx,
                   CAST(floor(latitude / ? + 1e-9) AS BIGINT) AS cy,
                   CAST(floor(longitude / ? + 1e-9) AS BIGINT) AS cx
            FROM hotspots WHERE {' AND '.join(filt)}
        ), base AS (SELECT * FROM g {veg}),
        cells AS (
            SELECT DISTINCT sensor AS k, acq_date, gy, gx, cy, cx FROM base
            UNION ALL
            SELECT DISTINCT 'MODIS_TERRA' AS k, acq_date, gy, gx, cy, cx FROM base
            WHERE sensor = 'MODIS' AND satellite = 'Terra'
        )
        SELECT k, year(acq_date) AS y, cy, cx, count(*) AS n FROM cells GROUP BY 1, 2, 3, 4
    """
    rows = store.query(sql, [*mask_params, p.grid_deg, p.grid_deg, cell_deg, cell_deg, *params], aoi=aoi)
    return {(r["k"], int(r["y"]), int(r["cy"]), int(r["cx"])): int(r["n"]) for r in rows}


def harmonized_value(counts: dict, cell: tuple[int, int], year: int, month: int, k: float, c: float,
                     switch_month: str, aqua_month: str | None) -> float:
    m = f"{year:04d}-{month:02d}"
    cy, cx = cell
    if aqua_month is not None and m < aqua_month:
        return k * c * counts.get(("MODIS_TERRA", year, cy, cx), 0)
    if m < switch_month:
        return k * counts.get(("MODIS", year, cy, cx), 0)
    return float(counts.get(("VIIRS", year, cy, cx), 0))


def robust_scale(values: np.ndarray) -> tuple[float, float]:
    med = float(np.median(values))
    mad = float(np.median(np.abs(values - med)))
    return med, max(1.4826 * mad, float(np.sqrt(max(med, 1.0))))


def build_map(store: HotspotStore, aoi: AOI, p: HarmonizationParams, target: str, baseline: tuple[int, int],
              k: float, c: float, windows: dict, cell_deg: float = 0.25, z_threshold: float = 2.0,
              min_value: float = 10.0, provisional: bool = False) -> dict:
    ty, tm = map(int, target.split("-"))
    b0, b1 = baseline
    years = [y for y in range(b0, b1 + 1) if y != ty]
    first_month = windows["series_start"]
    years = [y for y in years if f"{y:04d}-{tm:02d}" >= first_month]
    if len(years) < 5:
        raise ValueError("Need at least 5 baseline years for this calendar month")
    sw, aq = windows["switch_month"], windows["aqua_month"]
    hist = cell_month_counts(store, aoi, p, tm, cell_deg, PROCESSING_LEVELS, (min(years), max(years)))
    tgt_levels = ("nrt",) if provisional else PROCESSING_LEVELS
    tgt = cell_month_counts(store, aoi, p, tm, cell_deg, tgt_levels, (ty, ty))

    cells = {(cy, cx) for (_, _, cy, cx) in hist} | {(cy, cx) for (_, _, cy, cx) in tgt}
    out, flagged = [], 0
    for cell in cells:
        base = np.array([harmonized_value(hist, cell, y, tm, k, c, sw, aq) for y in years])
        if provisional:  # NRT VIIRS in the provisional window; MODIS NRT x k otherwise
            cy, cx = cell
            v = tgt.get(("VIIRS", ty, cy, cx))
            value = float(v) if v is not None else k * tgt.get(("MODIS", ty, cy, cx), 0)
        else:
            value = harmonized_value(tgt, cell, ty, tm, k, c, sw, aq)
        med, scale = robust_scale(base)
        if value == 0 and med == 0:
            continue
        z = (value - med) / scale
        is_anom = bool(z >= z_threshold and value >= min_value)
        flagged += is_anom
        rank = 1 + int((base > value).sum())
        out.append({
            "cell": [cell[0], cell[1]],
            "bbox": [round(cell[1] * cell_deg, 4), round(cell[0] * cell_deg, 4),
                     round((cell[1] + 1) * cell_deg, 4), round((cell[0] + 1) * cell_deg, 4)],
            "value": round(value, 1), "median": round(med, 1), "scale": round(scale, 2),
            "z": round(float(z), 2), "pct_vs_median": None if med == 0 else round(100 * (value - med) / med, 1),
            "baseline_max": round(float(base.max()), 1), "rank": rank, "of": len(years) + 1,
            "anomaly": is_anom, "below": bool(z <= -z_threshold and med >= min_value),
        })
    out.sort(key=lambda r: -r["z"])
    return {
        "month": target, "provisional": provisional, "cell_deg": cell_deg,
        "baseline_years": [min(years), max(years)], "n_baseline_years": len(years),
        "z_threshold": z_threshold, "min_value": min_value, "k": k, "c": c,
        "cells": out, "n_cells": len(out), "n_anomalous": flagged,
        "method": ("Fire cell-days (0.01 deg x day) grouped into map cells; harmonized per year with the area's k and c; "
                   "robust z = (value - median) / max(1.4826 MAD, sqrt(median)); flagged if z >= threshold and value >= min_value."),
    }


def to_geojson(result: dict, limit: int = 4000) -> dict:
    feats = []
    for r in result["cells"][:limit]:
        w, s, e, n = r["bbox"]
        feats.append({"type": "Feature", "properties": {k: v for k, v in r.items() if k not in ("bbox",)},
                      "geometry": {"type": "Polygon", "coordinates": [[[w, s], [e, s], [e, n], [w, n], [w, s]]]}})
    return {"type": "FeatureCollection", "features": feats}

