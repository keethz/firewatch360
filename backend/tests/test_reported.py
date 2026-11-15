"""Reported fires (NIFC WFIGS), service mocked."""
import json
from datetime import date
from urllib.parse import parse_qs

import httpx
import pytest

from app.services import reported as rep
from app.services.aoi import parse_aoi
from app.services import impact as imp


def handler(calls):
    def h(req: httpx.Request):
        body = {k: v[0] for k, v in parse_qs(req.content.decode()).items()}
        calls.append(body)
        assert body["where"].startswith("FireDiscoveryDateTime >= TIMESTAMP '2020-")
        if body.get("groupByFieldsForStatistics") == "IncidentTypeCategory":
            return httpx.Response(200, json={"features": [
                {"attributes": {"IncidentTypeCategory": "WF", "n": 12, "acres": 5000.4}},
                {"attributes": {"IncidentTypeCategory": "RX", "n": 3, "acres": 40}}]})
        if body.get("groupByFieldsForStatistics") == "FireCause":
            return httpx.Response(200, json={"features": [
                {"attributes": {"FireCause": "Human", "n": 7}}, {"attributes": {"FireCause": "Undetermined", "n": 3}},
                {"attributes": {"FireCause": "Unknown", "n": 1}}, {"attributes": {"FireCause": None, "n": 1}}]})
        if "outStatistics" in body:  # monthly
            return httpx.Response(200, json={"features": [{"attributes": {"n": 4, "acres": 10}}]})
        return httpx.Response(200, json={"features": [
            {"attributes": {"IncidentName": "CREEK", "FireDiscoveryDateTime": 1599269580000, "IncidentSize": 379895,
                            "FireCause": "Unknown", "POOState": "US-CA", "POOCounty": "Fresno"},
             "geometry": {"x": -119.27, "y": 37.2}}]})
    return h


def test_summary_and_cache(tmp_path):
    calls = []
    c = rep.ReportedClient(tmp_path, client=httpx.Client(transport=httpx.MockTransport(handler(calls))), today=date(2026, 11, 15))
    geom = imp.make_geo(parse_aoi(lat=37.2, lon=-119.3, radius_km=30)).geom
    s = c.summary(geom, "k", date(2020, 9, 1), date(2020, 9, 30))
    assert s["wildfires"] == 12 and s["prescribed"] == 3 and s["wildfire_acres"] == 5000
    assert s["causes"] == {"Human": 7, "Unknown": 4, "Not reported": 1}
    assert s["largest"][0] == {"name": "Creek", "date": "2020-09-05", "acres": 379895, "cause": "Unknown", "state": "CA", "county": "Fresno"}
    assert calls[0]["geometryType"] == "esriGeometryPolygon" and calls[0]["inSR"] == "4326"
    n = len(calls)
    c.summary(geom, "k", date(2020, 9, 1), date(2020, 9, 30))
    assert len(calls) == n  # old period cached
    json.dumps(s)


def test_coverage_and_monthly(tmp_path):
    calls = []
    c = rep.ReportedClient(tmp_path, client=httpx.Client(transport=httpx.MockTransport(handler(calls))), today=date(2026, 11, 15))
    assert c.summary(None, "r", date(2005, 1, 1), date(2005, 12, 31))["status"] == "outside_coverage"
    rows = c.monthly(None, "r", date(2020, 8, 1), date(2020, 10, 1))
    assert [r["month"] for r in rows] == ["2020-08", "2020-09", "2020-10"] and rows[1]["wildfires"] == 4
    assert all("geometry" not in b for b in calls)  # whole region: no spatial filter


def test_service_error(tmp_path):
    c = rep.ReportedClient(tmp_path, client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"error": {"message": "bad"}}))))
    with pytest.raises(rep.ReportedError):
        c.summary(None, "x", date(2020, 9, 1), date(2020, 9, 30))
