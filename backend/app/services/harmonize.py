"""MODIS/VIIRS harmonization (Phase 1).

Method (see docs/SCIENTIFIC_METHOD.md):
 1. Snap every detection to a regular lat/lon grid (default 0.01 deg) and count unique
    fire cell-days (cell x UTC date) per sensor per month.
 2. On the overlap period (first full VIIRS month onward) fit VIIRS ~= k * MODIS by least
    squares through the origin, on months where MODIS has enough cell-days.
 3. Harmonized series, in VIIRS-equivalent cell-days: k * MODIS before the first full VIIRS
    month, observed VIIRS from then on.
 4. Validate with leave-one-year-out over the overlap years.
If the AOI has too little overlap the US-wide factor is used and labelled as such.

All parameters are prototype defaults that need scientific validation. This is not an
official NASA algorithm.
"""
from __future__ import annotations

import threading
from dataclasses import asdict, dataclass
from datetime import date, timedelta

import numpy as np

from app.services.aoi import AOI
from app.services.store import HotspotStore

CONFIDENCE_LEVELS = {
    "low": ("low", "nominal", "high"),
    "nominal": ("nominal", "high"),
    "high": ("high",),
}


@dataclass(frozen=True)
class HarmonizationParams:
    grid_deg: float = 0.01
    min_confidence: str = "nominal"  # keep detections at or above this level
    vegetation_only: bool = True  # FIRMS type == 0 (presumed vegetation fire)
    min_overlap_months: int = 24
    min_monthly_cell_days: int = 5  # a month enters the fit only if MODIS >= this

    def validate(self) -> None:
        if not (0.001 <= self.grid_deg <= 1.0):
            raise ValueError("grid_deg must be in [0.001, 1.0]")
        if self.min_confidence not in CONFIDENCE_LEVELS:
            raise ValueError(f"min_confidence must be one of {sorted(CONFIDENCE_LEVELS)}")
        if self.min_overlap_months < 1 or self.min_monthly_cell_days < 1:
            raise ValueError("min_overlap_months and min_monthly_cell_days must be >= 1")


# Only finally processed rows are used. NRT rows have no `type` and are provisional.
PROCESSING_LEVELS = ("archive", "standard")


# ---------------------------------------------------------------- month helpers

