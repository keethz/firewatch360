"""Human-impact layer tests. Synthetic layers and mocked services only (no network)."""
import io
import json
from urllib.parse import parse_qs

import httpx
import numpy as np
import pandas as pd
import pytest
import shapely
import tifffile
from fastapi.testclient import TestClient
from pyproj import Transformer

from app.main import create_app
from app.services import impact as imp
from app.services.aoi import AOI, parse_aoi

LAT, LON = 39.0, -120.0  # inside CONUS


def km_east(km: float) -> float:
    """Longitude `km` east of LON at LAT."""
    return LON + km / (111.32 * np.cos(np.radians(LAT)))


# ---------------------------------------------------------------- geometry helpers

def test_esri_polygon_holes_and_validity():
    outer = [[0, 0], [0, 10], [10, 10], [10, 0], [0, 0]]  # clockwise = outer ring in Esri JSON
    hole = [[2, 2], [4, 2], [4, 4], [2, 4], [2, 2]]       # counter-clockwise = hole
    g = imp.esri_polygon([outer, hole])
    assert g.area == pytest.approx(100 - 4)
    bowtie = [[0, 0], [0, 2], [2, 0], [2, 2], [0, 0]]
    assert imp.esri_polygon([bowtie]).is_valid
    assert imp.esri_polygon([hole]) is None  # holes alone are not an area


def test_polygonal_drops_lines():
    gc = shapely.GeometryCollection([shapely.box(0, 0, 1, 1), shapely.LineString([(2, 2), (3, 3)])])
    assert imp.polygonal(gc).geom_type == "Polygon"


def test_circle_geometry_area():
    geo = imp.make_geo(parse_aoi(lat=LAT, lon=LON, radius_km=10))
    assert geo.area_km2 == pytest.approx(np.pi * 100, rel=0.01)


def test_fire_distances_and_zone():
    geo = imp.make_geo(parse_aoi(lat=LAT, lon=LON, radius_km=50))
    flon, flat = np.array([LON]), np.array([LAT])
    fx, fy = imp.to_m(geo, flon, flat)
    px, py = imp.to_m(geo, np.array([km_east(3), km_east(8)]), np.array([LAT, LAT]))
    d = imp.nearest_fire_km(geo, px, py, np.stack([fx, fy], axis=1))
    assert d == pytest.approx([3, 8], abs=0.05)
    zone = imp.fire_zone(geo, flon, flat, buffer_km=5)
    assert zone.area / 1e6 == pytest.approx(np.pi * 25, rel=0.03)
    assert imp.nearest_fire_km(geo, px, py, None).tolist() == [np.inf, np.inf]


def test_esri_geometry_envelope_vs_polygon():
    t, g = imp.esri_geometry(shapely.box(-1, -1, 1, 1))
    assert t == "esriGeometryEnvelope" and json.loads(g)["xmin"] == -1
    t, g = imp.esri_geometry(shapely.Point(0, 0).buffer(1))
    assert t == "esriGeometryPolygon" and len(json.loads(g)["rings"]) == 1


# ---------------------------------------------------------------- static layers

@pytest.fixture
def static(tmp_path):
    pts = lambda rows: pd.DataFrame(rows)
    pts([{"name": "Town", "state": "CA", "pop_max": 5000, "latitude": LAT, "longitude": km_east(2)},
         {"name": "Far City", "state": "CA", "pop_max": 90000, "latitude": LAT, "longitude": km_east(30)}]
        ).to_parquet(tmp_path / "places.parquet")
    pts([{"name": "Strip", "size": "small", "iata": "", "municipality": "Town", "latitude": LAT, "longitude": km_east(4)}]
        ).to_parquet(tmp_path / "airports.parquet")
    pts([{"name": "General", "city": "Town", "state": "CA", "type": "GENERAL ACUTE CARE", "beds": 50.0, "trauma": "",
          "latitude": LAT, "longitude": km_east(1)},
         {"name": "Clinic", "city": "Far", "state": "CA", "type": "CHILDREN", "beds": np.nan, "trauma": "",
          "latitude": LAT, "longitude": km_east(20)}]).to_parquet(tmp_path / "hospitals.parquet")
    pts([{"name": f"School {i}", "city": "Town", "state": "CA", "locale": "41", "latitude": LAT, "longitude": km_east(i)}
         for i in (1, 6, 12)]).to_parquet(tmp_path / "schools.parquet")
    pts([{"name": f"Station {i}", "city": "Town", "state": "CA", "latitude": LAT, "longitude": km_east(i)}
         for i in (2, 9)]).to_parquet(tmp_path / "firestations.parquet")
    road = shapely.LineString([(LON, LAT - 0.5), (LON, LAT + 0.5)])  # ~111 km north-south through the fire
    pd.DataFrame([{"type": "Major Highway", "name": "Hwy 1", "wkb": shapely.to_wkb(road)}]).to_parquet(tmp_path / "roads.parquet")
    (tmp_path / "manifest.json").write_text("{}")
    return imp.StaticLayers(tmp_path)


