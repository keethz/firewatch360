"""Anomaly map and AI explanation tests."""
import json

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import create_app
from app.services import anomaly_map as amap
from app.services.explain import Explainer

HAS_DATA = any(get_settings().data_dir.joinpath("processed", "hotspots").glob("**/*.parquet"))


def test_robust_scale_floor():
    med, s = amap.robust_scale(np.array([0, 0, 0, 0, 0, 1.0]))
    assert med == 0 and s == 1.0  # MAD = 0 -> floor of sqrt(max(median, 1))
    med, s = amap.robust_scale(np.array([100, 110, 90, 105, 95.0]))
    assert med == 100 and s == pytest.approx(max(1.4826 * 5, 10))


def test_harmonized_value_per_era():
    counts = {("MODIS_TERRA", 2001, 0, 0): 3, ("MODIS", 2005, 0, 0): 4, ("VIIRS", 2015, 0, 0): 7}
    kw = dict(k=2.0, c=1.5, switch_month="2012-02", aqua_month="2002-08")
    assert amap.harmonized_value(counts, (0, 0), 2001, 9, **kw) == 2.0 * 1.5 * 3
    assert amap.harmonized_value(counts, (0, 0), 2005, 9, **kw) == 8
    assert amap.harmonized_value(counts, (0, 0), 2015, 9, **kw) == 7
    assert amap.harmonized_value(counts, (0, 0), 2016, 9, **kw) == 0


def ex(tmp_path, handler):
    return Explainer("test-key", "gemini-3.8-flash", tmp_path, client=httpx.Client(transport=httpx.MockTransport(handler)),
                     fallback_model="gemini-3.5-flash-lite")


def reply(text, finish="STOP"):
    return httpx.Response(200, json={"candidates": [{"finishReason": finish, "content": {"parts": [{"text": text}]}}]})


def test_explainer_ok_and_cached(tmp_path):
    calls = []

    def handler(req: httpx.Request):
        body = json.loads(req.content)
        calls.append(body)
        assert req.url.path.endswith("/models/gemini-3.8-flash:generateContent")
        assert req.headers["x-goog-api-key"] == "test-key" and "key=" not in str(req.url)  # key in header only
        assert "Facts:" in body["contents"][0]["parts"][0]["text"] and body["systemInstruction"]["parts"][0]["text"]
        return reply("Activity was far above the baseline.")

    e = ex(tmp_path, handler)
    a = e.explain({"value": 50, "median": 5})
    assert a["status"] == "ok" and a["text"].startswith("Activity") and a["cached"] is False
    assert e.explain({"median": 5, "value": 50})["cached"] is True and len(calls) == 1


def test_explainer_off_and_errors(tmp_path):
    assert Explainer("", "gemini-3.8-flash", tmp_path).explain({"a": 1})["status"] == "not_configured"
    def down(r):
        raise httpx.ConnectError("refused")
    assert ex(tmp_path, down).explain({"a": 1})["status"] == "unavailable"
    bad = ex(tmp_path, lambda r: httpx.Response(400, json={"error": {"message": "API key not valid."}}))
    assert "rejected" in bad.explain({"a": 2})["message"]
    assert "quota" in ex(tmp_path, lambda r: httpx.Response(429)).explain({"a": 3})["message"]
    # busy main model -> fallback model answers; thought parts are dropped
    busy = lambda r: httpx.Response(503) if "3.8" in r.url.path else httpx.Response(200, json={"candidates": [
        {"finishReason": "STOP", "content": {"parts": [{"text": "plan", "thought": True}, {"text": "Lite answer."}]}}]})
    a = ex(tmp_path, busy).explain({"a": 5})
    assert a["text"] == "Lite answer." and a["model"] == "gemini-3.5-flash-lite"
    assert ex(tmp_path, lambda r: reply("", "SAFETY")).explain({"a": 4})["status"] == "unavailable"


@pytest.mark.skipif(not HAS_DATA, reason="processed data not built")
def test_anomaly_map_api_real_data(tmp_path):
    app = create_app()
    app.state.explainer = Explainer("", "gemini-3.8-flash", tmp_path)  # never call the real API in tests
    c = TestClient(app)
    d = c.get("/api/anomaly-map?month=2020-09&lat=37.5&lon=-120&radius_km=300").json()
    assert d["n_anomalous"] > 0 and d["top"][0]["anomaly"]
    top = d["top"][0]
    r = c.get(f"/api/explain/cell?month=2020-09&lat=37.5&lon=-120&radius_km=300&cy={top['cell'][0]}&cx={top['cell'][1]}").json()
    assert r["facts"]["value_cell_days"] == top["value"] and r["status"] in ("ok", "not_configured", "unavailable")
    assert c.get("/api/anomaly-map?month=1999-01").status_code == 422
    assert c.get("/api/explain/cell?month=2020-09&cy=0&cx=0").status_code == 404
