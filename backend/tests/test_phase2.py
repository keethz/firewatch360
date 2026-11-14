"""Phase 2 analytics tests. Series below are hand-made synthetic values."""
import csv
import io

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.services import analytics as an
from app.services.harmonize import month_range


def series(values_by_year: dict[int, list[float]]) -> list[dict]:
    out = []
    for y, vals in sorted(values_by_year.items()):
        for m, v in enumerate(vals, start=1):
            out.append({"month": f"{y}-{m:02d}", "harmonized": float(v), "modis": 0, "viirs": None,
                        "source": "MODIS_scaled" if y < 2012 else "VIIRS"})
    return out


# 6 complete baseline years with a known July distribution, then a partial 2026
JULY = [10, 12, 14, 16, 18, 20]
BASE = {2020 + i: [1] * 6 + [JULY[i]] + [1] * 5 for i in range(6)}


def test_complete_years_and_default_baseline():
    s = series({**BASE, 2026: [1] * 7})
    assert an.complete_years(s) == list(range(2020, 2026))
    assert an.resolve_baseline(s, None, None, None) == (2020, 2025)  # all complete years before 2026
    assert an.resolve_baseline(s, 2023, None, None) == (2020, 2022)
    assert an.resolve_baseline(s, None, 2021, 2024) == (2021, 2024)
    with pytest.raises(an.AnalyticsError):
        an.resolve_baseline(s, None, 2025, 2021)


def test_climatology_stats():
    clim = an.climatology(series(BASE), 2020, 2025)
    jul = clim["months"][6]
    assert clim["status"] == "ok"
    assert jul["n_years"] == 6
    assert jul["median"] == 15.0
    assert jul["mean"] == pytest.approx(15.0)
    assert jul["p25"] == pytest.approx(np.percentile(JULY, 25))
    assert jul["p75"] == pytest.approx(np.percentile(JULY, 75))
    assert jul["mad"] == 3.0  # |x - 15| = 5,3,1,1,3,5 -> median 3


def test_insufficient_history():
    clim = an.climatology(series(BASE), 2022, 2025)  # 4 years < 5
    assert clim["status"] == "insufficient_history"
    rows = an.annotate(series(BASE), clim, 2.0)
    assert all(r["z"] is None and r["anomaly"] is None for r in rows)


def test_robust_z_and_flags():
    assert an.robust_z(15 + 2 * 1.4826 * 3, 15, 3) == pytest.approx(2.0)
    assert an.robust_z(5, 5, 0) is None  # MAD 0 -> undefined, never flagged
    s = series({**BASE, 2026: [1] * 6 + [40]})
    clim = an.climatology(s, 2020, 2025)
    rows = an.annotate(s, clim, 2.0)
    jul26 = rows[-1]
    assert jul26["z"] == pytest.approx((40 - 15) / (1.4826 * 3))
    assert jul26["anomaly"] == "high"
    assert jul26["pct_vs_median"] == pytest.approx((40 - 15) / 15 * 100)
    assert [r["month"] for r in rows if r["anomaly"]] == ["2026-07"]  # baseline years are within 2 sigma
    latest = an.latest_vs_median(rows)
    assert latest["month"] == "2026-07" and latest["median"] == 15.0


def test_calendar_matrix():
    s = series({2020: list(range(1, 13)), 2021: [5, 5]})
    cal = an.calendar_matrix(s)
    assert [y["year"] for y in cal] == [2020, 2021]
    assert cal[0]["complete"] and cal[0]["total"] == 78
    assert not cal[1]["complete"] and cal[1]["months"][2] is None and cal[1]["total"] == 10


def test_theil_sen():
    x = np.arange(2000, 2010, dtype=float)
    y = 3 * x + 7
    y[4] = 1e6  # one outlier does not move the median slope
    t = an.theil_sen(x, y)
    assert t["slope_per_year"] == pytest.approx(3.0)
    assert t["intercept"] == pytest.approx(7.0)
    assert an.theil_sen(x[:2], y[:2]) is None


def test_rankings_and_peak_season():
    s = series(BASE)
    clim = an.climatology(s, 2020, 2025)
    rk = an.rankings(s, clim)
    assert rk["top_months"][0] == {"month": "2025-07", "harmonized": 20.0, "source": "VIIRS"}
    assert rk["top_years"][0]["year"] == 2025
    assert rk["peak_season"] == ["Jul"]
    assert rk["trend"]["slope_per_year"] == pytest.approx(2.0)


def test_window():
    s = series(BASE)
    w = an.window(s, "2021-03", "2021-05")
    assert [r["month"] for r in w] == ["2021-03", "2021-04", "2021-05"]


# ---------------------------------------------------------------- API (real data when available)

client = TestClient(create_app())


def _need_data():
    if not client.app.state.store.available:
        pytest.skip("processed data not built on this machine")


def test_api_validation():
    assert client.get("/api/calendar?start=2020-13").status_code in (422, 503)
    assert client.get("/api/calendar?start=2021-01&end=2020-01").status_code in (422, 503)
    assert client.get("/api/export/hotspots.csv?start=2020-02-01&end=2020-01-01").status_code in (422, 503)
    assert client.get("/api/export/hotspots.csv?start=2020-01-01&end=2020-01-02&sensors=GOES").status_code in (422, 503)


def test_api_calendar_and_anomalies():
    _need_data()
    cal = client.get("/api/calendar?bbox=-124.5,32.5,-114.1,42.0").json()
    years = [y["year"] for y in cal["years"]]
    assert years[0] == 2000 and years == sorted(years)
    assert cal["climatology"]["status"] == "ok"
    assert cal["years"][0]["months"][0] is None  # 2000 starts in November
    an_ = client.get("/api/anomalies?bbox=-124.5,32.5,-114.1,42.0").json()
    assert an_["latest_vs_median"]["month"] == cal["window"][1]
    assert all(abs(a["z"]) >= 2 for a in an_["anomalies"])
    assert len(an_["rankings"]["top_months"]) == 5
    clim = client.get("/api/climatology?bbox=-124.5,32.5,-114.1,42.0&year=2020").json()
    assert clim["climatology"]["baseline"][1] == 2019
    assert len(clim["selected_year_rows"]) == 12


def test_api_calendar_csv_matches_json():
    _need_data()
    q = "?bbox=-124.5,32.5,-114.1,42.0&start=2020-01&end=2020-12"
    text = client.get("/api/export/calendar.csv" + q).text
    rows = list(csv.DictReader(io.StringIO("\n".join(l for l in text.splitlines() if not l.startswith("#")))))
    assert len(rows) == 12
    cal = client.get("/api/calendar" + q).json()
    assert sum(float(r["harmonized_cell_days"]) for r in rows) == pytest.approx(cal["years"][0]["total"], rel=1e-6)


def test_api_hotspots_csv_counts():
    _need_data()
    q = "?lat=34.05&lon=-118.25&radius_km=80&start=2025-01-01&end=2025-01-31"
    r = client.get("/api/export/hotspots.csv" + q)
    assert r.status_code == 200
    rows = list(csv.DictReader(io.StringIO(r.text)))
    assert len(rows) == int(r.headers["x-row-count"]) > 0
    assert all("2025-01-01" <= x["acq_date"] <= "2025-01-31" for x in rows)
    assert client.get("/api/export/hotspots.csv?start=2000-01-01&end=2026-12-31").status_code == 413


def test_month_range_consistency():
    assert len(month_range("2000-11", "2026-07")) == 309
