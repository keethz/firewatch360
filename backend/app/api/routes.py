from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.services.aoi import AOI, AOIError, parse_aoi
from app.services.harmonize import CONFIDENCE_LEVELS, HarmonizationParams

router = APIRouter(prefix="/api")


def _store(request: Request):
    store = request.app.state.store
    if not store.available:
        raise HTTPException(503, "Processed data not found. Run backend/scripts/prepare_data.py.")
    return store


def aoi_param(
    bbox: str | None = Query(None, description="w,s,e,n in decimal degrees"),
    lat: float | None = None,
    lon: float | None = None,
    radius_km: float | None = None,
    polygon: str | None = Query(None, description="GeoJSON Polygon/MultiPolygon/Feature"),
) -> AOI:
    try:
        return parse_aoi(bbox, lat, lon, radius_km, polygon)
    except AOIError as exc:
        raise HTTPException(422, str(exc)) from exc


def params_param(
    grid_deg: float = 0.01,
    min_confidence: str = "nominal",
    vegetation_only: bool = True,
) -> HarmonizationParams:
    p = HarmonizationParams(grid_deg=grid_deg, min_confidence=min_confidence, vegetation_only=vegetation_only)
    try:
        p.validate()
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return p


@router.get("/health")
def health(request: Request) -> dict:
    store = request.app.state.store
    return {
        "status": "ok",
        "data_loaded": store.available,
        "firms_key_configured": bool(request.app.state.settings.firms_map_key),
    }


@router.get("/coverage")
def coverage(request: Request) -> dict:
    return _store(request).coverage()


@router.get("/calibration")
def calibration(
    request: Request,
    aoi: AOI = Depends(aoi_param),
    p: HarmonizationParams = Depends(params_param),
) -> dict:
    _store(request)
    h = request.app.state.harmonizer
    try:
        return {**h.meta(aoi, p), **h.calibration(aoi, p)}
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/harmonized")
def harmonized(
    request: Request,
    aoi: AOI = Depends(aoi_param),
    p: HarmonizationParams = Depends(params_param),
) -> dict:
    _store(request)
    h = request.app.state.harmonizer
    try:
        return {**h.meta(aoi, p), **h.harmonized(aoi, p)}
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


# ---------------------------------------------------------------- Phase 2: analytics and exports

import csv  # noqa: E402
import io  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
import tempfile  # noqa: E402
from datetime import date  # noqa: E402

from fastapi.responses import FileResponse, Response  # noqa: E402
from starlette.background import BackgroundTask  # noqa: E402

from app.services import analytics as an  # noqa: E402

MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
MAX_EXPORT_ROWS = 2_000_000


def window_param(
    start: str | None = Query(None, description="first month, YYYY-MM"),
    end: str | None = Query(None, description="last month, YYYY-MM"),
    year: int | None = Query(None, description="selected year (default: latest in window)"),
    baseline_start: int | None = None,
    baseline_end: int | None = None,
    z_threshold: float = Query(2.0, gt=0),
) -> dict:
    for name, v in (("start", start), ("end", end)):
        if v is not None and not MONTH_RE.match(v):
            raise HTTPException(422, f"{name} must be YYYY-MM")
    if start and end and start > end:
        raise HTTPException(422, "start must be <= end")
    return {"start": start, "end": end, "year": year, "b0": baseline_start, "b1": baseline_end, "z": z_threshold}


def _analysis(request: Request, aoi: AOI, p: HarmonizationParams, w: dict) -> dict:
    _store(request)
    h = request.app.state.harmonizer
    try:
        hz = h.harmonized(aoi, p)
        series = hz["series"]
        rows_all = an.window(series, w["start"], w["end"])
        if not rows_all:
            raise an.AnalyticsError("No months of data in the requested window")
        year = w["year"] if w["year"] is not None else int(rows_all[-1]["month"][:4])
        b0, b1 = an.resolve_baseline(series, year, w["b0"], w["b1"])
        clim = an.climatology(series, b0, b1)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    rows = an.window(an.annotate(series, clim, w["z"]), w["start"], w["end"])
    meta = h.meta(aoi, p)
    meta["window"] = [rows[0]["month"], rows[-1]["month"]]
    meta["selected_year"] = year
    meta["z_threshold"] = w["z"]
    meta["sensors"] = ["MODIS (scaled, before switch month)", "VIIRS S-NPP"]
    return {"meta": meta, "calibration": hz["calibration"], "climatology": clim, "rows": rows, "year": year}


