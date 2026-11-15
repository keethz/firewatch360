"""Autonomous fire investigation agent.

Given an area and a month, the agent investigates *why* fire activity there is (or is not) unusual by
calling the app's own analysis functions as tools: harmonized activity vs history, MODIS vs VIIRS
agreement, the most anomalous map cells, NIFC reported incidents, drought and fire weather, community
assets near the fire, burned area, smoke and the outlook. A tool can be pointed at a sub-area (a cell's
bounding box) so the agent can zoom in on hot spots it found.

Every tool result is stored as numbered evidence (E1, E2, ...). The report is submitted through a
`submit_report` tool whose findings must cite evidence IDs. After the model finishes, every finding is
checked: unknown evidence IDs are removed, and each number in the statement is looked up in the cited
evidence. Findings get `verified: true` only when all their numbers are found there.

Planner: Google Gemini function calling (same key and models as the AI explanations). Without a key,
or if the model fails, a deterministic planner runs a fixed investigation and writes rule-based
findings from the same evidence, so the feature always works and is labelled accordingly.
"""
from __future__ import annotations

import json
import logging
import math
import re
import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Callable, Iterator

import httpx

log = logging.getLogger(__name__)

API = "https://generativelanguage.googleapis.com/v1beta"
MAX_TOOL_CALLS = 12
MAX_TURNS = 10
CALL_TIMEOUT_S = 40
PRIMARY_TIMEOUT_S = 20

SYSTEM = """You are FIREWATCH 360's fire investigation agent, working for emergency managers.
You investigate satellite-observed fire activity in one area and month and explain what is going on, using ONLY \
evidence returned by your tools. Every tool result has an evidence ID (E1, E2, ...).

Method:
1. Start with get_fire_activity to establish how unusual the activity is.
2. Check compare_sensors: do MODIS and VIIRS agree? Disagreement lowers confidence.
3. Use find_anomalous_cells to locate where the activity is concentrated.
4. Explain it: get_reported_incidents (named fires, causes), get_drought_and_fire_weather (conditions). Zoom in by \
passing a cell's bbox when the area is large.
5. Assess consequences: assess_community_assets for the most anomalous cell or the area (needs a bbox when the area \
is the whole US). Add get_burned_area, get_smoke or get_outlook when they help.
6. Call submit_report. Aim for 6-10 tool calls; do not repeat a call with the same arguments.

Report rules:
- Each finding is one factual sentence with numbers copied exactly from the cited evidence (rounding allowed), and \
cites the evidence IDs that contain those numbers.
- Separate observation (what the data show) from interpretation (likely drivers); interpretations must still cite evidence \
and use cautious wording ("consistent with", "likely").
- Name fires, places or causes only if they appear in the evidence.
- Fire cell-days are ~1 km grid cells with a detection on a day; never call them fires or acres.
- Include gaps: what you could not check or what weakens confidence.
- Finish with `summary`: one plain paragraph that ties the findings together for a decision-maker.
- Never give safety instructions or forecast fire spread."""


# ------------------------------------------------------------------ evidence

@dataclass
class Evidence:
    id: str
    tool: str
    args: dict
    summary: str
    data: Any
    ok: bool = True
    seconds: float = 0.0

    def public(self) -> dict:
        return {"id": self.id, "tool": self.tool, "args": self.args, "summary": self.summary, "data": self.data,
                "ok": self.ok, "seconds": round(self.seconds, 2)}


@dataclass
class Investigation:
    aoi_label: str
    month: str | None
    evidence: list[Evidence] = field(default_factory=list)

    def add(self, tool: str, args: dict, summary: str, data: Any, ok: bool, seconds: float) -> Evidence:
        ev = Evidence(f"E{len(self.evidence) + 1}", tool, args, summary, data, ok, seconds)
        self.evidence.append(ev)
        return ev

    def by_id(self) -> dict[str, Evidence]:
        return {e.id: e for e in self.evidence}


# ------------------------------------------------------------------ tool declarations

_BBOX = {"type": "array", "items": {"type": "number"},
         "description": "Optional sub-area [west, south, east, north] in degrees, e.g. an anomalous cell's bbox. "
                        "Omit to use the investigated area."}
_WHY = {"type": "string", "description": "One short sentence: why you are calling this tool now."}


def _decl(name: str, desc: str, props: dict, required: list[str] | None = None) -> dict:
    return {"name": name, "description": desc,
            "parameters": {"type": "object", "properties": {"why": _WHY, **props}, "required": ["why", *(required or [])]}}


