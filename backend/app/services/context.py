"""Drought and fire-weather context for an area (US only).

Drought: U.S. Drought Monitor weekly maps (NDMC / USDA / NOAA, public), one GeoJSON per week
since 2000 with non-overlapping D0-D4 polygons. For a month we use the last weekly map dated in
that month. Share of the area in each category is computed in an equal-distance projection
centred on the area. DSCI (Drought Severity and Coverage Index, 0-500) = sum of the cumulative
shares D0+ .. D4+ in percent.

Fire weather: active NWS Red Flag Warnings and Fire Weather Watches (api.weather.gov) whose
zones intersect the area. Current conditions only; NWS keeps no public history here.

Context only: these are not fire-danger forecasts from this app.
"""
from __future__ import annotations

import json
import logging
import pickle
import time
from datetime import date, timedelta
from pathlib import Path

import httpx
import numpy as np
import shapely
from shapely.geometry import shape
from shapely.ops import transform as shp_transform

log = logging.getLogger(__name__)

USDM_JSON = "https://droughtmonitor.unl.edu/data/json/usdm_{d}.json"
NWS_ALERTS = "https://api.weather.gov/alerts/active"
UA = {"User-Agent": "firewatch360-prototype (fire activity research app)"}
GEOJSON = {"Accept": "application/geo+json"}  # NWS only; the drought server rejects it
CATEGORIES = ["D0", "D1", "D2", "D3", "D4"]
LABELS = {"D0": "Abnormally dry", "D1": "Moderate drought", "D2": "Severe drought",
          "D3": "Extreme drought", "D4": "Exceptional drought"}
FIRE_WEATHER_EVENTS = ("Red Flag Warning", "Fire Weather Watch")
FIRST_USDM = date(2000, 1, 4)


def usdm_week_for_month(month: str, today: date) -> date | None:
    """Last Tuesday in `month` that is already published (maps come out Thursday for Tuesday)."""
    y, m = map(int, month.split("-"))
    nxt = date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1)
    d = nxt - timedelta(days=1)
    d -= timedelta(days=(d.weekday() - 1) % 7)  # back to Tuesday
    latest = today - timedelta(days=2)
    latest -= timedelta(days=(latest.weekday() - 1) % 7)
    d = min(d, latest)
    if d < FIRST_USDM or (d.year, d.month) != (y, m):
        return None
    return d


def latest_usdm_week(today: date) -> date:
    d = today - timedelta(days=2)
    return d - timedelta(days=(d.weekday() - 1) % 7)


