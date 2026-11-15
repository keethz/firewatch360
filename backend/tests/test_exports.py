"""CSV export checks, including the polygon-after-export regression."""
import json

import duckdb
import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import create_app
from app.services.aoi import parse_aoi
from app.services.store import HotspotStore

HAS_DATA = any(get_settings().data_dir.joinpath("processed", "hotspots").glob("**/*.parquet"))
POLY = json.dumps({"type": "Polygon", "coordinates": [[[-121, 38], [-119, 38], [-119, 40], [-121, 40], [-121, 38]]]})


def test_store_polygon_after_copy(tmp_path):
    """A COPY export must not leave state that breaks the next polygon query (regression)."""
    con = duckdb.connect()
    con.execute("CREATE TABLE hotspots AS SELECT * FROM (VALUES (39.0, -120.0), (10.0, 10.0)) t(latitude, longitude)")
    s = HotspotStore(None, con=con)
    poly = parse_aoi(polygon=POLY)
    for _ in range(3):
        assert s.copy_csv("SELECT * FROM hotspots", [], str(tmp_path / "x.csv")) == 2
        assert s.query("SELECT count(*) AS n FROM hotspots WHERE in_aoi(latitude, longitude)", aoi=poly)[0]["n"] == 1
        assert s.copy_csv("SELECT * FROM hotspots WHERE in_aoi(latitude, longitude)", [], str(tmp_path / "y.csv"), aoi=poly) == 1


def rows(text: str) -> list[str]:
    return [line for line in text.splitlines() if line and not line.startswith("#")]


@pytest.mark.skipif(not HAS_DATA, reason="processed data not built")
@pytest.mark.parametrize("aoi", [{}, {"bbox": "-124.5,32.5,-114.1,42"}, {"lat": 37.2, "lon": -119.3, "radius_km": 50}, {"polygon": POLY}])
def test_csv_downloads_have_data(aoi):
    c = TestClient(create_app())
    for _ in range(2):  # twice: the second round runs after an export
        h = c.get("/api/export/hotspots.csv", params={**aoi, "start": "2025-08-01", "end": "2025-08-31"})
        assert h.status_code == 200 and len(rows(h.text)) > 1 and rows(h.text)[0].startswith("sensor,")
        assert int(h.headers["x-row-count"]) == len(rows(h.text)) - 1
        cal = c.get("/api/export/calendar.csv", params=aoi)
        assert cal.status_code == 200 and rows(cal.text)[0].startswith("year,month,harmonized_cell_days")
        assert len(rows(cal.text)) > 300


@pytest.mark.skipif(not HAS_DATA, reason="processed data not built")
def test_csv_empty_and_too_large_explain_themselves():
    c = TestClient(create_app())
    e = c.get("/api/export/hotspots.csv", params={"start": "2005-08-01", "end": "2005-08-31", "sensors": "VIIRS"})
    assert e.status_code == 200 and "VIIRS data start" in e.text and rows(e.text)[0].startswith("sensor,")
    big = c.get("/api/export/hotspots.csv", params={"start": "2015-01-01", "end": "2022-12-31"})
    assert big.status_code == 413 and big.text.startswith("# ") and "export limit" in big.text
    nrt = c.get("/api/export/hotspots.csv", params={"bbox": "-124.5,32.5,-114.1,42", "start": "2026-11-01", "end": "2026-11-15"})
    times = [r.split(",")[5] for r in rows(nrt.text)[1:]]
    assert times and all(len(t) == 4 for t in times)  # zero-padded HHMM