DECLARATIONS = [
    _decl("get_fire_activity", "Harmonized monthly fire activity (VIIRS-equivalent fire cell-days) for the area vs its "
          "history for the same calendar month: value, median, % difference, robust z, rank, anomaly level, trend, peak season.",
          {"month": {"type": "string", "description": "YYYY-MM; omit for the investigated month"}}),
    _decl("compare_sensors", "MODIS and VIIRS raw activity each scored against its own history, whether they agree, "
          "and the calibration factors k and c.", {"month": {"type": "string"}}),
    _decl("find_anomalous_cells", "The 0.25-degree map cells with the most unusual activity this month vs the same month in "
          "past years (value, median, record max, z, rank, bbox).", {"month": {"type": "string"}}),
    _decl("get_reported_incidents", "Agency-reported wildfires (NIFC WFIGS, 2014+) discovered in the dates: count, acres, "
          "causes, largest named fires.", {"start": {"type": "string", "description": "YYYY-MM-DD"},
                                          "end": {"type": "string", "description": "YYYY-MM-DD"}, "bbox": _BBOX},
          ["start", "end"]),
    _decl("get_drought_and_fire_weather", "US Drought Monitor categories and DSCI for the month (and latest week), plus "
          "Red Flag Warnings / Fire Weather Watches active right now (not historical).", {"month": {"type": "string"}, "bbox": _BBOX}),
    _decl("assess_community_assets", "People, homes in the wildland-urban interface, hospitals, schools, fire stations, "
          "airports, roads and protected land inside the area and within buffer_km of fire detections in the dates. "
          "Needs a bbox when the investigated area is the whole US.",
          {"start": {"type": "string"}, "end": {"type": "string"}, "buffer_km": {"type": "number"}, "bbox": _BBOX},
          ["start", "end"]),
    _decl("get_burned_area", "Mapped fire perimeters for a year (NIFC 2020+, MTBS before): acres burned in the area, "
          "number of fires, largest fires.", {"year": {"type": "integer"}, "bbox": _BBOX}, ["year"]),
    _decl("get_smoke", "NOAA HMS satellite smoke over the area on a date: share under light/medium/heavy smoke and smoke "
          "days in the 14 days before.", {"date": {"type": "string", "description": "YYYY-MM-DD"}, "bbox": _BBOX}, ["date"]),
    _decl("get_outlook", "Experimental 1-3 month statistical outlook of fire activity from today, with its hindcast skill. "
          "Only relevant when investigating the latest month.", {}),
]

REPORT_DECL = {
    "name": "submit_report",
    "description": "Submit the final investigation. Call once, after gathering evidence.",
    "parameters": {"type": "object", "properties": {
        "headline": {"type": "string", "description": "One-sentence conclusion."},
        "assessment": {"type": "string", "enum": ["Exceptional", "Unusual", "Normal", "Below normal", "Inconclusive"]},
        "confidence": {"type": "string", "enum": ["High", "Medium", "Low"]},
        "findings": {"type": "array", "items": {"type": "object", "properties": {
            "statement": {"type": "string"},
            "kind": {"type": "string", "enum": ["observation", "driver", "impact", "context"]},
            "evidence": {"type": "array", "items": {"type": "string"}}}, "required": ["statement", "kind", "evidence"]}},
        "gaps": {"type": "array", "items": {"type": "string"}},
        "next_steps": {"type": "array", "items": {"type": "string"}},
        "summary": {"type": "string", "description": "Closing paragraph (4-6 sentences, under 140 words) for a busy "
                    "emergency manager: how unusual the activity was, where, what likely explains it, who/what is exposed, "
                    "and how confident the evidence is. Plain prose, numbers copied from the evidence, no citations or lists."},
    }, "required": ["headline", "assessment", "confidence", "findings", "gaps", "next_steps", "summary"]},
}


# ------------------------------------------------------------------ number verification

# A sign only counts when not glued to a preceding digit, so "2020-09-15" reads as 2020, 9, 15.
_NUM = re.compile(r"(?<![A-Za-z0-9.])[-+]?\d[\d,]*(?:\.\d+)?")


def _numbers_in(text: str) -> list[float]:
    out = []
    for m in _NUM.findall(text):
        try:
            out.append(float(m.replace(",", "").lstrip("+")))
        except ValueError:
            pass
    return out


