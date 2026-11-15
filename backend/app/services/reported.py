"""Reported fires from NIFC: WFIGS Incident Locations (IRWIN), queried live per area.

Each record is one fire incident reported by a US agency, with point of origin, discovery date,
size (acres), cause and name. Coverage is essentially 2014 onward (IRWIN). Wildfires (WF) and
prescribed fires (RX) are counted separately. Counts are by discovery date (UTC).

These are reported incidents, not satellite detections: small or remote fires may be missing,
and one large fire produces many satellite detections but one incident.
"""
from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx
import shapely

from app.services.impact import esri_geometry

URL = ("https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/"
       "WFIGS_Incident_Locations/FeatureServer/0/query")
SOURCE = "NIFC WFIGS Incident Locations (IRWIN), reported wildland fire incidents"
COVERAGE_START = date(2014, 1, 1)  # IRWIN records before 2014 are near-empty
UA = {"User-Agent": "firewatch360-prototype"}


class ReportedError(RuntimeError):
    pass


def _where(start: date, end: date, category: str | None = None) -> str:
    """Discovery date in [start, end] (inclusive, UTC)."""
    w = (f"FireDiscoveryDateTime >= TIMESTAMP '{start.isoformat()} 00:00:00' AND "
         f"FireDiscoveryDateTime < TIMESTAMP '{(end + timedelta(days=1)).isoformat()} 00:00:00'")
    return w + (f" AND IncidentTypeCategory = '{category}'" if category else "")


class ReportedClient:
    def __init__(self, cache_dir: Path, client: httpx.Client | None = None, today: date | None = None):
        self.cache_dir = Path(cache_dir)
        self._client = client
        self._today = today

    def today(self) -> date:
        return self._today or datetime.now(timezone.utc).date()

    def http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(headers=UA, timeout=90)
        return self._client

    def _post(self, geom: shapely.Geometry | None, **params) -> dict:
        body = {"f": "json", **params}
        if geom is not None:
            gtype, gjson = esri_geometry(geom)
            body.update(geometry=gjson, geometryType=gtype, inSR="4326", spatialRel="esriSpatialRelIntersects")
        try:
            r = self.http().post(URL, data=body)
            r.raise_for_status()
            d = r.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ReportedError("Could not reach the NIFC incident service") from exc
        if "error" in d:
            raise ReportedError(f"NIFC incident service error: {d['error'].get('message', 'unknown')}")
        return d

    def _cached(self, key: str, fn, max_age_s: float | None):
        path = self.cache_dir / f"{key}.json"
        if path.exists() and (max_age_s is None or time.time() - path.stat().st_mtime < max_age_s):
            return json.loads(path.read_text())
        out = fn()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(out))
        return out

    def _age(self, end: date) -> float | None:
        """Past periods are final enough to cache for good; recent ones refresh hourly."""
        return None if (self.today() - end).days > 60 else 3600

    def summary(self, geom: shapely.Geometry | None, key: str, start: date, end: date, top: int = 10,
                points: int = 1000) -> dict:
        def build():
            stats = self._post(geom, where=_where(start, end), groupByFieldsForStatistics="IncidentTypeCategory",
                               outStatistics=json.dumps([
                                   {"statisticType": "count", "onStatisticField": "OBJECTID", "outStatisticFieldName": "n"},
                                   {"statisticType": "sum", "onStatisticField": "IncidentSize", "outStatisticFieldName": "acres"}]))
            by_type = {f["attributes"]["IncidentTypeCategory"]: f["attributes"] for f in stats.get("features", [])}
            wf = by_type.get("WF", {})
            causes = self._post(geom, where=_where(start, end, "WF"), groupByFieldsForStatistics="FireCause",
                                outStatistics=json.dumps([{"statisticType": "count", "onStatisticField": "OBJECTID",
                                                           "outStatisticFieldName": "n"}]))
            cause = {}
            for f in causes.get("features", []):
                c = f["attributes"]["FireCause"] or "Not reported"
                c = "Unknown" if c in ("Undetermined", "Unknown") else c
                cause[c] = cause.get(c, 0) + int(f["attributes"]["n"])
            pts = self._post(geom, where=_where(start, end, "WF"), outSR="4326", returnGeometry="true",
                             outFields="IncidentName,FireDiscoveryDateTime,IncidentSize,FireCause,POOState,POOCounty,IrwinID",
                             orderByFields="IncidentSize DESC", resultRecordCount=str(points))
            feats = []
            for f in pts.get("features", []):
                a, g = f["attributes"], f.get("geometry")
                if not g:
                    continue
                feats.append({"type": "Feature",
                              "geometry": {"type": "Point", "coordinates": [round(g["x"], 5), round(g["y"], 5)]},
                              "properties": {
                                  "name": (a["IncidentName"] or "").title(),
                                  "date": datetime.fromtimestamp(a["FireDiscoveryDateTime"] / 1000, timezone.utc).date().isoformat()
                                  if a["FireDiscoveryDateTime"] else None,
                                  "acres": a["IncidentSize"], "cause": a["FireCause"],
                                  "state": (a["POOState"] or "").replace("US-", ""), "county": a["POOCounty"]}})
            n_wf = int(wf.get("n") or 0)
            return {
                "status": "ok", "source": SOURCE,
                "window": [start.isoformat(), end.isoformat()],
                "wildfires": n_wf, "wildfire_acres": round(float(wf.get("acres") or 0)),
                "prescribed": int((by_type.get("RX") or {}).get("n") or 0),
                "causes": dict(sorted(cause.items(), key=lambda kv: -kv[1])),
                "largest": [f["properties"] for f in feats[:top]],
                "geojson": {"type": "FeatureCollection", "features": feats},
                "points_shown": len(feats), "points_capped": n_wf > len(feats),
            }
        if end < COVERAGE_START:
            return {"status": "outside_coverage", "message": "NIFC incident reports start in 2014.", "source": SOURCE}
        out = self._cached(f"sum_{key}_{start}_{end}_{top}_{points}", build, self._age(end))
        if start < COVERAGE_START:
            out = {**out, "note": "NIFC incident reports start in 2014; earlier dates are not counted."}
        return out

    def monthly(self, geom: shapely.Geometry | None, key: str, first: date, last: date) -> list[dict]:
        """Reported wildfire count per month, first..last (month starts). One statistics query per month."""
        months = []
        d = max(first, COVERAGE_START).replace(day=1)
        while d <= last:
            nxt = (d.replace(day=28) + timedelta(days=4)).replace(day=1)
            months.append((d, nxt - timedelta(days=1)))
            d = nxt

        def one(m: tuple[date, date]) -> dict:
            s, e = m
            def build():
                r = self._post(geom, where=_where(s, e, "WF"), outStatistics=json.dumps([
                    {"statisticType": "count", "onStatisticField": "OBJECTID", "outStatisticFieldName": "n"},
                    {"statisticType": "sum", "onStatisticField": "IncidentSize", "outStatisticFieldName": "acres"}]))
                a = (r.get("features") or [{"attributes": {}}])[0]["attributes"]
                return {"month": s.strftime("%Y-%m"), "wildfires": int(a.get("n") or 0), "acres": round(float(a.get("acres") or 0))}
            return self._cached(f"mon_{key}_{s:%Y-%m}", build, self._age(e))

        with ThreadPoolExecutor(max_workers=8) as ex:
            return list(ex.map(one, months))
