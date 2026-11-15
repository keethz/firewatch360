"""Smoke: NOAA Hazard Mapping System (HMS) smoke plumes, analysed per day by NOAA analysts.

Each day NOAA analysts outline smoke seen in GOES and polar-orbiter imagery and grade it Light, Medium
or Heavy (nominally about 5, 16 and 27 ug/m3 of PM2.5 in the column). These are smoke *in the
atmosphere* seen from space, not ground-level air quality: smoke aloft can sit over clean surface air
and vice versa. Files: https://satepsanone.nesdis.noaa.gov/pub/FIRE/web/HMS/Smoke_Polygons/

Daily files are cached on disk; days older than 3 days are final and never refetched.
"""
from __future__ import annotations

import io
import logging
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx
import shapefile
import shapely
from pyproj import Transformer
from shapely.geometry import mapping, shape
from shapely.ops import transform as shp_transform

BASE = "https://satepsanone.nesdis.noaa.gov/pub/FIRE/web/HMS/Smoke_Polygons/Shapefile"
SOURCE = "NOAA NESDIS Hazard Mapping System (HMS) smoke analysis"
DENSITIES = ("Light", "Medium", "Heavy")
PM25 = {"Light": 5, "Medium": 16, "Heavy": 27}  # nominal ug/m3 per HMS documentation
UA = {"User-Agent": "firewatch360-prototype"}
# HMS rings are drawn counter-clockwise; pyshp handles that but warns once per shape.
logging.getLogger("shapefile").setLevel(logging.ERROR)
MAX_DAYS = 31


class SmokeClient:
    def __init__(self, cache_dir: Path, client: httpx.Client | None = None, today: date | None = None):
        self.cache_dir = Path(cache_dir)
        self._client = client
        self._today = today

    def today(self) -> date:
        return self._today or datetime.now(timezone.utc).date()

    def http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(headers=UA, timeout=60, follow_redirects=True)
        return self._client

    def _zip(self, d: date) -> bytes | None:
        """The day's shapefile zip, or None when NOAA has not published it (yet)."""
        path = self.cache_dir / f"hms_smoke{d:%Y%m%d}.zip"
        missing = path.with_suffix(".missing")
        recent = (self.today() - d).days <= 3
        if path.exists() and (not recent or time.time() - path.stat().st_mtime < 3 * 3600):
            return path.read_bytes()
        if missing.exists() and time.time() - missing.stat().st_mtime < 3600:
            return None
        r = self.http().get(f"{BASE}/{d:%Y}/{d:%m}/hms_smoke{d:%Y%m%d}.zip")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        if r.status_code == 404:
            missing.touch()
            return None
        r.raise_for_status()
        path.write_bytes(r.content)
        missing.unlink(missing_ok=True)
        return r.content

    def plumes(self, d: date) -> list[dict] | None:
        """[{density, satellite, start, end, geom}] for the day (lon/lat), or None if unavailable."""
        raw = self._zip(d)
        if raw is None:
            return None
        z = zipfile.ZipFile(io.BytesIO(raw))
        names = {n.rsplit(".", 1)[-1].lower(): n for n in z.namelist()}
        rd = shapefile.Reader(shp=io.BytesIO(z.read(names["shp"])), shx=io.BytesIO(z.read(names["shx"])),
                              dbf=io.BytesIO(z.read(names["dbf"])))
        out = []
        for sr in rd.iterShapeRecords():
            rec = sr.record.as_dict()
            dens = str(rec.get("Density", "")).strip().title()
            if dens not in PM25:
                continue
            g = shapely.make_valid(shape(sr.shape.__geo_interface__))
            if g.is_empty:
                continue
            out.append({"density": dens, "satellite": rec.get("Satellite"), "start": rec.get("Start"),
                        "end": rec.get("End"), "geom": g})
        return out

    def latest_day(self) -> date:
        """Most recent day with a published analysis (today or up to 5 days back)."""
        for back in range(6):
            d = self.today() - timedelta(days=back)
            if self._zip(d) is not None:
                return d
        raise RuntimeError("No HMS smoke analysis published in the last 6 days")


