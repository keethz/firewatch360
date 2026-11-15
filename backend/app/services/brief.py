"""Fire Risk Brief: a plain-language summary of one area's current fire activity.

Every value is derived from the harmonized record; the rules are fixed and listed below so a
reader can check them. Nothing here forecasts fire.

Reference period
  The current month-to-date (NRT, provisional) if it covers >= MIN_MTD_DAYS days, otherwise the
  latest complete month in the series (which may itself be provisional).

Anomaly level (robust z vs the baseline median for that calendar period)
  Severe        z >= SEVERE_Z
  Moderate      z >= z_threshold (default 2)
  Normal        -z_threshold < z < z_threshold
  Below normal  z <= -z_threshold

Sensor agreement
  MODIS and VIIRS raw cell-days are each scored against their own history for the same period
  (MODIS: years with Terra + Aqua; VIIRS: 2012 onward). Their classes (high / normal / low) are
  compared.

Confidence starts at 3 (High) and loses a point for each reason that applies:
  provisional data; only one sensor available; sensors disagree; baseline median < 20 cell-days
  (two points if < 5); fewer than 10 baseline years; month-to-date covers fewer than 10 days;
  calibration factor from the US-wide fallback.  3 -> High, 2 -> Medium, <= 1 -> Low.

Trend
  Mann-Kendall test on annual totals of complete years with a Theil-Sen slope. "Increasing" or
  "Decreasing" only when the two-sided p < 0.05, else "No clear trend".
"""
from __future__ import annotations

import math

import numpy as np

from app.services import analytics as an

MIN_MTD_DAYS = 7
SEVERE_Z = 3.0
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
          "October", "November", "December"]


# ---------------------------------------------------------------- small pure helpers

def ordinal(n: int) -> str:
    suf = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf}"


def anomaly_level(z: float | None, z_threshold: float = 2.0) -> str:
    if z is None:
        return "Unknown"
    if z >= SEVERE_Z:
        return "Severe"
    if z >= z_threshold:
        return "Moderate"
    if z <= -z_threshold:
        return "Below normal"
    return "Normal"


def sensor_class(z: float | None, z_threshold: float) -> str | None:
    if z is None:
        return None
    return "high" if z >= z_threshold else "low" if z <= -z_threshold else "normal"


def rank_phrase(value: float, others: list[float], period: str, since: int) -> dict:
    """Rank among all years (others + value). Uses 'highest' or 'lowest', whichever is smaller."""
    arr = np.asarray(others, float)
    hi = int(1 + (arr > value).sum())
    lo = int(1 + (arr < value).sum())
    n = len(others) + 1
    if hi <= lo:
        word = "highest" if hi == 1 else f"{ordinal(hi)}-highest"
        rank, side = hi, "highest"
    else:
        word = "lowest" if lo == 1 else f"{ordinal(lo)}-lowest"
        rank, side = lo, "lowest"
    return {"rank": rank, "side": side, "of": n, "text": f"{word[0].upper()}{word[1:]} {period} since {since}"}


def stats(values: list[float]) -> dict | None:
    v = np.asarray(values, float)
    if len(v) < an.MIN_BASELINE_YEARS:
        return None
    med = float(np.median(v))
    return {"median": med, "mad": float(np.median(np.abs(v - med))), "n": int(len(v))}


def zscore(value: float, st: dict | None) -> float | None:
    return None if st is None else an.robust_z(value, st["median"], st["mad"])


def mann_kendall(x: list[float], y: list[float]) -> dict | None:
    """Mann-Kendall trend test (normal approximation, tie-corrected variance)."""
    y = np.asarray(y, float)
    n = len(y)
    if n < 8:
        return None
    s = sum(np.sign(y[j] - y[i]) for i in range(n - 1) for j in range(i + 1, n))
    _, counts = np.unique(y, return_counts=True)
    var = (n * (n - 1) * (2 * n + 5) - sum(t * (t - 1) * (2 * t + 5) for t in counts)) / 18
    z = 0.0 if s == 0 else (s - np.sign(s)) / math.sqrt(var)
    p = math.erfc(abs(z) / math.sqrt(2))
    ts = an.theil_sen(np.asarray(x, float), y)
    return {"s": int(s), "z": float(z), "p": float(p), "slope_per_year": ts["slope_per_year"], "n_years": n}


def trend_summary(annual: list[dict]) -> dict:
    full = [a for a in annual if a["complete"]]
    mk = mann_kendall([a["year"] for a in full], [a["total"] for a in full])
    if mk is None:
        return {"direction": "Unknown", "text": "Not enough complete years for a trend", "test": None}
    med = float(np.median([a["total"] for a in full]))
    pct = mk["slope_per_year"] / med * 100 if med else None
    if mk["p"] < 0.05:
        direction = "Increasing" if mk["slope_per_year"] > 0 else "Decreasing"
        text = f"{direction}: {pct:+.1f}% per year ({full[0]['year']}–{full[-1]['year']}, Mann–Kendall p = {mk['p']:.3f})"
    else:
        direction = "No clear trend"
        text = f"No clear trend ({full[0]['year']}–{full[-1]['year']}, Mann–Kendall p = {mk['p']:.2f})"
    return {"direction": direction, "pct_per_year": pct, "text": text, "test": mk}


