"""Drought / fire-weather context and saved-area alerts. Network mocked."""
import json
from datetime import date

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api import routes
from app.main import create_app
from app.services import alerts as al
from app.services import context as ctx
from app.services import impact as imp
from app.services.aoi import parse_aoi


def test_usdm_week_for_month():
    today = date(2026, 11, 14)
    assert ctx.usdm_week_for_month("2020-09", today) == date(2020, 9, 29)  # last Tuesday
    assert ctx.usdm_week_for_month("2026-11", today) == date(2026, 11, 10)  # latest published
    assert ctx.usdm_week_for_month("1999-05", today) is None
    assert ctx.latest_usdm_week(today) == date(2026, 11, 10)


def square(w, s, e, n):
    return {"type": "Polygon", "coordinates": [[[w, s], [e, s], [e, n], [w, n], [w, s]]]}


def usdm_mock(req):
    # Left half of a 2x2 deg box is D2, a quarter is D0 (non-overlapping, like USDM)
    return httpx.Response(200, json={"features": [
        {"properties": {"DM": 2}, "geometry": square(-121, 38, -120, 40)},
        {"properties": {"DM": 0}, "geometry": square(-120, 38, -119, 39)}]})


def test_drought_shares(tmp_path):
    c = ctx.ContextClient(tmp_path, client=httpx.Client(transport=httpx.MockTransport(usdm_mock)), today=date(2026, 11, 14))
    g = imp.make_geo(parse_aoi(bbox="-121,38,-119,40"))
    d = c.drought(g.geom, g.fwd, date(2020, 9, 29))
    assert d["share"]["D2"] == pytest.approx(0.5, abs=0.02) and d["share"]["D0"] == pytest.approx(0.25, abs=0.02)
    assert d["cumulative"]["D1+"] == pytest.approx(0.5, abs=0.02) and d["worst_category"] == "D2"
    assert d["dsci"] == pytest.approx(100 * (0.75 + 0.5 + 0.5), abs=4)
    assert "50% of the area in drought" in d["text"]
    c2 = ctx.ContextClient(tmp_path, client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500))))
    assert c2.drought(g.geom, g.fwd, date(2020, 9, 29))["dsci"] == d["dsci"]  # served from disk cache


def test_fire_weather_with_zone_lookup(tmp_path):
    def handler(req):
        if req.url.path == "/alerts/active":
            if req.url.params["event"] != "Red Flag Warning":
                return httpx.Response(200, json={"features": []})
            return httpx.Response(200, json={"features": [
                {"geometry": None, "properties": {"event": "Red Flag Warning", "headline": "RFW", "areaDesc": "Zone 1",
                                                  "onset": "2026-11-14T10:00", "ends": None, "expires": "x", "senderName": "NWS",
                                                  "affectedZones": ["https://api.weather.gov/zones/fire/CAZ001"]}},
                {"geometry": square(-80, 30, -79, 31), "properties": {"event": "Red Flag Warning", "headline": "far",
                                                                      "areaDesc": "FL", "onset": "o", "senderName": "NWS"}}]})
        assert req.headers["accept"] == "application/geo+json"
        return httpx.Response(200, json={"geometry": square(-121, 38, -119, 40)})
    c = ctx.ContextClient(tmp_path, client=httpx.Client(transport=httpx.MockTransport(handler)))
    fw = c.fire_weather(imp.make_geo(parse_aoi(lat=39, lon=-120, radius_km=20)).geom)
    assert fw["n_red_flag"] == 1 and fw["active"][0]["area"] == "Zone 1"


def test_alert_store_dedup_and_parsing(tmp_path):
    s = al.AlertStore(tmp_path)
    a = s.add_area({"name": "A", "aoi": "bbox=-121,38,-119,40"})
    e = {"area_id": a["id"], "key": "nrt:2026-11-14", "title": "t"}
    assert len(s.add_events([e])) == 1 and s.add_events([e]) == []
    assert al.aoi_kwargs("lat=1&lon=2&radius_km=3") == {"lat": 1.0, "lon": 2.0, "radius_km": 3.0}
    assert al.valid_webhook("https://hooks.example.com/x") and not al.valid_webhook("http://example.com")
    assert s.delete_area(a["id"]) and not s.delete_area(a["id"])


def test_webhook_delivery():
    got = []
    client = httpx.Client(transport=httpx.MockTransport(lambda r: (got.append(json.loads(r.content)), httpx.Response(200))[1]))
    out = al.deliver({"title": "T", "detail": "D"}, {"name": "Ranch", "webhook_url": "https://h.test/x", "email": "a@b.c"},
                     {"host": ""}, client)
    assert out["webhook"] == "sent" and out["email"] == "not configured on the server"
    assert got[0]["text"].startswith("[FIREWATCH 360] Ranch: T")


def test_alert_api(tmp_path, monkeypatch):
    app = create_app()
    app.state.alerts = al.AlertStore(tmp_path)
    monkeypatch.setattr(routes, "evaluate_area", lambda app, a: [{
        "id": "e1", "area_id": a["id"], "area_name": a["name"], "rule": "nrt", "key": "nrt:2026-11-14",
        "title": "3 new satellite fire detections", "detail": "d", "severity": "high", "created_utc": "now"}])
    c = TestClient(app)
    assert c.post("/api/alerts/areas", json={"name": "US", "aoi": "x=1"}).status_code == 422  # whole region
    assert c.post("/api/alerts/areas", json={"name": "A", "aoi": "bbox=-121,38,-119,40", "webhook_url": "http://x.com"}).status_code == 422
    a = c.post("/api/alerts/areas", json={"name": "A", "aoi": "bbox=-121,38,-119,40"}).json()
    r = c.post(f"/api/alerts/check?area_id={a['id']}").json()
    assert r["checked"] == 1 and r["new_events"][0]["delivery"] == {"in_app": True}
    assert c.post("/api/alerts/check").json()["new_events"] == []  # sent once
    assert len(c.get("/api/alerts/events").json()["events"]) == 1
    assert c.get("/api/alerts/areas").json()["areas"][0]["last_checked_utc"]
    assert c.delete(f"/api/alerts/areas/{a['id']}").status_code == 200
