"""Area-of-interest parsing and SQL filtering.

Supported forms (query parameters):
  bbox=w,s,e,n                     bounding box in decimal degrees
  lat=..&lon=..&radius_km=..       circle (great-circle distance)
  polygon=<GeoJSON Polygon/MultiPolygon or Feature>
No AOI means the whole loaded region (United States).
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field

import shapely
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry

MAX_RADIUS_KM = 1000.0
EARTH_RADIUS_KM = 6371.0088


class AOIError(ValueError):
    pass


@dataclass(frozen=True)
class AOI:
    kind: str  # "region" | "bbox" | "circle" | "polygon"
    bbox: tuple[float, float, float, float] | None = None  # w, s, e, n
    center: tuple[float, float] | None = None  # lat, lon
    radius_km: float | None = None
    geometry: BaseGeometry | None = field(default=None, compare=False, hash=False)
    key: str = "region"  # stable cache key

    def describe(self) -> dict:
        d: dict = {"kind": self.kind}
        if self.bbox:
            d["bbox"] = list(self.bbox)
        if self.center:
            d["lat"], d["lon"] = self.center
            d["radius_km"] = self.radius_km
        if self.geometry is not None:
            d["geometry"] = shapely.geometry.mapping(self.geometry)
        return d

    def sql(self) -> tuple[str, list]:
        """WHERE fragment (without 'WHERE') and parameters. Polygons are pre-filtered by
        bounding box here; the exact test uses the `in_aoi` UDF (see HotspotStore)."""
        if self.kind == "region":
            return "TRUE", []
        w, s, e, n = self.bbox  # type: ignore[misc]
        frag = "latitude BETWEEN ? AND ? AND longitude BETWEEN ? AND ?"
        params: list = [s, n, w, e]
        if self.kind == "circle":
            lat, lon = self.center  # type: ignore[misc]
            frag += (
                " AND 2 * ? * asin(sqrt(pow(sin(radians(latitude - ?) / 2), 2)"
                " + cos(radians(?)) * cos(radians(latitude)) * pow(sin(radians(longitude - ?) / 2), 2))) <= ?"
            )
            params += [EARTH_RADIUS_KM, lat, lat, lon, self.radius_km]
        elif self.kind == "polygon":
            frag += " AND in_aoi(latitude, longitude)"
        return frag, params


def _floats(text: str, n: int, name: str) -> list[float]:
    try:
        vals = [float(v) for v in text.split(",")]
    except ValueError as exc:
        raise AOIError(f"{name} must be {n} comma-separated numbers") from exc
    if len(vals) != n or not all(math.isfinite(v) for v in vals):
        raise AOIError(f"{name} must be {n} comma-separated numbers")
    return vals


def _check_lat_lon(lat: float, lon: float) -> None:
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise AOIError("latitude must be in [-90, 90] and longitude in [-180, 180]")


def parse_aoi(
    bbox: str | None = None,
    lat: float | None = None,
    lon: float | None = None,
    radius_km: float | None = None,
    polygon: str | None = None,
) -> AOI:
    given = sum(x is not None for x in (bbox, polygon)) + (lat is not None or lon is not None)
    if given > 1:
        raise AOIError("Give only one of bbox, lat/lon/radius_km, or polygon")

    if bbox is not None:
        w, s, e, n = _floats(bbox, 4, "bbox")
        _check_lat_lon(s, w)
        _check_lat_lon(n, e)
        if w >= e or s >= n:
            raise AOIError("bbox must be w,s,e,n with w < e and s < n")
        b = (w, s, e, n)
        return AOI("bbox", bbox=b, key=f"bbox:{b}")

    if lat is not None or lon is not None:
        if lat is None or lon is None or radius_km is None:
            raise AOIError("lat, lon and radius_km are all required for a circle AOI")
        _check_lat_lon(lat, lon)
        if not (0 < radius_km <= MAX_RADIUS_KM):
            raise AOIError(f"radius_km must be in (0, {MAX_RADIUS_KM:g}]")
        dlat = math.degrees(radius_km / EARTH_RADIUS_KM)
        coslat = max(math.cos(math.radians(lat)), 1e-6)
        dlon = min(180.0, dlat / coslat)
        b = (max(-180.0, lon - dlon), max(-90.0, lat - dlat), min(180.0, lon + dlon), min(90.0, lat + dlat))
        return AOI("circle", bbox=b, center=(lat, lon), radius_km=radius_km, key=f"circle:{lat},{lon},{radius_km}")

    if polygon is not None:
        try:
            obj = json.loads(polygon)
            if obj.get("type") == "Feature":
                obj = obj["geometry"]
            geom = shape(obj)
        except Exception as exc:  # noqa: BLE001 - any parse failure is a client error
            raise AOIError("polygon must be a GeoJSON Polygon, MultiPolygon or Feature") from exc
        if geom.geom_type not in ("Polygon", "MultiPolygon") or geom.is_empty or not geom.is_valid:
            raise AOIError("polygon must be a valid, non-empty Polygon or MultiPolygon")
        w, s, e, n = geom.bounds
        _check_lat_lon(s, w)
        _check_lat_lon(n, e)
        shapely.prepare(geom)
        return AOI("polygon", bbox=(w, s, e, n), geometry=geom, key=f"polygon:{geom.wkb_hex}")

    return AOI("region")
