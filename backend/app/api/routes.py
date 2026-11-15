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
    disp = {"Content-Disposition": 'attachment; filename="firewatch360_hotspots.csv"', "X-Row-Count": str(n)}
    if n > MAX_EXPORT_ROWS:
        # A downloaded file must explain itself, so the error is written as CSV comment text.
        return Response(f"# {n:,} hotspots match; this is over the export limit of {MAX_EXPORT_ROWS:,} rows.\n"
                        "# Narrow the area or the dates and download again.\n", status_code=413,
                        media_type="text/csv", headers=disp)
    if n == 0:
        why = " VIIRS data start on 2012-01-19." if sens == ["VIIRS"] and start < date(2012, 1, 19) else ""
        return Response(f"# No hotspots match this area, dates ({start} to {end}) and sensors ({','.join(sens)}).{why}\n"
                        + "sensor,processing_level,latitude,longitude,acq_date,acq_time_utc,acq_datetime_utc,satellite,"
                          "instrument,confidence_raw,confidence_norm,frp,daynight,type,brightness_k,brightness_2_k,scan,track,version\n",
                        media_type="text/csv", headers=disp)
    cols = ("sensor, processing_level, latitude, longitude, acq_date, lpad(CAST(acq_time_raw AS VARCHAR), 4, '0') AS acq_time_utc, "
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


# ---------------------------------------------------------------- Anomaly map + AI explanations

from app.services import anomaly_map as amap  # noqa: E402

_AMAP_CACHE: dict = {}


def _anomaly_map(request: Request, aoi: AOI, p: HarmonizationParams, month: str | None, cell_deg: float,
                 z_threshold: float, min_value: float, baseline_start: int | None, baseline_end: int | None) -> dict:
    _store(request)
    h = request.app.state.harmonizer
    win = h.windows()
    latest = win["provisional_end"] or win["series_end"]
    month = month or latest
    if not MONTH_RE.match(month):
        raise HTTPException(422, "month must be YYYY-MM")
    if month < win["series_start"] or month > latest:
        raise HTTPException(422, f"month must be between {win['series_start']} and {latest}")
    provisional = month > win["series_end"]
    se = win["series_end"]
    last_complete = int(se[:4]) if se.endswith("-12") else int(se[:4]) - 1
    first_complete = int(win["series_start"][:4]) + (0 if win["series_start"].endswith("-01") else 1)
    b0 = baseline_start or first_complete
    b1 = baseline_end or min(last_complete, int(month[:4]) - 1 if int(month[:4]) - 1 >= b0 + 4 else last_complete)
    key = (aoi.key, p, month, cell_deg, z_threshold, min_value, b0, b1)
    if key not in _AMAP_CACHE:
        cal = h.calibration(aoi, p)["calibration"]
        tc = h.terra_correction(aoi, p)
        try:
            res = amap.build_map(h.store, aoi, p, month, (b0, b1), cal["k"], tc["c"], win, cell_deg,
                                 z_threshold, min_value, provisional)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        res["calibration_scope"] = cal["scope"]
        if len(_AMAP_CACHE) > 64:
            _AMAP_CACHE.clear()
        _AMAP_CACHE[key] = res
    return _AMAP_CACHE[key]


@router.get("/anomaly-map")
def anomaly_map(request: Request, aoi: AOI = Depends(aoi_param), p: HarmonizationParams = Depends(params_param),
                month: str | None = Query(None, description="YYYY-MM (default: latest complete month)"),
                cell_deg: float = Query(0.25, ge=0.05, le=1.0), z_threshold: float = Query(2.0, gt=0),
                min_value: float = Query(10, ge=0), baseline_start: int | None = None,
                baseline_end: int | None = None) -> dict:
    """Map cells whose fire activity this month is unusual against the same month in past years."""
    res = _anomaly_map(request, aoi, p, month, cell_deg, z_threshold, min_value, baseline_start, baseline_end)
    return {**{k: v for k, v in res.items() if k != "cells"}, "top": res["cells"][:15],
            "geojson": amap.to_geojson(res)}


def _cell_facts(res: dict, cy: int, cx: int) -> dict:
    cell = next((c for c in res["cells"] if c["cell"] == [cy, cx]), None)
    if cell is None:
        raise HTTPException(404, "No fire activity in that map cell for this month or its baseline")
    w, s, e, n = cell["bbox"]
    return {
        "what": "One map cell compared with the same calendar month in earlier years",
        "location": {"lat_center": round((s + n) / 2, 3), "lon_center": round((w + e) / 2, 3),
                     "cell_size_deg": res["cell_deg"], "approx_size_km": round(res["cell_deg"] * 111)},
        "month": res["month"], "provisional_nrt_data": res["provisional"],
        "value_cell_days": cell["value"], "baseline_median_cell_days": cell["median"],
        "baseline_years": res["baseline_years"], "baseline_max_cell_days": cell["baseline_max"],
        "pct_vs_median": cell["pct_vs_median"],
        "times_median": None if not cell["median"] else round(cell["value"] / cell["median"], 1),
        "robust_z": cell["z"], "flagged_unusual": cell["anomaly"], "flag_threshold_z": res["z_threshold"],
        "rank_among_years": f"{cell['rank']} of {cell['of']} (1 = highest)",
        "calibration": {"k_viirs_per_modis": round(res["k"], 3), "c_pre_aqua": round(res["c"], 3),
                        "scope": res.get("calibration_scope")},
        "sensors": "MODIS before 2012-02 scaled to VIIRS-equivalent; VIIRS S-NPP after",
    }


@router.get("/explain/cell")
def explain_cell(request: Request, cy: int, cx: int, aoi: AOI = Depends(aoi_param),
                 p: HarmonizationParams = Depends(params_param), month: str | None = None,
                 cell_deg: float = Query(0.25, ge=0.05, le=1.0), z_threshold: float = Query(2.0, gt=0),
                 min_value: float = Query(10, ge=0)) -> dict:
    """AI explanation of one anomaly-map cell (statistics computed here, wording by Gemini)."""
    res = _anomaly_map(request, aoi, p, month, cell_deg, z_threshold, min_value, None, None)
    facts = _cell_facts(res, cy, cx)
    facts["drought"] = _drought_fact(request.app, facts["location"], res["cell_deg"], res["month"])
    return {"facts": facts, **request.app.state.explainer.explain(facts)}


@router.get("/explain/brief")
def explain_brief(request: Request, aoi: AOI = Depends(aoi_param), p: HarmonizationParams = Depends(params_param),
                  w: dict = Depends(window_param)) -> dict:
    """AI explanation of the Fire Risk Brief numbers for the selected area."""
    b = brief(request, aoi, p, w)
    drop = {"generated_utc", "caveat"}
    facts = {"what": "Fire Risk Brief for a selected area", **{k: v for k, v in b.items() if k not in drop}}
    try:
        c = area_context(request.app, aoi, b["reference"]["month"])
        d = c.get("drought_month", {})
        if d.get("status") == "ok":
            facts["drought_same_month"] = {"summary": d["text"], "dsci_0_500": d["dsci"], "week": d["week"],
                                           "source": "U.S. Drought Monitor"}
        if c["fire_weather"].get("status") == "ok" and b["reference"]["kind"] == "month_to_date":
            facts["active_fire_weather_alerts_now"] = [a["event"] for a in c["fire_weather"]["active"]]
    except Exception:  # context is optional for the explanation
        pass
    return {"facts": facts, **request.app.state.explainer.explain(facts)}


# ---------------------------------------------------------------- Drought and fire-weather context

import httpx  # noqa: E402

from app.services import context as ctx  # noqa: E402

CONUS_BBOX = "-125,24,-66,50"


def _context_geo(aoi: AOI):
    return imp.make_geo(parse_aoi(bbox=CONUS_BBOX) if aoi.kind == "region" else aoi)


def area_context(app, aoi: AOI, month: str | None, history: bool = False) -> dict:
    """Drought for `month` (and the latest week), plus active fire-weather alerts."""
    cli: ctx.ContextClient = app.state.context
    geo = _context_geo(aoi)
    out: dict = {"source": {"drought": "U.S. Drought Monitor (NDMC, USDA, NOAA), weekly maps",
                            "fire_weather": "NWS active Red Flag Warnings and Fire Weather Watches"},
                 "note": "Context only. Not a fire-danger forecast."}
    weeks = {}
    if month:
        weeks["month"] = ctx.usdm_week_for_month(month, cli.today())
    weeks["latest"] = ctx.latest_usdm_week(cli.today())
    for name, wk in weeks.items():
        if wk is None:
            out[f"drought_{name}"] = {"status": "unavailable", "message": "No Drought Monitor map for that month (maps start 2000-01)."}
            continue
        try:
            out[f"drought_{name}"] = {"status": "ok", **cli.drought(geo.geom, geo.fwd, wk)}
        except (httpx.HTTPError, ValueError, KeyError):
            out[f"drought_{name}"] = {"status": "unavailable", "message": "Could not load the Drought Monitor map."}
    if history and month and out.get("drought_month", {}).get("status") == "ok":
        y = int(month[:4])
        hist = cli.drought_history(geo.geom, geo.fwd, month, [v for v in range(2000, y)])
        if hist:
            cur = out["drought_month"]["dsci"]
            out["drought_month"]["history"] = hist
            out["drought_month"]["dsci_percentile"] = ctx.percentile_of(cur, [h["dsci"] for h in hist])
            out["drought_month"]["dsci_median"] = float(np.median([h["dsci"] for h in hist]))
    try:
        out["fire_weather"] = cli.fire_weather(geo.geom)
    except (httpx.HTTPError, ValueError, KeyError):
        out["fire_weather"] = {"status": "unavailable", "message": "Could not reach the National Weather Service."}
    return out


@router.get("/context")
def context(request: Request, aoi: AOI = Depends(aoi_param), month: str | None = None, history: bool = False) -> dict:
    """Drought (selected month and latest week) and active fire-weather alerts for the area."""
    if month is not None and not MONTH_RE.match(month):
        raise HTTPException(422, "month must be YYYY-MM")
    return area_context(request.app, aoi, month, history)


def _drought_fact(app, loc: dict, cell_deg: float, month: str) -> dict | None:
    lat, lon, h = loc["lat_center"], loc["lon_center"], cell_deg / 2
    try:
        cli = app.state.context
        wk = ctx.usdm_week_for_month(month, cli.today())
        if wk is None:
            return None
        geo = imp.make_geo(parse_aoi(bbox=f"{lon - h},{lat - h},{lon + h},{lat + h}"))
        d = cli.drought(geo.geom, geo.fwd, wk)
        return {"summary": d["text"], "dsci_0_500": d["dsci"], "week": d["week"], "source": "U.S. Drought Monitor"}
    except Exception:  # context is optional for the explanation
        return None


# ---------------------------------------------------------------- Saved areas and alerts

import json  # noqa: E402
import time  # noqa: E402
import uuid  # noqa: E402
from types import SimpleNamespace  # noqa: E402

from pydantic import BaseModel, Field  # noqa: E402

from app.services import alerts as al  # noqa: E402
from app.services.nrt import filter_aoi  # noqa: E402


class AreaIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    aoi: str = Field(min_length=1, max_length=20000, description="area query string, e.g. bbox=w,s,e,n")
    anomaly: bool = True
    nrt_km: float | None = Field(5, ge=0, le=100)
    fire_weather: bool = True
    webhook_url: str | None = Field(None, max_length=500)
    email: str | None = Field(None, max_length=200)


def _area_aoi(area: dict) -> AOI:
    try:
        aoi = parse_aoi(**al.aoi_kwargs(area["aoi"]))
    except (AOIError, ValueError) as exc:
        raise HTTPException(422, f"Invalid area: {exc}") from exc
    if aoi.kind == "region":
        raise HTTPException(422, "Save a specific area (preset, drawn shape or point + radius), not the whole region")
    return aoi


def evaluate_area(app, area: dict) -> list[dict]:
    """Evaluate one saved area's rules; return candidate events (not yet deduplicated)."""
    aoi = _area_aoi(area)
    now = time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime())
    events: list[dict] = []

    def ev(rule, key, title, detail, severity, **extra):
        events.append({"id": uuid.uuid4().hex[:12], "area_id": area["id"], "area_name": area["name"], "rule": rule,
                       "key": f"{rule}:{key}", "title": title, "detail": detail, "severity": severity,
                       "created_utc": now, **extra})

    if area.get("anomaly"):
        try:
            b = brief(SimpleNamespace(app=app), aoi, HarmonizationParams(),
                      {"start": None, "end": None, "year": None, "b0": None, "b1": None, "z": 2.0})
            if b["level"] in ("Moderate", "Severe"):
                ref = b["reference"]
                ev("anomaly", f"{ref['month']}:{b['level']}", f"{b['level']} fire activity anomaly",
                   f"{ref['label']}: {b['current_activity']['text'] or ''} (robust z {b['current_activity']['z']:.1f}); "
                   f"{b['rank']['text'] if isinstance(b.get('rank'), dict) and b['rank'].get('text') else ''}".strip(),
                   "high" if b["level"] == "Severe" else "medium",
                   note="Compared with the same period in the baseline years. Provisional data may change.")
        except HTTPException:
            pass
    km = area.get("nrt_km")
    if km is not None:
        geo = imp.make_geo(aoi)
        zone = geo.geom_m.buffer(km * 1000) if km > 0 else geo.geom_m
        inv = imp.Transformer.from_crs(geo.fwd.target_crs, 4326, always_xy=True)
        zone_ll = imp.shp_transform(inv.transform, zone.simplify(200))
        zone_aoi = parse_aoi(polygon=json.dumps(imp.mapping(zone_ll)))
        try:
            rows = filter_aoi(app.state.nrt.fetch(zone_aoi, 2, ["VIIRS", "MODIS"]), zone_aoi)
        except NrtError:
            rows = None
        rows = [r for r in (rows or []) if to_feature(r)["properties"]["confidence"] != "low"]
        if rows:
            last = max(str(r["acq_date"]) for r in rows)
            n_in = len(filter_aoi(rows, aoi))
            ev("nrt", last, f"{len(rows)} new satellite fire detections",
               f"{len(rows)} near-real-time detections in the last 2 days ({n_in} inside the area, the rest within {km:g} km), "
               f"latest on {last}.", "high" if n_in else "medium",
               note="Live FIRMS data; may include industrial heat sources.")
    if area.get("fire_weather"):
        fw = area_context(app, aoi, None)["fire_weather"]
        for a in fw.get("active", []):
            ev("fire_weather", f"{a['event']}:{a['onset']}", f"{a['event']} in effect", a.get("headline") or a["event"],
               "high" if a["event"] == "Red Flag Warning" else "medium", note=f"Issued by {a.get('sender') or 'NWS'}.")
    return events


