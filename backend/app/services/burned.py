"""Burned area and post-fire assessment.

Sources (queried live per area, cached on disk):
  * NIFC WFIGS Interagency Perimeters: mapped final/current perimeters of reported fires, 2020 onward
    (wildfires WF and prescribed fires RX), including fires still burning (percent contained).
  * MTBS Burned Area Boundaries (USFS/USGS): large fires mapped from Landsat, 1984 onward
    (≥1,000 acres in the West, ≥500 in the East). Used for the long record and for years before 2020.
  * MTBS thematic burn severity (MRLC WCS), CONUS and Alaska, through 2021: unburned-to-low, low,
    moderate, high, increased greenness.

Post-fire assessment of one fire combines its perimeter with this app's own data: satellite detections
inside the perimeter (fire duration, intensity, daily progression), burn severity, land cover burned and
mapped assets inside the perimeter (places, hospitals, schools, fire stations, airports, roads).
"""
from __future__ import annotations

import io
import json
import logging
import math
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from pathlib import Path

import httpx
import numpy as np
import shapely
from pyproj import Transformer
from shapely.geometry import mapping, shape
from shapely.ops import transform as shp_transform

from app.services.impact import esri_geometry, polygonal

WFIGS_URL = ("https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/"
             "WFIGS_Interagency_Perimeters/FeatureServer/0/query")
MTBS_URL = "https://apps.fs.usda.gov/arcx/rest/services/EDW/EDW_MTBS_01/MapServer/63/query"
MRLC_WCS = "https://www.mrlc.gov/geoserver/ows"
WFIGS_START = 2020
MTBS_START = 1984
SEVERITY_LAST_YEAR = 2021  # latest MTBS severity mosaic on MRLC
NLCD_EPOCHS = (2001, 2004, 2006, 2008, 2011, 2013, 2016, 2019, 2021)  # MRLC NLCD L48 releases


def pre_fire_nlcd(year: int) -> int:
    """Latest NLCD epoch before the fire year, so burned forest is not already mapped as grass/shrub."""
    return max([e for e in NLCD_EPOCHS if e < year], default=NLCD_EPOCHS[0])


SEVERITY = {1: "Unburned to low", 2: "Low", 3: "Moderate", 4: "High", 5: "Increased greenness", 6: "Non-processing area"}
ACRES_PER_KM2 = 247.105
MAX_FIRES = 2000
UA = {"User-Agent": "firewatch360-prototype"}
SOURCES = {
    "wfigs": "NIFC WFIGS Interagency Perimeters (2020+)",
    "mtbs": "MTBS Burned Area Boundaries, USFS/USGS (1984+, large fires)",
}


class BurnedError(RuntimeError):
    pass