@router.get("/calendar")
def calendar(request: Request, aoi: AOI = Depends(aoi_param), p: HarmonizationParams = Depends(params_param),
             w: dict = Depends(window_param)) -> dict:
    a = _analysis(request, aoi, p, w)
    clim = a["climatology"]
    return {**a["meta"], "calibration": a["calibration"],
            "climatology": {k: clim[k] for k in ("baseline", "baseline_years", "status")},
            "years": an.calendar_matrix(a["rows"])}


@router.get("/climatology")
def climatology(request: Request, aoi: AOI = Depends(aoi_param), p: HarmonizationParams = Depends(params_param),
                w: dict = Depends(window_param)) -> dict:
    a = _analysis(request, aoi, p, w)
    sel = [r for r in a["rows"] if int(r["month"][:4]) == a["year"]]
    return {**a["meta"], "calibration": a["calibration"], "climatology": a["climatology"], "selected_year_rows": sel}


@router.get("/anomalies")
def anomalies(request: Request, aoi: AOI = Depends(aoi_param), p: HarmonizationParams = Depends(params_param),
              w: dict = Depends(window_param)) -> dict:
    a = _analysis(request, aoi, p, w)
    rows = a["rows"]
    return {
        **a["meta"], "calibration": a["calibration"],
        "climatology": {k: a["climatology"][k] for k in ("baseline", "baseline_years", "status")},
        "method": "robust z = (value - baseline median) / (1.4826 * MAD), per calendar month",
        "latest_vs_median": an.latest_vs_median(rows),
        "current_month": an.month_to_date(
            request.app.state.harmonizer.current_month(aoi, p, a["climatology"]["baseline_years"]), w["z"]),
        "anomalies": [r for r in rows if r["anomaly"]],
        "rankings": an.rankings(rows, a["climatology"]),
    }


def _csv_header_lines(meta: dict, cal: dict) -> list[str]:
    aoi = {k: v for k, v in meta["aoi"].items() if k != "geometry"}
    return [
        "# FIREWATCH 360 harmonized monthly fire cell-days. Source: NASA FIRMS (MODIS C6.1, VIIRS S-NPP 375 m)",
        f"# aoi={aoi} window={meta['window'][0]}..{meta['window'][1]} grid_deg={meta['parameters']['grid_deg']}"
        f" min_confidence={meta['parameters']['min_confidence']} vegetation_only={meta['parameters']['vegetation_only']}",
        f"# k={cal['k']:.6f} calibration_scope={cal['scope']} calibration_status={cal['status']}"
        f" terra_c={cal['terra_c']:.6f} terra_scope={cal['terra_scope']} terra_applies_before={cal['terra_applies_before']}"
        f" switch_month={meta['windows']['switch_month']} data_as_of={meta['windows']['data_as_of']}",
        f"# {meta['caveat']}",
    ]


@router.get("/export/calendar.csv")
def export_calendar(request: Request, aoi: AOI = Depends(aoi_param), p: HarmonizationParams = Depends(params_param),
                    w: dict = Depends(window_param)) -> Response:
    a = _analysis(request, aoi, p, w)
    buf = io.StringIO()
    buf.write("\n".join(_csv_header_lines(a["meta"], a["calibration"])) + "\n")
    cols = ["year", "month", "harmonized_cell_days", "source", "modis_cell_days", "modis_terra_cell_days", "viirs_cell_days",
            "baseline_median", "pct_vs_median", "robust_z", "anomaly"]
    wr = csv.writer(buf)
    wr.writerow(cols)
    for r in a["rows"]:
        wr.writerow([r["month"][:4], r["month"][5:7], f"{r['harmonized']:.3f}", r["source"], r["modis"], r["modis_terra"],
                     "" if r["viirs"] is None else r["viirs"],
                     "" if r["clim_median"] is None else f"{r['clim_median']:.3f}",
                     "" if r["pct_vs_median"] is None else f"{r['pct_vs_median']:.2f}",
                     "" if r["z"] is None else f"{r['z']:.3f}", r["anomaly"] or ""])
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="firewatch360_calendar.csv"'})