def _area_km2(g: shapely.Geometry, fwd: Transformer) -> float:
    return shp_transform(fwd.transform, g).area / 1e6


def day_summary(plumes: list[dict], area: shapely.Geometry, fwd: Transformer, area_km2: float) -> dict:
    """Share of the area under each density (the heaviest plume wins where they overlap)."""
    covered = {}
    above = shapely.Polygon()
    for dens in reversed(DENSITIES):  # Heavy first
        polys = [p["geom"] for p in plumes if p["density"] == dens]
        if not polys:
            covered[dens] = 0.0
            continue
        u = shapely.intersection(shapely.union_all(polys), area)
        own = shapely.difference(u, above) if not above.is_empty else u
        covered[dens] = round(100 * _area_km2(own, fwd) / area_km2, 1) if area_km2 > 0 else 0.0
        above = shapely.union(above, u)
    any_pct = round(sum(covered.values()), 1)
    worst = next((d for d in reversed(DENSITIES) if covered[d] > 0), None)
    return {"pct_covered": {k: covered[k] for k in DENSITIES}, "pct_any": min(any_pct, 100.0), "worst": worst}


def summarize(client: SmokeClient, area: shapely.Geometry, day: date | None, days: int) -> dict:
    """Smoke over `area` on `day` (default: latest published) and a daily series for the `days` before."""
    days = max(1, min(days, MAX_DAYS))
    day = day or client.latest_day()
    c = area.centroid
    fwd = Transformer.from_crs(4326, f"+proj=laea +lat_0={c.y} +lon_0={c.x} +units=m", always_xy=True)
    area_km2 = _area_km2(area, fwd)
    shapely.prepare(area)
    dates = [day - timedelta(days=i) for i in range(days - 1, -1, -1)]
    with ThreadPoolExecutor(6) as ex:
        loaded = dict(zip(dates, ex.map(client.plumes, dates)))

    series = []
    for d in dates:
        pl = loaded[d]
        if pl is None:
            series.append({"date": d.isoformat(), "status": "unavailable"})
            continue
        hits = [p for p in pl if shapely.intersects(area, p["geom"])]
        series.append({"date": d.isoformat(), "status": "ok", **day_summary(hits, area, fwd, area_km2)})

    today = loaded[day]
    if today is None:
        return {"status": "unavailable", "date": day.isoformat(), "message": f"No HMS smoke analysis for {day}.",
                "series": series, "source": SOURCE}
    hits = [p for p in today if shapely.intersects(area, p["geom"])]
    feats = [{"type": "Feature", "geometry": mapping(shapely.simplify(p["geom"], 0.01, preserve_topology=True)),
              "properties": {"density": p["density"], "pm25_nominal": PM25[p["density"]], "satellite": p["satellite"],
                             "start": p["start"], "end": p["end"]}}
             for p in sorted(hits, key=lambda p: DENSITIES.index(p["density"]))]  # heavy drawn last (on top)
    ok = [s for s in series if s["status"] == "ok"]
    smoky = [s for s in ok if s["pct_any"] >= 10]
    heavy = [s for s in ok if s["pct_covered"]["Heavy"] >= 10]
    summ = series[-1]
    if summ.get("worst"):
        headline = (f"{summ['pct_any']:.0f}% of this area was under satellite-observed smoke on {day:%d %b %Y}"
                    f" (heaviest: {summ['worst'].lower()}).")
    else:
        headline = f"No smoke plumes were analysed over this area on {day:%d %b %Y}."
    return {
        "status": "ok", "date": day.isoformat(), "headline": headline, "area_km2": round(area_km2, 1),
        "today": summ, "plumes": {"type": "FeatureCollection", "features": feats},
        "series": series,
        "days_with_smoke": len(smoky), "days_with_heavy_smoke": len(heavy), "days_analysed": len(ok),
        "source": SOURCE,
        "note": ("Analyst-drawn smoke in the atmospheric column from satellite imagery, not ground-level air quality. "
                 "Densities correspond nominally to 5 / 16 / 27 µg/m³ PM2.5. Days with ≥10% of the area covered count as smoke days."),
    }