def no_live(tmp_path):
    return imp.LiveCache(tmp_path / "cache", client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500))))


def test_static_layers_inside_and_near_fire(static, tmp_path):
    aoi = parse_aoi(lat=LAT, lon=LON, radius_km=25)
    out = imp.summarize(static, no_live(tmp_path), aoi, np.array([LON]), np.array([LAT]), buffer_km=5, include_live=False)
    L = out["layers"]
    assert L["places"]["in_area"] == 1 and L["places"]["near_fire"] == 1  # Far City is 30 km out
    assert L["places"]["population_estimate_near_fire"] == 5000
    assert L["hospitals"]["in_area"] == 2 and L["hospitals"]["near_fire"] == 1 and L["hospitals"]["beds_near_fire"] == 50
    assert L["hospitals"]["items"][0]["name"] == "General" and L["hospitals"]["items"][0]["distance_to_fire_km"] == pytest.approx(1, abs=0.05)
    assert L["hospitals"]["items"][1]["beds"] is None  # unknown beds stay unknown
    assert L["schools"]["in_area"] == 3 and L["schools"]["near_fire"] == 1
    assert L["firestations"]["in_area"] == 2 and L["firestations"]["near_fire"] == 1
    assert L["firestations"]["items"][0]["name"] == "Station 2"
    assert L["airports"]["near_fire_by_size"]["small"] == 1
    assert L["roads"]["in_area_km"] == pytest.approx(50, rel=0.02)  # road clipped to the 25 km circle
    assert L["roads"]["near_fire_km"] == pytest.approx(10, rel=0.05)  # 5 km either side of the fire
    assert out["fire"]["detections"] == 1
    json.dumps(out)  # fully JSON-serializable


def test_no_fire_means_nothing_near(static, tmp_path):
    out = imp.summarize(static, no_live(tmp_path), parse_aoi(lat=LAT, lon=LON, radius_km=25),
                        np.array([]), np.array([]), buffer_km=5, include_live=False)
    assert out["layers"]["schools"]["near_fire"] == 0 and out["layers"]["roads"]["near_fire_km"] == 0
    assert out["fire"]["zone_geojson"] is None


def test_region_requires_aoi(static, tmp_path):
    assert imp.summarize(static, no_live(tmp_path), AOI("region"), np.array([]), np.array([]), 5)["status"] == "aoi_required"


# ---------------------------------------------------------------- live services (mocked)

def tiff_bytes(arr: np.ndarray) -> bytes:
    buf = io.BytesIO()
    tifffile.imwrite(buf, arr)
    return buf.getvalue()