@router.get("/export/hotspots.csv")
def export_hotspots(
    request: Request,
    aoi: AOI = Depends(aoi_param),
    start: date = Query(..., description="first UTC date, YYYY-MM-DD"),
    end: date = Query(..., description="last UTC date, YYYY-MM-DD"),
    sensors: str = Query("MODIS,VIIRS"),
) -> FileResponse:
    """Every hotspot row (all types, confidences and processing levels) in the AOI and date range."""
    store = _store(request)
    if start > end:
        raise HTTPException(422, "start must be <= end")
    sens = [s.strip().upper() for s in sensors.split(",") if s.strip()]
    if not sens or any(s not in ("MODIS", "VIIRS") for s in sens):
        raise HTTPException(422, "sensors must be a comma-separated subset of MODIS,VIIRS")
    where, params = aoi.sql()
    filt = f"{where} AND acq_date BETWEEN ? AND ? AND sensor IN ({','.join('?' * len(sens))})"
    params = [*params, start, end, *sens]
    n = store.query(f"SELECT count(*) AS n FROM hotspots WHERE {filt}", params, aoi=aoi)[0]["n"]
    if n > MAX_EXPORT_ROWS:
        raise HTTPException(413, f"{n:,} hotspots match; narrow the area or dates (limit {MAX_EXPORT_ROWS:,}).")
    cols = ("sensor, processing_level, latitude, longitude, acq_date, acq_time_raw AS acq_time_utc, "
            "acq_datetime_utc, satellite, instrument, confidence_raw, confidence_norm, frp, daynight, type, "
            "brightness_k, brightness_2_k, scan, track, version")
    fd, path = tempfile.mkstemp(suffix=".csv", prefix="fw360_")
    os.close(fd)
    store.copy_csv(f"SELECT {cols} FROM hotspots WHERE {filt} ORDER BY acq_datetime_utc, sensor", params, path, aoi=aoi)
    return FileResponse(path, media_type="text/csv", filename="firewatch360_hotspots.csv",
                        headers={"X-Row-Count": str(n)}, background=BackgroundTask(os.unlink, path))


# ---------------------------------------------------------------- Phase 3: hotspots for the map

MAX_MAP_POINTS = 50_000


