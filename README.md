# FIREWATCH 360

**A harmonized burning-activity calendar from two decades of NASA satellite hotspots.**

NASA Space Apps Challenge 2026: *MODIS and VIIRS Hot Spots*.

FIREWATCH 360 merges NASA FIRMS active-fire detections from **MODIS** (2000 onward, 1 km) and **VIIRS** (2012 onward, 375 m) into one sensor-independent monthly record. You can explore it for any area of the United States:
- a year × month calendar from 2000 to today
- comparisons against the historical median
- anomaly flags
- near-real-time context for the current month
- CSV downloads

> Prototype. It is not an official NASA product, not an operational emergency tool, and not a forecast.

## Features

- **Explore**
  - Map of hotspots for any date range.
  - Set the area of interest by preset, drawn rectangle or polygon, clicked circle, or typed lat/lon + radius.
  - Filter by sensor, confidence and vegetation fires only.
  - Optional live FIRMS near-real-time layer.
- **Calendar**
  - Heatmap of harmonized monthly fire activity, 2000 → now, with colorblind-safe ramps (Viridis or Cividis for counts; blue–orange or purple–orange for anomalies).
  - Tooltips show the harmonized value, raw MODIS and VIIRS, the median, % difference and z-score.
  - Pick any year to see it against the baseline median and its P25–P75 band.
  - Rankings, peak fire season, trend.
- **Fire Risk Brief**: a plain-language summary card for any area: headline, % vs historical median, historical rank, anomaly level (Severe / Moderate / Normal / Below normal), MODIS–VIIRS agreement, confidence with reasons, peak season, long-term trend. Works for the latest data or any past month, with one-click PDF or PNG download.
- **What could this affect?**:
  - for any area, what lies inside it and within a chosen distance of its fire detections: 2020 Census population and housing, homes in the wildland–urban interface, population centers, major roads, hospitals (with beds), schools, fire stations, airports, protected land and farmland
  - nearest named facilities
  - map overlays for each layer