def _flatten_numbers(obj: Any, out: list[float]) -> None:
    if isinstance(obj, bool) or obj is None:
        return
    if isinstance(obj, (int, float)):
        if math.isfinite(obj):
            out.append(float(obj))
            if abs(obj) < 1:  # shares stored as fractions may be quoted as percentages
                out.append(float(obj) * 100)
    elif isinstance(obj, str):
        out.extend(_numbers_in(obj))
    elif isinstance(obj, dict):
        for k, v in obj.items():
            _flatten_numbers(k, out)
            _flatten_numbers(v, out)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _flatten_numbers(v, out)


def _matches(x: float, pool: list[float]) -> bool:
    for y in pool:
        if abs(x - y) <= max(0.51, 0.015 * abs(y)):
            return True
        # "1.9 million" / "33k" style rounding of big numbers
        for scale in (1e3, 1e6):
            if abs(y) >= scale and abs(x - y / scale) <= max(0.051, 0.015 * abs(y / scale)):
                return True
    return False


def verify(findings: list[dict], inv: Investigation) -> list[dict]:
    """Drop unknown evidence IDs and check every number in each statement against the cited evidence."""
    ids = inv.by_id()
    out = []
    for f in findings:
        cited = [e for e in dict.fromkeys(f.get("evidence") or []) if e in ids]
        dropped = [e for e in (f.get("evidence") or []) if e not in ids]
        pool: list[float] = _numbers_in(inv.aoi_label)  # the area's own name (e.g. cell coordinates) is not a claim
        for e in cited:
            _flatten_numbers(ids[e].data, pool)
            _flatten_numbers(ids[e].args, pool)
        nums = [n for n in _numbers_in(f.get("statement", "")) if not (n.is_integer() and 1 <= n <= 12)]  # month/lead numbers
        unmatched = [n for n in nums if not _matches(n, pool)]
        out.append({**f, "evidence": cited, "invalid_citations": dropped,
                    "numbers_checked": len(nums), "numbers_unmatched": [_fmt(n) for n in unmatched],
                    "verified": bool(cited) and not unmatched})
    return out


def verify_summary(text: str, inv: Investigation) -> dict:
    """Numbers in the closing paragraph must appear somewhere in the evidence (it cites no IDs)."""
    pool: list[float] = _numbers_in(inv.aoi_label)
    for e in inv.evidence:
        _flatten_numbers(e.data, pool)
        _flatten_numbers(e.args, pool)
    nums = [n for n in _numbers_in(text) if not (n.is_integer() and 1 <= n <= 12)]
    unmatched = [_fmt(n) for n in nums if not _matches(n, pool)]
    return {"text": text, "numbers_checked": len(nums), "numbers_unmatched": unmatched, "verified": bool(text) and not unmatched}


def _fmt(x: float) -> str:
    return f"{x:,.0f}" if x.is_integer() else f"{x:,.2f}"


# ------------------------------------------------------------------ tools (bound to one request)

def _r(x, n=1):
    return None if x is None else round(float(x), n)