@router.get("/hotspots")
def hotspots(
    request: Request,
    aoi: AOI = Depends(aoi_param),
    start: date = Query(..., description="first UTC date, YYYY-MM-DD"),
    end: date = Query(..., description="last UTC date, YYYY-MM-DD"),
    sensors: str = Query("MODIS,VIIRS"),
    min_confidence: str = "nominal",
    vegetation_only: bool = True,
    limit: int = Query(20_000, ge=1, le=MAX_MAP_POINTS),
) -> dict:
    """Hotspots as GeoJSON points. Over `limit`, a deterministic spatial-temporal sample is returned
    and `sampled` is true; `total` is always the full matching count.

    `vegetation_only` keeps FIRMS type 0. NRT rows have no type, so they are kept and marked
    `processing_level: nrt` (provisional)."""
    store = _store(request)
    if start > end:
        raise HTTPException(422, "start must be <= end")
    if (end - start).days > 366 * 30:
        raise HTTPException(422, "date range too long")
    sens = [s.strip().upper() for s in sensors.split(",") if s.strip()]
    if not sens or any(s not in ("MODIS", "VIIRS") for s in sens):
        raise HTTPException(422, "sensors must be a comma-separated subset of MODIS,VIIRS")
    if min_confidence not in CONFIDENCE_LEVELS:
        raise HTTPException(422, f"min_confidence must be one of {sorted(CONFIDENCE_LEVELS)}")
    conf = CONFIDENCE_LEVELS[min_confidence]
    where, params = aoi.sql()
    filt = (f"{where} AND acq_date BETWEEN ? AND ? AND sensor IN ({','.join('?' * len(sens))})"
            f" AND confidence_norm IN ({','.join('?' * len(conf))})")
    params = [*params, start, end, *sens, *conf]
    if vegetation_only:
        filt += " AND (type = 0 OR processing_level = 'nrt')"
    agg = store.query(
        f"""SELECT count(*) AS n, count(*) FILTER (WHERE sensor = 'MODIS') AS modis,
                   count(*) FILTER (WHERE sensor = 'VIIRS') AS viirs,
                   count(*) FILTER (WHERE processing_level = 'nrt') AS nrt,
                   min(acq_date) AS first, max(acq_date) AS last
            FROM hotspots WHERE {filt}""", params, aoi=aoi)[0]
    rows = store.query(
        f"""SELECT sensor, processing_level, latitude, longitude, acq_date, acq_time_raw, frp,
                   confidence_norm, daynight, type
            FROM hotspots WHERE {filt}
            ORDER BY hash(latitude, longitude, acq_datetime_utc, sensor) LIMIT ?""",
        [*params, limit], aoi=aoi)
    feats = [{
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [round(r["longitude"], 5), round(r["latitude"], 5)]},
        "properties": {
            "sensor": r["sensor"], "level": r["processing_level"], "date": r["acq_date"].isoformat(),
            "time_utc": (r["acq_time_raw"] or "").zfill(4), "frp": r["frp"],
            "confidence": r["confidence_norm"], "daynight": r["daynight"], "type": r["type"],
        },
    } for r in rows]
    return {
        "type": "FeatureCollection",
        "features": feats,
        "total": agg["n"],
        "returned": len(feats),
        "sampled": agg["n"] > len(feats),
        "counts": {"MODIS": agg["modis"], "VIIRS": agg["viirs"], "nrt_provisional": agg["nrt"]},
        "first_date": agg["first"].isoformat() if agg["first"] else None,
        "last_date": agg["last"].isoformat() if agg["last"] else None,
        "aoi": {k: v for k, v in aoi.describe().items()},
        "filters": {"start": start.isoformat(), "end": end.isoformat(), "sensors": sens,
                    "min_confidence": min_confidence, "vegetation_only": vegetation_only},
        "note": "Raw detections (not harmonized). Each point is one satellite pixel centre. NRT rows are provisional.",
    }


# ---------------------------------------------------------------- Phase 5: current month

@router.get("/current")
def current(request: Request, aoi: AOI = Depends(aoi_param), p: HarmonizationParams = Depends(params_param),
            w: dict = Depends(window_param)) -> dict:
    """Month-to-date (near-real-time, provisional) vs the same days of that month in baseline years."""
    a = _analysis(request, aoi, p, w)
    cur = an.month_to_date(request.app.state.harmonizer.current_month(aoi, p, a["climatology"]["baseline_years"]), w["z"])
    return {**a["meta"], "calibration": a["calibration"],
            "climatology": {k: a["climatology"][k] for k in ("baseline", "baseline_years", "status")},
            "current_month": cur,
            "method": ("Month-to-date VIIRS NRT fire cell-days (static-source mask instead of the missing type field) "
                       "vs harmonized cell-days for the same calendar days in each baseline year."),
            "mask_validation": MASK_VALIDATION}


# Measured 2026-11-14 on standard-processing 2026 months, where `type` is known: masked count vs
# true type-0 count. Recomputed by backend/scripts/validate_mask.py.
MASK_VALIDATION = {
    "VIIRS": {"months": "2026-01..2026-07", "error_pct_range": [-6.5, -2.3]},
    "MODIS": {"months": "2026-01..2026-08", "error_pct_range": [0.0, 0.4]},
    "note": "Masked NRT counts slightly under-count vegetation fires (VIIRS). Without the mask they over-count by 14-44%.",
}


# ---------------------------------------------------------------- Phase 5: live FIRMS NRT proxy

from collections import Counter  # noqa: E402

from app.services.nrt import MAX_DAYS, NrtError, to_feature  # noqa: E402


