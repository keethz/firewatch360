"""Agricultural filter, outlook, burned area / post-fire and smoke. Synthetic data, mocked services."""
import io
import json
import zipfile
from datetime import date
from urllib.parse import parse_qs

import duckdb
import httpx
import numpy as np
import pytest
import shapefile
import shapely
from fastapi.testclient import TestClient

from app.main import create_app
from app.services import burned as bu
from app.services import outlook as ol
from app.services import smoke as sm
from app.services.aoi import AOI
from app.services.harmonize import HarmonizationParams, monthly_cell_days
from app.services.store import HotspotStore

# ---------------------------------------------------------------- agricultural filter


def ag_store(with_mask=True):
    con = duckdb.connect()
    con.execute("""CREATE TABLE hotspots (sensor VARCHAR, latitude DOUBLE, longitude DOUBLE, acq_date DATE,
                   confidence_norm VARCHAR, type INTEGER, processing_level VARCHAR, satellite VARCHAR)""")
    rows = [("VIIRS", 35.005, -90.005, "2020-10-01"),   # cropland cell (80%)
            ("VIIRS", 35.015, -90.005, "2020-10-01"),   # mixed cell (30%): kept
            ("VIIRS", 37.005, -119.005, "2020-10-01")]  # forest, no row in the mask: kept
    con.executemany("INSERT INTO hotspots VALUES (?, ?, ?, ?, 'nominal', 0, 'archive', 'N')", rows)
    if with_mask:
        con.execute("CREATE TABLE cropland (gy BIGINT, gx BIGINT, crop_pct UTINYINT, crops_pct UTINYINT)")
        con.executemany("INSERT INTO cropland VALUES (?, ?, ?, ?)", [(3500, -9001, 80, 80), (3501, -9001, 30, 30)])
    return HotspotStore(None, con=con)


def test_agriculture_filter_drops_only_mostly_cropland_cells():
    store = ag_store()
    assert store.has_cropland
    all_ = monthly_cell_days(store, AOI("region"), HarmonizationParams())
    noag = monthly_cell_days(store, AOI("region"), HarmonizationParams(exclude_agriculture=True))
    assert all_[("VIIRS", "2020-10")] == 3 and noag[("VIIRS", "2020-10")] == 2


def test_agriculture_filter_requires_mask():
    assert not ag_store(with_mask=False).has_cropland


# ---------------------------------------------------------------- outlook


def synthetic_series(years=range(2001, 2026), persist=0.8, seed=1):
    """Seasonal cycle with AR(1) anomalies in log space -> a skilful persistence outlook."""
    rng = np.random.default_rng(seed)
    season = 4 + 2 * np.sin(np.arange(12) / 12 * 2 * np.pi)
    out, a = [], 0.0
    for y in years:
        for m in range(12):
            a = persist * a + rng.normal(0, 0.5)
            out.append({"month": f"{y}-{m + 1:02d}", "harmonized": float(np.expm1(season[m] + a)), "provisional": False})
    return out


def test_outlook_has_skill_on_persistent_series_and_says_so():
    r = ol.outlook(synthetic_series())
    assert r["status"] == "ok" and r["issued_from"] == "2025-12"
    l1 = r["leads"][0]
    assert l1["month"] == "2026-01" and l1["skill"]["useful"] and l1["skill"]["msss"] > 0.3
    assert 0.5 < l1["beta"] < 1.2
    assert abs(sum(l1["probabilities"].values()) - 1) < 1e-6
    assert l1["range"][0] <= l1["expected"] <= l1["range"][1]
    assert r["leads"][0]["skill"]["msss"] > r["leads"][2]["skill"]["msss"]  # skill fades with lead time


def test_outlook_reports_no_skill_on_white_noise():
    r = ol.outlook(synthetic_series(persist=0.0, seed=3))
    assert r["status"] == "ok"
    assert not r["leads"][0]["skill"]["useful"] and r["headline"].startswith("No skilful outlook")


def test_outlook_needs_history():
    assert ol.outlook(synthetic_series(years=range(2020, 2025)))["status"] == "insufficient_history"


# ---------------------------------------------------------------- smoke


def hms_zip(polys):
    shp, shx, dbf = io.BytesIO(), io.BytesIO(), io.BytesIO()
    w = shapefile.Writer(shp=shp, shx=shx, dbf=dbf, shapeType=shapefile.POLYGON)
    for f in ("Satellite", "Start", "End", "Density"):
        w.field(f, "C", 20)
    for ring, dens in polys:
        w.poly([ring])
        w.record("GOES-WEST", "2026318 1300", "2026318 1600", dens)
    w.close()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for ext, b in (("shp", shp), ("shx", shx), ("dbf", dbf)):
            z.writestr(f"hms_smoke.{ext}", b.getvalue())
    return buf.getvalue()


