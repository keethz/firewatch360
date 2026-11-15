"""FRP intensity metric: same harmonization pipeline, summing fire radiative power (MW)."""
import duckdb
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.services.aoi import AOI, parse_aoi
from app.services.harmonize import HarmonizationParams, Harmonizer, monthly_cell_days
from app.services.store import HotspotStore

FRP = HarmonizationParams(metric="frp")


def make_store(rows):
    """rows: (sensor, lat, lon, date, frp, type, satellite)"""
    con = duckdb.connect()
    con.execute("""CREATE TABLE hotspots (sensor VARCHAR, latitude DOUBLE, longitude DOUBLE, acq_date DATE,
                   confidence_norm VARCHAR, type INTEGER, processing_level VARCHAR, satellite VARCHAR, frp DOUBLE)""")
    con.executemany("INSERT INTO hotspots VALUES (?, ?, ?, ?, 'nominal', ?, 'archive', ?, ?)",
                    [(s, la, lo, d, t, sat, f) for s, la, lo, d, f, t, sat in rows])
    return HotspotStore(None, con=con)


def test_frp_sums_every_detection_and_clips_negatives():
    store = make_store([
        # two VIIRS pixels in the same cell-day: both count (intensity is not deduplicated)
        ("VIIRS", 37.001, -120.001, "2015-07-01", 4.0, 0, "N"),
        ("VIIRS", 37.004, -120.004, "2015-07-01", 6.0, 0, "N"),
        ("MODIS", 37.005, -120.005, "2015-07-01", 30.0, 0, "Terra"),
        ("MODIS", 37.005, -120.005, "2015-07-01", 20.0, 0, "Aqua"),
        ("MODIS", 37.005, -120.005, "2015-07-02", -5.0, 0, "Terra"),   # retrieval artefact -> 0
        ("MODIS", 37.005, -120.005, "2015-07-03", None, 0, "Terra"),   # missing -> 0
        ("VIIRS", 37.005, -120.005, "2015-07-02", 99.0, 2, "N"),       # static source, dropped
    ])
    c = monthly_cell_days(store, AOI("region"), FRP)
    assert c == {("VIIRS", "2015-07"): 10.0, ("MODIS", "2015-07"): 50.0, ("MODIS_TERRA", "2015-07"): 30.0}
    # cell-days on the same data are unchanged
    cd = monthly_cell_days(store, AOI("region"), HarmonizationParams())
    assert cd[("VIIRS", "2015-07")] == 1 and cd[("MODIS", "2015-07")] == 3


def test_frp_calibration_recovers_known_ratio():
    """MODIS detections 25 MW, VIIRS 5 MW with twice as many -> VIIRS FRP = 0.4 x MODIS FRP."""
    rows = [("MODIS", 45.0, -100.0, "2000-11-01", 25.0, 0, "Terra")]
    for y in range(2012, 2016):
        for m in range(1, 13):
            d = f"{y}-{m:02d}-15"
            for i in range(10):
                rows.append(("MODIS", 36.0 + i * 0.02, -119.0, d, 25.0, 0, "Terra"))
                rows += [("VIIRS", 36.0 + i * 0.02, -119.0 + j * 0.05, d, 5.0, 0, "N") for j in range(2)]
    rows.append(("VIIRS", 45.0, -100.0, "2012-01-20", 5.0, 0, "N"))
    rows += [("MODIS", 45.0, -100.0, "2015-12-31", 25.0, 0, "Terra"), ("VIIRS", 45.0, -100.0, "2015-12-31", 5.0, 0, "N")]
    h = Harmonizer(make_store(rows))
    aoi = parse_aoi(bbox="-119.5,35.5,-118.5,36.5")
    cal = h.calibration(aoi, FRP)["calibration"]
    assert cal["status"] == "ok" and cal["k"] == pytest.approx(0.4)
    assert "total FRP" in cal["model"]
    s = {r["month"]: r for r in h.harmonized(aoi, FRP)["series"]}
    assert s["2012-01"]["source"] == "MODIS_scaled" and s["2012-01"]["harmonized"] == pytest.approx(100.0)
    assert s["2013-05"]["harmonized"] == pytest.approx(100.0)
    # FRP and cell-day results are cached separately
    assert h.calibration(aoi, HarmonizationParams())["calibration"]["k"] == pytest.approx(2.0)


def test_metric_validation():
    with pytest.raises(ValueError):
        HarmonizationParams(metric="acres").validate()
    assert FRP.min_fit_value == 100 and HarmonizationParams().min_fit_value == 5


client = TestClient(create_app())


def test_api_frp_calendar_real_data():
    r = client.get("/api/calendar?metric=frp")
    if r.status_code == 503:
        pytest.skip("processed data not built on this machine")
    body = r.json()
    assert body["metric"] == "frp" and "MW" in body["units"]
    assert body["calibration"]["k"] > 0
    assert client.get("/api/calendar?metric=acres").status_code == 422
    csv = client.get("/api/export/calendar.csv?metric=frp").text
    assert "harmonized_frp_mw" in csv and "radiative power" in csv
