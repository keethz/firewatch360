"""Human-impact summary: what lies inside an area, and near its fire detections.

Layers and sources (all public; see docs/DATA_SOURCES.md):
  places, roads, airports, hospitals, schools, fire stations   static files built by scripts/prepare_impact.py
  population + WUI      USFS SILVIS WUI 2020 (2020 Census blocks), ArcGIS statistics query   CONUS
  protected areas       USGS PAD-US 3.0 (Fee Manager), ArcGIS query, clipped locally          50 states
  agriculture           NLCD 2021 land cover (MRLC WCS), classes 81 pasture/hay, 82 crops    CONUS

"Near fire" = within `buffer_km` of any fire detection in the date range (distances computed in
an azimuthal equidistant projection centred on the area). Every layer returns a `status`;
failures and unsupported areas are reported, never filled in.
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import math
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import httpx
import numpy as np
import pandas as pd
import shapely
from pyproj import Transformer
from shapely.geometry import box, mapping, shape
from shapely.ops import transform as shp_transform

from app.services.aoi import AOI

UA = {"User-Agent": "firewatch360-prototype/0.1"}
WUI_URL = "https://apps.fs.usda.gov/arcx/rest/services/EDW/EDW_WUI_2020_01/MapServer/0/query"
PADUS_URL = "https://services.arcgis.com/v01gqwM5QqNysAAi/ArcGIS/rest/services/Fee_Manager/FeatureServer/0/query"
NLCD_URL = "https://www.mrlc.gov/geoserver/ows"
NLCD_COVERAGE = "mrlc_download__NLCD_2021_Land_Cover_L48"
CONUS = box(-125.0, 24.0, -66.5, 49.5)
CACHE_VERSION = 1

# Area limits for the live services (km²). Larger areas get status "too_large".
MAX_WUI_KM2 = 1_200_000
MAX_PADUS_KM2 = 400_000
MAX_NLCD_KM2 = 1_500_000
NLCD_MAX_PX = 1500  # longest side of the requested land-cover raster

NLCD_CLASSES = {
    11: "Open water", 12: "Perennial ice/snow", 21: "Developed, open space", 22: "Developed, low intensity",
    23: "Developed, medium intensity", 24: "Developed, high intensity", 31: "Barren land", 41: "Deciduous forest",
    42: "Evergreen forest", 43: "Mixed forest", 51: "Dwarf scrub", 52: "Shrub/scrub", 71: "Grassland/herbaceous",
    72: "Sedge/herbaceous", 73: "Lichens", 74: "Moss", 81: "Pasture/hay", 82: "Cultivated crops",
    90: "Woody wetlands", 95: "Emergent herbaceous wetlands",
}
AGRICULTURE = (81, 82)

POINT_LAYERS = ("places", "airports", "hospitals", "schools", "firestations")


@dataclass
class Geo:
    """AOI geometry in lon/lat plus a local metric projection for distances and areas."""
    aoi: AOI
    geom: shapely.Geometry  # lon/lat
    fwd: Transformer
    geom_m: shapely.Geometry  # metres

    @property
    def area_km2(self) -> float:
        return self.geom_m.area / 1e6


def aoi_geometry(aoi: AOI) -> shapely.Geometry:
    if aoi.kind == "bbox":
        return box(*aoi.bbox)
    if aoi.kind == "polygon":
        return aoi.geometry
    if aoi.kind == "circle":
        lat, lon = aoi.center
        t = Transformer.from_crs(f"+proj=aeqd +lat_0={lat} +lon_0={lon} +units=m", 4326, always_xy=True)
        circle_m = shapely.Point(0, 0).buffer(aoi.radius_km * 1000, quad_segs=32)
        return shp_transform(t.transform, circle_m)
    raise ValueError("region AOI has no single geometry")


def make_geo(aoi: AOI) -> Geo:
    g = aoi_geometry(aoi)
    c = g.centroid
    fwd = Transformer.from_crs(4326, f"+proj=aeqd +lat_0={c.y} +lon_0={c.x} +units=m", always_xy=True)
    return Geo(aoi, g, fwd, shp_transform(fwd.transform, g))


def to_m(geo: Geo, lon: np.ndarray, lat: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    return geo.fwd.transform(np.asarray(lon, float), np.asarray(lat, float))


# ---------------------------------------------------------------- fire proximity

def fire_zone(geo: Geo, lon: np.ndarray, lat: np.ndarray, buffer_km: float, cell_m: float = 1000.0):
    """Union of buffers around fire detections, in metres. Detections are first snapped to
    `cell_m` cells (dedupe), which shifts each by at most ~0.7 km."""
    if len(lon) == 0:
        return None
    x, y = to_m(geo, lon, lat)
    cells = np.unique(np.stack([np.round(x / cell_m), np.round(y / cell_m)], axis=1), axis=0) * cell_m
    pts = shapely.points(cells[:, 0], cells[:, 1])
    return shapely.union_all(shapely.buffer(pts, buffer_km * 1000, quad_segs=6))


def nearest_fire_km(geo: Geo, px: np.ndarray, py: np.ndarray, fire_xy: np.ndarray | None) -> np.ndarray:
    """Distance (km) from each point to the nearest fire detection (inf if none)."""
    if fire_xy is None or len(fire_xy) == 0 or len(px) == 0:
        return np.full(len(px), np.inf)
    tree = shapely.STRtree(shapely.points(fire_xy[:, 0], fire_xy[:, 1]))
    pts = shapely.points(px, py)
    idx, dist = tree.query_nearest(pts, return_distance=True, all_matches=False)
    out = np.full(len(px), np.inf)
    out[idx[0]] = dist / 1000
    return out


# ---------------------------------------------------------------- static layers

class StaticLayers:
    def __init__(self, folder: Path):
        self.folder = Path(folder)
        self.available = (self.folder / "manifest.json").exists()
        self.frames: dict[str, pd.DataFrame] = {}
        self.manifest = json.loads((self.folder / "manifest.json").read_text()) if self.available else {}
        if self.available:
            for n in (*POINT_LAYERS, "roads"):
                f = self.folder / f"{n}.parquet"
                if f.exists():  # layers added later are optional until prepare_impact.py is re-run
                    self.frames[n] = pd.read_parquet(f)
            r = self.frames["roads"]
            self.road_geoms = shapely.from_wkb(r["wkb"].to_numpy())
            self.road_tree = shapely.STRtree(self.road_geoms)

    def points_in(self, layer: str, geo: Geo) -> pd.DataFrame:
        df = self.frames[layer]
        w, s, e, n = geo.geom.bounds
        sub = df[(df.longitude >= w) & (df.longitude <= e) & (df.latitude >= s) & (df.latitude <= n)]
        if len(sub) and geo.aoi.kind != "bbox":
            sub = sub[shapely.contains_xy(geo.geom, sub.longitude.to_numpy(), sub.latitude.to_numpy())]
        return sub

    def roads_in(self, geo: Geo) -> pd.DataFrame:
        idx = self.road_tree.query(geo.geom, predicate="intersects")
        if len(idx) == 0:
            return pd.DataFrame(columns=["type", "name", "geom_m"])
        clipped = shapely.intersection(self.road_geoms[idx], geo.geom)
        sub = self.frames["roads"].iloc[idx][["type", "name"]].copy()
        sub["geom"] = clipped
        sub["geom_m"] = [shp_transform(geo.fwd.transform, g) for g in clipped]
        return sub[~shapely.is_empty(sub["geom"].to_numpy())]


# ---------------------------------------------------------------- live services (cached)

class LiveCache:
    def __init__(self, folder: Path, client: httpx.Client | None = None):
        self.folder = Path(folder)
        self.client = client
        self._lock = threading.Lock()

    def key(self, *parts) -> Path:
        h = hashlib.sha1(json.dumps([CACHE_VERSION, *parts], sort_keys=True, default=str).encode()).hexdigest()[:20]
        return self.folder / f"{h}.json"

    def get(self, parts: tuple, fn):
        path = self.key(*parts)
        if path.exists():
            return json.loads(path.read_text())
        val = fn()
        self.folder.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(val))
        return val

    def http(self):
        """Context manager yielding a client. An injected client is shared and never closed here."""
        if self.client is not None:
            return contextlib.nullcontext(self.client)
        return httpx.Client(headers=UA, timeout=180, follow_redirects=True)


def esri_geometry(g: shapely.Geometry) -> tuple[str, str]:
    """Shapely lon/lat geometry -> (geometryType, JSON) for ArcGIS queries."""
    if g.geom_type == "Polygon" and g.equals(box(*g.bounds)):
        w, s, e, n = g.bounds
        return "esriGeometryEnvelope", json.dumps({"xmin": w, "ymin": s, "xmax": e, "ymax": n, "spatialReference": {"wkid": 4326}})
    polys = [g] if g.geom_type == "Polygon" else list(g.geoms)
    rings = []
    for p in polys:
        rings.append([list(c) for c in p.exterior.coords])
        rings += [[list(c) for c in i.coords] for i in p.interiors]
    return "esriGeometryPolygon", json.dumps({"rings": rings, "spatialReference": {"wkid": 4326}})


def polygonal(g: shapely.Geometry) -> shapely.Geometry:
    """make_valid() may return collections with stray lines/points; keep only area parts."""
    g = shapely.make_valid(g)
    if g.geom_type in ("Polygon", "MultiPolygon"):
        return g
    parts = [p for p in shapely.get_parts(g) if p.geom_type in ("Polygon", "MultiPolygon")]
    return shapely.union_all(parts) if parts else shapely.Polygon()


def esri_polygon(rings: list) -> shapely.Geometry | None:
    """Esri JSON rings -> shapely geometry. Outer rings are clockwise, holes counter-clockwise;
    each hole is attached to the outer ring that contains it."""
    outers, holes = [], []
    for r in rings:
        if len(r) < 4:
            continue
        a = np.asarray(r, float)
        signed = 0.5 * np.sum(a[:-1, 0] * a[1:, 1] - a[1:, 0] * a[:-1, 1])  # > 0 counter-clockwise
        # Clockwise (Esri outer) or degenerate/self-crossing rings (signed area ~ 0) are outers.
        (holes if signed > 0 else outers).append(r)
    if not outers:
        return None
    polys = []
    for o in outers:
        op = polygonal(shapely.Polygon(o))
        mine = [h for h in holes if op.contains(shapely.Point(h[0]))]
        polys.append(polygonal(shapely.Polygon(o, mine)))
    g = shapely.union_all(polys, grid_size=1e-6)  # ~0.1 m in degrees
    return None if g.is_empty else g


def arcgis_post(client: httpx.Client, url: str, params: dict) -> dict:
    r = client.post(url, data={"f": "json", **params})
    r.raise_for_status()
    d = r.json()
    if "error" in d:
        raise RuntimeError(f"ArcGIS error {d['error'].get('code')}: {d['error'].get('message')}")
    return d


def wui_stats(client: httpx.Client, g: shapely.Geometry) -> dict:
    """Population, housing units and blocks by 2020 WUI class for census blocks intersecting g."""
    gtype, gjson = esri_geometry(g)
    stats = [{"statisticType": "sum", "onStatisticField": "pop2020", "outStatisticFieldName": "pop"},
             {"statisticType": "sum", "onStatisticField": "hu2020", "outStatisticFieldName": "hu"},
             {"statisticType": "count", "onStatisticField": "objectid", "outStatisticFieldName": "blocks"}]
    d = arcgis_post(client, WUI_URL, {
        "where": "1=1", "geometry": gjson, "geometryType": gtype, "inSR": 4326,
        "spatialRel": "esriSpatialRelIntersects", "outStatistics": json.dumps(stats),
        "groupByFieldsForStatistics": "wuiclass2020"})
    rows = [f["attributes"] for f in d.get("features", [])]
    out = {"population": 0, "housing_units": 0, "blocks": 0,
           "wui": {"population": 0, "housing_units": 0, "blocks": 0},
           "wui_intermix": {"population": 0, "housing_units": 0},
           "wui_interface": {"population": 0, "housing_units": 0}, "by_class": {}}
    for r in rows:
        cls = r["wuiclass2020"] or "Unknown"
        pop, hu, n = int(r["pop"] or 0), int(r["hu"] or 0), int(r["blocks"] or 0)
        out["by_class"][cls] = {"population": pop, "housing_units": hu, "blocks": n}
        out["population"] += pop
        out["housing_units"] += hu
        out["blocks"] += n
        for kind in ("Intermix", "Interface"):
            if cls.endswith(kind):
                out["wui"]["population"] += pop
                out["wui"]["housing_units"] += hu
                out["wui"]["blocks"] += n
                out[f"wui_{kind.lower()}"]["population"] += pop
                out[f"wui_{kind.lower()}"]["housing_units"] += hu
    return out


def padus_features(client: httpx.Client, g: shapely.Geometry, max_features: int = 4000) -> dict:
    """Protected-area polygons intersecting g (generalized ~100 m), as GeoJSON-like dicts."""
    gtype, gjson = esri_geometry(g)
    feats, offset = [], 0
    while True:
        d = arcgis_post(client, PADUS_URL, {
            "where": "1=1", "geometry": gjson, "geometryType": gtype, "inSR": 4326, "outSR": 4326,
            "spatialRel": "esriSpatialRelIntersects", "returnGeometry": "true", "maxAllowableOffset": 0.001,
            "geometryPrecision": 5, "outFields": "Unit_Nm,Mang_Type,Mang_Name,Des_Tp,GAP_Sts",
            "resultOffset": offset, "resultRecordCount": 1000, "orderByFields": "OBJECTID"})
        page = d.get("features", [])
        feats += page
        if len(page) < 1000 or len(feats) >= max_features:
            break
        offset += 1000
    return {"features": feats[:max_features], "truncated": len(feats) >= max_features}


def nlcd_histogram(client: httpx.Client, g: shapely.Geometry, zone_lonlat: shapely.Geometry | None) -> dict:
    """NLCD 2021 class areas (km²) inside g and inside g ∩ fire zone, from a WCS raster."""
    to5070 = Transformer.from_crs(4326, 5070, always_xy=True)
    g5070 = shp_transform(to5070.transform, g)
    x0, y0, x1, y1 = g5070.bounds
    side = max(x1 - x0, y1 - y0)
    scale = min(1.0, NLCD_MAX_PX * 30.0 / side)
    r = client.get(NLCD_URL, params=[
        ("service", "WCS"), ("version", "2.0.1"), ("request", "GetCoverage"), ("coverageId", NLCD_COVERAGE),
        ("subset", f"X({x0},{x1})"), ("subset", f"Y({y0},{y1})"), ("scaleFactor", f"{scale:.6f}"),
        ("format", "image/geotiff")])
    r.raise_for_status()
    if not r.headers.get("content-type", "").startswith("image"):
        raise RuntimeError(f"NLCD service returned {r.headers.get('content-type')}: {r.text[:120]}")
    import logging

    import tifffile
    logging.getLogger("tifffile").setLevel(logging.ERROR)  # MRLC writes GDAL_NODATA as '0.0'
    arr = tifffile.imread(io.BytesIO(r.content))
    h, w = arr.shape[:2]
    px = (x1 - x0) / w
    py = (y1 - y0) / h
    xs = x0 + (np.arange(w) + 0.5) * px
    ys = y1 - (np.arange(h) + 0.5) * py
    X, Y = np.meshgrid(xs, ys)
    inside = shapely.contains_xy(g5070, X, Y) & (arr > 0)
    near = np.zeros_like(inside)
    if zone_lonlat is not None:
        z5070 = shp_transform(to5070.transform, zone_lonlat)
        near = inside & shapely.contains_xy(z5070, X, Y)
    cell_km2 = px * py / 1e6  # EPSG:5070 is equal-area
    def hist(mask):
        v, c = np.unique(arr[mask], return_counts=True)
        return {int(k): float(n * cell_km2) for k, n in zip(v, c)}
    return {"pixel_m": round((px + py) / 2, 1), "in_area": hist(inside), "near_fire": hist(near)}


# ---------------------------------------------------------------- assembly

def _py(v):
    """numpy/pandas scalar -> plain Python (JSON-serializable), NaN -> None."""
    if v is None or (isinstance(v, float) and math.isnan(v)) or (not isinstance(v, str) and pd.isna(v)):
        return None
    return v.item() if hasattr(v, "item") else v


def _point_items(df: pd.DataFrame, dist_km: np.ndarray, cols: list[str], limit: int = 8) -> list[dict]:
    order = np.argsort(dist_km, kind="stable")[:limit]
    out = []
    for i in order:
        row = df.iloc[i]
        item = {c: _py(row[c]) for c in cols}
        item["distance_to_fire_km"] = None if not np.isfinite(dist_km[i]) else round(float(dist_km[i]), 1)
        out.append(item)
    return out


def summarize(static: StaticLayers, live: LiveCache, aoi: AOI, fire_lon: np.ndarray, fire_lat: np.ndarray,
              buffer_km: float, include_live: bool = True) -> dict:
    if aoi.kind == "region":
        return {"status": "aoi_required", "message": "Select an area (preset, drawn shape or point + radius) to see impacts."}
    geo = make_geo(aoi)
    fx, fy = to_m(geo, fire_lon, fire_lat)
    fire_xy = np.stack([fx, fy], axis=1) if len(fx) else None
    zone_m = fire_zone(geo, fire_lon, fire_lat, buffer_km)
    zone_m_in = shapely.intersection(zone_m, geo.geom_m) if zone_m is not None else None
    inv = Transformer.from_crs(geo.fwd.target_crs, 4326, always_xy=True)
    zone_ll = shp_transform(inv.transform, zone_m_in) if zone_m_in is not None and not zone_m_in.is_empty else None

    layers: dict[str, dict] = {}
    if not static.available:
        for n in (*POINT_LAYERS, "roads"):
            layers[n] = {"status": "unavailable", "message": "Run backend/scripts/prepare_impact.py"}
    else:
        spec = {
            "places": (["name", "state", "pop_max"], "Natural Earth 10m populated places (population estimates)"),
            "airports": (["name", "size", "iata", "municipality"], "OurAirports (public domain)"),
            "hospitals": (["name", "city", "state", "type", "beds", "trauma"], "HIFLD Hospitals (ORNL/DHS), open facilities"),
            "schools": (["name", "city", "state"], "NCES EDGE public school locations 2022-23"),
            "firestations": (["name", "city", "state"], "USGS National Structures Dataset, fire stations / EMS stations"),
        }
        for n, (cols, src) in spec.items():
            if n not in static.frames:
                layers[n] = {"status": "unavailable", "message": "Re-run backend/scripts/prepare_impact.py to build this layer"}
                continue
            sub = static.points_in(n, geo)
            px_, py_ = to_m(geo, sub.longitude.to_numpy(), sub.latitude.to_numpy())
            d = nearest_fire_km(geo, px_, py_, fire_xy)
            near = d <= buffer_km
            entry = {"status": "ok", "source": src, "in_area": int(len(sub)), "near_fire": int(near.sum()),
                     "items": _point_items(sub, d, cols)}
            if n == "places":
                entry["population_estimate_near_fire"] = int(sub.pop_max.to_numpy()[near].sum())
            if n == "airports":
                entry["by_size"] = {s: int((sub["size"] == s).sum()) for s in ("large", "medium", "small")}
                entry["near_fire_by_size"] = {s: int(((sub["size"] == s).to_numpy() & near).sum()) for s in ("large", "medium", "small")}
            if n == "hospitals":
                beds = sub["beds"].to_numpy(dtype=float)
                entry["beds_near_fire"] = int(np.nansum(beds[near]))
            layers[n] = entry
        roads = static.roads_in(geo)
        lens = np.array([g.length / 1000 for g in roads["geom_m"]]) if len(roads) else np.array([])
        near_len = 0.0
        if zone_m_in is not None and len(roads):
            near_len = float(sum(shapely.intersection(g, zone_m_in).length for g in roads["geom_m"]) / 1000)
        by_type = {}
        for t, L in zip(roads["type"], lens):
            by_type[t] = by_type.get(t, 0) + float(L)
        names = sorted({n for n in roads["name"] if n})[:12] if len(roads) else []
        layers["roads"] = {"status": "ok", "source": "Natural Earth 10m roads (major and secondary highways only)",
                           "in_area_km": round(float(lens.sum()), 1), "near_fire_km": round(near_len, 1),
                           "by_type_km": {k: round(v, 1) for k, v in by_type.items()}, "names": names}

    conus = geo.geom.intersects(CONUS) and geo.geom.difference(CONUS).area / max(geo.geom.area, 1e-12) < 0.5
    with live.http() as client:
        # Population + WUI
        if not include_live:
            layers["population"] = layers["wui"] = {"status": "skipped"}
        elif not conus:
            layers["population"] = layers["wui"] = {"status": "outside_coverage", "message": "SILVIS WUI covers the conterminous US only"}
        elif geo.area_km2 > MAX_WUI_KM2:
            layers["population"] = layers["wui"] = {"status": "too_large", "message": f"Area over {MAX_WUI_KM2:,} km²; draw a smaller area"}
        else:
            try:
                g_simple = geo.geom.simplify(0.001) if geo.aoi.kind != "bbox" else geo.geom
                tot = live.get(("wui", g_simple.wkt), lambda: wui_stats(client, g_simple))
                near = None
                if zone_ll is not None:
                    zs = zone_ll.simplify(0.003)
                    near = live.get(("wui", zs.wkt), lambda: wui_stats(client, zs))
                src = "USFS / SILVIS Lab Wildland-Urban Interface 2020 (2020 Census blocks)"
                note = "Totals over census blocks intersecting the area (edge blocks counted whole)."
                layers["population"] = {"status": "ok", "source": src, "note": note,
                                        "in_area": {"population": tot["population"], "housing_units": tot["housing_units"]},
                                        "near_fire": None if near is None else {"population": near["population"], "housing_units": near["housing_units"]}}
                layers["wui"] = {"status": "ok", "source": src, "note": note,
                                 "in_area": {**tot["wui"], "intermix": tot["wui_intermix"], "interface": tot["wui_interface"],
                                             "share_of_population": tot["wui"]["population"] / tot["population"] if tot["population"] else None},
                                 "near_fire": None if near is None else {**near["wui"], "intermix": near["wui_intermix"], "interface": near["wui_interface"]}}
            except Exception as exc:  # noqa: BLE001 - surface any upstream failure
                layers["population"] = layers["wui"] = {"status": "unavailable", "message": f"USFS WUI service: {exc}"}

        # Protected areas
        if not include_live:
            layers["protected"] = {"status": "skipped"}
        elif geo.area_km2 > MAX_PADUS_KM2:
            layers["protected"] = {"status": "too_large", "message": f"Area over {MAX_PADUS_KM2:,} km²; draw a smaller area"}
        else:
            try:
                gq = geo.geom.simplify(0.001) if geo.aoi.kind != "bbox" else geo.geom
                pad = live.get(("padus", gq.wkt), lambda: padus_features(client, gq))
                polys, attrs = [], []
                for f in pad["features"]:
                    poly = esri_polygon(f.get("geometry", {}).get("rings") or [])
                    if poly is None:
                        continue
                    polys.append(poly)
                    attrs.append(f["attributes"])
                # Overlays in metres on a 1 m precision grid: robust to the slivers and
                # self-touching rings that generalized PAD-US polygons contain.
                aoi_m = polygonal(geo.geom_m)
                zone_v = polygonal(zone_m_in) if zone_m_in is not None else None
                clip = []
                for p in polys:
                    pm = polygonal(shp_transform(geo.fwd.transform, p))
                    clip.append(polygonal(shapely.intersection(pm, aoi_m, grid_size=1.0)))
                area = np.array([c.area / 1e6 for c in clip])
                near_area = (np.array([shapely.intersection(c, zone_v, grid_size=1.0).area / 1e6 for c in clip])
                             if zone_v is not None else np.zeros(len(clip)))
                by_mgr: dict[str, float] = {}
                units: dict[str, dict] = {}
                for a, ar, nr in zip(attrs, area, near_area):
                    by_mgr[a["Mang_Type"] or "Unknown"] = by_mgr.get(a["Mang_Type"] or "Unknown", 0) + float(ar)
                    key = a["Unit_Nm"] or "Unnamed"
                    u = units.setdefault(key, {"name": key, "manager": a["Mang_Name"], "designation": a["Des_Tp"], "km2": 0.0, "near_fire_km2": 0.0})
                    u["km2"] += float(ar)
                    u["near_fire_km2"] += float(nr)
                # Overlapping PAD-US records (e.g. designations on the same land) are unioned for the total.
                nonempty = [c for c in clip if not c.is_empty]
                union = shapely.union_all(nonempty, grid_size=1.0) if nonempty else None
                total = union.area / 1e6 if union is not None else 0.0
                near_total = shapely.intersection(union, zone_v, grid_size=1.0).area / 1e6 if union is not None and zone_v is not None else 0.0
                top = sorted(units.values(), key=lambda u: (-u["near_fire_km2"], -u["km2"]))[:8]
                layers["protected"] = {
                    "status": "ok", "source": "USGS PAD-US 3.0 (Fee Manager)", "truncated": pad["truncated"],
                    "in_area_km2": round(total, 1), "share_of_area": total / geo.area_km2 if geo.area_km2 else None,
                    "near_fire_km2": round(near_total, 1), "units_in_area": len(units),
                    "units_near_fire": sum(1 for u in units.values() if u["near_fire_km2"] > 0.01),
                    "by_manager_km2": {k: round(v, 1) for k, v in sorted(by_mgr.items(), key=lambda kv: -kv[1])},
                    "items": [{**u, "km2": round(u["km2"], 1), "near_fire_km2": round(u["near_fire_km2"], 1)} for u in top],
                }
            except Exception as exc:  # noqa: BLE001
                layers["protected"] = {"status": "unavailable", "message": f"USGS PAD-US service: {exc}"}

        # Agriculture (NLCD)
        if not include_live:
            layers["agriculture"] = {"status": "skipped"}
        elif not conus:
            layers["agriculture"] = {"status": "outside_coverage", "message": "NLCD 2021 here covers the conterminous US only"}
        elif geo.area_km2 > MAX_NLCD_KM2:
            layers["agriculture"] = {"status": "too_large", "message": f"Area over {MAX_NLCD_KM2:,} km²"}
        else:
            try:
                zone_key = zone_ll.simplify(0.002).wkt if zone_ll is not None else None
                hist = live.get(("nlcd", geo.geom.wkt, zone_key), lambda: nlcd_histogram(client, geo.geom, zone_ll))
                ina = {int(k): v for k, v in hist["in_area"].items()}
                nea = {int(k): v for k, v in hist["near_fire"].items()}
                total = sum(ina.values())
                ag = sum(ina.get(c, 0) for c in AGRICULTURE)
                layers["agriculture"] = {
                    "status": "ok", "source": f"NLCD 2021 land cover (MRLC), sampled at ~{hist['pixel_m']:.0f} m",
                    "in_area_km2": round(ag, 1), "share_of_area": ag / total if total else None,
                    "crops_km2": round(ina.get(82, 0), 1), "pasture_hay_km2": round(ina.get(81, 0), 1),
                    "near_fire_km2": round(sum(nea.get(c, 0) for c in AGRICULTURE), 1),
                    "land_cover_km2": {NLCD_CLASSES.get(k, str(k)): round(v, 1) for k, v in sorted(ina.items(), key=lambda kv: -kv[1])},
                }
            except Exception as exc:  # noqa: BLE001
                layers["agriculture"] = {"status": "unavailable", "message": f"MRLC NLCD service: {exc}"}

    return {
        "status": "ok",
        "area_km2": round(geo.area_km2, 1),
        "fire": {"detections": int(len(fire_lon)), "buffer_km": buffer_km,
                 "zone_km2": round(zone_m_in.area / 1e6, 1) if zone_m_in is not None else 0.0,
                 "zone_geojson": mapping(zone_ll.simplify(0.002)) if zone_ll is not None else None},
        "layers": layers,
    }
