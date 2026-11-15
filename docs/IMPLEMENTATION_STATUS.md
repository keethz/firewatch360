# Implementation Status

_Last updated: 2026-11-14_

## Phase 0: Scaffold and data (done)
- [x] FIRMS US yearly CSVs downloaded (MODIS 2000–2025, VIIRS 2012–2025)
- [x] `backend/scripts/prepare_data.py`: CSV → normalized Parquet, partitioned by sensor and year, row counts verified
- [x] `data/processed/coverage.json` generated from the data
- [x] FastAPI app with `GET /api/health` and `GET /api/coverage`, plus CORS for the frontend
- [x] Frontend scaffold (Vite, React, TypeScript, Tailwind, TanStack Query, MapLibre and Recharts installed) with an `/api` proxy and coverage cards
- [x] `.env.example`, `.gitignore`, README, docs

### Test and build status
- Backend `pytest`: 3 passed (after the 2026 merge)
- Frontend `npm run build`: passes

- [x] 2026 data fetched from the FIRMS API (standard and NRT), clipped to the US and merged with a `processing_level` field

## Blocked or pending
- [x] `FIRMS_MAP_KEY` set in `.env` and checked (limit 5000 transactions per 10 min). The NRT CSV has no `type` column.

## Phase 1: Harmonization (done)
- [x] All project code moved into `firewatch360/`
- [x] `app/services/aoi.py`: bbox, circle and GeoJSON polygon AOIs, validated
- [x] `app/services/harmonize.py`: gridding, unique cell-days, ratio fit, leave-one-year-out validation, insufficient-overlap fallback, harmonized series, cached per AOI and parameters
- [x] `GET /api/calibration` and `GET /api/harmonized` (params: AOI, `grid_deg`, `min_confidence`, `vegetation_only`)
- [x] Frontend calibration panel: k, R², validation, raw MODIS / raw VIIRS / scaled MODIS chart, preset and bbox AOIs
- [x] Fixed: relative `DATA_DIR` now resolves against the project root (before, Phase 0 data tests skipped silently when run from `backend/`)

### Test and build status
- Backend `pytest`: 20 passed (17 Phase 1 tests, including real-data checks)
- Frontend `npm run build`: passes

## Phase 2: Analytics and exports (done)
- [x] `app/services/analytics.py`: complete years, baseline, climatology (median, mean, P25/P75, MAD), robust-z anomalies, calendar matrix, rankings, peak season, Theil–Sen trend, latest month vs median
- [x] `GET /api/calendar`, `/api/climatology`, `/api/anomalies` (params: AOI, `start`/`end` YYYY-MM, `year`, `baseline_start`/`baseline_end`, `z_threshold`, plus Phase 1 options)
- [x] `GET /api/export/calendar.csv` and `/api/export/hotspots.csv` (DuckDB COPY to a temp file, 2M row limit)
- [x] Frontend: shared area picker, "Historical comparison" panel (latest vs median, peak season, trend, top months and years, anomaly list), CSV download buttons

### Test and build status
- Backend `pytest`: 33 passed (13 Phase 2 tests)
- Frontend `npm run build`: passes

## Phase 3: Map, area drawing, hotspots layer (done)
- [x] `GET /api/hotspots`: GeoJSON points for an AOI and UTC date range, filters for sensors, confidence and vegetation-only. Returns `total` and per-sensor counts; above `limit` (default 20,000, max 50,000) it returns a deterministic sample and `sampled: true`
- [x] Frontend tabs: Explore, Calendar & history, Method & data. One shared area of interest across all tabs
- [x] Explore: MapLibre map (CARTO dark basemap, plain background if offline), hotspots as a GeoJSON circle layer coloured by sensor (Okabe–Ito) with a labelled legend, white outline on provisional NRT points, click popup (sensor, UTC time, FRP, confidence)
- [x] Area tools: presets, drag a rectangle, click a polygon (double-click or Finish), click a circle centre, or type lat/lon + radius. Esc cancels
- [x] Summary cards: hotspots in the date range, peak month on record, latest month vs median
- [x] Fixed MapLibre 6 worker loading under Vite (`setWorkerUrl` with a `?url` import)