class Tools:
    """Wraps the app's own analysis functions. `api` is the routes module; `request` the HTTP request."""

    def __init__(self, api, request, aoi, params, month: str | None):
        self.api, self.request, self.aoi, self.p = api, request, aoi, params
        self.month = month
        self._brief: dict[str | None, dict] = {}

    # helpers
    def _area(self, bbox) -> Any:
        if not bbox:
            return self.aoi
        if len(bbox) != 4:
            raise ValueError("bbox must be [west, south, east, north]")
        return self.api.parse_aoi(bbox=",".join(f"{float(v):.4f}" for v in bbox))

    def _brief_for(self, month: str | None) -> dict:
        m = month or self.month
        if m not in self._brief:
            w = {"start": None, "end": m, "year": None, "b0": None, "b1": None, "z": 2.0}
            self._brief[m] = self.api.brief(self.request, self.aoi, self.p, w)
        return self._brief[m]

    def ref_month(self) -> str:
        return self._brief_for(None)["reference"]["month"]

    # tools -> (summary, data)
    def get_fire_activity(self, month=None, **_):
        b = self._brief_for(month)
        ca, rk = b["current_activity"], b["rank"]
        data = {"month": b["reference"]["label"], "kind": b["reference"]["kind"], "provisional": b["reference"]["provisional"],
                "value_cell_days": _r(ca["value"], 0), "median_cell_days": _r(ca["median"], 0),
                "pct_vs_median": _r(ca["pct_vs_median"], 0), "robust_z": _r(ca["z"], 1),
                "anomaly_level": b["level"], "rank": rk.get("text") if rk else None,
                "peak_season": b.get("peak_season"), "trend": (b.get("trend") or {}).get("text"),
                "baseline_years": b["baseline"]["years"], "confidence": b["confidence"]["level"],
                "confidence_reasons": b["confidence"]["reasons"]}
        s = (f"{data['month']}: {data['value_cell_days']:,.0f} cell-days vs median {data['median_cell_days']:,.0f}"
             f" ({ca.get('text') or 'n/a'}), level {b['level']}" + (f"; {rk['text']}" if rk and rk.get("text") else ""))
        return s, data

    def compare_sensors(self, month=None, **_):
        b = self._brief_for(month)
        sens = {k: {"available": v.get("available"), "value": v.get("value"), "median": v.get("median"),
                    "z": _r(v.get("z"), 1), "class": v.get("class"), "years_of_history": v.get("n_years")}
                for k, v in b["sensors"].items()}
        data = {"sensors": sens, "agreement": b["agreement"]["label"],
                "calibration": {"k": _r(b["factors"]["k"], 3), "c": _r(b["factors"]["c"], 3),
                                "k_scope": b["factors"]["k_scope"]}}
        parts = [f"{k} {v['class'] or 'n/a'} (z {v['z']})" for k, v in sens.items() if v["available"]]
        return f"{b['agreement']['label']}: " + ", ".join(parts), data

    def find_anomalous_cells(self, month=None, **_):
        m = month or self.ref_month()
        win = self.request.app.state.harmonizer.windows()
        latest = win["provisional_end"] or win["series_end"]
        if m > latest:
            m = latest
        res = self.api._anomaly_map(self.request, self.aoi, self.p, m, 0.25, 2.0, 10, None, None)
        cells = res["cells"]
        high = [c for c in cells if c.get("anomaly") and not c.get("below")]
        top = [{"bbox": c["bbox"], "value": c["value"], "median": c["median"], "record_max_before": c.get("baseline_max"),
                "z": _r(c["z"], 1), "rank": f"{c.get('rank')} of {c.get('of')}"} for c in high[:5]]
        data = {"month": m, "cells_with_fire": len(cells), "anomalous_high_cells": len(high),
                "anomalous_low_cells": len([c for c in cells if c.get("anomaly") and c.get("below")]), "top": top}
        s = f"{m}: {len(high)} of {len(cells)} active cells unusually high"
        if top:
            s += f"; top cell {top[0]['bbox']} with {top[0]['value']:,.0f} cell-days (record before: {top[0]['record_max_before']})"
        return s, data

    def get_reported_incidents(self, start, end, bbox=None, **_):
        a = self._area(bbox)
        r = self.api.reported(self.request, a, date.fromisoformat(start), date.fromisoformat(end), 0)
        if r.get("status") != "ok":
            return f"Reported fires unavailable: {r.get('message')}", {"status": r.get("status"), "message": r.get("message")}
        data = {"window": r["window"], "wildfires": r["wildfires"], "wildfire_acres": r["wildfire_acres"],
                "prescribed": r["prescribed"], "causes": r["causes"],
                "largest": [{k: f.get(k) for k in ("name", "date", "acres", "cause", "county")} for f in r["largest"][:5]]}
        big = ", ".join(f"{f['name']} ({(f['acres'] or 0):,.0f} ac)" for f in data["largest"][:3])
        return f"{r['wildfires']:,} reported wildfires, {r['wildfire_acres']:,.0f} acres; largest: {big or 'none'}", data

    def get_drought_and_fire_weather(self, month=None, bbox=None, **_):
        m = month or self.ref_month()
        c = self.api.area_context(self.request.app, self._area(bbox), m)
        dm, dl, fw = c.get("drought_month", {}), c.get("drought_latest", {}), c.get("fire_weather", {})
        pick = lambda d: {k: d.get(k) for k in ("week", "text", "dsci", "dsci_percentile", "worst_label", "cumulative")} if d.get("status") == "ok" else {"status": d.get("status"), "message": d.get("message")}  # noqa: E731
        data = {"drought_in_month": pick(dm), "drought_latest_week": pick(dl),
                "fire_weather_now": {"red_flag_warnings": fw.get("n_red_flag"), "fire_weather_watches": fw.get("n_watch"),
                                     "active": [{k: a.get(k) for k in ("event", "area", "expires")} for a in (fw.get("active") or [])[:5]]}}
        s = (f"Drought {m}: {dm.get('text', 'n/a')} (DSCI {dm.get('dsci', 'n/a')}); "
             f"{fw.get('n_red_flag', 0)} Red Flag Warnings active today")
        return s, data

    def assess_community_assets(self, start, end, buffer_km=5, bbox=None, **_):
        a = self._area(bbox)
        if a.kind == "region":
            raise ValueError("The investigated area is the whole US: pass the bbox of an anomalous cell.")
        r = self.api.impact(self.request, a, date.fromisoformat(start), date.fromisoformat(end),
                            float(min(max(buffer_km or 5, 1), 25)), "MODIS,VIIRS", "nominal", True)
        if r.get("status") != "ok":
            return f"Asset assessment unavailable: {r.get('message')}", {"status": r.get("status"), "message": r.get("message")}
        L = r["layers"]
        g = lambda k, *path: _dig(L.get(k, {}), *path)  # noqa: E731
        data = {"buffer_km": buffer_km, "fire_detections": _dig(r, "fire", "detections"),
                "population_near_fire": g("population", "near_fire", "population"),
                "housing_units_near_fire": g("population", "near_fire", "housing_units"),
                "wui_housing_near_fire": g("wui", "near_fire", "housing_units"),
                "hospitals_near_fire": g("hospitals", "near_fire"), "schools_near_fire": g("schools", "near_fire"),
                "fire_stations_near_fire": g("firestations", "near_fire"), "airports_near_fire": g("airports", "near_fire"),
                "major_roads_near_fire_km": g("roads", "near_fire_km"), "protected_land_near_fire_km2": g("protected", "near_fire_km2"),
                "nearest": {k: [i.get("name") for i in (L.get(k, {}).get("items") or [])[:3]]
                            for k in ("hospitals", "schools", "firestations", "airports")}}
        s = (f"Within {buffer_km} km of detections: ~{data['population_near_fire'] or 0:,} people, "
             f"{data['schools_near_fire'] or 0} schools, {data['hospitals_near_fire'] or 0} hospitals, "
             f"{data['fire_stations_near_fire'] or 0} fire stations")
        return s, data

    def get_burned_area(self, year, bbox=None, **_):
        r = self.api.burned_area(self.request, self._area(bbox), int(year))
        data = {"year": r["year"], "source": r["source_label"], "acres_burned_in_area": r["acres_in_area"],
                "pct_of_area": r["pct_of_area"], "fires": r["n_fires"], "wildfires": r["n_wildfires"],
                "largest": [{k: f.get(k) for k in ("name", "acres", "start", "type")} for f in r["fires"][:5]]}
        return r["headline"], data

    def get_smoke(self, date: str, bbox=None, **_):  # noqa: A002 - tool argument name
        from datetime import date as _d

        from app.services import smoke as sm
        r = sm.summarize(self.request.app.state.smoke, self.api._area(self._area(bbox)), _d.fromisoformat(date), 14)
        if r.get("status") != "ok":
            return r.get("message", "Smoke unavailable"), {"status": r.get("status"), "message": r.get("message")}
        data = {"date": r["date"], "pct_under_smoke": r["today"]["pct_covered"], "pct_any": r["today"]["pct_any"],
                "smoke_days_last_14": r["days_with_smoke"], "heavy_smoke_days_last_14": r["days_with_heavy_smoke"]}
        return r["headline"], data

    def get_outlook(self, **_):
        r = self.api.fire_outlook(self.request, self.aoi, self.p)
        if r.get("status") != "ok":
            return r.get("message", "No outlook"), {"status": r.get("status")}
        data = {"headline": r["headline"], "issued_from": r["issued_from"],
                "leads": [{"month": l["month_name"], "most_likely": l["most_likely"], "probabilities": l["probabilities"],
                           "skill_score": l["skill"]["msss"], "useful": l["skill"]["useful"]} for l in r["leads"]]}
        return r["headline"], data

    def run(self, name: str, args: dict) -> tuple[str, Any, bool]:
        fn: Callable | None = getattr(self, name, None) if name in {d["name"] for d in DECLARATIONS} else None
        if fn is None:
            return f"Unknown tool {name}", {"error": f"unknown tool {name}"}, False
        clean = {k: v for k, v in args.items() if k != "why"}
        try:
            s, data = fn(**clean)
            return s, data, True
        except Exception as exc:  # noqa: BLE001 - report the failure as evidence, the agent can adapt
            detail = getattr(exc, "detail", None) or str(exc) or type(exc).__name__
            return f"Tool error: {detail}", {"error": str(detail)[:300]}, False


