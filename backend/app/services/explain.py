"""AI explanations of computed anomaly statistics, from Google Gemini (REST API).

The model only receives numbers this app computed (a JSON "facts" object) and is told to use
nothing else. Every answer is labelled as AI-generated. If the model server is not running,
or the call fails, callers fall back to the rule-based text. Responses are cached on disk by a
hash of the model and facts.

Default model: gemini-3.8-flash, falling back to gemini-3.5-flash-lite when it is busy or slow (both have a free tier). The key (GEMINI_API_KEY in .env) stays on the
server and is sent only in a request header, never in a URL or to the browser. Only the computed
statistics are sent to Google.
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

SYSTEM = """You explain satellite fire-detection statistics from FIREWATCH 360 to non-specialists \
(emergency planners, journalists, residents).

You receive one JSON object of facts computed by the app. Write 3-5 plain sentences:
1. What the numbers show: the value compared with the historical baseline for the same calendar month \
(use the median, the percentage difference or "x times", and the rank when given).
2. How unusual it is, in words (robust z-score: about 2 = unusual, 3+ = very unusual).
3. How much to trust it: mention sensor or provisional-data caveats and calibration notes present in the facts.
4. One careful next step, such as "Further investigation may be warranted" or checking local fire reports.

Rules:
- Use only the facts given. Do not name specific fires, causes, weather, places or events unless they appear in the facts.
- Copy numbers exactly as given (you may round). Never compute new numbers.
- Units are "fire cell-days": one ~1 km grid cell with at least one satellite detection on one day. \
Do not call them fires or acres.
- Describe observed detections only; never forecast fire danger or spread, and never give safety instructions.
- No headings, lists or markdown. Under 120 words."""


API = "https://generativelanguage.googleapis.com/v1beta"


class Explainer:
    def __init__(self, api_key: str, model: str, cache_dir: Path, client: httpx.Client | None = None,
                 fallback_model: str = "", timeout: float = 45):
        self.api_key = api_key
        self.model = model
        self.fallback_model = fallback_model
        self.timeout = timeout
        self.cache_dir = Path(cache_dir)
        self.enabled = bool(api_key and model)
        self.client = client or httpx.Client(timeout=60)

    def _call(self, model: str, payload: str) -> httpx.Response:
        return self.client.post(
            f"{API}/models/{model}:generateContent",
            headers={"x-goog-api-key": self.api_key},
            json={
                "systemInstruction": {"parts": [{"text": SYSTEM}]},
                "contents": [{"role": "user", "parts": [{"text": f"Facts:\n{payload}"}]}],
                "generationConfig": {"temperature": 0.2, "maxOutputTokens": 1024,
                                     "thinkingConfig": {"thinkingLevel": "low"}},
            },
            timeout=self.timeout,
        )

    def explain(self, facts: dict) -> dict:
        if not self.enabled:
            return {"status": "not_configured", "message": "AI explanations are off: set GEMINI_API_KEY in .env on the server."}
        payload = json.dumps(facts, sort_keys=True, default=str)
        key = hashlib.sha256(f"{self.model}|{SYSTEM}|{payload}".encode()).hexdigest()[:24]
        path = self.cache_dir / f"{key}.json"
        if path.exists():
            return {**json.loads(path.read_text()), "cached": True}
        r, used = None, None
        for model in dict.fromkeys([self.model, self.fallback_model]):  # fallback when busy, over quota or slow
            if not model:
                continue
            try:
                r, used = self._call(model, payload), model
            except httpx.TimeoutException:
                continue
            except httpx.HTTPError:
                return {"status": "unavailable", "message": "Could not reach the Gemini API."}
            if r.status_code not in (429, 500, 503):
                break
        if r is None:
            return {"status": "unavailable", "message": "Gemini did not answer in time; try again later."}
        if r.status_code in (400, 401, 403) and "API key" in r.text:
            return {"status": "unavailable", "message": "Gemini rejected the server's API key."}
        if r.status_code == 429:
            return {"status": "unavailable", "message": "Gemini rate limit or free-tier quota reached; try again later."}
        if r.status_code in (500, 503):
            return {"status": "unavailable", "message": "Gemini is overloaded right now; try again in a few minutes."}
        if r.status_code != 200:
            log.warning("explain failed: HTTP %s", r.status_code)  # body not logged
            return {"status": "unavailable", "message": f"The Gemini API returned an error (HTTP {r.status_code})."}
        body = r.json()
        cands = body.get("candidates") or []
        if not cands or cands[0].get("finishReason") in ("SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST"):
            return {"status": "unavailable", "message": "Gemini declined to explain these numbers."}
        parts = (cands[0].get("content") or {}).get("parts", [])
        text = " ".join(p.get("text", "") for p in parts if not p.get("thought")).strip()
        if not text:
            return {"status": "unavailable", "message": "Gemini returned no text."}
        out = {"status": "ok", "text": text, "model": used,
               "label": "AI-generated by Google Gemini from the statistics shown; check the numbers before relying on it."}
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(out))
        return {**out, "cached": False}
