"""Fire activity outlook: an experimental statistical forecast for the next 1-3 months.

Model (per area, on the harmonized monthly series, either metric):
  x_t   = log(1 + value_t)
  a_t   = (x_t - mu_m) / sigma_m          standardized anomaly vs that calendar month's history
  P_t   = mean(a_{t-2}, a_{t-1}, a_t)     recent persistence: how unusual the last 3 months were
  a_{t+h} ~ beta_h * P_t + e,  e ~ N(0, s_h)   for lead h = 1, 2, 3 months

beta_h and s_h are fitted on every past issue month. Fire anomalies persist because the drivers
(drought, fuel load, a fire season starting early) persist for weeks to months.

Validation is a hindcast: for each year Y, mu/sigma and beta are refitted without Y and the forecasts
issued in Y are scored. Skill = 1 - MSE(model) / MSE(climatology), where climatology predicts a = 0
(the median-ish month). Skill <= 0 means the outlook is no better than the historical average, and the
response says so. Tercile probabilities (below / near / above normal) come from the forecast
distribution against the historical terciles of a.

This is not a fire-danger forecast or a prediction of individual fires: it is a statistical outlook
of total monthly fire activity in an area.
"""
from __future__ import annotations

import math
from collections import defaultdict

import numpy as np

LEADS = (1, 2, 3)
WINDOW = 3  # months averaged for the persistence predictor
MIN_YEARS = 8
SIGMA_FLOOR = 0.25  # log units; keeps near-zero months from producing huge anomalies
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _phi(z: float) -> float:
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def _next_month(m: str, h: int) -> str:
    y, mo = int(m[:4]), int(m[5:7]) - 1 + h
    return f"{y + mo // 12:04d}-{mo % 12 + 1:02d}"