def _dig(d, *path):
    for p in path:
        if not isinstance(d, dict):
            return None
        d = d.get(p)
    return d


def month_dates(month: str) -> tuple[str, str]:
    y, m = int(month[:4]), int(month[5:7])
    nxt = date(y + (m == 12), m % 12 + 1, 1)
    return f"{month}-01", (nxt - timedelta(days=1)).isoformat()


# ------------------------------------------------------------------ planners

def _event(kind: str, **kw) -> dict:
    return {"type": kind, **kw}


def run_agent(tools: Tools, inv: Investigation, api_key: str, models: list[str],
              client: httpx.Client | None = None) -> Iterator[dict]:
    """Gemini function-calling loop. Yields progress events; the last one is the report (or a fallback)."""
    client = client or httpx.Client(timeout=CALL_TIMEOUT_S)
    ref = tools.ref_month()
    start, end = month_dates(ref)
    latest = tools.month is None or tools._brief_for(None)["reference"]["kind"] == "month_to_date"
    task = (f"Investigate fire activity in {inv.aoi_label} for {ref} (dates {start} to {end}). "
            f"The investigated area {'is the whole United States' if tools.aoi.kind == 'region' else 'is a user-selected area'}. "
            "Find out how unusual it is, whether the sensors agree, where it is concentrated, what explains it, and what "
            "communities and assets are exposed. Then submit_report."
            + ("" if latest else " This is a past month: do not use get_outlook, and current fire-weather alerts do not "
                                 "describe that month."))
    contents: list[dict] = [{"role": "user", "parts": [{"text": task}]}]
    decl = [*DECLARATIONS, REPORT_DECL]
    model_i, calls, seen = 0, 0, set()
    for turn in range(MAX_TURNS):
        force_report = calls >= MAX_TOOL_CALLS or turn == MAX_TURNS - 1
        body = {"systemInstruction": {"parts": [{"text": SYSTEM}]}, "contents": contents,
                "tools": [{"functionDeclarations": decl}],
                "toolConfig": {"functionCallingConfig": {"mode": "ANY", **({"allowedFunctionNames": ["submit_report"]} if force_report else {})}},
                "generationConfig": {"temperature": 0.2, "thinkingConfig": {"thinkingLevel": "low"}}}
        r = None
        while model_i < len(models):
            try:
                # The main model gets a shorter leash: when it is slow the fallback usually answers in seconds.
                r = client.post(f"{API}/models/{models[model_i]}:generateContent", headers={"x-goog-api-key": api_key},
                                json=body, timeout=PRIMARY_TIMEOUT_S if model_i == 0 and len(models) > 1 else CALL_TIMEOUT_S)
                if r.status_code in (429, 500, 503):
                    model_i += 1
                    yield _event("note", text=f"Model busy (HTTP {r.status_code}); switching to the fallback model.")
                    continue
                break
            except httpx.TimeoutException:
                model_i += 1
                yield _event("note", text="Model timed out; switching to the fallback model.")
        if r is None or r.status_code != 200:
            raise RuntimeError(f"model unavailable (HTTP {getattr(r, 'status_code', 'timeout')})")
        cand = (r.json().get("candidates") or [{}])[0]
        content = cand.get("content") or {}
        parts = content.get("parts") or []
        calls_here = [p["functionCall"] for p in parts if "functionCall" in p]
        thought = " ".join(p.get("text", "") for p in parts if p.get("text") and not p.get("thought")).strip()
        if not calls_here:
            raise RuntimeError("model returned no tool call")
        contents.append({"role": "model", "parts": parts})  # verbatim, keeps thought signatures
        responses = []
        for fc in calls_here:
            name, args = fc.get("name"), dict(fc.get("args") or {})
            if name == "submit_report":
                yield _event("report", report=args, model=models[min(model_i, len(models) - 1)], mode="agent")
                return
            why = args.get("why") or thought or ""
            key = json.dumps([name, {k: v for k, v in args.items() if k != "why"}], sort_keys=True)
            yield _event("tool_call", tool=name, args={k: v for k, v in args.items() if k != "why"}, why=why)
            t0 = time.time()
            if key in seen:
                s, data, ok = "Duplicate call skipped: same tool and arguments as before.", {"error": "duplicate"}, False
            else:
                seen.add(key)
                s, data, ok = tools.run(name, args)
            ev = inv.add(name, {k: v for k, v in args.items() if k != "why"}, s, data, ok, time.time() - t0)
            calls += 1
            yield _event("evidence", evidence=ev.public(), why=why)
            responses.append({"functionResponse": {"name": name, "response": {"evidence_id": ev.id, "ok": ok,
                                                                              "summary": s, "data": data}}})
        contents.append({"role": "user", "parts": responses})
    raise RuntimeError("agent did not submit a report")


