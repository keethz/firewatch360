"""Build the static human-impact layers (US only) used by /api/impact.

Usage:  backend/.venv/bin/python backend/scripts/prepare_impact.py

Downloads (cached in data/raw/impact/, skipped if present) and writes
data/processed/impact/<layer>.parquet:

  places     Natural Earth 10m populated places (name, pop_max)      public domain
  roads      Natural Earth 10m roads, clipped to the US (WKB lines)  public domain
  airports   OurAirports: large, medium and small airports            public domain
  hospitals  HIFLD Hospitals (ORNL/DHS) via PASDA, STATUS = OPEN       public domain
  schools    NCES EDGE public school locations 2022-23                 public domain
  firestations  USGS National Structures Dataset, Fire Stations/EMS     public domain

No values are invented: every output row is one source record. Live layers (population and
WUI, protected areas, land cover) are queried per area by app/services/impact.py.
"""
from __future__ import annotations

import csv
import io
import json
import sys
import time
import zipfile
from pathlib import Path

import httpx
import pandas as pd
import shapefile
import shapely
from shapely.geometry import shape

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.config import get_settings  # noqa: E402

UA = {"User-Agent": "firewatch360-prototype/0.1"}
SOURCES = {
    "places": "https://naciscdn.org/naturalearth/10m/cultural/ne_10m_populated_places_simple.zip",
    "roads": "https://naciscdn.org/naturalearth/10m/cultural/ne_10m_roads.zip",
    "airports": "https://davidmegginson.github.io/ourairports-data/airports.csv",
    "schools": "https://nces.ed.gov/programs/edge/data/EDGE_GEOCODE_PUBLICSCH_2223.zip",
}
FIRESTATIONS = "https://carto.nationalmap.gov/arcgis/rest/services/structures/MapServer/51/query"
HOSPITALS = "https://mapservices.pasda.psu.edu/server/rest/services/pasda/HIFLD_FEMA/MapServer/51/query"


def download(url: str, dest: Path) -> Path:
    if not dest.exists():
        print(f"downloading {url}")
        with httpx.stream("GET", url, headers=UA, timeout=600, follow_redirects=True) as r:
            r.raise_for_status()
            with dest.open("wb") as f:
                for chunk in r.iter_bytes():
                    f.write(chunk)
    return dest


def reader(zpath: Path, base: str, encoding: str = "utf-8") -> shapefile.Reader:
    z = zipfile.ZipFile(zpath)
    parts = {ext: io.BytesIO(z.read(f"{base}.{ext}")) for ext in ("shp", "dbf", "shx") if f"{base}.{ext}" in z.namelist()}
    return shapefile.Reader(**parts, encoding=encoding)


def us_boundary(data_dir: Path):
    gj = json.loads((data_dir / "reference" / "ne_10m_admin_0_countries.geojson").read_text())
    usa = next(f for f in gj["features"] if f["properties"].get("ADM0_A3") == "USA")
    return shape(usa["geometry"]).buffer(0.05)


def places(raw: Path) -> pd.DataFrame:
    r = reader(download(SOURCES["places"], raw / "ne_10m_populated_places_simple.zip"), "ne_10m_populated_places_simple")
    rows = [{"name": x["name"], "state": x["adm1name"], "pop_max": int(x["pop_max"]),
             "latitude": float(x["latitude"]), "longitude": float(x["longitude"])}
            for x in r.records() if x["adm0_a3"] == "USA"]
    return pd.DataFrame(rows)


def roads(raw: Path, us) -> pd.DataFrame:
    r = reader(download(SOURCES["roads"], raw / "ne_10m_roads.zip"), "ne_10m_roads")
    shapely.prepare(us)
    rows = []
    for sr in r.iterShapeRecords():
        rec = sr.record
        if rec["continent"] != "North America" or rec["type"] == "Ferry Route":
            continue
        g = shape(sr.shape.__geo_interface__)
        if not us.intersects(g):
            continue
        g = g.intersection(us)
        if g.is_empty:
            continue
        rows.append({"type": rec["type"], "name": rec["name"] or rec["label"] or "", "wkb": shapely.to_wkb(g)})
    return pd.DataFrame(rows)


