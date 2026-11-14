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

## Next: Phase 6 (deploy, README, demo recording)