class ContextClient:
    def __init__(self, cache_dir: Path, client: httpx.Client | None = None, today: date | None = None):
        self.cache_dir = Path(cache_dir)
        self._client = client
        self._today = today
        self._mem: dict[str, list] = {}

    def today(self) -> date:
        return self._today or date.today()

    def http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(headers=UA, timeout=120, follow_redirects=True)
        return self._client

    # ------------------------------------------------------------ drought
    def _week(self, d: date) -> list[tuple[str, shapely.Geometry]]:
        key = d.strftime("%Y%m%d")
        if key in self._mem:
            return self._mem[key]
        path = self.cache_dir / "usdm" / f"{key}.pkl"
        if path.exists():
            data = pickle.loads(path.read_bytes())
        else:
            r = self.http().get(USDM_JSON.format(d=key))
            r.raise_for_status()
            data = []
            for f in r.json()["features"]:
                cat = int(f["properties"]["DM"])
                g = shapely.make_valid(shape(f["geometry"]).simplify(0.005))
                data.append((f"D{cat}", shapely.to_wkb(g)))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(pickle.dumps(data))
        geoms = [(c, shapely.from_wkb(w)) for c, w in data]
        for _, g in geoms:
            shapely.prepare(g)
        if len(self._mem) > 12:
            self._mem.clear()
        self._mem[key] = geoms
        return geoms

    def drought(self, geom_ll: shapely.Geometry, fwd, week: date) -> dict:
        """Share of the area in each drought category for one weekly map."""
        area_geom = shp_transform(fwd.transform, geom_ll)
        total = area_geom.area
        w, s, e, n = geom_ll.bounds
        box = shapely.box(w - 0.1, s - 0.1, e + 0.1, n + 0.1)
        share = {}
        for cat, g in self._week(week):
            if not g.intersects(box):
                share[cat] = 0.0
                continue
            part = shapely.intersection(g, geom_ll)
            share[cat] = 0.0 if part.is_empty else shp_transform(fwd.transform, part).area / total
        exclusive = {c: share.get(c, 0.0) for c in CATEGORIES}
        cumulative = {f"{c}+": sum(exclusive[x] for x in CATEGORIES[i:]) for i, c in enumerate(CATEGORIES)}
        dsci = round(100 * sum(cumulative.values()))
        worst = next((c for c in reversed(CATEGORIES) if exclusive[c] >= 0.01), None)
        in_drought = cumulative["D1+"]
        if in_drought >= 0.005:
            text = f"{in_drought * 100:.0f}% of the area in drought (D1 or worse)"
            if worst and worst != "D0":
                text += f", worst: {LABELS[worst]} ({worst})"
        else:
            text = "No drought (D1 or worse) mapped in this area"
            if exclusive["D0"] >= 0.01:
                text += f"; {exclusive['D0'] * 100:.0f}% abnormally dry (D0)"
        return {
            "week": week.isoformat(),
            "share": {c: round(v, 4) for c, v in exclusive.items()},
            "cumulative": {c: round(v, 4) for c, v in cumulative.items()},
            "dsci": dsci,
            "worst_category": worst,
            "worst_label": LABELS.get(worst, "No drought"),
            "text": text,
        }

    def drought_history(self, geom_ll, fwd, month: str, years: list[int]) -> list[dict]:
        out = []
        for y in years:
            wk = usdm_week_for_month(f"{y:04d}-{month[5:]}", self.today())
            if wk is None:
                continue
            try:
                d = self.drought(geom_ll, fwd, wk)
                out.append({"year": y, "week": d["week"], "dsci": d["dsci"], "d1_plus": d["cumulative"]["D1+"]})
            except httpx.HTTPError:
                continue
        return out

    # ------------------------------------------------------------ fire weather
    def _zone_geometry(self, url: str) -> shapely.Geometry | None:
        path = self.cache_dir / "nws_zones" / (url.rstrip("/").split("/")[-1] + ".json")
        if path.exists():
            gj = json.loads(path.read_text())
        else:
            r = self.http().get(url, headers=GEOJSON)
            if r.status_code != 200:
                return None
            gj = r.json().get("geometry")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(gj))
        return shapely.make_valid(shape(gj)) if gj else None

    def fire_weather(self, geom_ll: shapely.Geometry) -> dict:
        path = self.cache_dir / "nws_active.json"
        if path.exists() and time.time() - path.stat().st_mtime < 900:
            feats = json.loads(path.read_text())
        else:
            feats = []
            for ev in FIRE_WEATHER_EVENTS:
                r = self.http().get(NWS_ALERTS, params={"event": ev}, headers=GEOJSON)
                r.raise_for_status()
                feats += r.json().get("features", [])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(feats))
        hits = []
        for f in feats:
            p = f["properties"]
            geoms = [shapely.make_valid(shape(f["geometry"]))] if f.get("geometry") else \
                [g for g in (self._zone_geometry(z) for z in p.get("affectedZones", [])[:40]) if g is not None]
            if any(g.intersects(geom_ll) for g in geoms):
                hits.append({"event": p["event"], "headline": p.get("headline"), "area": p.get("areaDesc"),
                             "onset": p.get("onset"), "ends": p.get("ends") or p.get("expires"),
                             "sender": p.get("senderName")})
        return {"status": "ok", "as_of": time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime()),
                "active": hits, "n_red_flag": sum(h["event"] == "Red Flag Warning" for h in hits),
                "n_watch": sum(h["event"] == "Fire Weather Watch" for h in hits)}


def percentile_of(value: float, history: list[float]) -> int | None:
    if not history:
        return None
    return int(round(100 * float(np.mean(np.array(history) <= value))))