def airports(raw: Path) -> pd.DataFrame:
    path = download(SOURCES["airports"], raw / "airports.csv")
    keep = {"large_airport": "large", "medium_airport": "medium", "small_airport": "small"}
    rows = [{"name": a["name"], "size": keep[a["type"]], "iata": a["iata_code"], "municipality": a["municipality"],
             "latitude": float(a["latitude_deg"]), "longitude": float(a["longitude_deg"])}
            for a in csv.DictReader(path.open(encoding="utf-8"))
            if a["iso_country"] == "US" and a["type"] in keep]
    return pd.DataFrame(rows)


def hospitals(raw: Path) -> pd.DataFrame:
    cache = raw / "hifld_hospitals.json"
    if not cache.exists():
        feats, offset = [], 0
        with httpx.Client(headers=UA, timeout=120) as c:
            while True:
                r = c.get(HOSPITALS, params={
                    "f": "json", "where": "STATUS='OPEN'", "returnGeometry": "false",
                    "outFields": "NAME,CITY,STATE,TYPE,BEDS,TRAUMA,LATITUDE,LONGITUDE,SOURCEDATE",
                    "resultOffset": offset, "resultRecordCount": 1000, "orderByFields": "OBJECTID"})
                r.raise_for_status()
                page = r.json().get("features", [])
                feats += [f["attributes"] for f in page]
                print(f"hospitals: {len(feats)}")
                if len(page) < 1000:
                    break
                offset += 1000
                time.sleep(0.3)
        cache.write_text(json.dumps(feats))
    rows = json.loads(cache.read_text())
    df = pd.DataFrame(rows).rename(columns=str.lower)
    df = df.rename(columns={"sourcedate": "source_date_ms"})
    df["beds"] = pd.to_numeric(df["beds"], errors="coerce").where(lambda s: s > 0)  # HIFLD uses -999 for unknown
    return df.dropna(subset=["latitude", "longitude"])


def firestations(raw: Path) -> pd.DataFrame:
    cache = raw / "usgs_firestations.json"
    if not cache.exists():
        feats, offset = [], 0
        with httpx.Client(headers=UA, timeout=120) as c:
            while True:
                r = c.get(FIRESTATIONS, params={
                    "f": "json", "where": "1=1", "outFields": "NAME,CITY,STATE", "outSR": 4326,
                    "returnGeometry": "true", "resultOffset": offset, "resultRecordCount": 2000,
                    "orderByFields": "OBJECTID"})
                r.raise_for_status()
                page = r.json().get("features", [])
                feats += [{**{k.lower(): v for k, v in f["attributes"].items()},
                           "longitude": f["geometry"]["x"], "latitude": f["geometry"]["y"]}
                          for f in page if f.get("geometry")]
                print(f"fire stations: {len(feats)}")
                if len(page) < 2000:
                    break
                offset += 2000
                time.sleep(0.3)
        cache.write_text(json.dumps(feats))
    df = pd.DataFrame(json.loads(cache.read_text()))
    for col in ("name", "city", "state"):
        df[col] = df[col].fillna("").astype(str).str.strip()
    return df.dropna(subset=["latitude", "longitude"])


def schools(raw: Path) -> pd.DataFrame:
    r = reader(download(SOURCES["schools"], raw / "EDGE_GEOCODE_PUBLICSCH_2223.zip"),
               "Shapefiles_SCH/EDGE_GEOCODE_PUBLICSCH_2223", encoding="latin-1")
    rows = [{"name": x["NAME"], "city": x["CITY"], "state": x["STATE"], "locale": x["LOCALE"],
             "latitude": float(x["LAT"]), "longitude": float(x["LON"])} for x in r.iterRecords()]
    return pd.DataFrame(rows)


def main() -> None:
    data = Path(get_settings().data_dir)
    raw = data / "raw" / "impact"
    out = data / "processed" / "impact"
    raw.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    us = us_boundary(data)
    builders = {"places": lambda: places(raw), "roads": lambda: roads(raw, us), "airports": lambda: airports(raw),
                "hospitals": lambda: hospitals(raw), "schools": lambda: schools(raw), "firestations": lambda: firestations(raw)}
    summary = {}
    for name, build in builders.items():
        df = build()
        df.to_parquet(out / f"{name}.parquet", index=False)
        summary[name] = len(df)
        print(f"{name}: {len(df):,} rows")
    (out / "manifest.json").write_text(json.dumps({
        "built_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "rows": summary,
        "sources": {**SOURCES, "hospitals": HOSPITALS, "firestations": FIRESTATIONS}}, indent=2))


if __name__ == "__main__":
    main()
