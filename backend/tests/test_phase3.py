"""Phase 3: hotspots GeoJSON endpoint."""
import json

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

client = TestClient(create_app())
LA = "lat=34.05&lon=-118.25&radius_km=80"


def _need_data():
    if not client.app.state.store.available:
        pytest.skip("processed data not built on this machine")


def test_hotspots_validation():
    for q in ("start=2020-02-01&end=2020-01-01", "start=2020-01-01&end=2020-01-02&sensors=GOES",
              "start=2020-01-01&end=2020-01-02&min_confidence=x", "start=2020-01-01&end=2020-01-02&limit=0",
              "end=2020-01-02"):
        assert client.get(f"/api/hotspots?{q}").status_code in (422, 503), q


def test_hotspots_geojson_and_counts():
    _need_data()
    j = client.get(f"/api/hotspots?{LA}&start=2025-01-01&end=2025-01-31&vegetation_only=false&min_confidence=low").json()
    assert j["type"] == "FeatureCollection" and j["total"] > 0 and not j["sampled"]
    assert j["returned"] == j["total"] == j["counts"]["MODIS"] + j["counts"]["VIIRS"]
    # matches the CSV export over the same filters (export keeps everything)
    r = client.get(f"/api/export/hotspots.csv?{LA}&start=2025-01-01&end=2025-01-31")
    assert int(r.headers["x-row-count"]) == j["total"]
    f = j["features"][0]
    assert f["geometry"]["type"] == "Point" and f["properties"]["sensor"] in ("MODIS", "VIIRS")
    assert all("2025-01-01" <= x["properties"]["date"] <= "2025-01-31" for x in j["features"])


def test_hotspots_sampling_is_capped_and_deterministic():
    _need_data()
    q = "start=2020-09-01&end=2020-09-30&limit=500"
    a, b = client.get(f"/api/hotspots?{q}").json(), client.get(f"/api/hotspots?{q}").json()
    assert a["sampled"] and a["returned"] == 500 and a["total"] > 500
    assert a["features"] == b["features"]


def test_hotspots_filters_reduce_counts():
    _need_data()
    base = f"{LA}&start=2025-01-01&end=2025-01-31"
    everything = client.get(f"/api/hotspots?{base}&vegetation_only=false&min_confidence=low").json()["total"]
    default = client.get(f"/api/hotspots?{base}").json()["total"]
    viirs = client.get(f"/api/hotspots?{base}&sensors=VIIRS").json()
    assert default <= everything
    assert viirs["counts"]["MODIS"] == 0 and viirs["total"] <= default


def test_hotspots_polygon_inside_bbox():
    _need_data()
    poly = {"type": "Polygon", "coordinates": [[[-119, 33.5], [-117.5, 33.5], [-117.5, 34.6], [-119, 34.6], [-119, 33.5]]]}
    p = client.get("/api/hotspots", params={"polygon": json.dumps(poly), "start": "2025-01-01", "end": "2025-01-31"}).json()
    b = client.get("/api/hotspots?bbox=-119,33.5,-117.5,34.6&start=2025-01-01&end=2025-01-31").json()
    assert p["total"] == b["total"] > 0
