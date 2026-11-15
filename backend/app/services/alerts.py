"""Saved areas and alerts.

Each saved area has rules; `check_area` evaluates them and records new events once (deduplicated
by a key per rule and period). Events go to the in-app inbox and, if set, to the area's webhook
(JSON POST) and e-mail (SMTP, if the server has SMTP settings).

Rules:
  anomaly    the area's Fire Risk Brief level is Moderate or Severe (robust z vs baseline)
  nrt_km     near-real-time FIRMS detections inside the area or within this many km of it
             (last 2 UTC days; NRT has no fire-type field, so static sources may be included)
  fire_weather  an NWS Red Flag Warning or Fire Weather Watch is active over the area

Storage is two JSON files in data/alerts/ (a prototype; one server, few users).
"""
from __future__ import annotations

import json
import logging
import smtplib
import threading
import time
import uuid
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx

log = logging.getLogger(__name__)

MAX_AREAS = 200
MAX_EVENTS = 2000


def valid_webhook(url: str) -> bool:
    u = urlparse(url)
    return u.scheme == "https" or (u.scheme == "http" and u.hostname in ("localhost", "127.0.0.1"))


def aoi_kwargs(query: str) -> dict:
    """'bbox=..' | 'lat=..&lon=..&radius_km=..' | 'polygon=..' -> parse_aoi keyword arguments."""
    q = {k: v[0] for k, v in parse_qs(query, keep_blank_values=False).items()}
    out: dict = {}
    if "bbox" in q:
        out["bbox"] = q["bbox"]
    if "polygon" in q:
        out["polygon"] = q["polygon"]
    for k in ("lat", "lon", "radius_km"):
        if k in q:
            out[k] = float(q[k])
    return out


class AlertStore:
    def __init__(self, folder: Path):
        self.folder = Path(folder)
        self.lock = threading.Lock()

    def _read(self, name: str) -> list:
        p = self.folder / name
        return json.loads(p.read_text()) if p.exists() else []

    def _write(self, name: str, rows: list) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        tmp = self.folder / f".{name}.tmp"
        tmp.write_text(json.dumps(rows, indent=1))
        tmp.replace(self.folder / name)

    def areas(self) -> list[dict]:
        return self._read("areas.json")

    def add_area(self, area: dict) -> dict:
        with self.lock:
            rows = self.areas()
            if len(rows) >= MAX_AREAS:
                raise ValueError(f"At most {MAX_AREAS} saved areas")
            area = {**area, "id": uuid.uuid4().hex[:10], "created_utc": time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime()),
                    "last_checked_utc": None}
            rows.append(area)
            self._write("areas.json", rows)
            return area

    def delete_area(self, area_id: str) -> bool:
        with self.lock:
            rows = self.areas()
            keep = [a for a in rows if a["id"] != area_id]
            self._write("areas.json", keep)
            return len(keep) != len(rows)

    def touch(self, area_id: str) -> None:
        with self.lock:
            rows = self.areas()
            for a in rows:
                if a["id"] == area_id:
                    a["last_checked_utc"] = time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime())
            self._write("areas.json", rows)

    def events(self, area_id: str | None = None) -> list[dict]:
        ev = self._read("events.json")
        return [e for e in ev if area_id is None or e["area_id"] == area_id]

    def add_events(self, new: list[dict]) -> list[dict]:
        """Store events whose (area, key) is new; return only those."""
        with self.lock:
            ev = self._read("events.json")
            seen = {(e["area_id"], e["key"]) for e in ev}
            fresh = [e for e in new if (e["area_id"], e["key"]) not in seen]
            if fresh:
                self._write("events.json", (fresh + ev)[:MAX_EVENTS])
            return fresh


def deliver(event: dict, area: dict, smtp: dict, client: httpx.Client | None = None) -> dict:
    out = {"in_app": True}
    if area.get("webhook_url"):
        try:
            c = client or httpx.Client(timeout=15)
            r = c.post(area["webhook_url"], json={"source": "FIREWATCH 360", "area": area["name"], **event,
                                                   "text": f"[FIREWATCH 360] {area['name']}: {event['title']}. {event['detail']}"})
            out["webhook"] = "sent" if r.status_code < 300 else f"failed (HTTP {r.status_code})"
        except httpx.HTTPError:
            out["webhook"] = "failed (could not connect)"
    if area.get("email"):
        if not smtp.get("host"):
            out["email"] = "not configured on the server"
        else:
            try:
                msg = EmailMessage()
                msg["Subject"] = f"[FIREWATCH 360] {area['name']}: {event['title']}"
                msg["From"] = smtp["from"]
                msg["To"] = area["email"]
                msg.set_content(f"{event['detail']}\n\n{event.get('note', '')}\n\nPrototype alert from FIREWATCH 360. "
                                "Not an official warning; follow local authorities.")
                with smtplib.SMTP(smtp["host"], smtp["port"], timeout=20) as s:
                    s.starttls()
                    if smtp.get("user"):
                        s.login(smtp["user"], smtp["password"])
                    s.send_message(msg)
                out["email"] = "sent"
            except (OSError, smtplib.SMTPException):
                out["email"] = "failed"
    return out