def agreement(modis_cls: str | None, viirs_cls: str | None, modis_ok: bool, viirs_ok: bool) -> dict:
    if modis_ok and viirs_ok and modis_cls and viirs_cls:
        if modis_cls == viirs_cls:
            return {"label": "MODIS and VIIRS agree", "status": "agree"}
        if viirs_cls == "high" and modis_cls != "high":
            return {"label": "VIIRS-only signal", "status": "viirs_only"}
        if modis_cls == "high" and viirs_cls != "high":
            return {"label": "MODIS-only signal", "status": "modis_only"}
        return {"label": "MODIS and VIIRS disagree", "status": "disagree"}
    if modis_ok and modis_cls:
        return {"label": "MODIS only (no VIIRS data for this period)", "status": "single"}
    if viirs_ok and viirs_cls:
        return {"label": "VIIRS only (no MODIS data for this period)", "status": "single"}
    return {"label": "Not enough sensor history", "status": "unknown"}


def confidence(*, provisional: bool, agree_status: str, median: float | None, n_baseline: int,
               mtd_days: int | None, fallback: bool) -> dict:
    score, reasons = 3, []
    if provisional:
        score -= 1
        reasons.append("Near-real-time data (provisional)")
    # One sensor is enough for a reliable count (VIIRS S-NPP NRT ended in 2026; MODIS-only years
    # are normal before 2012), so "single" costs nothing. No usable sensor data at all means Low.
    no_sensor = agree_status == "unknown"
    if no_sensor:
        reasons.append("No sensor data available for this period")
    elif agree_status in ("viirs_only", "modis_only", "disagree"):
        score -= 1
        reasons.append("MODIS and VIIRS do not agree")
    if median is not None and median < 5:
        score -= 2
        reasons.append("Very little fire activity in this area normally (median < 5 cell-days)")
    elif median is not None and median < 20:
        score -= 1
        reasons.append("Little fire activity in this area normally (median < 20 cell-days)")
    if n_baseline < 10:
        score -= 1
        reasons.append(f"Short baseline ({n_baseline} years)")
    if mtd_days is not None and mtd_days < 10:
        score -= 1
        reasons.append(f"Month-to-date covers only {mtd_days} days")
    if fallback:
        score -= 1
        reasons.append("Calibration uses the US-wide fallback factor")
    level = "Low" if no_sensor else "High" if score >= 3 else "Medium" if score == 2 else "Low"
    return {"level": level, "score": score, "reasons": reasons}


HEADLINES = {
    "Severe": "This area is experiencing unusually high fire activity compared with its historical baseline.",
    "Moderate": "This area is experiencing unusually high fire activity compared with its historical baseline.",
    "Below normal": "This area is experiencing unusually low fire activity compared with its historical baseline.",
    "Normal": "Fire activity in this area is within its normal historical range.",
    "Unknown": "There is not enough history in this area to compare current fire activity with a baseline.",
}


# ---------------------------------------------------------------- the brief