def check_areas(app, area_id: str | None = None) -> dict:
    store: al.AlertStore = app.state.alerts
    s = app.state.settings
    smtp = {"host": s.smtp_host, "port": s.smtp_port, "user": s.smtp_user, "password": s.smtp_password, "from": s.alert_from}
    areas = [a for a in store.areas() if area_id is None or a["id"] == area_id]
    if area_id and not areas:
        raise HTTPException(404, "No saved area with that id")
    new = []
    for a in areas:
        fresh = store.add_events(evaluate_area(app, a))
        for e in fresh:
            e["delivery"] = al.deliver(e, a, smtp)
        store.touch(a["id"])
        new += fresh
    return {"checked": len(areas), "new_events": new}


@router.get("/alerts/areas")
def list_areas(request: Request) -> dict:
    return {"areas": request.app.state.alerts.areas()}


@router.post("/alerts/areas")
def add_area(request: Request, body: AreaIn) -> dict:
    area = body.model_dump()
    _area_aoi(area)
    if area["webhook_url"] and not al.valid_webhook(area["webhook_url"]):
        raise HTTPException(422, "webhook_url must be https:// (or http://localhost)")
    if area["email"] and "@" not in area["email"]:
        raise HTTPException(422, "email is not valid")
    try:
        return request.app.state.alerts.add_area(area)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.delete("/alerts/areas/{area_id}")
