import pytest
from fastapi.testclient import TestClient

from app.main import create_app

client = TestClient(create_app())


def test_health():
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_coverage_matches_data():
    r = client.get("/api/coverage")
    if r.status_code == 503:
        pytest.skip("processed data not built on this machine")
    body = r.json()
    assert set(body["sensors"]) == {"MODIS", "VIIRS"}
    assert body["sensors"]["MODIS"]["first_date"].startswith("2000")
    assert body["sensors"]["VIIRS"]["first_date"].startswith("2012")
    total = sum(y["rows"] for y in body["per_year"])
    assert total == body["sensors"]["MODIS"]["rows"] + body["sensors"]["VIIRS"]["rows"]


def test_parquet_counts_match_coverage():
    store = client.app.state.store
    if not store.available:
        pytest.skip("processed data not built on this machine")
    rows = store.query("SELECT sensor, count(*) AS n FROM hotspots GROUP BY sensor")
    cov = store.coverage()["sensors"]
    for r in rows:
        assert r["n"] == cov[r["sensor"]]["rows"]
