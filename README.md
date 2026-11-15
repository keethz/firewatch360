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
- **Fire investigation agent** (Investigate tab, or "Investigate this cell" on the Anomaly map): an AI agent (Gemini function calling) investigates an area and month by calling the app's own tools: fire activity vs history, MODIS/VIIRS agreement, anomalous cells, NIFC incidents, drought and fire weather, exposed community assets, burned area, smoke and outlook. It can zoom a tool into an anomalous cell. Each call is streamed live with the agent's reason and stored as numbered evidence; the report's findings must cite evidence, and every number in a finding is checked against the cited evidence (✓ verified / ⚠ unverified). Without a Gemini key, or if the model fails, a rule-based investigator runs the same tools. Evidence downloads as JSON; the report as PDF.
- **Exclude farmland fires**: a checkbox on the Explore tab (under "Vegetation fires only") that drops detections in 0.01° cells that are at least half cropland or pasture (NLCD 2021) from every tab, separating agricultural burning from wildfires. Built once, from the `firewatch360` folder, with `backend/.venv/bin/python backend/scripts/prepare_cropland.py` (about 2 minutes the first time).
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


## Known limitations

- Hotspots are thermal detections at overpass times, not fire perimeters, burned area or fire counts. Clouds and smoke hide fires.
- One k and one c per area. The real sensor relationship varies with season, fire type and orbit drift.
- FIRMS VIIRS S-NPP NRT ended on 2026-11-02, so the current month uses MODIS NRT × k. NOAA-20/21 VIIRS are not used.
- Only United States data are loaded.
- The farmland filter and land-cover summaries use NLCD, which covers the conterminous US only. Burn severity mosaics stop at 2021.
- The outlook is a statistical persistence model with modest skill, not a fire-danger forecast.

## Data and attribution

Fire data: NASA Fire Information for Resource Management System (FIRMS), <https://firms.modaps.eosdis.nasa.gov/>. MODIS Collection 6.1 and VIIRS S-NPP 375 m active fire products. Country boundary, places and roads: Natural Earth (public domain). Population and WUI: US Census 2020 via USFS / SILVIS Lab WUI 2020. Protected areas: USGS PAD-US. Hospitals: HIFLD (ORNL/DHS). Schools: NCES EDGE. Fire stations: USGS National Structures Dataset. Airports: OurAirports. Land cover: NLCD 2021 (MRLC). Reported fires: National Interagency Fire Center (NIFC) WFIGS Incident Locations. Drought: U.S. Drought Monitor (NDMC, USDA, NOAA). Fire weather: NOAA National Weather Service. Basemap © CARTO, © OpenStreetMap contributors.