def _stats(x: np.ndarray, cal: np.ndarray, years: np.ndarray, use: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Per calendar month mean and std of x over rows where `use` is true."""
    mu, sd = np.zeros(13), np.ones(13)
    for m in range(1, 13):
        v = x[use & (cal == m)]
        if len(v):
            mu[m] = v.mean()
            sd[m] = max(v.std(ddof=1) if len(v) > 1 else 0.0, SIGMA_FLOOR)
    return mu, sd


def _design(a: np.ndarray, h: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(issue index, predictor P, target a) for every issue month with a full window and a target."""
    idx = np.arange(WINDOW - 1, len(a) - h)
    p = np.array([a[i - WINDOW + 1: i + 1].mean() for i in idx])
    return idx, p, a[idx + h]


def _beta(p: np.ndarray, y: np.ndarray) -> float:
    d = float(np.dot(p, p))
    return float(np.dot(p, y) / d) if d > 0 else 0.0


def outlook(series: list[dict]) -> dict:
    rows = [r for r in series if r.get("harmonized") is not None]
    months = [r["month"] for r in rows]
    full_years = defaultdict(int)
    for r in rows:
        if not r.get("provisional"):
            full_years[int(r["month"][:4])] += 1
    complete = sorted(y for y, n in full_years.items() if n == 12)
    if len(complete) < MIN_YEARS:
        return {"status": "insufficient_history",
                "message": f"Needs at least {MIN_YEARS} complete years of data; this area has {len(complete)}."}

    x = np.log1p(np.array([float(r["harmonized"]) for r in rows]))
    cal = np.array([int(m[5:7]) for m in months])
    yrs = np.array([int(m[:4]) for m in months])
    in_clim = np.isin(yrs, complete)

    # Full fit (used for the live outlook)
    mu, sd = _stats(x, cal, yrs, in_clim)
    a = (x - mu[cal]) / sd[cal]
    t1, t2 = (float(q) for q in np.quantile(a[in_clim], [1 / 3, 2 / 3]))

    # Hindcast: leave one complete year out
    hind = {h: {"pred": [], "obs": []} for h in LEADS}
    for y in complete:
        train = in_clim & (yrs != y)
        mu_y, sd_y = _stats(x, cal, yrs, train)
        a_y = (x - mu_y[cal]) / sd_y[cal]
        for h in LEADS:
            idx, p, tgt = _design(a_y, h)
            issue_year = yrs[idx]
            fit = (issue_year != y) & np.isin(yrs[idx + h], complete) & (yrs[idx + h] != y)
            test = issue_year == y
            if fit.sum() < 24 or not test.any():
                continue
            b = _beta(p[fit], tgt[fit])
            hind[h]["pred"] += list(b * p[test])
            hind[h]["obs"] += list(tgt[test])

    last = len(rows) - 1
    p_now = float(a[last - WINDOW + 1: last + 1].mean())
    recent = [{"month": months[i], "value": float(rows[i]["harmonized"]), "anomaly": round(float(a[i]), 2),
               "provisional": bool(rows[i].get("provisional"))} for i in range(last - WINDOW + 1, last + 1)]
    leads = []
    for h in LEADS:
        idx, p, tgt = _design(a, h)
        ok = np.isin(yrs[idx + h], complete)
        b = _beta(p[ok], tgt[ok])
        s = float(np.std(tgt[ok] - b * p[ok], ddof=1))
        pred, obs = np.array(hind[h]["pred"]), np.array(hind[h]["obs"])
        mse_m, mse_c = float(np.mean((obs - pred) ** 2)), float(np.mean(obs ** 2))
        skill = 1 - mse_m / mse_c if mse_c > 0 else 0.0
        cat_obs = np.digitize(obs, [t1, t2])
        cat_pred = np.digitize(pred, [t1, t2])
        hit = float(np.mean(cat_obs == cat_pred))

        target = _next_month(months[-1], h)
        m = int(target[5:7])
        ahat = b * p_now
        q = {k: float(np.expm1(mu[m] + sd[m] * (ahat + z * s))) for k, z in (("p10", -1.2816), ("p50", 0.0), ("p90", 1.2816))}
        below = _phi((t1 - ahat) / s)
        above = 1 - _phi((t2 - ahat) / s)
        probs = {"below": below, "near": max(0.0, 1 - below - above), "above": above}
        median = float(np.expm1(mu[m]))
        leads.append({
            "lead": h, "month": target, "month_name": f"{MONTHS[m - 1]} {target[:4]}",
            "expected": max(0.0, q["p50"]), "range": [max(0.0, q["p10"]), max(0.0, q["p90"])],
            "typical": median, "anomaly": round(ahat, 2),
            "probabilities": {k: round(v, 3) for k, v in probs.items()},
            "most_likely": max(probs, key=probs.get),
            "beta": round(b, 3),
            "skill": {"msss": round(skill, 3), "tercile_hit_rate": round(hit, 3), "n_hindcasts": int(len(obs)),
                      "useful": bool(skill > 0.05)},
        })

    first = leads[0]
    word = {"below": "below normal", "near": "near normal", "above": "above normal"}
    if first["skill"]["useful"]:
        headline = (f"{first['month_name']}: {word[first['most_likely']]} fire activity most likely "
                    f"({first['probabilities'][first['most_likely']] * 100:.0f}% chance).")
    else:
        headline = "No skilful outlook for this area: past patterns here did not predict the next month better than the historical average."
    return {
        "status": "ok",
        "headline": headline,
        "issued_from": months[-1],
        "issued_from_provisional": bool(rows[-1].get("provisional")),
        "recent": recent,
        "persistence_index": round(p_now, 2),
        "leads": leads,
        "climatology_years": [complete[0], complete[-1]],
        "terciles": [round(t1, 3), round(t2, 3)],
        "method": ("Persistence of the standardized log anomaly: the mean anomaly of the last 3 months, times a "
                   "coefficient fitted per lead time on all past months. Skill from leave-one-year-out hindcasts vs "
                   "the climatological average (MSSS > 0 = better than climatology)."),
        "caveat": ("Experimental statistical outlook of total monthly fire activity in this area. Not a fire-danger "
                   "forecast, not a prediction of individual fires, and it does not use weather forecasts."),
    }