def test_smoke_coverage_and_heaviest_density(tmp_path):
    light = [(-121, 37), (-121, 39), (-119, 39), (-119, 37), (-121, 37)]      # whole area
    heavy = [(-121, 37), (-121, 38), (-120, 38), (-120, 37), (-121, 37)]      # a quarter
    files = {"20261114": hms_zip([(light, "Light"), (heavy, "Heavy")])}

    def handler(req):
        key = req.url.path[-12:-4]
        return httpx.Response(200, content=files[key]) if key in files else httpx.Response(404)
    sc = sm.SmokeClient(tmp_path, client=httpx.Client(transport=httpx.MockTransport(handler)), today=date(2026, 11, 14))
    r = sm.summarize(sc, shapely.box(-121, 37, -119, 39), None, 3)
    assert r["date"] == "2026-11-14" and r["today"]["worst"] == "Heavy"
    pct = r["today"]["pct_covered"]
    assert pct["Heavy"] == pytest.approx(25, abs=1.5) and pct["Light"] == pytest.approx(75, abs=1.5)
    assert [s["status"] for s in r["series"]] == ["unavailable", "unavailable", "ok"]
    assert r["days_with_smoke"] == 1 and len(r["plumes"]["features"]) == 2


# ---------------------------------------------------------------- burned area


def burned_client(tmp_path):
    ring = [[-120.0, 37.0], [-120.0, 37.1], [-119.9, 37.1], [-119.9, 37.0], [-120.0, 37.0]]  # clockwise

    def handler(req):
        q = {k: v[0] for k, v in parse_qs(req.content.decode()).items()}
        if "outStatistics" in q:
            if "MapServer" in str(req.url):
                return httpx.Response(200, json={"features": [{"attributes": {"year": 2008, "acres": 5000, "n": 2}}]})
            return httpx.Response(200, json={"features": [{"attributes": {"acres": 2500, "n": 1}}]})
        attrs = {"OBJECTID": 7, "poly_IncidentName": "Test Fire", "poly_GISAcres": 2500.0,
                 "attr_FireDiscoveryDateTime": 1597622400000, "attr_ContainmentDateTime": None,
                 "attr_PercentContained": 40, "attr_IncidentTypeCategory": "WF", "attr_FireCause": "Natural"}
        return httpx.Response(200, json={"features": [{"attributes": attrs, "geometry": {"rings": [ring]}}]})
    return bu.BurnedClient(tmp_path, client=httpx.Client(transport=httpx.MockTransport(handler)), today=date(2026, 11, 14))


def test_burned_fires_area_and_yearly(tmp_path):
    bc = burned_client(tmp_path)
    area = shapely.box(-120.05, 36.95, -119.95, 37.15)  # covers half the fire
    fires = bc.fires(area, 2020)
    assert fires[0]["name"] == "Test Fire" and fires[0]["source"] == "wfigs" and fires[0]["start"] == "2020-08-17"
    s = bu.area_summary(fires, area)
    full_km2 = 0.1 * 111.0 * 0.1 * 111.0 * np.cos(np.radians(37.05))
    assert s["km2_in_area"] == pytest.approx(full_km2 / 2, rel=0.03)
    y = {r["year"]: r for r in bc.yearly(area)}
    assert y[2008]["mtbs_acres"] == 5000 and y[2020]["wfigs_fires"] == 1 and "wfigs_acres" not in y[2008]


def test_pre_fire_land_cover_epoch():
    assert bu.pre_fire_nlcd(2020) == 2019 and bu.pre_fire_nlcd(2022) == 2021 and bu.pre_fire_nlcd(2001) == 2001


def test_severity_outside_mosaic_years_is_reported(tmp_path):
    out = burned_client(tmp_path).severity(shapely.box(-120, 37, -119.9, 37.1), 2024)
    assert out["status"] == "not_available"


# ---------------------------------------------------------------- API on real data (skipped without data)

client = TestClient(create_app())


def test_api_outlook_real_data():
    r = client.get("/api/outlook")
    if r.status_code == 503:
        pytest.skip("processed data not built on this machine")
    body = r.json()
    assert body["status"] == "ok" and len(body["leads"]) == 3
    assert all(0 <= ld["probabilities"]["above"] <= 1 for ld in body["leads"])


def test_api_agriculture_filter_real_data():
    r = client.get("/api/calendar?bbox=-91.8,32.5,-90.0,35.5&exclude_agriculture=true")
    if r.status_code == 503:
        pytest.skip("processed data or cropland mask not built on this machine")
    full = client.get("/api/calendar?bbox=-91.8,32.5,-90.0,35.5").json()
    tot = lambda b: sum(y["total"] for y in b["years"])  # noqa: E731
    assert tot(r.json()) < 0.5 * tot(full)  # the Mississippi Delta is mostly crop-residue burning


def test_api_rejects_bad_hazard_params():
    assert client.get("/api/burned/fire?source=nasa&id=1").status_code == 422
    assert client.get("/api/burned?year=1900").status_code == 422
    assert client.get("/api/smoke?date=2099-01-01").status_code == 422