def run_rules(tools: Tools, inv: Investigation, start_at: int = 0) -> Iterator[dict]:
    """Deterministic investigation: fixed plan, rule-based findings from the same evidence."""
    ref = tools.ref_month()
    start, end = month_dates(ref)
    plan: list[tuple[str, dict, str]] = [
        ("get_fire_activity", {}, "Establish how unusual activity is against this area's history."),
        ("compare_sensors", {}, "Check that MODIS and VIIRS tell the same story."),
        ("find_anomalous_cells", {}, "Locate where the activity is concentrated."),
        ("get_reported_incidents", {"start": start, "end": end}, "Look for agency-reported fires behind the detections."),
        ("get_drought_and_fire_weather", {}, "Check drought and fire-weather conditions."),
    ]
    done = {e.tool for e in inv.evidence}
    for name, args, why in plan:
        if name in done:
            continue
        yield _event("tool_call", tool=name, args=args, why=why)
        t0 = time.time()
        s, data, ok = tools.run(name, args)
        ev = inv.add(name, args, s, data, ok, time.time() - t0)
        yield _event("evidence", evidence=ev.public(), why=why)
    cells = next((e for e in inv.evidence if e.tool == "find_anomalous_cells" and e.ok), None)
    focus = None
    if tools.aoi.kind != "region":
        focus = None
    elif cells and cells.data["top"]:
        focus = cells.data["top"][0]["bbox"]
    if "assess_community_assets" not in done and (tools.aoi.kind != "region" or focus):
        args = {"start": start, "end": end, "buffer_km": 5, **({"bbox": focus} if focus else {})}
        why = "Assess which communities and facilities are near the detections" + (" in the most anomalous cell." if focus else ".")
        yield _event("tool_call", tool="assess_community_assets", args=args, why=why)
        t0 = time.time()
        s, data, ok = tools.run("assess_community_assets", args)
        ev = inv.add("assess_community_assets", args, s, data, ok, time.time() - t0)
        yield _event("evidence", evidence=ev.public(), why=why)
    yield _event("report", report=rule_report(inv), model=None, mode="rule-based")