@router.get("/nrt")
def nrt(
    request: Request,
    aoi: AOI = Depends(aoi_param),
    days: int = Query(3, ge=1, le=MAX_DAYS),
    sensors: str = Query("VIIRS,MODIS"),
    min_confidence: str = "nominal",
) -> dict:
    """Live near-real-time hotspots from the FIRMS area API (last `days` UTC days, including today).
    Cached on disk; recent chunks refresh after 3 hours. Not type-filtered: may include static sources."""
    sens = [s.strip().upper() for s in sensors.split(",") if s.strip()]
    if not sens or any(s not in ("MODIS", "VIIRS") for s in sens):
        raise HTTPException(422, "sensors must be a comma-separated subset of MODIS,VIIRS")
    if min_confidence not in CONFIDENCE_LEVELS:
        raise HTTPException(422, f"min_confidence must be one of {sorted(CONFIDENCE_LEVELS)}")
    client = request.app.state.nrt
    try:
        rows = client.fetch(aoi, days, sens)
    except NrtError as exc:
        raise HTTPException(502 if client.key else 503, str(exc)) from exc
    keep = set(CONFIDENCE_LEVELS[min_confidence])
    feats = [f for f in map(to_feature, rows) if f["properties"]["confidence"] in keep]
    by_day = Counter((f["properties"]["date"], f["properties"]["sensor"]) for f in feats)
    return {
        "type": "FeatureCollection",
        "features": feats,
        "total": len(feats),
        "counts": {s: sum(1 for f in feats if f["properties"]["sensor"] == s) for s in sens},
        "by_day": [{"date": d, "sensor": s, "n": n} for (d, s), n in sorted(by_day.items())],
        "window": [client._chunks(days)[0][0].isoformat(), client.today().isoformat()],
        "sources": {s: f"FIRMS {'VIIRS_SNPP_NRT' if s == 'VIIRS' else 'MODIS_NRT'}" for s in sens},
        "note": "Live near-real-time detections, provisional. Not filtered by fire type (may include static sources).",
    }


# ---------------------------------------------------------------- Fire Risk Brief

from app.services.brief import build_brief  # noqa: E402
from app.services.harmonize import harmonize as _harmonize  # noqa: E402