### Test and build status
- Backend `pytest`: 38 passed (5 Phase 3 tests)
- Frontend `npm run build` and `oxlint`: pass
- Checked in the browser: rectangle, polygon, circle and lat/lon AOIs, popup, all three tabs

## Phase 4: Calendar heatmap and comparison chart (done)
- [x] Calendar tab: year × month heatmap of harmonized fire cell-days, 2000 → latest
  - Counts view: log colour scale from the smallest non-zero month to the max, Viridis or Cividis, numeric legend ticks
  - Anomaly view: robust z on a diverging ramp (blue–orange or purple–orange), clipped at ±4
  - Source column (MODIS × k / mixed / VIIRS) and a divider at the switch month; partial years marked `*`; no-data cells hatched; anomalous months outlined in the counts view (shape cue, not colour alone)
  - Hover/focus tooltip: harmonized value, raw MODIS and VIIRS, median, % vs median, z, anomaly flag. Each cell has a descriptive `aria-label`
  - Values on/off, baseline-year selectors, calendar CSV download
- [x] Selected year vs baseline chart: year line, baseline median, P25–P75 band, ringed anomalous months. Uses exactly the calendar's baseline
- [x] Historical comparison panel moved under the calendar and follows the chosen baseline

### Test and build status
- Backend `pytest`: 38 passed (no backend changes this phase)
- Frontend `npm run build` and `oxlint`: pass
- Checked in the browser: both views, ramps, year selection, chart, tooltip

## Fix: Terra-only undercount before Aqua (done)
- [x] Found in Phase 4: US-wide 2001–2002 showed strongly negative anomalies. In the data, Aqua starts 2002-07-04, and when both fly Terra+Aqua see about 2× Terra alone
- [x] Terra-only cell-days counted separately; factor c fitted on 2002-08 to 2012-01 with fallback and leave-one-year-out validation; pre-Aqua months use `k × c × Terra`
- [x] `/api/calibration` returns `terra_correction`; `/api/harmonized` and the analytics endpoints carry `terra_c`; calendar CSV adds `modis_terra_cell_days` and `terra_c` in the header
- [x] UI: source column (Terra × c·k / mixed / MODIS × k / VIIRS), a dashed line at the Aqua start, tooltip labels, and a Terra correction section on the Method tab
- [x] Tests: 42 passed (4 new: known-factor recovery, fallback, no-Aqua case, real data)

## Phase 5: Near-real-time, Method page, polish (done)
- [x] **Provisional months.** After the final data (VIIRS through 2026-07), months up to the last complete NRT month (2026-08 to 2026-10) are added from FIRMS standard and NRT data, with `provisional: true`, source `VIIRS_provisional`. They never count toward a complete baseline year
- [x] **Static-source mask** for NRT (which has no `type`): drop NRT detections in grid cells ever flagged type 1/2/3 in the archive. Checked on 2026 standard months with known type: VIIRS −2.3% to −6.5%, MODIS 0.0% to +0.4% (no mask: +14% to +44% and +2.5% to +15%). Script: `backend/scripts/validate_mask.py`
- [x] **Current month** (`GET /api/current`, and `current_month` in `/api/anomalies`): month-to-date vs the same calendar days in each baseline year. Uses the sensor whose NRT reaches furthest (MODIS × k when VIIRS lags) and drops the last NRT day, which is usually still being filled
- [x] **Live FIRMS proxy** (`GET /api/nrt`): last 1–10 days, server-side key, disk cache (`data/cache/nrt`, recent chunks refresh after 3 h), exact AOI filtering. Errors never include the key
- [x] Frontend:
  - Current-month card (provisional badge) on Explore and Calendar
  - Provisional calendar cells (dashed outline, tooltip badge, legend)
  - Live NRT ring layer with day selector on the map
- [x] Method & data tab: how it works (live k and c), calibration panel, parameters table, sources and as-of dates, limitations, attribution and disclaimer
- [x] Polish: footer disclaimer on every tab, arrow-key tab navigation, no horizontal page scroll at 375 px on any tab