def delete_area(request: Request, area_id: str) -> dict:
    if not request.app.state.alerts.delete_area(area_id):
        raise HTTPException(404, "No saved area with that id")
    return {"deleted": area_id}


@router.post("/alerts/check")
def run_check(request: Request, area_id: str | None = None) -> dict:
    """Evaluate saved areas now (also run by scripts/check_alerts.py on a schedule)."""
    return check_areas(request.app, area_id)


@router.get("/alerts/events")
def list_events(request: Request, area_id: str | None = None, limit: int = Query(100, ge=1, le=1000)) -> dict:
    return {"events": request.app.state.alerts.events(area_id)[:limit]}


# ---------------------------------------------------------------- Reported fires (NIFC)

import hashlib  # noqa: E402

from app.services import reported as rep  # noqa: E402


def _reported_geom(aoi: AOI):
    """None = whole region (all US incidents); otherwise the area, simplified for the request."""
    if aoi.kind == "region":
        return None, "region"
    g = imp.make_geo(aoi).geom
    if aoi.kind != "bbox":
        g = g.simplify(0.002)
    return g, hashlib.sha1(aoi.key.encode() if isinstance(aoi.key, str) else repr(aoi.key).encode()).hexdigest()[:16]


@router.get("/reported")
def reported(request: Request, aoi: AOI = Depends(aoi_param),
             start: date = Query(..., description="first UTC discovery date"),
             end: date = Query(..., description="last UTC discovery date"),
             points: int = Query(1000, ge=0, le=2000)) -> dict:
    """Agency-reported fire incidents (NIFC WFIGS) discovered in the area and dates."""
    if start > end:
        raise HTTPException(422, "start must be <= end")
    geom, key = _reported_geom(aoi)
    try:
        return request.app.state.reported.summary(geom, key, start, end, points=points)
    except rep.ReportedError as exc:
        return {"status": "unavailable", "message": str(exc), "source": rep.SOURCE}