class BurnedClient:
    def __init__(self, cache_dir: Path, client: httpx.Client | None = None, today: date | None = None):
        self.cache_dir = Path(cache_dir)
        self._client = client
        self._today = today

    def today(self) -> date:
        return self._today or datetime.now(timezone.utc).date()

    def http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(headers=UA, timeout=120, follow_redirects=True)
        return self._client

    def _cached(self, key: str, fresh_for_s: float | None, fn):
        import hashlib
        path = self.cache_dir / (hashlib.sha1(key.encode()).hexdigest()[:20] + ".json")
        if path.exists() and (fresh_for_s is None or time.time() - path.stat().st_mtime < fresh_for_s):
            return json.loads(path.read_text())
        val = fn()
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(val))
        return val

    def _ttl(self, year: int) -> float | None:
        return 3 * 3600 if year >= self.today().year - 1 else None

    def _post(self, url: str, params: dict) -> dict:
        try:
            r = self.http().post(url, data={"f": "json", **params})
            r.raise_for_status()
            body = r.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise BurnedError(f"Perimeter service unavailable: {type(exc).__name__}") from exc
        if "error" in body:
            raise BurnedError(f"Perimeter service error: {body['error'].get('message', '')[:120]}")
        return body

    # ------------------------------------------------------------ queries

    def _geom_params(self, area: shapely.Geometry) -> dict:
        gtype, gjson = esri_geometry(area)
        return {"geometry": gjson, "geometryType": gtype, "inSR": "4326", "spatialRel": "esriSpatialRelIntersects"}

    def _features(self, url: str, where: str, fields: str, area: shapely.Geometry, order: str,
                  offset_deg: float) -> list[dict]:
        feats, start = [], 0
        while len(feats) < MAX_FIRES:
            body = self._post(url, {"where": where, "outFields": fields, "orderByFields": order, "returnGeometry": "true",
                                    "outSR": "4326", "maxAllowableOffset": offset_deg, "geometryPrecision": 5,
                                    "resultOffset": start, "resultRecordCount": 1000, **self._geom_params(area)})
            page = body.get("features", [])
            feats += page
            if not body.get("exceededTransferLimit") or not page:
                break
            start += len(page)
        return feats[:MAX_FIRES]

    def fires(self, area: shapely.Geometry, year: int, offset_deg: float = 0.002) -> list[dict]:
        """Fires intersecting the area that started in `year`, largest first, with lon/lat geometry."""
        def build():
            if year >= WFIGS_START:
                where = (f"attr_FireDiscoveryDateTime >= TIMESTAMP '{year}-01-01 00:00:00' AND "
                         f"attr_FireDiscoveryDateTime < TIMESTAMP '{year + 1}-01-01 00:00:00' "
                         "AND attr_IncidentTypeCategory IN ('WF', 'RX')")
                raw = self._features(WFIGS_URL, where, "OBJECTID,poly_IncidentName,poly_GISAcres,attr_FireDiscoveryDateTime,"
                                     "attr_ContainmentDateTime,attr_PercentContained,attr_IncidentTypeCategory,attr_FireCause",
                                     area, "poly_GISAcres DESC", offset_deg)
                out = []
                for f in raw:
                    a = f["attributes"]
                    disc = a.get("attr_FireDiscoveryDateTime")
                    cont = a.get("attr_ContainmentDateTime")
                    out.append({"source": "wfigs", "id": a["OBJECTID"], "name": (a.get("poly_IncidentName") or "Unnamed").strip(),
                                "acres": a.get("poly_GISAcres"), "type": "Wildfire" if a.get("attr_IncidentTypeCategory") == "WF" else "Prescribed fire",
                                "start": _iso(disc), "contained": _iso(cont), "percent_contained": a.get("attr_PercentContained"),
                                "cause": a.get("attr_FireCause"), "rings": f.get("geometry", {}).get("rings")})
                return out
            raw = self._features(MTBS_URL, f"year = {year}", "objectid,fire_id,fire_name,acres,ig_date,fire_type", area,
                                 "acres DESC", offset_deg)
            out = []
            for f in raw:
                a = f["attributes"]
                out.append({"source": "mtbs", "id": a["objectid"], "mtbs_id": a.get("fire_id"),
                            "name": (a.get("fire_name") or "Unnamed").strip().title(), "acres": a.get("acres"),
                            "type": {"WF": "Wildfire", "RX": "Prescribed fire", "WFU": "Wildland fire use"}.get(a.get("fire_type"), a.get("fire_type")),
                            "start": _iso(a.get("ig_date")), "contained": None, "percent_contained": None, "cause": None,
                            "rings": f.get("geometry", {}).get("rings")})
            return out
        return self._cached(f"fires|{area.wkt}|{year}|{offset_deg}", self._ttl(year), build)

    def yearly(self, area: shapely.Geometry) -> list[dict]:
        """Acres and fire counts per year for fires intersecting the area (whole-fire acres)."""
        def mtbs():
            body = self._post(MTBS_URL, {"where": "1=1", "groupByFieldsForStatistics": "year", "returnGeometry": "false",
                                         "outStatistics": json.dumps([
                                             {"statisticType": "sum", "onStatisticField": "acres", "outStatisticFieldName": "acres"},
                                             {"statisticType": "count", "onStatisticField": "objectid", "outStatisticFieldName": "n"}]),
                                         **self._geom_params(area)})
            return {int(f["attributes"]["year"]): (f["attributes"]["acres"] or 0, f["attributes"]["n"]) for f in body.get("features", [])}

        def wfigs(y):
            where = (f"attr_FireDiscoveryDateTime >= TIMESTAMP '{y}-01-01 00:00:00' AND "
                     f"attr_FireDiscoveryDateTime < TIMESTAMP '{y + 1}-01-01 00:00:00' AND attr_IncidentTypeCategory IN ('WF', 'RX')")
            body = self._post(WFIGS_URL, {"where": where, "returnGeometry": "false", "outStatistics": json.dumps([
                {"statisticType": "sum", "onStatisticField": "poly_GISAcres", "outStatisticFieldName": "acres"},
                {"statisticType": "count", "onStatisticField": "OBJECTID", "outStatisticFieldName": "n"}]), **self._geom_params(area)})
            a = (body.get("features") or [{"attributes": {}}])[0]["attributes"]
            return y, (a.get("acres") or 0, a.get("n") or 0)

        def build():
            m = mtbs()
            years = list(range(WFIGS_START, self.today().year + 1))
            with ThreadPoolExecutor(4) as ex:
                w = dict(ex.map(wfigs, years))
            out = []
            for y in range(MTBS_START, self.today().year + 1):
                ma, mn = m.get(y, (0, 0))
                row = {"year": y, "mtbs_acres": round(ma), "mtbs_fires": mn}
                if y in w:
                    row.update(wfigs_acres=round(w[y][0]), wfigs_fires=w[y][1])
                out.append(row)
            return out
        return self._cached(f"yearly|{area.wkt}|{self.today()}", 3 * 3600, build)

    def fire(self, source: str, fid: int) -> dict:
        """One fire with a detailed geometry (for the post-fire assessment)."""
        if source not in SOURCES:
            raise BurnedError("source must be wfigs or mtbs")
        url, idf = (WFIGS_URL, "OBJECTID") if source == "wfigs" else (MTBS_URL, "objectid")

        def build():
            body = self._post(url, {"where": f"{idf} = {int(fid)}", "outFields": "*", "returnGeometry": "true",
                                    "outSR": "4326", "maxAllowableOffset": 0.0003, "geometryPrecision": 6})
            feats = body.get("features", [])
            if not feats:
                raise BurnedError("Fire not found")
            return {"attributes": feats[0]["attributes"], "rings": feats[0]["geometry"]["rings"]}
        return self._cached(f"fire|{source}|{fid}", 3 * 3600 if source == "wfigs" else None, build)

    def severity(self, geom: shapely.Geometry, year: int, max_px: int = 1500) -> dict:
        """MTBS burn severity class areas (acres) inside geom, from the MRLC mosaic for `year`."""
        if year < MTBS_START or year > SEVERITY_LAST_YEAR:
            return {"status": "not_available",
                    "message": f"MTBS burn severity mosaics on MRLC cover {MTBS_START}–{SEVERITY_LAST_YEAR}."}
        c = geom.centroid
        region = "AK" if c.y > 51 and c.x < -129 else "CONUS"

        def build():
            to3857 = Transformer.from_crs(4326, 3857, always_xy=True)
            g = shp_transform(to3857.transform, geom)
            x0, y0, x1, y1 = g.bounds
            side = max(x1 - x0, y1 - y0)
            scale = min(1.0, max_px * 30.0 / side)
            r = self.http().get(MRLC_WCS, params=[
                ("service", "WCS"), ("version", "2.0.1"), ("request", "GetCoverage"),
                ("coverageId", f"mtbs__mtbs_{region}_{year}"), ("subset", f"X({x0},{x1})"), ("subset", f"Y({y0},{y1})"),
                ("scaleFactor", f"{scale:.6f}"), ("format", "image/geotiff")])
            if r.status_code != 200 or not r.headers.get("content-type", "").startswith("image"):
                raise BurnedError(f"MTBS severity service returned HTTP {r.status_code}")
            import tifffile
            logging.getLogger("tifffile").setLevel(logging.ERROR)
            arr = tifffile.imread(io.BytesIO(r.content))
            h, w = arr.shape[:2]
            px, py = (x1 - x0) / w, (y1 - y0) / h
            X, Y = np.meshgrid(x0 + (np.arange(w) + 0.5) * px, y1 - (np.arange(h) + 0.5) * py)
            inside = shapely.contains_xy(g, X, Y)
            # Web Mercator stretches areas by 1/cos²(lat); correct per pixel row.
            lat = np.degrees(2 * np.arctan(np.exp(Y / 6378137.0)) - math.pi / 2)
            px_km2 = px * py / 1e6 * np.cos(np.radians(lat)) ** 2
            out = {}
            for k, label in SEVERITY.items():
                m = inside & (arr == k)
                if m.any():
                    out[label] = round(float(px_km2[m].sum()) * ACRES_PER_KM2)
            mapped = sum(v for k, v in out.items() if k != "Non-processing area")
            return {"status": "ok" if mapped else "no_data", "year": year, "acres_by_class": out,
                    "pixel_m": round(px * math.cos(math.radians(c.y)), 1),
                    "source": f"MTBS thematic burn severity {region} {year} (MRLC)"}
        try:
            return self._cached(f"sev|{geom.wkt}|{year}|{max_px}", None, build)
        except (httpx.HTTPError, BurnedError) as exc:
            return {"status": "unavailable", "message": str(exc)}