def build_brief(*, series: list[dict], windows: dict, calibration: dict, terra: dict, clim: dict,
                rankings: dict, current: dict | None, current_counts: tuple[dict, dict] | None,
                k: float, c: float, z_threshold: float, harmonize_fn) -> dict:
    """Assemble the brief. `harmonize_fn(hist, months)` converts raw history counts to harmonized rows."""
    baseline_years = set(clim["baseline_years"])
    switch, aqua = windows["switch_month"], windows["aqua_month"]
    use_mtd = current is not None and current_counts is not None and current["days"] >= MIN_MTD_DAYS

    if use_mtd:
        m, day = current["month"], current["days"]
        mm, year = int(m[5:7]), int(m[:4])
        pc, hist = current_counts
        # Every year the record covers this calendar period, including years with zero detections.
        first = int(windows["series_start"][:4]) + (1 if int(windows["series_start"][5:7]) > mm else 0)
        years = list(range(first, year))
        rows = harmonize_fn(hist, [f"{y:04d}-{mm:02d}" for y in years])
        hist_vals = {int(r["month"][:4]): r["harmonized"] for r in rows}
        value = current["value"]
        period = f"1–{day} {MONTHS[mm - 1]}"
        label = f"{MONTHS[mm - 1]} {year}, days 1–{day} (through {current['through']} UTC)"
        provisional = True
        cur_modis, cur_viirs = pc.get(("MODIS", m), 0), pc.get(("VIIRS", m), 0)
        nrt = windows["nrt_as_of"]
        modis_ok = nrt.get("MODIS", "") >= current["through"]
        viirs_ok = nrt.get("VIIRS", "") >= current["through"]
        raw_hist = {s: {y: hist.get((s, f"{y:04d}-{mm:02d}"), 0) for y in years} for s in ("MODIS", "VIIRS")}
        mtd_days = day
    else:
        ref = series[-1]
        m = ref["month"]
        mm, year = int(m[5:7]), int(m[:4])
        same = [r for r in series if int(r["month"][5:7]) == mm and r["month"] != m]
        hist_vals = {int(r["month"][:4]): r["harmonized"] for r in same}
        value = ref["harmonized"]
        period = MONTHS[mm - 1]
        label = f"{MONTHS[mm - 1]} {year}" + (" (provisional)" if ref.get("provisional") else "")
        provisional = bool(ref.get("provisional"))
        cur_modis, cur_viirs = ref["modis"], ref["viirs"]
        modis_ok, viirs_ok = True, ref["viirs"] is not None
        raw_hist = {"MODIS": {int(r["month"][:4]): r["modis"] for r in same},
                    "VIIRS": {int(r["month"][:4]): r["viirs"] for r in same if r["viirs"] is not None}}
        mtd_days = None

    # Harmonized comparison against the baseline years
    base_vals = [v for y, v in hist_vals.items() if y in baseline_years]
    st = stats(base_vals)
    z = zscore(value, st)
    level = anomaly_level(z, z_threshold)
    pct = (value - st["median"]) / st["median"] * 100 if st and st["median"] else None
    since = min(hist_vals) if hist_vals else year
    rank = rank_phrase(value, list(hist_vals.values()), period, since) if hist_vals else None

    # Each sensor against its own history (MODIS: Terra + Aqua years; VIIRS: from the switch month)
    def sensor_z(s: str, cur: float, ok: bool) -> dict:
        first = aqua if s == "MODIS" else switch
        vals = [v for y, v in raw_hist[s].items() if y in baseline_years and f"{y:04d}-{mm:02d}" >= (first or "0000")]
        sst = stats(vals)
        sz = zscore(cur, sst) if ok else None
        return {"available": ok, "value": cur if ok else None, "median": sst["median"] if sst else None,
                "n_years": sst["n"] if sst else len(vals), "z": sz, "class": sensor_class(sz, z_threshold)}
    sens = {"MODIS": sensor_z("MODIS", cur_modis, modis_ok), "VIIRS": sensor_z("VIIRS", cur_viirs or 0, viirs_ok)}
    agree = agreement(sens["MODIS"]["class"], sens["VIIRS"]["class"],
                      sens["MODIS"]["available"] and sens["MODIS"]["z"] is not None,
                      sens["VIIRS"]["available"] and sens["VIIRS"]["z"] is not None)

    fallback = calibration.get("scope") == "region_fallback" or (
        terra.get("scope") == "region_fallback" and any(y < int(aqua[:4]) for y in hist_vals) if aqua else False)
    conf = confidence(provisional=provisional, agree_status=agree["status"],
                      median=st["median"] if st else None, n_baseline=st["n"] if st else 0,
                      mtd_days=mtd_days, fallback=fallback)
    trend = trend_summary(rankings["annual_totals"])

    pct_text = None
    if pct is not None:
        pct_text = f"+{pct:,.0f}% above historical median" if pct >= 0 else f"{abs(pct):,.0f}% below historical median"

    return {
        "headline": HEADLINES[level],
        "level": level,
        "reference": {"label": label, "month": m, "period": period, "provisional": provisional,
                      "kind": "month_to_date" if use_mtd else "month"},
        "current_activity": {"value": value, "median": st["median"] if st else None, "pct_vs_median": pct,
                             "text": pct_text, "z": z, "units": "VIIRS-equivalent fire cell-days"},
        "rank": rank,
        "sensors": sens,
        "agreement": agree,
        "confidence": conf,
        "peak_season": rankings["peak_season"],
        "trend": trend,
        "baseline": {"years": [min(baseline_years), max(baseline_years)] if baseline_years else None,
                     "n_years": st["n"] if st else 0},
        "factors": {"k": k, "c": c, "k_scope": calibration.get("scope"), "c_scope": terra.get("scope")},
        "rules": {
            "reference": f"Current month-to-date if it covers at least {MIN_MTD_DAYS} days, else the latest complete month",
            "anomaly_level": f"Severe z ≥ {SEVERE_Z:g}; Moderate z ≥ {z_threshold:g}; Below normal z ≤ −{z_threshold:g}; else Normal (robust z vs baseline median)",
            "agreement": "Each sensor's raw cell-days scored against its own history for the same period",
            "confidence": "Starts High; one level lower per listed reason; one sensor alone is not a reason; Low if no sensor has data",
            "trend": "Mann–Kendall on annual totals of complete years; direction only if p < 0.05",
        },
    }