### Findings
- FIRMS `VIIRS_SNPP_NRT` ends 2026-11-02 (checked with the data_availability endpoint on 2026-11-14), while MODIS_NRT and VIIRS NOAA-20/21 NRT run to today. The current month therefore uses MODIS NRT × k.
- US November 2026, 1–13 Nov: 4,208 VIIRS-equivalent cell-days vs median 7,084 (−41%, z −1.1, not anomalous; provisional).

### Test and build status
- Backend `pytest`: 51 passed (9 Phase 5 tests; the FIRMS API is mocked in tests)
- Frontend `npm run build` and `oxlint`: pass
- Checked in the browser: live layer, current-month card, provisional calendar cells, Method page, phone width

## Feature: Fire Risk Brief (done)
- [x] `GET /api/brief`:
  - headline, current activity vs median, historical rank, anomaly level, sensor agreement, confidence (with reasons), peak season, long-term trend (Mann–Kendall), baseline and factors
  - the rules are returned with the brief
- [x] Prominent card at the top of Explore and Calendar:
  - level shown as colour plus icon plus text
  - "Brief for" picker: latest data or any past month
  - "How is this decided?" disclosure
- [x] One-click **PDF** (A4 landscape, about 230 KB) and **PNG** download of the brief, made in the browser (`html-to-image`, `jspdf`, loaded only on click)
- [x] Tests: 60 passed (9 brief tests: rules, ranks, agreement, confidence, Mann–Kendall, end-to-end synthetic and real data)
- [x] Checked in the browser: California Sep 2020 brief, PNG and PDF generation, phone width

## Feature: Human-impact overlays (done)
- [x] Scope change: the build plan listed community-asset analysis as out of scope; added at the user's request.
- [x] Sources for all eight layers (see DATA_SOURCES): Census 2020 population and SILVIS WUI 2020 (USFS), Natural Earth places and roads, USGS PAD-US, OurAirports, HIFLD hospitals (PASDA), NCES schools, NLCD 2021 agriculture (MRLC)
- [x] Fire stations added as a ninth layer (USGS National Structures, 52,010 fire/EMS stations): count in area and near fire, nearest stations, map overlay (F markers).
- [x] `backend/scripts/prepare_impact.py` builds the static layers (about 20 s)
- [x] `GET /api/impact`:
  - each layer inside the area and within the exposure distance of fire detections
  - nearest named facilities
  - per-layer status and sources
  - live results cached on disk
- [x] `GET /api/impact/features`: GeoJSON for the map
- [x] Explore:
  - "Impact layers" panel: exposure distance and 9 overlay toggles
  - map overlays: lettered point markers, roads, fire exposure zone, PAD-US / WUI / NLCD tiles
  - "What could this affect?" summary with headline sentence and 9 tiles
- [x] Tests: 71 passed (11 impact tests; external services mocked)
- [x] Checked in the browser: Thomas Fire scenario, overlays, legend, phone width

Known limits: WUI and land cover cover the lower 48 only; WUI tiles are slow (about 8 s per tile from USFS); PAD-US tiles stop at zoom 7; the first WUI query for a very large area can take up to about 75 s (then cached).

## Anomaly detection and AI explanations

- [x] Per-cell anomaly map (`/api/anomaly-map`, Anomaly map tab): map cells of 0.1°–1°, robust z vs the same month in past years, flags, ranked list, colorblind-safe diverging ramps
- [x] AI explanations (`/api/explain/brief`, `/api/explain/cell`) with Google Gemini, facts-only prompt, disk cache, rule-based fallback
- [x] Tests (5 new, model calls mocked); checked in the browser (Oct 2026, selected cell, AI-off fallback)
- [ ] Not done: Isolation Forest (one value per cell per month gives it little to add over robust z); AI output checked live on the Sep 2020 Creek Fire cell; numbers match the statistics

## Alerts, drought & fire-weather context, compare