def month_str(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def month_range(start: str, end: str) -> list[str]:
    y, m = map(int, start.split("-"))
    ey, em = map(int, end.split("-"))
    out = []
    while (y, m) <= (ey, em):
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def first_full_month(first_day: date) -> str:
    if first_day.day == 1:
        return month_str(first_day)
    nxt = (first_day.replace(day=28) + timedelta(days=4)).replace(day=1)
    return month_str(nxt)


def last_full_month(last_day: date) -> str:
    if (last_day + timedelta(days=1)).day == 1:
        return month_str(last_day)
    return month_str(last_day.replace(day=1) - timedelta(days=1))


# ---------------------------------------------------------------- counting

def monthly_cell_days(
    store: HotspotStore,
    aoi: AOI,
    p: HarmonizationParams,
    levels: tuple[str, ...] = PROCESSING_LEVELS,
    extra_where: str = "",
    extra_params: tuple = (),
) -> dict[tuple[str, str], int]:
    """Unique fire cell-days per (sensor, 'YYYY-MM') inside the AOI.

    Keys: "MODIS", "VIIRS", and "MODIS_TERRA" (Terra-only, for the pre-Aqua correction).
    NRT rows have no `type`. When `vegetation_only` and NRT is requested, an NRT detection is
    kept unless its grid cell ever had a non-vegetation detection (type 1, 2 or 3) in the
    archive (the static-source mask; see docs/SCIENTIFIC_METHOD.md).
    """
    where, params = aoi.sql()
    conf = CONFIDENCE_LEVELS[p.min_confidence]
    filters = [where, f"processing_level IN ({','.join('?' * len(levels))})",
               f"confidence_norm IN ({','.join('?' * len(conf))})"]
    params = [*params, *levels, *conf]
    if extra_where:
        filters.append(extra_where)
        params += list(extra_params)
    use_mask = p.vegetation_only and "nrt" in levels
    veg = ""
    if p.vegetation_only:
        veg = ("WHERE type = 0 OR (processing_level = 'nrt' AND NOT EXISTS "
               "(SELECT 1 FROM static_mask m WHERE m.gy = g.gy AND m.gx = g.gx))") if use_mask else "WHERE type = 0"
    mask_cte = ""
    mask_params: list = []
    if use_mask:
        mask_cte = """static_mask AS (
            SELECT DISTINCT CAST(floor(latitude / ? + 1e-9) AS BIGINT) AS gy,
                            CAST(floor(longitude / ? + 1e-9) AS BIGINT) AS gx
            FROM hotspots WHERE processing_level = 'archive' AND type IN (1, 2, 3)
        ),"""
        mask_params = [p.grid_deg, p.grid_deg]
    # The tiny epsilon keeps values on a cell edge (e.g. 37.12 / 0.01 = 3711.99999...)
    # in the cell they belong to.
    sql = f"""
        WITH {mask_cte}
        g AS (
            SELECT sensor, satellite, acq_date, type, processing_level,
                   CAST(floor(latitude / ? + 1e-9) AS BIGINT) AS gy,
                   CAST(floor(longitude / ? + 1e-9) AS BIGINT) AS gx
            FROM hotspots WHERE {' AND '.join(filters)}
        ), base AS (SELECT * FROM g {veg}),
        cells AS (
            SELECT DISTINCT sensor AS k, acq_date, gy, gx FROM base
            UNION ALL
            SELECT DISTINCT 'MODIS_TERRA' AS k, acq_date, gy, gx FROM base
            WHERE sensor = 'MODIS' AND satellite = 'Terra'
        )
        SELECT k AS sensor, strftime(acq_date, '%Y-%m') AS ym, count(*) AS n FROM cells GROUP BY 1, 2
    """
    rows = store.query(sql, [*mask_params, p.grid_deg, p.grid_deg, *params], aoi=aoi)
    return {(r["sensor"], r["ym"]): int(r["n"]) for r in rows}


def nrt_window(store: HotspotStore) -> dict[str, tuple[date, date]]:
    rows = store.query("SELECT sensor, min(acq_date) AS a, max(acq_date) AS b FROM hotspots "
                       "WHERE processing_level = 'nrt' GROUP BY 1")
    return {r["sensor"]: (r["a"], r["b"]) for r in rows}


def sensor_windows(store: HotspotStore) -> dict[str, tuple[date, date]]:
    """First and last acquisition date per sensor across the loaded region (final data only)."""
    rows = store.query(
        f"SELECT sensor, min(acq_date) AS a, max(acq_date) AS b FROM hotspots "
        f"WHERE processing_level IN ({','.join('?' * len(PROCESSING_LEVELS))}) GROUP BY 1",
        list(PROCESSING_LEVELS),
    )
    out = {r["sensor"]: (r["a"], r["b"]) for r in rows}
    aqua = store.query(
        f"SELECT min(acq_date) AS a FROM hotspots WHERE sensor = 'MODIS' AND satellite = 'Aqua' "
        f"AND processing_level IN ({','.join('?' * len(PROCESSING_LEVELS))})",
        list(PROCESSING_LEVELS),
    )
    if aqua and aqua[0]["a"] is not None:
        out["AQUA"] = (aqua[0]["a"], aqua[0]["a"])
    return out


def _month_last_day(m: str) -> date:
    y, mo = map(int, m.split("-"))
    nxt = date(y + 1, 1, 1) if mo == 12 else date(y, mo + 1, 1)
    return nxt - timedelta(days=1)


def month_after(m: str) -> str:
    y, mo = map(int, m.split("-"))
    return f"{y + 1:04d}-01" if mo == 12 else f"{y:04d}-{mo + 1:02d}"


def prev_month(m: str) -> str:
    y, mo = map(int, m.split("-"))
    return f"{y - 1:04d}-12" if mo == 1 else f"{y:04d}-{mo - 1:02d}"


# ---------------------------------------------------------------- fitting

def fit_ratio(x: np.ndarray, y: np.ndarray) -> dict:
    """Least squares y = k*x through the origin, plus an ordinary a + b*x fit for reference."""
    x = np.asarray(x, float)
    y = np.asarray(y, float)
    if len(x) == 0 or float(np.dot(x, x)) == 0.0:
        raise ValueError("cannot fit a ratio with no non-zero x values")
    k = float(np.dot(x, y) / np.dot(x, x))
    resid = y - k * x
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    r2 = 1.0 - float(np.sum(resid**2)) / ss_tot if ss_tot > 0 else None
    out = {"k": k, "r2": r2, "n_months": int(len(x)), "rmse": float(np.sqrt(np.mean(resid**2)))}
    if len(x) >= 3 and np.ptp(x) > 0:
        b, a = np.polyfit(x, y, 1)
        lres = y - (a + b * x)
        out["linear"] = {
            "a": float(a), "b": float(b),
            "r2": 1.0 - float(np.sum(lres**2)) / ss_tot if ss_tot > 0 else None,
        }
    else:
        out["linear"] = None
    return out


def leave_one_year_out(months: list[str], x: np.ndarray, y: np.ndarray) -> dict:
    """Refit k without each overlap year and score the held-out year.

    annual_error_pct = |sum(k*MODIS) - sum(VIIRS)| / sum(VIIRS) for that year.
    monthly_mape_pct = mean over the year's months of |k*MODIS - VIIRS| / VIIRS.
    """
    years = np.array([m[:4] for m in months])
    per_year = []
    for yr in sorted(set(years)):
        test = years == yr
        train = ~test
        if train.sum() == 0 or np.dot(x[train], x[train]) == 0 or y[test].sum() == 0:
            continue
        k = float(np.dot(x[train], y[train]) / np.dot(x[train], x[train]))
        pred, obs = k * x[test], y[test]
        nz = obs > 0
        per_year.append({
            "year": int(yr),
            "k_train": k,
            "n_months": int(test.sum()),
            "annual_error_pct": float(abs(pred.sum() - obs.sum()) / obs.sum() * 100),
            "monthly_mape_pct": float(np.mean(np.abs(pred[nz] - obs[nz]) / obs[nz]) * 100) if nz.any() else None,
        })
    def mean(key: str) -> float | None:
        vals = [r[key] for r in per_year if r[key] is not None]
        return float(np.mean(vals)) if vals else None
    return {
        "method": "leave-one-year-out",
        "per_year": per_year,
        "mean_annual_error_pct": mean("annual_error_pct"),
        "mean_monthly_mape_pct": mean("monthly_mape_pct"),
    }


def harmonize(
    counts: dict[tuple[str, str], int],
    k: float,
    months: list[str],
    switch_month: str,
    c: float = 1.0,
    aqua_month: str | None = None,
) -> list[dict]:
    """Monthly series in VIIRS-equivalent cell-days.

    m <  aqua_month   : k * c * Terra-only MODIS  (source MODIS_terra_scaled)
    m <  switch_month : k * MODIS (Terra + Aqua)  (source MODIS_scaled)
    otherwise         : observed VIIRS            (source VIIRS)
    """
    out = []
    for m in months:
        modis = counts.get(("MODIS", m), 0)
        terra = counts.get(("MODIS_TERRA", m), 0)
        viirs = counts.get(("VIIRS", m), 0) if m >= switch_month else None
        row = {"month": m, "modis": modis, "modis_terra": terra, "viirs": viirs}
        if aqua_month is not None and m < aqua_month:
            row.update(harmonized=k * c * terra, source="MODIS_terra_scaled")
        elif m < switch_month:
            row.update(harmonized=k * modis, source="MODIS_scaled")
        else:
            row.update(harmonized=float(viirs), source="VIIRS")
        out.append(row)
    return out


# ---------------------------------------------------------------- service

class Harmonizer:
    """Computes and caches calibration and harmonized series per (AOI, parameters)."""

    def __init__(self, store: HotspotStore):
        self.store = store
        self._cache: dict = {}
        self._lock = threading.Lock()

    def _cached(self, key, fn):
        with self._lock:
            if key in self._cache:
                return self._cache[key]
        val = fn()
        with self._lock:
            self._cache[key] = val
        return val

    def windows(self) -> dict:
        def build():
            w = sensor_windows(self.store)
            m0, m1 = w["MODIS"]
            v0, v1 = w["VIIRS"]
            return {
                "series_start": first_full_month(m0),
                "switch_month": first_full_month(v0),
                "overlap_end": min(last_full_month(m1), last_full_month(v1)),
                "series_end": last_full_month(v1),
                # Before this month only Terra MODIS observed; Terra-only counts are scaled by c.
                "aqua_month": first_full_month(w["AQUA"][0]) if "AQUA" in w else None,
                "terra_fit_window": [first_full_month(w["AQUA"][0]), prev_month(first_full_month(v0))] if "AQUA" in w else None,
                "data_as_of": {"MODIS": m1.isoformat(), "VIIRS": v1.isoformat()},
                # Near-real-time VIIRS after the final data: provisional months and the current month.
                **self._provisional_windows(last_full_month(v1)),
            }
        return self._cached("windows", build)

    def _provisional_windows(self, series_end: str) -> dict:
        """NRT coverage after the final data. The last NRT day is dropped because it is usually
        still being filled (fewer overpasses ingested)."""
        nrt = nrt_window(self.store)
        through = {s: (b - timedelta(days=1)) for s, (_, b) in nrt.items()}
        out = {"provisional_end": None, "nrt_as_of": {s: d.isoformat() for s, d in through.items()},
               "current": None}
        if "VIIRS" in through and last_full_month(through["VIIRS"]) > series_end:
            out["provisional_end"] = last_full_month(through["VIIRS"])  # last complete provisional VIIRS month
        if through:
            # Current month: the sensor whose NRT reaches furthest (VIIRS on a tie).
            sensor = max(through, key=lambda s: (through[s], s == "VIIRS"))
            d = through[sensor]
            m = month_str(d)
            if m > (out["provisional_end"] or series_end) and last_full_month(d) != m:
                out["current"] = {"month": m, "through": d.isoformat(), "sensor": sensor}
        return out

    def provisional_counts(self, aoi: AOI, p: HarmonizationParams) -> dict:
        w = self.windows()
        def build():
            if not w["provisional_end"]:
                return {}
            return monthly_cell_days(self.store, aoi, p, levels=("standard", "nrt"),
                                     extra_where="acq_date BETWEEN ? AND ?",
                                     extra_params=(_month_last_day(w["series_end"]) + timedelta(days=1),
                                                   _month_last_day(w["provisional_end"])))
        return self._cached(("prov", aoi.key, p), build)

    def counts(self, aoi: AOI, p: HarmonizationParams) -> dict:
        return self._cached(("counts", aoi.key, p), lambda: monthly_cell_days(self.store, aoi, p))

    def _fit_pair(self, counts: dict, xkey: str, ykey: str, window: list[str], p: HarmonizationParams):
        x = np.array([counts.get((xkey, m), 0) for m in window], float)
        y = np.array([counts.get((ykey, m), 0) for m in window], float)
        use = x >= p.min_monthly_cell_days
        months = [m for m, u in zip(window, use) if u]
        if use.sum() < p.min_overlap_months:
            return None, months
        fit = fit_ratio(x[use], y[use])
        fit["validation"] = leave_one_year_out(months, x[use], y[use])
        return fit, months

    def _fit(self, counts: dict, p: HarmonizationParams):
        w = self.windows()
        return self._fit_pair(counts, "MODIS", "VIIRS", month_range(w["switch_month"], w["overlap_end"]), p)

    def terra_correction(self, aoi: AOI, p: HarmonizationParams) -> dict:
        """Fit MODIS(Terra+Aqua) = c * MODIS(Terra only) while both flew, before VIIRS."""
        def build():
            w = self.windows()
            if w["aqua_month"] is None:
                return {"c": 1.0, "status": "not_applicable", "scope": "none", "applies_before": None,
                        "note": "No Aqua MODIS data loaded; no correction applied."}
            counts = self.counts(aoi, p)
            fit, used = self._fit_pair(counts, "MODIS_TERRA", "MODIS", month_range(*w["terra_fit_window"]), p)
            status, scope = "ok", "aoi"
            if fit is None:
                if aoi.kind == "region":
                    raise ValueError("Not enough Terra/Aqua overlap months in the whole region")
                region = self.terra_correction(AOI("region"), p)
                fit = {k: region[k] for k in ("c", "r2", "n_months", "rmse", "linear", "validation")}
                status, scope = "insufficient_overlap", "region_fallback"
            else:
                fit = {"c": fit.pop("k"), **fit}
            return {
                **fit, "status": status, "scope": scope, "aoi_usable_months": len(used),
                "model": "MODIS(Terra+Aqua) = c * MODIS(Terra only) (least squares through origin, monthly unique fire cell-days)",
                "fit_window": w["terra_fit_window"],
                "applies_before": w["aqua_month"],
            }
        return self._cached(("terra", aoi.key, p), build)

    def calibration(self, aoi: AOI, p: HarmonizationParams) -> dict:
        def build():
            w = self.windows()
            counts = self.counts(aoi, p)
            fit, used = self._fit(counts, p)
            status, scope = "ok", "aoi"
            if fit is None:
                status = "insufficient_overlap"
                if aoi.kind == "region":
                    raise ValueError("Not enough overlap months in the whole region to calibrate")
                region = self.calibration(AOI("region"), p)
                fit = {k: region["calibration"][k] for k in ("k", "r2", "n_months", "rmse", "linear", "validation")}
                scope = "region_fallback"
            k = fit["k"]
            overlap = month_range(w["switch_month"], w["overlap_end"])
            used_set = set(used)
            series = []
            for m in overlap:
                mo, vi = counts.get(("MODIS", m), 0), counts.get(("VIIRS", m), 0)
                series.append({"month": m, "modis": mo, "viirs": vi, "modis_scaled": k * mo,
                               "residual": vi - k * mo, "used_in_fit": m in used_set})
            return {
                "calibration": {
                    **fit,
                    "status": status,
                    "scope": scope,
                    "aoi_usable_months": len(used),
                    "model": "VIIRS = k * MODIS (least squares through origin, monthly unique fire cell-days)",
                    "overlap_window": [w["switch_month"], w["overlap_end"]],
                },
                "terra_correction": self.terra_correction(aoi, p),
                "series": series,
            }
        return self._cached(("calibration", aoi.key, p), build)

    def harmonized(self, aoi: AOI, p: HarmonizationParams) -> dict:
        w = self.windows()
        cal = self.calibration(aoi, p)["calibration"]
        tc = self.terra_correction(aoi, p)
        months = month_range(w["series_start"], w["series_end"])
        return {
            "calibration": {
                **{k: cal[k] for k in ("k", "r2", "n_months", "status", "scope")},
                "terra_c": tc["c"], "terra_status": tc["status"], "terra_scope": tc["scope"],
                "terra_applies_before": tc["applies_before"],
            },
            "series": [
                {**r, "provisional": False}
                for r in harmonize(self.counts(aoi, p), cal["k"], months, w["switch_month"], tc["c"], w["aqua_month"])
            ] + self._provisional_rows(aoi, p),
        }

    def _provisional_rows(self, aoi: AOI, p: HarmonizationParams) -> list[dict]:
        w = self.windows()
        if not w["provisional_end"]:
            return []
        pc = self.provisional_counts(aoi, p)
        out = []
        for m in month_range(month_after(w["series_end"]), w["provisional_end"]):
            v = pc.get(("VIIRS", m), 0)
            out.append({"month": m, "modis": pc.get(("MODIS", m), 0), "modis_terra": pc.get(("MODIS_TERRA", m), 0),
                        "viirs": v, "harmonized": float(v), "source": "VIIRS_provisional", "provisional": True})
        return out

    def current_month(self, aoi: AOI, p: HarmonizationParams, baseline_years: list[int]) -> dict | None:
        """Month-to-date provisional NRT vs the same days of that month in baseline years.
        If the current-month sensor is MODIS (VIIRS NRT lagging), its count is scaled by k."""
        w = self.windows()
        if not w["current"]:
            return None
        m, through, sensor = w["current"]["month"], w["current"]["through"], w["current"]["sensor"]
        day = int(through[8:10])
        mm = int(m[5:7])
        def build():
            pc = monthly_cell_days(self.store, aoi, p, levels=("standard", "nrt"),
                                   extra_where="acq_date BETWEEN ? AND ?",
                                   extra_params=(date.fromisoformat(f"{m}-01"), date.fromisoformat(through)))
            hist = monthly_cell_days(self.store, aoi, p,
                                     extra_where="month(acq_date) = ? AND day(acq_date) <= ?", extra_params=(mm, day))
            return pc, hist
        pc, hist = self._cached(("current", aoi.key, p, m, day), build)
        cal = self.calibration(aoi, p)["calibration"]
        tc = self.terra_correction(aoi, p)
        months = [f"{y:04d}-{mm:02d}" for y in baseline_years]
        rows = harmonize(hist, cal["k"], months, w["switch_month"], tc["c"], w["aqua_month"])
        raw = pc.get((sensor, m), 0)
        value = float(raw) if sensor == "VIIRS" else cal["k"] * raw
        return {"month": m, "through": through, "days": day, "sensor": sensor, "raw_cell_days": raw,
                "value": value, "value_source": "VIIRS NRT" if sensor == "VIIRS" else "MODIS NRT × k",
                "baseline_rows": [{"year": int(r["month"][:4]), "value": r["harmonized"], "source": r["source"]} for r in rows]}

    def meta(self, aoi: AOI, p: HarmonizationParams) -> dict:
        w = self.windows()
        return {
            "aoi": aoi.describe(),
            "parameters": {**asdict(p), "processing_levels": list(PROCESSING_LEVELS)},
            "windows": w,
            "units": "unique fire cell-days per month (VIIRS-equivalent after harmonization)",
            "caveat": "Prototype parameters requiring scientific validation. Not an official NASA algorithm. Not a forecast.",
        }
