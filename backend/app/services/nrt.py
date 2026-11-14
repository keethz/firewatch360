"""Live FIRMS near-real-time proxy (server side only; the MAP_KEY never leaves the backend).

Fetches the FIRMS area API for the last N days over the AOI's bounding box, caches each raw
response on disk, filters rows to the exact AOI, and returns them unchanged apart from parsing.
API: https://firms.modaps.eosdis.nasa.gov/api/area/  (day range per request is 1..5)
"""
from __future__ import annotations

import csv
import hashlib
import io
import math
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx
import numpy as np
import shapely

from app.services.aoi import AOI, EARTH_RADIUS_KM

API = "https://firms.modaps.eosdis.nasa.gov/api/area/csv"
SOURCES = {"VIIRS": "VIIRS_SNPP_NRT", "MODIS": "MODIS_NRT"}
MAX_CHUNK_DAYS = 5
MAX_DAYS = 10
RECENT_TTL_S = 3 * 3600  # chunks that include the last two days can still grow
# Non-overlapping boxes covering the 50 states (same as scripts/fetch_2026.py)
US_BOXES = ["-129,24,-66,50", "-180,50,-129,72", "-161,18,-154,23"]


class NrtError(RuntimeError):
    pass


class NrtClient:
    def __init__(self, key: str, cache_dir: Path, transport: httpx.BaseTransport | None = None,
                 today: date | None = None):
        self.key = key
        self.cache_dir = Path(cache_dir)
        self.transport = transport
        self._today = today

    def today(self) -> date:
        return self._today or datetime.now(timezone.utc).date()

    def _chunks(self, days: int) -> list[tuple[date, int]]:
        start = self.today() - timedelta(days=days - 1)
        out, d = [], start
        while d <= self.today():
            n = min(MAX_CHUNK_DAYS, (self.today() - d).days + 1)
            out.append((d, n))
            d += timedelta(days=n)
        return out

    def _get(self, client: httpx.Client, source: str, bbox: str, start: date, n: int) -> str:
        tag = hashlib.sha1(bbox.encode()).hexdigest()[:10]
        path = self.cache_dir / source / f"{tag}_{start}_{n}.csv"
        recent = start + timedelta(days=n - 1) >= self.today() - timedelta(days=1)
        if path.exists() and (not recent or time.time() - path.stat().st_mtime < RECENT_TTL_S):
            return path.read_text()
        url = f"{API}/{self.key}/{source}/{bbox}/{n}/{start}"
        try:
            r = client.get(url)
        except httpx.HTTPError as exc:
            raise NrtError(f"FIRMS request failed: {type(exc).__name__}") from exc
        if r.status_code != 200 or not r.text.startswith("latitude"):
            # Do not echo the URL: it contains the key.
            raise NrtError(f"FIRMS returned HTTP {r.status_code}: {r.text[:120].strip()}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(r.text)
        return r.text

    def fetch(self, aoi: AOI, days: int, sensors: list[str]) -> list[dict]:
        if not self.key:
            raise NrtError("FIRMS_MAP_KEY is not configured on the server")
        if not 1 <= days <= MAX_DAYS:
            raise ValueError(f"days must be in 1..{MAX_DAYS}")
        if aoi.kind == "region":
            boxes = US_BOXES
        else:
            w, s, e, n = aoi.bbox
            boxes = [f"{w:.4f},{s:.4f},{e:.4f},{n:.4f}"]
        rows: list[dict] = []
        with httpx.Client(timeout=60, transport=self.transport) as client:
            for sensor in sensors:
                for bbox in boxes:
                    for start, n in self._chunks(days):
                        for r in csv.DictReader(io.StringIO(self._get(client, SOURCES[sensor], bbox, start, n))):
                            r["sensor"] = sensor
                            rows.append(r)
        return filter_aoi(rows, aoi)


def filter_aoi(rows: list[dict], aoi: AOI) -> list[dict]:
    if not rows or aoi.kind in ("region", "bbox"):
        return rows  # the request bbox already is the AOI (region: US boxes)
    lat = np.array([float(r["latitude"]) for r in rows])
    lon = np.array([float(r["longitude"]) for r in rows])
    if aoi.kind == "circle":
        clat, clon = aoi.center
        p1, p2 = np.radians(lat), math.radians(clat)
        dlat, dlon = p1 - p2, np.radians(lon - clon)
        h = np.sin(dlat / 2) ** 2 + math.cos(p2) * np.cos(p1) * np.sin(dlon / 2) ** 2
        keep = 2 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(h)) <= aoi.radius_km
    else:
        keep = shapely.contains_xy(aoi.geometry, lon, lat)
    return [r for r, k in zip(rows, keep) if k]


def to_feature(r: dict) -> dict:
    def num(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None
    conf = r.get("confidence", "")
    if r["sensor"] == "VIIRS":
        cn = {"l": "low", "n": "nominal", "h": "high"}.get(conf.lower(), conf)
    else:
        c = num(conf)
        cn = None if c is None else ("low" if c < 30 else "nominal" if c < 80 else "high")
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [round(float(r["longitude"]), 5), round(float(r["latitude"]), 5)]},
        "properties": {
            "sensor": r["sensor"], "level": "nrt", "live": True, "date": r.get("acq_date"),
            "time_utc": (r.get("acq_time") or "").zfill(4), "frp": num(r.get("frp")),
            "confidence": cn, "daynight": r.get("daynight"), "type": None,
        },
    }
