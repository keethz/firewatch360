"""Investigation agent: evidence verification, rule-based planner, Gemini tool loop (mocked), API stream."""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.services import agent as ag


class FakeTools(ag.Tools):
    """Tools with canned results (no data or network needed)."""

    def __init__(self, region=False):
        self.aoi = type("A", (), {"kind": "region" if region else "bbox"})()
        self.month = "2020-09"
        self.calls = []

    def ref_month(self):
        return "2020-09"

    def _brief_for(self, month):
        return {"reference": {"kind": "month"}}

    def run(self, name, args):
        self.calls.append((name, {k: v for k, v in args.items() if k != "why"}))
        data = {
            "get_fire_activity": {"month": "September 2020", "value_cell_days": 32448.0, "median_cell_days": 1987.0,
                                  "pct_vs_median": 1533.0, "robust_z": 25.7, "rank": "Highest September since 2001",
                                  "confidence": "High", "anomaly_level": "Severe"},
            "compare_sensors": {"agreement": "MODIS and VIIRS agree", "calibration": {"k": 1.775}},
            "find_anomalous_cells": {"anomalous_high_cells": 70, "top": [{"bbox": [-119.5, 37.25, -119.25, 37.5], "value": 2448.0,
                                                                          "record_max_before": 7.1}]},
            "get_reported_incidents": {"wildfires": 1045, "wildfire_acres": 981333,
                                       "largest": [{"name": "Creek", "acres": 379895}]},
            "get_drought_and_fire_weather": {"drought_in_month": {"text": "59% of the area in drought", "dsci": 187}},
            "assess_community_assets": {"buffer_km": 5, "population_near_fire": 163, "schools_near_fire": 0,
                                        "hospitals_near_fire": 0, "fire_stations_near_fire": 2},
        }.get(name)
        if data is None:
            return "error", {"error": "boom"}, False
        return f"{name} ok", data, True


def drain(gen):
    return list(gen)


def test_verify_checks_numbers_and_citations():
    inv = ag.Investigation("CA", "2020-09")
    inv.add("get_fire_activity", {}, "s", {"value_cell_days": 32448.0, "pct_vs_median": 1533.01, "share": 0.59}, True, 0)
    out = ag.verify([
        {"statement": "Activity reached 32,448 cell-days, +1,533% vs median, with 59% in drought.", "evidence": ["E1"]},
        {"statement": "Activity reached 40,000 cell-days.", "evidence": ["E1"]},
        {"statement": "Activity was 32.4k cell-days.", "evidence": ["E1", "E9"]},
        {"statement": "No numbers but no evidence either.", "evidence": []},
    ], inv)
    assert out[0]["verified"] and out[0]["numbers_checked"] == 3
    assert not out[1]["verified"] and out[1]["numbers_unmatched"] == ["40,000"]
    assert out[2]["verified"] and out[2]["invalid_citations"] == ["E9"]
    assert not out[3]["verified"]


def test_verify_reads_iso_dates_as_day_month_year():
    inv = ag.Investigation("CA", "2020-09")
    inv.add("get_smoke", {"date": "2020-09-15"}, "s", {"date": "2020-09-15", "pct_any": 100.0}, True, 0)
    out = ag.verify([{"statement": "On September 15, 2020, 100% of the area was under smoke.", "evidence": ["E1"]}], inv)
    assert out[0]["verified"], out[0]["numbers_unmatched"]
    assert ag._numbers_in("change of -12.5% on 2020-09-15") == [-12.5, 2020, 9, 15]


def test_area_name_numbers_are_not_claims():
    inv = ag.Investigation("the 0.25° cell at 37.63°N, 119.63°W", "2026-10")
    inv.add("get_fire_activity", {}, "s", {"value_cell_days": 104.0}, True, 0)
    assert ag.verify_summary("The cell at 37.63°N, 119.63°W had 104 cell-days.", inv)["verified"]


def test_rule_based_investigation_cites_everything():
    tools, inv = FakeTools(), ag.Investigation("CA", "2020-09")
    events = drain(ag.investigate(tools, inv, api_key="", models=[]))
    done = events[-1]
    assert done["type"] == "done" and done["mode"] == "rule-based"
    assert [c[0] for c in tools.calls] == ["get_fire_activity", "compare_sensors", "find_anomalous_cells",
                                           "get_reported_incidents", "get_drought_and_fire_weather", "assess_community_assets"]
    assert done["report"]["assessment"] == "Exceptional"
    assert done["verification"]["verified"] == done["verification"]["findings"] >= 6
    assert done["report"]["summary"]["verified"] and "32,448" in done["report"]["summary"]["text"]