def rule_report(inv: Investigation) -> dict:
    E = {e.tool: e for e in inv.evidence if e.ok}
    findings, gaps, steps = [], [], []
    act = E.get("get_fire_activity")
    assessment, conf = "Inconclusive", "Low"
    if act:
        d = act.data
        z = d["robust_z"]
        assessment = ("Exceptional" if z is not None and z >= 3 else "Unusual" if z is not None and z >= 2
                      else "Below normal" if z is not None and z <= -2 else "Normal")
        conf = d["confidence"] or "Medium"
        pct = f"{d['pct_vs_median']:+,.0f}% vs the median" if d["pct_vs_median"] is not None else "no median"
        findings.append({"statement": f"{d['month']} had {d['value_cell_days']:,.0f} fire cell-days against a median of "
                                      f"{d['median_cell_days']:,.0f} ({pct}, robust z {d['robust_z']}).",
                         "kind": "observation", "evidence": [act.id]})
        if d["rank"]:
            findings.append({"statement": f"{d['rank']}.", "kind": "observation", "evidence": [act.id]})
    sen = E.get("compare_sensors")
    if sen:
        findings.append({"statement": f"{sen.data['agreement']}; calibration factor k = {sen.data['calibration']['k']}.",
                         "kind": "observation", "evidence": [sen.id]})
        if "disagree" in sen.data["agreement"].lower():
            gaps.append("The two sensors disagree, so the size of the anomaly is uncertain.")
    cel = E.get("find_anomalous_cells")
    if cel and cel.data["top"]:
        t = cel.data["top"][0]
        findings.append({"statement": f"{cel.data['anomalous_high_cells']} map cells were unusually high; the top cell "
                                      f"{t['bbox']} had {t['value']:,.0f} cell-days against a previous record of "
                                      f"{t['record_max_before']}.", "kind": "observation", "evidence": [cel.id]})
    rep = E.get("get_reported_incidents")
    if rep and rep.data.get("wildfires") is not None:
        big = rep.data["largest"][:3]
        if big:
            names = ", ".join(f"{f['name']} ({(f['acres'] or 0):,.0f} acres)" for f in big)
            findings.append({"statement": f"Agencies reported {rep.data['wildfires']:,} wildfires burning "
                                          f"{rep.data['wildfire_acres']:,.0f} acres; the largest were {names}.",
                             "kind": "driver", "evidence": [rep.id]})
        else:
            findings.append({"statement": "No agency-reported wildfires were found for these dates.", "kind": "driver", "evidence": [rep.id]})
    dr = E.get("get_drought_and_fire_weather")
    if dr and isinstance(dr.data["drought_in_month"], dict) and dr.data["drought_in_month"].get("text"):
        dm = dr.data["drought_in_month"]
        findings.append({"statement": f"Drought that month: {dm['text']} (DSCI {dm['dsci']}).", "kind": "context", "evidence": [dr.id]})
    ass = E.get("assess_community_assets")
    if ass and ass.data.get("population_near_fire") is not None:
        d = ass.data
        findings.append({"statement": f"Within {d['buffer_km']} km of detections: about {d['population_near_fire']:,} people, "
                                      f"{d['schools_near_fire']} schools, {d['hospitals_near_fire']} hospitals and "
                                      f"{d['fire_stations_near_fire']} fire stations.", "kind": "impact", "evidence": [ass.id]})
    for e in inv.evidence:
        if not e.ok:
            gaps.append(f"{e.tool} could not be checked: {e.summary}")
    gaps.append("Rule-based report: findings are templated from the evidence, without AI interpretation.")
    steps.append("Compare with local incident reports and fire perimeters before acting.")
    head = {"Exceptional": "Fire activity here is exceptional for the time of year.",
            "Unusual": "Fire activity here is unusually high for the time of year.",
            "Normal": "Fire activity here is within its normal range.",
            "Below normal": "Fire activity here is below normal for the time of year.",
            "Inconclusive": "The investigation could not establish how unusual activity is."}[assessment]
    summary = " ".join([head] + [f["statement"] for f in findings if f["kind"] != "observation" or f is findings[0]]
                       + [f"Overall confidence is {conf.lower()}."])
    return {"headline": head, "assessment": assessment, "confidence": conf, "findings": findings,
            "gaps": gaps, "next_steps": steps, "summary": summary}