def live_mock(calls):
    def handler(req: httpx.Request):
        calls.append(req.url.host)
        if "fs.usda.gov" in req.url.host:
            body = parse_qs(req.content.decode())
            assert body["groupByFieldsForStatistics"] == ["wuiclass2020"]
            return httpx.Response(200, json={"features": [
                {"attributes": {"wuiclass2020": "Med_Dens_Interface", "pop": 1000, "hu": 400, "blocks": 10}},
                {"attributes": {"wuiclass2020": "Low_Dens_Intermix", "pop": 200, "hu": 90, "blocks": 5}},
                {"attributes": {"wuiclass2020": "High_Dens_NoVeg", "pop": 3000, "hu": 1200, "blocks": 20}}]})
        if "arcgis.com" in req.url.host:  # PAD-US: one forest square straddling the area edge
            w, s, e, n = LON - 0.1, LAT - 0.1, LON + 0.1, LAT + 0.1
            ring = [[w, s], [w, n], [e, n], [e, s], [w, s]]
            return httpx.Response(200, json={"features": [{"attributes": {
                "Unit_Nm": "Test NF", "Mang_Type": "FED", "Mang_Name": "USFS", "Des_Tp": "NF", "GAP_Sts": "3"},
                "geometry": {"rings": [ring]}}]})
        if "mrlc.gov" in req.url.host:  # half crops (82), half forest (42)
            arr = np.full((100, 100), 42, np.uint8)
            arr[:, :50] = 82
            return httpx.Response(200, content=tiff_bytes(arr), headers={"content-type": "image/geotiff"})
        return httpx.Response(404)
    return httpx.MockTransport(handler)


def test_live_layers_with_mocks(static, tmp_path):
    calls: list[str] = []
    live = imp.LiveCache(tmp_path / "cache", client=httpx.Client(transport=live_mock(calls)))
    aoi = parse_aoi(bbox=f"{LON - 0.2},{LAT - 0.2},{LON + 0.2},{LAT + 0.2}")
    out = imp.summarize(static, live, aoi, np.array([LON]), np.array([LAT]), buffer_km=5)
    L = out["layers"]
    assert L["population"]["in_area"] == {"population": 4200, "housing_units": 1690}
    assert L["wui"]["in_area"]["population"] == 1200 and L["wui"]["in_area"]["intermix"]["population"] == 200
    assert L["wui"]["in_area"]["share_of_population"] == pytest.approx(1200 / 4200)
    area = out["area_km2"]
    assert L["protected"]["in_area_km2"] == pytest.approx(area / 4, rel=0.02)  # 0.2° square inside a 0.4° box
    assert L["protected"]["near_fire_km2"] == pytest.approx(np.pi * 25, rel=0.05)  # fire zone lies inside the forest
    assert L["agriculture"]["share_of_area"] == pytest.approx(0.5, abs=0.02)
    assert L["agriculture"]["crops_km2"] > 0 and L["agriculture"]["pasture_hay_km2"] == 0
    n = len(calls)
    imp.summarize(static, live, aoi, np.array([LON]), np.array([LAT]), buffer_km=5)
    assert len(calls) == n  # second run served from the disk cache


def test_live_failures_and_coverage_are_reported(static, tmp_path):
    out = imp.summarize(static, no_live(tmp_path), parse_aoi(lat=LAT, lon=LON, radius_km=20),
                        np.array([LON]), np.array([LAT]), 5)
    for k in ("population", "wui", "protected", "agriculture"):
        assert out["layers"][k]["status"] == "unavailable" and "message" in out["layers"][k]
    alaska = imp.summarize(static, no_live(tmp_path), parse_aoi(lat=64.8, lon=-147.7, radius_km=20),
                           np.array([]), np.array([]), 5)
    assert alaska["layers"]["wui"]["status"] == "outside_coverage"
    assert alaska["layers"]["agriculture"]["status"] == "outside_coverage"
    big = imp.summarize(static, no_live(tmp_path), parse_aoi(bbox="-110,30,-95,45"), np.array([]), np.array([]), 5)
    assert big["layers"]["protected"]["status"] == "too_large"


# ---------------------------------------------------------------- API

def test_api_impact_validation_and_region():
    c = TestClient(create_app())
    assert c.get("/api/impact?start=2020-01-02&end=2020-01-01&bbox=-120,38,-119,39").status_code in (422, 503)
    assert c.get("/api/impact?start=2020-01-01&end=2020-01-02&buffer_km=0&bbox=-120,38,-119,39").status_code in (422, 503)
    r = c.get("/api/impact?start=2020-01-01&end=2020-01-02")
    if r.status_code != 503:
        assert r.json()["status"] == "aoi_required"
    assert c.get("/api/impact/features?layer=bridges&bbox=-120,38,-119,39").status_code in (422, 503)