@router.get("/brief")
def brief(request: Request, aoi: AOI = Depends(aoi_param), p: HarmonizationParams = Depends(params_param),
          w: dict = Depends(window_param)) -> dict:
    """Plain-language Fire Risk Brief for the area. See app/services/brief.py for the rules."""
    a = _analysis(request, aoi, p, w)
    h = request.app.state.harmonizer
    win = a["meta"]["windows"]
    cal = h.calibration(aoi, p)["calibration"]
    tc = h.terra_correction(aoi, p)
    rows = a["rows"]
    # The current month-to-date is used only when the requested window reaches the present.
    live = win["current"] is not None and (w["end"] is None or w["end"] >= win["current"]["month"])
    cur = h.current_month(aoi, p, a["climatology"]["baseline_years"]) if live else None
    out = build_brief(
        series=rows, windows=win, calibration=cal, terra=tc, clim=a["climatology"],
        rankings=an.rankings(rows, a["climatology"]), current=cur,
        current_counts=h.current_counts(aoi, p) if live else None,
        k=cal["k"], c=tc["c"], z_threshold=w["z"],
        harmonize_fn=lambda hist, months: _harmonize(hist, cal["k"], months, win["switch_month"], tc["c"], win["aqua_month"]),
    )
    aoi_desc = {k: v for k, v in a["meta"]["aoi"].items() if k != "geometry"}
    return {**out, "aoi": aoi_desc, "generated_utc": __import__("datetime").datetime.now(
                __import__("datetime").timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "data_as_of": {"final": win["data_as_of"], "nrt": win["nrt_as_of"]},
            "caveat": "Describes observed satellite fire detections. Not a forecast of fire danger or spread."}


# ---------------------------------------------------------------- Human impact

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from app.services import impact as imp  # noqa: E402

MAX_IMPACT_FIRE_POINTS = 200_000


def _fire_points(store, aoi: AOI, start: date, end: date, sens: list[str], min_confidence: str, veg: bool):
    where, params = aoi.sql()
    conf = CONFIDENCE_LEVELS[min_confidence]
    filt = (f"{where} AND acq_date BETWEEN ? AND ? AND sensor IN ({','.join('?' * len(sens))})"
            f" AND confidence_norm IN ({','.join('?' * len(conf))})")
    params = [*params, start, end, *sens, *conf]
    if veg:
        filt += " AND (type = 0 OR processing_level = 'nrt')"
    rows = store.query(f"SELECT longitude AS x, latitude AS y FROM hotspots WHERE {filt} LIMIT ?",
                       [*params, MAX_IMPACT_FIRE_POINTS + 1], aoi=aoi)
    return np.array([r["x"] for r in rows], float), np.array([r["y"] for r in rows], float)


@router.get("/impact")
def impact(
    request: Request,
    aoi: AOI = Depends(aoi_param),
    start: date = Query(..., description="first UTC date of fire detections, YYYY-MM-DD"),
    end: date = Query(..., description="last UTC date, YYYY-MM-DD"),
    buffer_km: float = Query(5.0, gt=0, le=50),
    sensors: str = Query("MODIS,VIIRS"),
    min_confidence: str = "nominal",
    vegetation_only: bool = True,
) -> dict:
    """What could the fire activity in this area affect? Assets inside the area and within
    `buffer_km` of its fire detections. See app/services/impact.py for sources and rules."""
    store = _store(request)
    if start > end:
        raise HTTPException(422, "start must be <= end")
    sens = [s.strip().upper() for s in sensors.split(",") if s.strip()]
    if not sens or any(s not in ("MODIS", "VIIRS") for s in sens):
        raise HTTPException(422, "sensors must be a comma-separated subset of MODIS,VIIRS")
    if min_confidence not in CONFIDENCE_LEVELS:
        raise HTTPException(422, f"min_confidence must be one of {sorted(CONFIDENCE_LEVELS)}")
    if aoi.kind == "region":
        return {"status": "aoi_required", "message": "Select an area (preset, drawn shape or point + radius) to see what fire activity could affect."}
    fx, fy = _fire_points(store, aoi, start, end, sens, min_confidence, vegetation_only)
    if len(fx) > MAX_IMPACT_FIRE_POINTS:
        raise HTTPException(413, f"More than {MAX_IMPACT_FIRE_POINTS:,} detections; narrow the area or dates.")
    out = imp.summarize(request.app.state.impact_static, request.app.state.impact_live, aoi, fx, fy, buffer_km)
    out["filters"] = {"start": start.isoformat(), "end": end.isoformat(), "sensors": sens,
                      "min_confidence": min_confidence, "vegetation_only": vegetation_only}
    out["note"] = ("Exposure means located inside the area or within the buffer distance of a satellite fire "
                   "detection. It is not a damage assessment or a forecast.")
    return out


@router.get("/impact/features")
def impact_features(request: Request, layer: str, aoi: AOI = Depends(aoi_param), limit: int = Query(5000, ge=1, le=20000)) -> dict:
    """GeoJSON of a static impact layer inside the area (points, or road lines)."""
    st = request.app.state.impact_static
    if not st.available:
        raise HTTPException(503, "Impact layers not built. Run backend/scripts/prepare_impact.py.")
    if aoi.kind == "region":
        raise HTTPException(422, "Select an area first")
    if layer not in (*imp.POINT_LAYERS, "roads"):
        raise HTTPException(422, f"layer must be one of {[*imp.POINT_LAYERS, 'roads']}")
    geo = imp.make_geo(aoi)
    if layer == "roads":
        sub = st.roads_in(geo)
        feats = [{"type": "Feature", "geometry": imp.mapping(g), "properties": {"layer": "roads", "type": t, "name": n}}
                 for g, t, n in zip(sub["geom"], sub["type"], sub["name"])][:limit]
        return {"type": "FeatureCollection", "features": feats, "total": len(sub)}
    sub = st.points_in(layer, geo)
    total = len(sub)
    if total > limit:
        sub = sub.nlargest(limit, "pop_max") if layer == "places" else sub.sample(limit, random_state=0)
    keep = [c for c in sub.columns if c not in ("latitude", "longitude", "source_date_ms")]
    feats = [{"type": "Feature",
              "geometry": {"type": "Point", "coordinates": [round(r.longitude, 5), round(r.latitude, 5)]},
              "properties": {"layer": layer, **{c: imp._py(getattr(r, c)) for c in keep}}}
             for r in sub.itertuples(index=False)]
    return {"type": "FeatureCollection", "features": feats, "total": total, "sampled": total > len(feats)}
