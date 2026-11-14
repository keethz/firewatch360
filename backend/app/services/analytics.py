"""Phase 2 analytics over the harmonized monthly series.

Inputs are the rows returned by `Harmonizer.harmonized()` (one per month, with
`harmonized`, `modis`, `viirs`, `source`). Everything here is a pure function so it is
deterministic and unit-testable. See docs/SCIENTIFIC_METHOD.md, "Analytics".
"""
from __future__ import annotations

from collections import defaultdict

import numpy as np

MAD_SCALE = 1.4826  # makes the MAD a consistent estimator of the SD for normal data
MIN_BASELINE_YEARS = 5
MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


class AnalyticsError(ValueError):
    pass


def complete_years(series: list[dict]) -> list[int]:
    """Years with all 12 months present in final (non-provisional) data."""
    n = defaultdict(int)
    for r in series:
        if not r.get("provisional"):
            n[int(r["month"][:4])] += 1
    return sorted(y for y, c in n.items() if c == 12)


def resolve_baseline(series: list[dict], year: int | None, start: int | None, end: int | None) -> tuple[int, int]:
    """Default baseline: every complete year before the selected year (default: latest year)."""
    years = complete_years(series)
    if not years:
        raise AnalyticsError("No complete years in the series")
    latest = int(series[-1]["month"][:4])
    sel = year if year is not None else latest
    b0 = start if start is not None else years[0]
    b1 = end if end is not None else max([y for y in years if y < sel], default=years[0])
    if b0 > b1:
        raise AnalyticsError("baseline_start must be <= baseline_end")
    return b0, b1


def climatology(series: list[dict], b0: int, b1: int) -> dict:
    """Per calendar month statistics of harmonized values over complete baseline years."""
    years = [y for y in complete_years(series) if b0 <= y <= b1]
    by_month: dict[int, list[float]] = defaultdict(list)
    for r in series:
        y, m = int(r["month"][:4]), int(r["month"][5:7])
        if y in years:
            by_month[m].append(float(r["harmonized"]))
    months = []
    for m in range(1, 13):
        v = np.array(by_month.get(m, []), float)
        if len(v) == 0:
            months.append({"month": m, "name": MONTH_NAMES[m - 1], "n_years": 0, "median": None, "mean": None,
                           "p25": None, "p75": None, "mad": None})
            continue
        med = float(np.median(v))
        months.append({
            "month": m, "name": MONTH_NAMES[m - 1], "n_years": int(len(v)),
            "median": med, "mean": float(v.mean()),
            "p25": float(np.percentile(v, 25)), "p75": float(np.percentile(v, 75)),
            "mad": float(np.median(np.abs(v - med))),
        })
    status = "ok" if len(years) >= MIN_BASELINE_YEARS else "insufficient_history"
    return {"baseline": [b0, b1], "baseline_years": years, "status": status, "months": months}


def robust_z(value: float, median: float | None, mad: float | None) -> float | None:
    """(value - median) / (1.4826 * MAD). None when undefined (no history or MAD = 0)."""
    if median is None or mad is None or mad == 0:
        return None
    return (value - median) / (MAD_SCALE * mad)


def annotate(series: list[dict], clim: dict, z_threshold: float) -> list[dict]:
    """Add climatology median, robust z and anomaly flag to each month."""
    ok = clim["status"] == "ok"
    cm = {c["month"]: c for c in clim["months"]}
    out = []
    for r in series:
        c = cm[int(r["month"][5:7])]
        z = robust_z(r["harmonized"], c["median"], c["mad"]) if ok else None
        flag = None
        if z is not None and abs(z) >= z_threshold:
            flag = "high" if z > 0 else "low"
        pct = None
        if ok and c["median"]:
            pct = (r["harmonized"] - c["median"]) / c["median"] * 100
        out.append({**r, "clim_median": c["median"] if ok else None, "pct_vs_median": pct, "z": z, "anomaly": flag})
    return out