- [x] Drought (U.S. Drought Monitor) and NWS fire-weather context: `/api/context`, card on Explore and anomaly cells, added to AI facts
- [x] Saved-area alerts: unusual activity, new NRT detections within X km, Red Flag / Fire Weather Watch; inbox, webhook, SMTP e-mail; `scripts/check_alerts.py` for cron
- [x] Compare tab: two years or two areas, cell-days or % vs own median
- [x] Tests (6 new, network mocked); checked in the browser (California drought, 2020 vs 2025, saved area with a live alert, phone width)
- [ ] Not done: e-mail not tested against a real SMTP server; no scheduler inside the app (use cron); no user accounts (saved areas are shared by everyone using this server)

## Reported fires and image downloads

- [x] NIFC reported fires: `/api/reported`, `/api/reported/monthly`; Explore card, map layer (size by acres), Calendar chart vs satellite cell-days. Checked: California Sep 2020 → 1,045 wildfires, 981,333 acres, largest Creek, Slater, August Complex.
- [x] PNG downloads for the calendar and charts (footer with area, date, source)
- [ ] PNG downloads not yet tested in a visible browser (the hidden preview pane pauses rendering)

## PDF downloads and fire intensity (FRP) calendar (done)

- [x] "Download all as PDF" on the Calendar tab (every panel) and the Explore tab (summary cards, map, open detail panel), one A4 landscape page each. Checked in a visible browser: 4 and 3 pages.
- [x] `metric` parameter (`cell_days` default, `frp`) on every harmonized endpoint. FRP = fire radiative power summed over all detections per month (MW), negative MODIS values clipped to 0. Same k / c / baseline / anomaly / trend pipeline.
- [x] Calendar tab: Activity / Intensity (FRP) switch drives the heatmap, legend, tooltip, year chart, CSV (`*_frp_mw` columns) and image/PDF captions. The historical summary panel stays on cell-days.
- [x] US-wide FRP fit: k = 0.492 (R² 0.935, leave-one-year-out annual error 16%), Terra c = 2.07. Cell-days for comparison: k = 2.07, error 5%. Sep 2020 FRP: 3.85 M MW vs median 0.36 M (z 18.7).
- [x] Tests: 95 passed (4 FRP tests: summing and clipping, known-ratio recovery, validation, real-data API and CSV)

## Fire types, outlook, burned area and smoke (done)

- [x] Agricultural-burning filter: `prepare_cropland.py` (NLCD 2021 cropland/pasture share per 0.01° fire cell, 87 s), `exclude_agriculture` on all harmonized endpoints and `/api/hotspots`, header switch applied to every tab. Mississippi Delta 2024 −85%, Sierra Nevada −29% (includes Central Valley edge), US −22%.
- [x] `/api/outlook`: 1–3 month persistence outlook with leave-one-year-out hindcast skill, tercile probabilities, expected value and 80% range; works with either metric and the farmland filter. Explore "Outlook" tab and a Calendar panel.
- [x] `/api/burned` (WFIGS 2020+, MTBS 1984+; burned acres inside the area; yearly record) and `/api/burned/fire` (post-fire assessment: detections and daily FRP inside the perimeter, MTBS severity to 2021, pre-fire NLCD land cover, assets inside). Explore "Burned area" tab, fire-perimeter map layer, click a fire on the map or table.
- [x] `/api/smoke` (NOAA HMS daily smoke, share of area by density, smoke-day series). Explore "Smoke" tab and smoke map layer.
- [x] Checked: California 2020 → 768 fires, 3.79 M acres inside; DOE fire (August Complex) → 44,436 detections over 65 days, 26% high severity, mostly evergreen forest and shrub (NLCD 2019); California 10 Sep 2020 → 100% under smoke, 72% heavy.
- [x] Found while testing: NLCD 2021 maps 2020 burn scars as grassland, so post-fire land cover uses the epoch before the fire.
- [x] Tests: 107 passed (12 new: farmland filter, outlook skill and no-skill, smoke coverage, burned area clipping and yearly record, severity years, API checks)

## Next: Phase 6 (deploy, README, demo recording)