def _iso(ms) -> str | None:
    if ms is None:
        return None
    if isinstance(ms, str):
        return ms[:10]
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).date().isoformat()


def rings_to_geom(rings: list | None) -> shapely.Geometry | None:
    """ArcGIS rings (outer clockwise, holes counter-clockwise) -> valid shapely (Multi)Polygon."""
    if not rings:
        return None
    from app.services.impact import esri_polygon
    return esri_polygon(rings)


def area_summary(fires: list[dict], area: shapely.Geometry) -> dict:
    """Union of perimeters clipped to the area: acres burned inside it (overlaps counted once)."""
    geoms = [g for g in (rings_to_geom(f["rings"]) for f in fires) if g is not None and not g.is_empty]
    if not geoms:
        return {"acres_in_area": 0, "pct_of_area": 0.0}
    burned = polygonal(shapely.intersection(shapely.union_all(geoms), area))
    c = area.centroid
    fwd = Transformer.from_crs(4326, f"+proj=laea +lat_0={c.y} +lon_0={c.x} +units=m", always_xy=True)
    km2 = shp_transform(fwd.transform, burned).area / 1e6
    area_km2 = shp_transform(fwd.transform, area).area / 1e6
    return {"acres_in_area": round(km2 * ACRES_PER_KM2), "km2_in_area": round(km2, 1),
            "pct_of_area": round(100 * km2 / area_km2, 2) if area_km2 else 0.0}


def fires_geojson(fires: list[dict]) -> dict:
    feats = []
    for f in fires:
        g = rings_to_geom(f["rings"])
        if g is None or g.is_empty:
            continue
        props = {k: v for k, v in f.items() if k != "rings"}
        feats.append({"type": "Feature", "geometry": mapping(g), "properties": props})
    return {"type": "FeatureCollection", "features": feats}


def geom_from_feature(fire: dict) -> shapely.Geometry:
    g = rings_to_geom(fire["rings"])
    if g is None or g.is_empty:
        raise BurnedError("Fire has no perimeter geometry")
    return g


__all__ = ["BurnedClient", "BurnedError", "area_summary", "fires_geojson", "geom_from_feature", "shape"]
