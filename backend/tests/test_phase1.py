"""Phase 1 harmonization tests. All fixtures here are hand-made synthetic rows."""
import json
from datetime import date

import duckdb
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.services.aoi import AOI, AOIError, parse_aoi
from app.services.harmonize import (
    HarmonizationParams,
    Harmonizer,
    first_full_month,
    fit_ratio,
    harmonize,
    last_full_month,
    leave_one_year_out,
    month_range,
    monthly_cell_days,
)
from app.services.store import HotspotStore

P = HarmonizationParams()


def make_store(rows):
    """rows: (sensor, lat, lon, 'YYYY-MM-DD', confidence_norm, type, processing_level, satellite)"""
    con = duckdb.connect()
    con.execute("""CREATE TABLE hotspots (sensor VARCHAR, latitude DOUBLE, longitude DOUBLE, acq_date DATE,
                   confidence_norm VARCHAR, type INTEGER, processing_level VARCHAR, satellite VARCHAR)""")
    con.executemany("INSERT INTO hotspots VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)
    return HotspotStore(None, con=con)


def row(sensor, lat, lon, d, conf="nominal", typ=0, level="archive", sat=None):
    sat = sat or ("Terra" if sensor == "MODIS" else "N")
    return (sensor, lat, lon, d, conf, typ, level, sat)


# ---------------------------------------------------------------- gridding / counting

def test_unique_cell_days_dedupes_within_cell_and_day():
    store = make_store([
        # three VIIRS pixels in one 0.01 deg cell on one day -> 1 cell-day
        row("VIIRS", 37.001, -120.001, "2015-07-01"),
        row("VIIRS", 37.004, -120.006, "2015-07-01"),
        row("VIIRS", 37.009, -120.009, "2015-07-01"),
        # same cell, next day -> +1
        row("VIIRS", 37.005, -120.005, "2015-07-02"),
        # neighbouring cell, same day -> +1
        row("VIIRS", 37.011, -120.005, "2015-07-01"),
        # Terra + Aqua MODIS in the same cell-day -> 1
        row("MODIS", 37.005, -120.005, "2015-07-01"),
        row("MODIS", 37.006, -120.004, "2015-07-01"),
    ])
    c = monthly_cell_days(store, AOI("region"), P)
    assert c == {("VIIRS", "2015-07"): 3, ("MODIS", "2015-07"): 1, ("MODIS_TERRA", "2015-07"): 1}


def test_cell_edge_values_stay_in_their_cell():
    # 37.12 / 0.01 is 3711.9999... in floating point; it must land in cell 3712 like 37.125.
    store = make_store([row("VIIRS", 37.12, -120.5, "2015-07-01"), row("VIIRS", 37.125, -120.5, "2015-07-01")])
    assert monthly_cell_days(store, AOI("region"), P)[("VIIRS", "2015-07")] == 1


def test_grid_size_is_configurable():
    store = make_store([row("VIIRS", 37.00, -120.0, "2015-07-01"), row("VIIRS", 37.05, -120.0, "2015-07-01")])
    assert monthly_cell_days(store, AOI("region"), P)[("VIIRS", "2015-07")] == 2
    coarse = HarmonizationParams(grid_deg=0.1)
    assert monthly_cell_days(store, AOI("region"), coarse)[("VIIRS", "2015-07")] == 1


def test_filters_type_confidence_and_nrt():
    store = make_store([
        row("VIIRS", 37.0, -120.0, "2015-07-01"),
        row("VIIRS", 38.0, -120.0, "2015-07-01", conf="low"),       # dropped at min_confidence=nominal
        row("VIIRS", 39.0, -120.0, "2015-07-01", typ=2),             # static source, dropped
        row("VIIRS", 40.0, -120.0, "2015-07-01", typ=None, level="nrt"),  # provisional, dropped
    ])
    assert monthly_cell_days(store, AOI("region"), P)[("VIIRS", "2015-07")] == 1
    loose = HarmonizationParams(min_confidence="low", vegetation_only=False)
    assert monthly_cell_days(store, AOI("region"), loose)[("VIIRS", "2015-07")] == 3


def test_aoi_filters_bbox_circle_polygon():
    store = make_store([
        row("VIIRS", 34.0, -118.0, "2015-07-01"),   # inside all
        row("VIIRS", 34.4, -118.0, "2015-07-01"),   # in bbox, ~44 km north: outside 30 km circle
        row("VIIRS", 36.0, -118.0, "2015-07-01"),   # outside everything
    ])
    bbox = parse_aoi(bbox="-118.5,33.5,-117.5,34.5")
    circle = parse_aoi(lat=34.0, lon=-118.0, radius_km=30)
    # L-shape: its bbox contains (34.4, -118.0) but the notch cut from the north-west corner excludes it
    ell = {"type": "Polygon", "coordinates": [[[-118.5, 33.5], [-117.5, 33.5], [-117.5, 34.5], [-117.9, 34.5],
                                               [-117.9, 34.2], [-118.5, 34.2], [-118.5, 33.5]]]}
    poly = parse_aoi(polygon=json.dumps({"type": "Feature", "geometry": ell}))
    assert monthly_cell_days(store, bbox, P)[("VIIRS", "2015-07")] == 2
    assert monthly_cell_days(store, circle, P)[("VIIRS", "2015-07")] == 1
    assert monthly_cell_days(store, poly, P)[("VIIRS", "2015-07")] == 1


def test_aoi_validation():
    with pytest.raises(AOIError):
        parse_aoi(bbox="1,2,3")
    with pytest.raises(AOIError):
        parse_aoi(bbox="-100,40,-110,45")  # w > e
    with pytest.raises(AOIError):
        parse_aoi(lat=34, lon=-118)  # no radius
    with pytest.raises(AOIError):
        parse_aoi(lat=34, lon=-118, radius_km=5000)
    with pytest.raises(AOIError):
        parse_aoi(polygon="not json")
    with pytest.raises(AOIError):
        parse_aoi(bbox="-118,33,-117,34", lat=34, lon=-118, radius_km=10)


# ---------------------------------------------------------------- fitting

def test_fit_ratio_exact():
    x = np.array([10, 20, 30, 40.0])
    fit = fit_ratio(x, 2.5 * x)
    assert fit["k"] == pytest.approx(2.5)
    assert fit["r2"] == pytest.approx(1.0)
    assert fit["rmse"] == pytest.approx(0.0, abs=1e-9)
    assert fit["linear"]["b"] == pytest.approx(2.5)
    assert fit["linear"]["a"] == pytest.approx(0.0, abs=1e-9)


def test_fit_ratio_hand_computed():
    # k = sum(xy)/sum(x^2) = (1*2 + 2*5 + 3*6) / (1 + 4 + 9) = 30/14
    fit = fit_ratio(np.array([1, 2, 3.0]), np.array([2, 5, 6.0]))
    assert fit["k"] == pytest.approx(30 / 14)
    resid = np.array([2, 5, 6]) - 30 / 14 * np.array([1, 2, 3])
    ss_tot = np.sum((np.array([2, 5, 6]) - 13 / 3) ** 2)
    assert fit["r2"] == pytest.approx(1 - np.sum(resid**2) / ss_tot)


def test_fit_ratio_rejects_all_zero():
    with pytest.raises(ValueError):
        fit_ratio(np.zeros(5), np.ones(5))


def test_leave_one_year_out():
    months = month_range("2013-01", "2014-12")
    x = np.arange(1, 25, dtype=float)
    y = 2.0 * x
    y[:12] *= 1.1  # 2013 runs 10% hot
    v = leave_one_year_out(months, x, y)
    assert [r["year"] for r in v["per_year"]] == [2013, 2014]
    r2014 = v["per_year"][1]
    assert r2014["k_train"] == pytest.approx(2.2)  # trained on 2013 only
    assert r2014["annual_error_pct"] == pytest.approx(10.0)
    assert r2014["monthly_mape_pct"] == pytest.approx(10.0)


def test_harmonize_switches_source():
    counts = {("MODIS", "2011-12"): 10, ("MODIS", "2012-02"): 5, ("VIIRS", "2012-02"): 13}
    s = harmonize(counts, 2.0, ["2011-12", "2012-01", "2012-02"], "2012-02")
    assert s[0] == {"month": "2011-12", "modis": 10, "modis_terra": 0, "viirs": None, "harmonized": 20.0, "source": "MODIS_scaled"}
    assert s[1]["harmonized"] == 0.0 and s[1]["source"] == "MODIS_scaled"
    assert s[2] == {"month": "2012-02", "modis": 5, "modis_terra": 0, "viirs": 13, "harmonized": 13.0, "source": "VIIRS"}


def test_harmonize_terra_only_period():
    counts = {("MODIS", "2002-06"): 7, ("MODIS_TERRA", "2002-06"): 7,
              ("MODIS", "2002-08"): 20, ("MODIS_TERRA", "2002-08"): 10}
    s = harmonize(counts, 2.0, ["2002-06", "2002-08"], "2012-02", c=1.5, aqua_month="2002-08")
    assert s[0]["source"] == "MODIS_terra_scaled" and s[0]["harmonized"] == pytest.approx(2.0 * 1.5 * 7)
    assert s[1]["source"] == "MODIS_scaled" and s[1]["harmonized"] == pytest.approx(40.0)


def test_month_helpers():
    assert first_full_month(date(2012, 1, 20)) == "2012-02"
    assert first_full_month(date(2000, 11, 1)) == "2000-11"
    assert first_full_month(date(2012, 12, 5)) == "2013-01"
    assert last_full_month(date(2026, 7, 31)) == "2026-07"
    assert last_full_month(date(2026, 11, 14)) == "2026-10"
    assert month_range("2011-11", "2012-02") == ["2011-11", "2011-12", "2012-01", "2012-02"]


# ---------------------------------------------------------------- service / guards

def synthetic_region(k=2.0, years=range(2012, 2016)):
    """MODIS at 2000-11 plus overlap years. California-ish AOI gets 30 cells/month, elsewhere 20."""
    rows = [row("MODIS", 45.0, -100.0, "2000-11-01")]
    for y in years:
        for m in range(1, 13):
            d = f"{y}-{m:02d}-15"
            for i in range(10):  # AOI cells
                rows.append(row("MODIS", 36.0 + i * 0.02, -119.0, d))
                for j in range(int(k)):
                    rows.append(row("VIIRS", 36.0 + i * 0.02, -119.0 + j * 0.05, d))
            for i in range(10):  # elsewhere, ratio 3
                rows.append(row("MODIS", 45.0 + i * 0.02, -100.0, d))
                for j in range(3):
                    rows.append(row("VIIRS", 45.0 + i * 0.02, -100.0 + j * 0.05, d))
    rows.append(row("VIIRS", 45.0, -100.0, "2012-01-20"))  # VIIRS starts mid-month
    last = f"{max(years)}-12-31"  # make December the last complete month for both sensors
    rows += [row("MODIS", 45.0, -100.0, last), row("VIIRS", 45.0, -100.0, last)]
    return make_store(rows)


def test_calibration_recovers_known_ratio():
    h = Harmonizer(synthetic_region())
    aoi = parse_aoi(bbox="-119.5,35.5,-118.5,36.5")
    out = h.calibration(aoi, P)
    cal = out["calibration"]
    assert cal["status"] == "ok" and cal["scope"] == "aoi"
    assert cal["k"] == pytest.approx(2.0)
    # 2012-01 is a partial VIIRS month, so the overlap starts 2012-02
    assert cal["overlap_window"] == ["2012-02", "2015-12"]
    assert cal["n_months"] == 47
    assert cal["validation"]["mean_annual_error_pct"] == pytest.approx(0.0, abs=1e-9)


def test_insufficient_overlap_falls_back_to_region():
    h = Harmonizer(synthetic_region())
    # Strict guard: needs more usable months than exist in the AOI
    p = HarmonizationParams(min_overlap_months=100)
    aoi = parse_aoi(bbox="-119.5,35.5,-118.5,36.5")
    with pytest.raises(ValueError):  # the region itself cannot satisfy it either
        h.calibration(aoi, p)
    # AOI with no fires at all -> region factor, labelled
    empty = parse_aoi(bbox="-80,30,-79,31")
    cal = h.calibration(empty, P)["calibration"]
    assert cal["status"] == "insufficient_overlap"
    assert cal["scope"] == "region_fallback"
    assert cal["aoi_usable_months"] == 0
    region_k = h.calibration(AOI("region"), P)["calibration"]["k"]
    assert cal["k"] == pytest.approx(region_k)
    assert 2.0 < region_k < 3.0


def test_harmonized_series_from_store():
    h = Harmonizer(synthetic_region())
    aoi = parse_aoi(bbox="-119.5,35.5,-118.5,36.5")
    out = h.harmonized(aoi, P)
    s = {r["month"]: r for r in out["series"]}
    assert out["series"][0]["month"] == "2000-11"
    assert s["2012-01"]["source"] == "MODIS_scaled" and s["2012-01"]["harmonized"] == pytest.approx(20.0)
    assert s["2012-02"]["source"] == "VIIRS" and s["2012-02"]["harmonized"] == 20.0
    assert s["2005-06"]["harmonized"] == 0.0


# ---------------------------------------------------------------- API

client = TestClient(create_app())


def test_api_rejects_bad_aoi_and_params():
    assert client.get("/api/calibration?bbox=1,2").status_code in (422, 503)
    assert client.get("/api/calibration?min_confidence=extreme").status_code in (422, 503)


def test_api_calibration_real_data():
    r = client.get("/api/calibration")
    if r.status_code == 503:
        pytest.skip("processed data not built on this machine")
    body = r.json()
    cal = body["calibration"]
    assert cal["status"] == "ok" and cal["scope"] == "aoi"
    assert cal["k"] > 0 and cal["n_months"] >= 24
    assert body["windows"]["switch_month"] == "2012-02"
    assert len(body["series"]) == len(month_range(*cal["overlap_window"]))
    h = client.get("/api/harmonized").json()
    assert h["series"][0]["month"] == "2000-11"
    assert h["calibration"]["k"] == cal["k"]


def terra_aqua_store():
    """Terra only from 2000-11; Aqua joins 2002-07-04 and sees as many extra cells as Terra (c = 2).
    VIIRS from 2012-01-20 sees 3x the combined MODIS (k = 3)."""
    rows = []
    for m in month_range("2000-11", "2015-12"):
        d = f"{m}-15"
        for i in range(10):
            rows.append(row("MODIS", 36.0 + i * 0.02, -119.0, d, sat="Terra"))
            if m >= "2002-07":
                rows.append(row("MODIS", 36.0 + i * 0.02, -118.0, d, sat="Aqua"))  # different cells
            if m >= "2012-01":
                for j in range(6):
                    rows.append(row("VIIRS", 36.0 + i * 0.02, -117.0 + j * 0.05, d))
    rows += [row("MODIS", 36.0, -119.0, "2002-07-04", sat="Aqua"), row("VIIRS", 36.0, -117.0, "2012-01-20"),
             row("MODIS", 36.0, -119.0, "2015-12-31"), row("VIIRS", 36.0, -117.0, "2015-12-31")]
    return make_store(rows)


def test_terra_correction_recovers_known_factor():
    h = Harmonizer(terra_aqua_store())
    w = h.windows()
    assert w["aqua_month"] == "2002-08"
    assert w["terra_fit_window"] == ["2002-08", "2012-01"]
    tc = h.terra_correction(AOI("region"), P)
    assert tc["status"] == "ok" and tc["c"] == pytest.approx(2.0)
    s = {r["month"]: r for r in h.harmonized(AOI("region"), P)["series"]}
    k = h.calibration(AOI("region"), P)["calibration"]["k"]
    assert k == pytest.approx(3.0, rel=1e-2)  # the month-end boundary rows add a little noise
    # Pre-Aqua month: 10 Terra cells -> 10 * c * k = 60, same level as the VIIRS era
    assert s["2001-06"]["source"] == "MODIS_terra_scaled"
    assert s["2001-06"]["harmonized"] == pytest.approx(10 * tc["c"] * k)
    assert s["2001-06"]["harmonized"] == pytest.approx(60.0, rel=1e-2)
    assert s["2005-06"]["harmonized"] == pytest.approx(60.0, rel=1e-2) and s["2005-06"]["source"] == "MODIS_scaled"
    assert s["2014-06"]["harmonized"] == 60.0


def test_terra_correction_falls_back_and_is_absent_without_aqua():
    h = Harmonizer(terra_aqua_store())
    tc = h.terra_correction(parse_aoi(bbox="-80,30,-79,31"), P)
    assert tc["status"] == "insufficient_overlap" and tc["scope"] == "region_fallback"
    assert tc["c"] == pytest.approx(2.0)
    no_aqua = Harmonizer(synthetic_region())
    assert no_aqua.terra_correction(AOI("region"), P)["status"] == "not_applicable"
    assert no_aqua.windows()["aqua_month"] is None


def test_api_terra_correction_real_data():
    r = client.get("/api/calibration")
    if r.status_code == 503:
        pytest.skip("processed data not built on this machine")
    tc = r.json()["terra_correction"]
    assert tc["status"] == "ok" and tc["applies_before"] == "2002-08"
    assert 1.0 < tc["c"] < 3.0
    first = client.get("/api/harmonized").json()["series"][0]
    assert first["month"] == "2000-11" and first["source"] == "MODIS_terra_scaled"