def test_rule_based_zooms_into_top_cell_for_whole_us():
    tools = FakeTools(region=True)
    drain(ag.investigate(tools, ag.Investigation("US", None), api_key="", models=[]))
    assert tools.calls[-1] == ("assess_community_assets", {"start": "2020-09-01", "end": "2020-09-30", "buffer_km": 5,
                                                           "bbox": [-119.5, 37.25, -119.25, 37.5]})


def gemini(script):
    """Mock Gemini: returns the scripted function calls in order, recording each request body."""
    seen = []

    def handler(req):
        body = json.loads(req.content)
        seen.append(body)
        step = script[len(seen) - 1]
        if isinstance(step, int):
            return httpx.Response(step, json={"error": {}})
        parts = [{"functionCall": {"name": n, "args": a}} for n, a in step]
        return httpx.Response(200, json={"candidates": [{"content": {"role": "model", "parts": parts}}]})
    return httpx.Client(transport=httpx.MockTransport(handler)), seen


def test_agent_loop_runs_tools_and_verifies_report():
    report = {"headline": "Exceptional.", "assessment": "Exceptional", "confidence": "High", "gaps": [], "next_steps": [],
              "summary": "Activity reached 32,448 cell-days, with about 50,000 acres burned.",
              "findings": [{"statement": "32,448 cell-days, highest September since 2001.", "kind": "observation", "evidence": ["E1"]},
                           {"statement": "The Creek fire burned 400,000 acres.", "kind": "driver", "evidence": ["E2"]}]}
    client, seen = gemini([
        503,  # main model busy -> fallback
        [("get_fire_activity", {"why": "start"})],
        [("get_reported_incidents", {"why": "causes", "start": "2020-09-01", "end": "2020-09-30"}),
         ("get_fire_activity", {"why": "again"})],  # duplicate -> skipped
        [("submit_report", report)],
    ])
    tools, inv = FakeTools(), ag.Investigation("CA", "2020-09")
    events = drain(ag.investigate(tools, inv, "key", ["main", "fallback"], client))
    done = events[-1]
    assert done["mode"] == "agent" and done["model"] == "fallback"
    assert any(e["type"] == "note" for e in events)
    assert [e["evidence"]["tool"] for e in events if e["type"] == "evidence"] == ["get_fire_activity", "get_reported_incidents", "get_fire_activity"]
    assert len(tools.calls) == 2  # the duplicate never ran
    f = done["report"]["findings"]
    assert f[0]["verified"] and not f[1]["verified"] and f[1]["numbers_unmatched"] == ["400,000"]
    sm = done["report"]["summary"]
    assert not sm["verified"] and sm["numbers_unmatched"] == ["50,000"]
    # tool results go back to the model with their evidence IDs
    sent = seen[-1]["contents"][-1]["parts"]
    assert sent[0]["functionResponse"]["response"]["evidence_id"] == "E2"


def test_agent_failure_falls_back_to_rules_and_keeps_evidence():
    client, _ = gemini([[("get_fire_activity", {"why": "start"})], 400])
    tools, inv = FakeTools(), ag.Investigation("CA", "2020-09")
    events = drain(ag.investigate(tools, inv, "key", ["main"], client))
    done = events[-1]
    assert done["mode"] == "rule-based"
    assert [c[0] for c in tools.calls].count("get_fire_activity") == 1  # not re-run by the fallback
    assert done["verification"]["verified"] == done["verification"]["findings"]


client = TestClient(create_app())


def test_api_investigate_stream_rules_real_data():
    with client.stream("GET", "/api/investigate?bbox=-124.5,32.5,-114.1,42.0&month=2020-09&mode=rules") as r:
        if r.status_code == 503:
            pytest.skip("processed data not built on this machine")
        events = [json.loads(line[6:]) for line in r.iter_lines() if line.startswith("data: ")]
    done = events[-1]
    assert done["type"] == "done" and done["report"]["assessment"] == "Exceptional"
    assert len(done["evidence"]) >= 5 and done["verification"]["verified"] >= 5


def test_api_investigate_rejects_bad_month():
    assert client.get("/api/investigate?month=2020-13&mode=rules").status_code in (422, 503)
