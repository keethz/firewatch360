"""Fire Risk Brief rules. Hand-made inputs only."""
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.services import analytics as an
from app.services.brief import (agreement, anomaly_level, build_brief, confidence, mann_kendall, ordinal,
                                rank_phrase, trend_summary)
from app.services.harmonize import harmonize


def test_ordinal():
    assert [ordinal(n) for n in (1, 2, 3, 4, 11, 12, 13, 21, 22, 101, 111)] == \
        ["1st", "2nd", "3rd", "4th", "11th", "12th", "13th", "21st", "22nd", "101st", "111th"]


def test_anomaly_levels():
    assert anomaly_level(None) == "Unknown"
    assert anomaly_level(3.0) == "Severe"
    assert anomaly_level(2.0) == "Moderate"
    assert anomaly_level(1.99) == "Normal"
    assert anomaly_level(-2.0) == "Below normal"
    assert anomaly_level(2.5, z_threshold=3.0) == "Normal"


def test_rank_phrase_picks_nearer_end():
    r = rank_phrase(80, [10, 20, 90, 100, 30], "October", 2001)
    assert r == {"rank": 3, "side": "highest", "of": 6, "text": "3rd-highest October since 2001"}
    assert rank_phrase(5, [10, 20, 30], "May", 2000)["text"] == "Lowest May since 2000"
    assert rank_phrase(50, [10, 20, 30], "May", 2000)["text"] == "Highest May since 2000"


def test_agreement_rules():
    assert agreement("high", "high", True, True)["label"] == "MODIS and VIIRS agree"
    assert agreement("normal", "high", True, True)["label"] == "VIIRS-only signal"
    assert agreement("high", "normal", True, True)["label"] == "MODIS-only signal"
    assert agreement("low", "normal", True, True)["status"] == "disagree"
    assert agreement("high", None, True, False)["status"] == "single"
    assert agreement(None, None, False, False)["status"] == "unknown"


def test_confidence_scoring():
    assert confidence(provisional=False, agree_status="agree", median=500, n_baseline=20, mtd_days=None, fallback=False) \
        == {"level": "High", "score": 3, "reasons": []}
    c = confidence(provisional=True, agree_status="agree", median=500, n_baseline=20, mtd_days=13, fallback=False)
    assert c["level"] == "Medium" and len(c["reasons"]) == 1
    c = confidence(provisional=True, agree_status="single", median=3, n_baseline=6, mtd_days=8, fallback=True)
    assert c["level"] == "Low" and c["score"] < 0 and len(c["reasons"]) == 6


def test_mann_kendall_detects_monotonic_trend_only():
    years = list(range(2001, 2021))
    up = mann_kendall(years, [100 + 5 * i for i in range(20)])
    assert up["s"] == 190 and up["p"] < 1e-6 and up["slope_per_year"] == pytest.approx(5)
    flat = mann_kendall(years, [100, 120] * 10)
    assert flat["p"] > 0.05
    assert mann_kendall(years[:5], [1, 2, 3, 4, 5]) is None
    t = trend_summary([{"year": y, "total": 100 + 5 * i, "complete": True} for i, y in enumerate(years)])
    assert t["direction"] == "Increasing" and t["pct_per_year"] > 0
    t = trend_summary([{"year": y, "total": v, "complete": True} for y, v in zip(years, [100, 120] * 10)])
    assert t["direction"] == "No clear trend"


# ---------------------------------------------------------------- build_brief end to end on a synthetic series

def series_with_october(value_2025: float):
    rows = []
    for y in range(2013, 2026):
        for m in range(1, 13):
            v = 100.0 + (y - 2013) if m == 10 else 10.0
            if (y, m) == (2025, 10):
                v = value_2025
            rows.append({"month": f"{y}-{m:02d}", "harmonized": v, "modis": v / 2, "modis_terra": 0,
                         "viirs": v, "source": "VIIRS"})
    return rows[: 12 * 12 + 10]  # end at 2025-10


WIN = {"switch_month": "2012-02", "aqua_month": "2002-08", "series_start": "2013-01",
       "nrt_as_of": {}, "current": None}


def run(series):
    clim = an.climatology(series, 2013, 2024)
    return build_brief(series=series, windows=WIN, calibration={"scope": "aoi"}, terra={"scope": "aoi"},
                       clim=clim, rankings=an.rankings(series, clim), current=None, current_counts=None,
                       k=2.0, c=1.8, z_threshold=2.0, harmonize_fn=lambda h, m: harmonize(h, 2.0, m, "2012-02"))


def test_brief_severe_high_month():
    b = run(series_with_october(400.0))
    assert b["level"] == "Severe"
    assert b["headline"].startswith("This area is experiencing unusually high fire activity")
    assert b["reference"]["label"] == "October 2025"
    med = 100 + 5.5  # median of 100..111
    assert b["current_activity"]["pct_vs_median"] == pytest.approx((400 - med) / med * 100)
    assert b["current_activity"]["text"].startswith("+")
    assert b["rank"]["text"] == "Highest October since 2013"
    assert b["agreement"]["label"] == "MODIS and VIIRS agree"
    assert b["confidence"]["level"] == "High"
    assert b["peak_season"] == ["Oct"]


def test_brief_normal_month_says_normal():
    b = run(series_with_october(106.0))
    assert b["level"] == "Normal"
    assert b["headline"] == "Fire activity in this area is within its normal historical range."
    assert "below" in b["current_activity"]["text"] or "above" in b["current_activity"]["text"]


def test_api_brief_real_data():
    client = TestClient(create_app())
    r = client.get("/api/brief?bbox=-124.5,32.5,-114.1,42.0&end=2020-09")
    if r.status_code == 503:
        pytest.skip("processed data not built on this machine")
    b = r.json()
    assert b["reference"]["label"] == "September 2020"
    assert b["level"] == "Severe" and b["rank"]["text"] == "Highest September since 2001"
    assert b["agreement"]["status"] == "agree"
    cur = client.get("/api/brief").json()
    assert cur["reference"]["provisional"]
    assert cur["confidence"]["level"] in ("Medium", "Low")  # provisional data is never High