- **Anomaly map**: which places had unusual fire activity in a month. Detections are grouped into map cells (0.1°–1°), each compared with the same month in past years (robust z-score), with a ranked list of the most unusual cells.
- **AI explanations** (optional): an "Explain with AI" button on the Fire Risk Brief and on each anomaly cell. Google Gemini (gemini-3.8-flash, with gemini-3.5-flash-lite as a fallback) writes a short plain-language explanation using only the statistics the app computed. Needs `GEMINI_API_KEY` in `.env` on the server (free key from Google AI Studio); without it the app shows the rule-based text.
- **Reported fires (NIFC)**: agency-reported wildfires from the NIFC WFIGS incident database (2014 onward) for the selected area and dates: count, acres, prescribed burns, causes and the largest named fires; a map layer sized by acres; and a monthly chart of reported wildfires against harmonized satellite detections on the Calendar tab.
- **Image downloads**: PNG of the calendar, year chart, historical comparison, reported-vs-detected chart, Compare chart and harmonization charts, with a footer naming the area, date and data source.
- **Drought & fire weather**: for the selected area, the share in each U.S. Drought Monitor category (D0–D4) for the chosen month and now, the Drought Severity and Coverage Index, and any active NWS Red Flag Warning or Fire Weather Watch. Shown on Explore and for each anomaly cell, and passed to the AI explanations.
- **Compare**: two years for the same area, or two areas in the same year. Monthly chart (cell-days or % vs each side's own median) and a table of totals, % vs median, peak month and unusual months.
- **Alerts**: watch a saved area and get an alert when its activity is unusual, when new satellite detections appear inside it or within X km, or when a Red Flag Warning / Fire Weather Watch is issued. In-app inbox, optional webhook (Slack, Teams…) and e-mail (SMTP). Each alert is sent once. Run `backend/scripts/check_alerts.py` on a schedule (e.g. hourly cron).
- **Exclude farmland fires**: a header switch that drops detections in 0.01° cells that are at least half cropland or pasture (NLCD 2021) from every tab, separating agricultural burning from wildfires. Built once with `backend/scripts/prepare_cropland.py` (about 2 minutes).
- **Fire activity outlook** (experimental): the next 1–3 months for any area as below / near / above normal probabilities, an expected value and an 80% range, from the persistence of recent anomalies, with each month's hindcast skill shown (and "no skill" said plainly). On Explore and Calendar.
- **Burned area and post-fire assessment**: mapped fire perimeters for any year (NIFC WFIGS 2020+, MTBS 1984+) on the map and as a table, acres burned inside the area, and the yearly record. Click a fire for its post-fire assessment: satellite detections and daily fire radiative power inside the perimeter, MTBS burn severity (to 2021), land cover before the fire (NLCD) and the mapped assets inside.
- **Smoke**: NOAA HMS satellite smoke plumes (light / medium / heavy) on the map for any day since 2005, the share of the area covered, and a 14-day smoke series.
- **Fire intensity (FRP) calendar** and **Download all as PDF** on the Calendar and Explore tabs.
- **Current month**: month-to-date compared with the same days of that month in every baseline year. Labelled provisional.
- **Method & data**: calibration charts and statistics, parameters, data as-of dates, limitations, attribution.
- **Downloads**: harmonized calendar CSV (with the calibration in its header) and a raw hotspot CSV for the selected area and dates.

## How the harmonization works

1. **Common grid.** Each detection is snapped to a 0.01° grid (about 1 km). The unit is a *fire cell-day*: one grid cell with at least one detection on one UTC day. This removes most of the resolution difference between the sensors, and counts Terra and Aqua overpasses on the same day once.
2. **MODIS → VIIRS.** On the overlap years (2012-02 onward) fit `VIIRS ≈ k × MODIS` (least squares through the origin).
3. **Terra-only years.** Aqua MODIS starts on 2002-07-04. On 2002-08 → 2012-01 fit `MODIS(Terra + Aqua) ≈ c × MODIS(Terra only)` and apply it to 2000-11 → 2002-07.
4. **One series** in VIIRS-equivalent cell-days:
   - `k × c × Terra` before Aqua
   - `k × MODIS` until 2012-02
   - observed VIIRS after that
5. **Validation**: leave-one-year-out for both factors. Areas with too little data fall back to the US-wide factor, and the app says so.
6. **Anomalies**: robust z = (value − median) / (1.4826 × MAD) per calendar month over complete baseline years, flagged at |z| ≥ 2.
7. **Near-real-time**: provisional months use FIRMS NRT data. NRT has no fire-type field, so detections in grid cells that the archive ever flagged as static sources (industry, volcano, offshore) are dropped. On months where the true type is known this is within −2% to −7% of the VIIRS vegetation count, versus +14% to +44% without the mask.

Results on the loaded US data (2026-11-14, default parameters):

| Area | k (VIIRS / MODIS) | R² | c (Terra+Aqua / Terra) | R² |
|---|---|---|---|---|
| United States | 2.066 | 0.918 | 1.836 | 0.879 |
| California | 1.775 | 0.981 | 1.688 | 0.991 |

All parameters are prototype defaults that need scientific validation. Full details and limitations are in [docs/SCIENTIFIC_METHOD.md](docs/SCIENTIFIC_METHOD.md).

## Quick start

Requirements: Python 3.12+, Node 20.19+ (or 22.12+).

```bash
git clone https://github.com/keethz/firewatch360.git
cd firewatch360
cp .env.example .env               # add your FIRMS_MAP_KEY (see FIRMS_KEY_SETUP.md)
```

**1. Data.** The fire data is not in this repository (about 1 GB). Download the FIRMS yearly **United States** country files for MODIS and VIIRS S-NPP from <https://firms.modaps.eosdis.nasa.gov/data/country/> into:

```
data/raw/modis/modis_<YEAR>_United_States.csv
data/raw/viirs-snpp/viirs-snpp_<YEAR>_United_States.csv
```

See [data/README.md](data/README.md). Then build the dataset:

```bash
python3.12 -m venv backend/.venv
backend/.venv/bin/pip install -r backend/requirements.txt
backend/.venv/bin/python backend/scripts/fetch_2026.py     # optional: data after the archive, needs FIRMS_MAP_KEY
backend/.venv/bin/python backend/scripts/prepare_data.py   # CSV → Parquet in data/processed/
backend/.venv/bin/python backend/scripts/prepare_impact.py # human-impact layers (downloads ~60 MB, ~20 s)
```

**2. Run.**

```bash
cd backend && .venv/bin/uvicorn app.main:app --port 8000    # API on http://localhost:8000
cd frontend && npm install && npm run dev                   # app on http://localhost:5173
```

**3. Test.**

```bash
cd backend && .venv/bin/pytest -q      # 85 tests; data-dependent tests skip if data is not built
cd frontend && npm run build && npm run lint
```

## API

All endpoints accept an area of interest. Leave it out for the whole US, or give one of:
- `bbox=w,s,e,n`
- `lat=..&lon=..&radius_km=..`
- `polygon=<GeoJSON>`

Common options: `grid_deg` (0.01), `min_confidence` (`low|nominal|high`, default `nominal`), `vegetation_only` (true).

| Endpoint | What it returns |
|---|---|
| `GET /api/health`, `/api/coverage` | Status, rows and date range per sensor |
| `GET /api/calibration` | k and c with R², leave-one-year-out validation, overlap series |
| `GET /api/harmonized` | Monthly series 2000-11 → now (provisional months flagged) |
| `GET /api/calendar` | Year × month matrix with medians, z-scores and anomaly flags |
| `GET /api/climatology` | Per-month baseline stats and the selected year |
| `GET /api/anomalies` | Anomalous months, rankings, trend, latest and current month |
| `GET /api/brief` | Fire Risk Brief for the area (`end=YYYY-MM` for a past month) |
| `GET /api/impact` | Population, WUI, roads, hospitals, schools, airports, protected land and farmland in the area and near its fire detections (`start`, `end`, `buffer_km`) |
| `GET /api/impact/features` | GeoJSON of an impact layer for the map (`layer=places|roads|hospitals|schools|firestations|airports`) |
| `GET /api/anomaly-map` | Map cells with this month's value, baseline median, robust z and anomaly flag (`month`, `cell_deg`, `z_threshold`, `min_value`) |
| `GET /api/explain/brief`, `/api/explain/cell` | AI explanation of the Brief or one anomaly cell, plus the facts sent to the model |
| `GET /api/reported` | NIFC reported wildfires in the area and dates (`start`, `end`): counts, acres, causes, largest fires, GeoJSON |
| `GET /api/reported/monthly` | Reported wildfires per month with harmonized satellite cell-days alongside |
| `GET /api/context` | Drought (selected `month` and latest week) and active fire-weather alerts for the area |
| `GET/POST /api/alerts/areas`, `DELETE /api/alerts/areas/{id}` | Saved areas and their alert rules |
| `POST /api/alerts/check`, `GET /api/alerts/events` | Check saved areas now; list alerts |
| `GET /api/current` | Current month-to-date vs the same days in baseline years |
| `GET /api/hotspots` | GeoJSON points for the map (capped; exact `total`) |
| `GET /api/nrt` | Live FIRMS NRT hotspots, last 1–10 days (server-side key, cached) |
| `GET /api/export/calendar.csv` | Harmonized calendar CSV |
| `GET /api/export/hotspots.csv` | Raw hotspots CSV for an area and date range |

Analytics endpoints also take `start`/`end` (YYYY-MM), `year`, `baseline_start`/`baseline_end`, and `z_threshold`.

## Project layout

```
backend/
  app/api/routes.py          API endpoints
  app/services/aoi.py        area-of-interest parsing and filtering
  app/services/harmonize.py  gridding, cell-days, k and c fits, harmonized series, NRT months
  app/services/analytics.py  climatology, anomalies, rankings, trend, month-to-date
  app/services/nrt.py        live FIRMS NRT proxy with disk cache
  app/services/brief.py      Fire Risk Brief rules
  app/services/impact.py     human-impact layers and fire exposure
  app/services/anomaly_map.py  per-cell anomaly map
  app/services/reported.py   NIFC reported fires (WFIGS incidents)
  app/services/context.py    drought (USDM) and NWS fire-weather context
  app/services/alerts.py     saved areas, alert store, webhook/e-mail delivery
  app/services/explain.py    AI explanations (Gemini), facts-only prompt, disk cache
  scripts/                   data download, Parquet build, NRT mask validation
  tests/                     pytest suite
frontend/src/
  components/                Explore map, calendar heatmap, charts, method page
  lib/                       AOI helpers, colour ramps, API client
data/                        raw CSVs and processed Parquet (not committed)
docs/                        scientific method, data sources, implementation status
```

## Status

Phases 0–5 of the build plan are done:
- data pipeline
- harmonization, including the pre-Aqua correction
- analytics and exports
- map and area tools
- calendar heatmap
- near-real-time and Method page

Phase 6 (deployment and demo) is not started. See [docs/IMPLEMENTATION_STATUS.md](docs/IMPLEMENTATION_STATUS.md).

## Known limitations

- Hotspots are thermal detections at overpass times, not fire perimeters, burned area or fire counts. Clouds and smoke hide fires.
- One k and one c per area. The real sensor relationship varies with season, fire type and orbit drift.
- FIRMS VIIRS S-NPP NRT ended on 2026-11-02, so the current month uses MODIS NRT × k. NOAA-20/21 VIIRS are not used.
- Only United States data are loaded.
- The farmland filter and land-cover summaries use NLCD, which covers the conterminous US only. Burn severity mosaics stop at 2021.
- The outlook is a statistical persistence model with modest skill, not a fire-danger forecast.

## Data and attribution

Fire data: NASA Fire Information for Resource Management System (FIRMS), <https://firms.modaps.eosdis.nasa.gov/>. MODIS Collection 6.1 and VIIRS S-NPP 375 m active fire products. Country boundary, places and roads: Natural Earth (public domain). Population and WUI: US Census 2020 via USFS / SILVIS Lab WUI 2020. Protected areas: USGS PAD-US. Hospitals: HIFLD (ORNL/DHS). Schools: NCES EDGE. Fire stations: USGS National Structures Dataset. Airports: OurAirports. Land cover: NLCD 2021 (MRLC). Reported fires: National Interagency Fire Center (NIFC) WFIGS Incident Locations. Drought: U.S. Drought Monitor (NDMC, USDA, NOAA). Fire weather: NOAA National Weather Service. Basemap © CARTO, © OpenStreetMap contributors.