def calendar_matrix(rows: list[dict]) -> list[dict]:
    """Year x month matrix. Months without data are None."""
    by_year: dict[int, list] = defaultdict(lambda: [None] * 12)
    for r in rows:
        by_year[int(r["month"][:4])][int(r["month"][5:7]) - 1] = r
    return [
        {"year": y, "total": sum(c["harmonized"] for c in cells if c), "complete": all(cells), "months": cells}
        for y, cells in sorted(by_year.items())
    ]


def theil_sen(x: np.ndarray, y: np.ndarray) -> dict | None:
    """Median of pairwise slopes; intercept = median(y - slope * x)."""
    if len(x) < 3:
        return None
    i, j = np.triu_indices(len(x), k=1)
    dx = x[j] - x[i]
    keep = dx != 0
    slope = float(np.median((y[j] - y[i])[keep] / dx[keep]))
    return {"slope_per_year": slope, "intercept": float(np.median(y - slope * x)), "n_years": int(len(x))}


def rankings(rows: list[dict], clim: dict, top: int = 5) -> dict:
    by_value = sorted(rows, key=lambda r: r["harmonized"], reverse=True)
    years = calendar_matrix(rows)
    full = [y for y in years if y["complete"]]
    med = [c["median"] for c in clim["months"] if c["median"] is not None]
    peak = []
    if len(med) == 12 and clim["status"] == "ok":
        ref = float(np.mean(med))
        peak = [c["name"] for c in clim["months"] if c["median"] > ref]
    trend = theil_sen(np.array([y["year"] for y in full], float), np.array([y["total"] for y in full], float))
    return {
        "top_months": [{"month": r["month"], "harmonized": r["harmonized"], "source": r["source"]} for r in by_value[:top]],
        "top_years": [{"year": y["year"], "total": y["total"]} for y in sorted(full, key=lambda y: -y["total"])[:top]],
        "peak_season": peak,
        "peak_season_rule": "calendar months whose baseline median exceeds the mean of the 12 monthly medians",
        "annual_totals": [{"year": y["year"], "total": y["total"], "complete": y["complete"]} for y in years],
        "trend": trend,
        "trend_note": "Theil-Sen slope of annual totals over complete years in the window. Descriptive only.",
    }


def latest_vs_median(rows: list[dict]) -> dict | None:
    """Latest month in the window against its calendar-month baseline median."""
    if not rows:
        return None
    r = rows[-1]
    return {
        "month": r["month"], "value": r["harmonized"], "median": r["clim_median"],
        "pct_vs_median": r["pct_vs_median"], "z": r["z"], "anomaly": r["anomaly"],
        "provisional": bool(r.get("provisional")),
        "data_status": "near-real-time, provisional" if r.get("provisional") else "final (archive or standard processing)",
    }


def month_to_date(cur: dict | None, z_threshold: float) -> dict | None:
    """Compare a partial month with the same days of that month in each baseline year."""
    if cur is None:
        return None
    vals = np.array([r["value"] for r in cur["baseline_rows"]], float)
    out = {**cur, "status": "provisional", "n_baseline_years": int(len(vals))}
    if len(vals) < MIN_BASELINE_YEARS:
        return {**out, "median": None, "p25": None, "p75": None, "z": None, "pct_vs_median": None,
                "anomaly": None, "history_status": "insufficient_history"}
    med = float(np.median(vals))
    mad = float(np.median(np.abs(vals - med)))
    z = robust_z(cur["value"], med, mad)
    return {
        **out, "history_status": "ok", "median": med,
        "p25": float(np.percentile(vals, 25)), "p75": float(np.percentile(vals, 75)),
        "z": z, "pct_vs_median": (cur["value"] - med) / med * 100 if med else None,
        "anomaly": None if z is None or abs(z) < z_threshold else ("high" if z > 0 else "low"),
        "rank": int(1 + (vals > cur["value"]).sum()),
    }


def window(series: list[dict], start: str | None, end: str | None) -> list[dict]:
    return [r for r in series if (start is None or r["month"] >= start) and (end is None or r["month"] <= end)]