@router.get("/reported/monthly")
def reported_monthly(request: Request, aoi: AOI = Depends(aoi_param),
                     start: str = Query("2014-01", description="first month YYYY-MM"),
                     end: str | None = Query(None, description="last month YYYY-MM (default: this month)")) -> dict:
    """Reported wildfires per month (NIFC, 2014 onward) with the harmonized satellite series alongside."""
    for v in (start, end):
        if v is not None and not MONTH_RE.match(v):
            raise HTTPException(422, "months must be YYYY-MM")
    cli: rep.ReportedClient = request.app.state.reported
    last = date.fromisoformat(f"{end}-01") if end else cli.today().replace(day=1)
    first = date.fromisoformat(f"{start}-01")
    if first > last:
        raise HTTPException(422, "start must be <= end")
    geom, key = _reported_geom(aoi)
    try:
        rows = cli.monthly(geom, key, first, last)
    except rep.ReportedError as exc:
        return {"status": "unavailable", "message": str(exc), "source": rep.SOURCE, "months": []}
    sat = {}
    try:
        for r in request.app.state.harmonizer.harmonized(aoi, HarmonizationParams())["series"]:
            sat[r["month"]] = {"satellite_cell_days": round(r["harmonized"], 1), "provisional": r.get("provisional", False)}
    except (ValueError, HTTPException):
        pass
    months = [{**r, **sat.get(r["month"], {"satellite_cell_days": None, "provisional": None})} for r in rows]
    return {"status": "ok", "source": rep.SOURCE, "coverage_start": rep.COVERAGE_START.isoformat()[:7], "months": months,
            "note": "Reported incidents by discovery month vs harmonized satellite fire cell-days. One large fire is one "
                    "incident but many satellite detections; small fires may be reported but not detected."}