def investigate(tools: Tools, inv: Investigation, api_key: str, models: list[str],
                client: httpx.Client | None = None) -> Iterator[dict]:
    """Run the AI agent if configured, otherwise (or on failure) the rule-based planner. Final event: 'done'."""
    t0 = time.time()
    report_ev = None
    mode = "rule-based"
    if api_key and models:
        yield _event("status", text=f"AI agent ({models[0]}) is planning the investigation…")
        try:
            for ev in run_agent(tools, inv, api_key, models, client):
                if ev["type"] == "report":
                    report_ev = ev
                else:
                    yield ev
            mode = "agent"
        except Exception as exc:  # noqa: BLE001 - fall back with the evidence gathered so far
            log.warning("agent failed: %s", exc)
            yield _event("note", text=f"AI agent stopped ({exc}); finishing with the rule-based investigator.")
    else:
        yield _event("status", text="No AI key configured: running the rule-based investigator.")
    if report_ev is None:
        for ev in run_rules(tools, inv):
            if ev["type"] == "report":
                report_ev = ev
            else:
                yield ev
        mode = "rule-based"
    rep = report_ev["report"]
    findings = verify(rep.get("findings") or [], inv)
    n_ok = sum(f["verified"] for f in findings)
    summary = rep.get("summary") or rule_report(inv)["summary"]
    yield _event("done", mode=mode, model=report_ev.get("model"),
                 report={**rep, "findings": findings, "summary": verify_summary(summary, inv)},
                 verification={"findings": len(findings), "verified": n_ok,
                               "numbers_checked": sum(f["numbers_checked"] for f in findings)},
                 evidence=[e.public() for e in inv.evidence], seconds=round(time.time() - t0, 1),
                 area=inv.aoi_label, month=tools.ref_month())
