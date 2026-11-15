# Scientific Method

## Observation normalization (Phase 0, done)
Each FIRMS row becomes one record, and no rows are dropped or merged.
- `acq_datetime_utc` = `acq_date` + `acq_time` (HHMM, UTC, zero-padded)
- `confidence_norm`:
  - MODIS: <30 low, 30–79 nominal, ≥80 high
  - VIIRS: l/n/h becomes low/nominal/high
  - `confidence_raw` keeps the original value.
- `brightness_k`: MODIS `brightness` (channel 21/22) or VIIRS `bright_ti4`
- `brightness_2_k`: MODIS `bright_t31` or VIIRS `bright_ti5`
- All other source columns are kept.

The script checks that the row count per sensor in the CSVs equals the count in the Parquet output.

## Harmonization (Phase 1, done)
Code: `backend/app/services/harmonize.py`. Endpoints: `GET /api/calibration`, `GET /api/harmonized`.

1. **Filter.** Only final data (`processing_level` archive or standard). NRT rows are provisional and lack `type`, so they are left out. By default `type = 0` (presumed vegetation fire) and `confidence_norm` nominal or high.
2. **Common grid.** Each detection is snapped to a cell of `grid_deg` (default 0.01°, about 1 km north–south; east–west it narrows with latitude). Cell index = `floor(coord / grid_deg)`.
3. **Unique fire cell-days.** Count distinct (cell, UTC date) per sensor per month. Several VIIRS 375 m pixels in one cell count once, and Terra and Aqua MODIS overpasses on the same day count once.
4. **Overlap calibration.** Overlap = first full VIIRS month (2012-02; VIIRS starts 2012-01-20) to the last month complete for both sensors. A month enters the fit if MODIS has at least `min_monthly_cell_days` (5) cell-days. Fit `VIIRS = k × MODIS` by least squares through the origin: `k = Σxy / Σx²`. R² is `1 − SS_res / SS_tot` with centred SS_tot. An ordinary `a + b × MODIS` fit is reported as a check.
5. **Terra-only correction.** Aqua MODIS starts 2002-07-04, so from 2000-11 to 2002-07 only Terra observed, roughly half the daily overpasses. On 2002-08 to 2012-01 (both MODIS satellites, before VIIRS) fit `MODIS(Terra + Aqua) = c × MODIS(Terra only)` the same way as k, with the same guards, fallback and leave-one-year-out validation. Terra-only cell-days are counted separately (`satellite = 'Terra'`).
6. **Harmonized series** in VIIRS-equivalent cell-days:
   - 2000-11 to 2002-07: `k × c × MODIS(Terra only)` (source `MODIS_terra_scaled`)
   - 2002-08 to 2012-01: `k × MODIS(Terra + Aqua)` (source `MODIS_scaled`)
   - 2012-02 onward: observed VIIRS (source `VIIRS`)
7. **Validation.** Leave-one-year-out over the overlap years: refit k without year Y, predict Y. Reported: annual-total error and monthly MAPE (months with VIIRS > 0), per year and averaged.
8. **Guard.** Fewer than `min_overlap_months` (24) usable months in the AOI gives `status: insufficient_overlap` and the US-wide factor, labelled `scope: region_fallback`.
9. **AOI.** bbox, circle (haversine distance) or GeoJSON polygon (exact point-in-polygon with Shapely). Filtering is on the detection's own coordinates.

### Results on the loaded data (2026-11-14, default parameters)
| AOI | k | R² | Months in fit | LOYO annual error | LOYO monthly MAPE |
|---|---|---|---|---|---|
| United States | 2.066 | 0.918 | 174 | 5.2% | 13.5% |
| California bbox (−124.5, 32.5, −114.1, 42.0) | 1.775 | 0.981 | 174 | 10.0% | 21.9% |

Terra-only correction (fit 2002-08 to 2012-01):

| AOI | c | R² | Months in fit | LOYO annual error | LOYO monthly MAPE |
|---|---|---|---|---|---|
| United States | 1.836 | 0.879 | 114 | 11.4% | 19.3% |
| California bbox | 1.688 | 0.991 | 114 | 10.9% | 17.3% |

Before this correction the US 2001 total was 79k harmonized cell-days. After it, 144k, in line with 2002–2003 (167k, 188k). Most 2001 months moved from strongly negative z (down to −5.4) to within ±2.

The factor differs by region, so the AOI factor is preferred when there is enough data.

### Parameters and limitations
All defaults (grid size, confidence threshold, 5 cell-day and 24 month minimums) are **prototype parameters that need scientific validation**. This is not an official NASA algorithm.
- The factor varies by region, fire type and season. One k per AOI does not capture seasonal change.
- Terra and Aqua orbits have drifted over time, and Terra's overpass time has shifted since about 2020.
- VIIRS detects more small and cool fires, so the relationship is not strictly proportional (see the linear check's intercept).
- The harmonized pre-2012 series inherits MODIS's lower sensitivity. Scaling corrects the mean level, not missed small fires in a given month.
- Before Aqua (2002-07-04) only Terra observed. This is corrected with factor c (step 5), which assumes the Terra/Aqua relationship seen in 2002–2011 also held in 2000–2002. A few US-wide spring months in 2001–2003 remain below normal (z −2 to −4.5). After the correction these may be real, but they have not been checked against independent records.
- The series ends at the last complete month of final VIIRS data. NRT months are handled separately (Phase 5).

## Fire intensity metric (FRP)

`metric=frp` replaces unique cell-days with total fire radiative power per month: the FRP (MW) of every
retained detection, summed, with no cell-day deduplication (two pixels burning in one cell both add energy).
Negative MODIS FRP retrievals are clipped to 0. Filters (confidence, vegetation-only, static-source mask) are the same.

The total depends on the sensor (VIIRS 375 m pixels see more small fires but report lower per-pixel FRP) and on the
number of overpasses, so it goes through the same harmonization: VIIRS FRP = k · MODIS FRP on the overlap, and
Terra+Aqua = c · Terra before Aqua. A month enters a fit when MODIS FRP ≥ 20 MW × `min_monthly_cell_days`.

US-wide: k ≈ 0.49 (R² 0.94) but leave-one-year-out annual error ≈ 16%, against ≈ 5% for cell-days. A summed FRP is
dominated by a few very large fires and is less stable year to year than an extent count. Read FRP as a relative
intensity index, not as energy released; it is a sum of instantaneous snapshots, not time-integrated fire radiative energy.

## Agricultural-burning filter

`exclude_agriculture=true` drops detections whose 0.01° cell is at least 50% cropland or pasture/hay in NLCD 2021
(classes 81, 82; 16 sample points per cell on a ~90 m raster). It applies to the harmonized series (so k, c,
baselines and anomalies are recomputed on non-agricultural fires) and to the hotspot map. Effect in 2024: Mississippi
Delta −85%, US −22%. Limits: a wildfire that burns into fields is partly removed; rangeland burning (NLCD grassland,
e.g. the Flint Hills) is kept; Alaska and Hawaii are not filtered.

## Fire activity outlook (experimental)

On the harmonized monthly series x = log(1 + value): standardized anomaly a = (x − μ_m) / σ_m per calendar month
(σ floored at 0.25). Predictor P = mean anomaly of the last 3 months. For lead h = 1, 2, 3: a(t+h) ≈ β_h · P(t),
β_h fitted through the origin on all past issue months, residual spread s_h. Forecast: a normal distribution
N(β_h · P, s_h) in anomaly space, converted to an expected value and 10th–90th percentile range, and to
probabilities of the climatological terciles (below / near / above normal).

Hindcast: for every complete year Y, μ, σ and β are refitted without Y and the outlooks issued in Y are scored.
Mean square skill score vs climatology (predicting a = 0) and tercile hit rate are returned per lead.
A lead counts as "useful" when MSSS > 0.05. Results on 2001–2025: California MSSS 0.16 / 0.09 / 0.08 for leads
1–3 (hit rate 43% vs 33% by chance); US 0.08 / 0.01 / −0.01. It uses no weather, fuel or ignition information.

## Burned area and post-fire assessment

Fires intersecting the area for a year: WFIGS perimeters (2020+) or MTBS boundaries (before 2020). Burned acres
inside the area = area of the union of perimeters clipped to the area (overlaps counted once, local equal-area
projection). The yearly record uses whole-fire acres (MTBS sum per year; WFIGS sum per discovery year).

Post-fire assessment of one fire: satellite detections inside the perimeter from 3 days before the discovery date to
3 days after containment (or 150 days), with daily count and FRP; MTBS severity class areas from the mosaic for the
fire year (Web Mercator pixels corrected by cos² latitude); NLCD class areas from the epoch before the fire; counts
of mapped places, hospitals, schools, fire stations and airports inside, and road length.

## Smoke

NOAA HMS daily smoke polygons intersected with the area. Where densities overlap, the heaviest counts. A smoke day
is a day with ≥ 10% of the area under any smoke; a heavy-smoke day has ≥ 10% under heavy smoke.

## Analytics (Phase 2, done)
Code: `backend/app/services/analytics.py`. Endpoints: `/api/calendar`, `/api/climatology`, `/api/anomalies`, `/api/export/calendar.csv`, `/api/export/hotspots.csv`.

All statistics use the harmonized monthly series (VIIRS-equivalent fire cell-days).
- **Complete year**: a year with all 12 months in the series. 2000 (Nov–Dec only) and the current year are partial.
- **Baseline**: by default every complete year before the selected year (default selected year = latest year in the window). Configurable with `baseline_start` / `baseline_end`. Fewer than 5 baseline years gives `insufficient_history`, and no anomalies are computed.
- **Climatology** per calendar month over the baseline years: median, mean, P25, P75 (linear interpolation) and MAD (median absolute deviation).
- **Anomaly**: robust z = (value − median) / (1.4826 × MAD). The 1.4826 factor scales the MAD to match a standard deviation for normal data, so |z| ≥ 2 (default, `z_threshold`) reads like "2 sigma". If MAD = 0 the z-score is undefined and the month is not flagged. Baseline years are scored against a baseline that includes themselves, so their z-scores are slightly conservative.
- **Latest month vs median**: the last month of final data in the window. NRT months are added in Phase 5.
- **Rankings**: top 5 months by value, and top 5 complete years by annual total.
- **Peak fire season**: calendar months whose baseline median is above the mean of the 12 monthly medians.
- **Trend**: Theil–Sen slope (median of pairwise slopes) of annual totals over complete years. Descriptive only: no significance test.

### Exports
- `calendar.csv`: one row per month (harmonized value, source, raw MODIS and VIIRS cell-days, baseline median, % vs median, robust z, anomaly flag). Lines starting with `#` hold the AOI, parameters, k and data-as-of. Read with `pandas.read_csv(..., comment="#")`.
- `hotspots.csv`: every raw detection in the AOI and UTC date range (all types, confidences and processing levels, so users can filter themselves). Requires `start` and `end` (YYYY-MM-DD). Limit 2,000,000 rows (HTTP 413 above that).

## Calendar display (Phase 4)
- Counts colour: log10 scale from the smallest non-zero monthly value in view to the largest. Zero is the darkest colour. Sequential ramps Viridis and Cividis are perceptually uniform and colour-vision-deficiency safe.
- Anomaly colour: robust z clipped to ±4 on a diverging ramp centred on 0 (white).
- The year comparison chart and the calendar use the same baseline years.

## Near-real-time (Phase 5)
- **Provisional months.** After the last final VIIRS month, monthly cell-days come from FIRMS standard and NRT rows, up to the last complete NRT month. They are labelled provisional and are excluded from baselines.
- **Static-source mask.** NRT rows have no `type`. With `vegetation_only`, an NRT detection is dropped if its grid cell ever had a type 1, 2 or 3 detection in the archive (17,977 cells at 0.01°). Checked on 2026 standard-processing months, where the true type is known:

| Sensor | Months | Masked vs true type-0 cell-days | No mask |
|---|---|---|---|
| VIIRS | 2026-01..07 | −2.3% to −6.5% | +14% to +44% |
| MODIS | 2026-01..08 | 0.0% to +0.4% | +2.5% to +15% |

  The mask slightly under-counts VIIRS (some cells near industrial sites also burn). No bias correction is applied. Rerun with `backend/scripts/validate_mask.py`.
- **Current month.** Month-to-date value vs the harmonized value for the same days (1..D) of that month in each baseline year. Median, P25/P75, robust z and rank are reported, labelled provisional. The sensor is whichever NRT feed reaches furthest (VIIRS on a tie); MODIS is scaled by k. The last NRT day is dropped as possibly incomplete (for example 2026-11-14 had 5 MODIS overpasses vs about 20 on full days).
- **Live map layer.** `/api/nrt` shows raw FIRMS NRT detections for the last 1–10 days, not type-filtered. Not used in any statistic.

## Fire Risk Brief
Code: `backend/app/services/brief.py`. Endpoint: `GET /api/brief` (AOI and the analytics parameters; `end=YYYY-MM` for a past month). It describes observed detections only and does not forecast fire danger.

- **Reference period**: the current month-to-date if it covers at least 7 days (and the requested window reaches the present). Otherwise the latest complete month in the window, which may be provisional.
- **Current activity**: % difference between the harmonized value and the baseline median for the same calendar period (same days 1..D for month-to-date).
- **Historical rank**: among every year the record covers for that period, including years with zero detections. "Nth-highest" or "Nth-lowest", whichever is nearer.
- **Anomaly level** (robust z):

  | Level | z |
  |---|---|
  | Severe | ≥ 3 |
  | Moderate | ≥ 2 |
  | Below normal | ≤ −2 |
  | Normal | otherwise |

  The headline sentence follows the level.
- **Sensor agreement**: MODIS and VIIRS raw cell-days are each compared with their own history for the period (MODIS from 2002-08 with Terra + Aqua; VIIRS from 2012-02) and classified high, normal or low. Labels:
  - `MODIS and VIIRS agree`
  - `VIIRS-only signal` / `MODIS-only signal`
  - `MODIS and VIIRS disagree`
  - single-sensor, when the other has no data for the period
- **Confidence**: starts High; one level lower for each of the following:
  - provisional data
  - sensors disagree (when both report)
  - baseline median < 20 cell-days (two levels if < 5)
  - fewer than 10 baseline years
  - month-to-date covering fewer than 10 days
  - US-wide fallback calibration

  One sensor alone does not lower confidence: MODIS-only years before 2012 are normal, and the current month relies on MODIS since the VIIRS S-NPP NRT feed ended. Confidence is always Low when neither sensor has usable data for the period.
- **Long-term activity**: Mann–Kendall test on annual totals of complete years with a Theil–Sen slope. "Increasing" or "Decreasing" only when p < 0.05.

Checks against known events (default parameters):

| AOI, period | Level | Rank | Agreement | Confidence |
|---|---|---|---|---|
| California bbox, Sep 2020 | Severe (+1,533%) | Highest September since 2001 | agree (MODIS z 27, VIIRS z 45) | High |
| California bbox, Aug 2021 | Severe (+574%) | Highest August since 2001 | agree | High |
| 60 km around Santa Barbara/Ventura, Dec 2017 (Thomas fire) | Severe | Highest December since 2000 | agree | Medium (small baseline) |
| US, Nov 2026 days 1–13 | Normal (41% below) | 2nd-lowest since 2000 | MODIS only (VIIRS NRT ended) | Low |

## Human impact: "What could this affect?"
Code: `backend/app/services/impact.py`. Endpoints: `GET /api/impact`, `GET /api/impact/features`.

- **Inputs**: the area of interest, the fire detections in it for the selected UTC dates (same sensor, confidence and vegetation filters as the map), and an exposure distance (default 5 km).
- **Fire exposure zone**: detections are snapped to 1 km cells (moves each by ≤0.7 km), buffered by the distance and unioned, then clipped to the area. Distances and areas use an azimuthal equidistant projection centred on the area.
- **Point layers** (population centers, hospitals, schools, airports): counted inside the area and within the distance of the nearest detection. The nearest items are listed with their distance.
- **Roads**: length inside the area and inside the exposure zone.
- **Population and WUI**: sums of 2020 Census population and housing units over blocks intersecting the area, and over blocks intersecting the zone. WUI is the SILVIS 2020 interface and intermix classes.
- **Protected areas**: PAD-US polygons clipped to the area. Overlapping designations are unioned for totals; per-unit areas are listed.
- **Agriculture**: NLCD 2021 pixels (cultivated crops 82, pasture/hay 81) inside the area and zone, from a raster downsampled to ≤1,500 px per side (equal-area EPSG:5070).
- Every layer reports a status: `ok`, `unavailable` (with the service error), `too_large`, or `outside_coverage`. No fallback numbers are invented.

**Limits.** "Exposure" means proximity to satellite detections. It is not fire perimeter, damage, evacuation zone or risk. Detections mark hot pixels at overpass times. A 5 km distance is a display choice, not a hazard threshold.

Checks against known events (default 5 km):

| Event | Detections | Residents near fire | Schools | Hospitals (beds) | Protected land near fire |
|---|---|---|---|---|---|
| Thomas Fire, 60 km around 34.4, −119.2, 2017-12-04..31 | 10,810 | 506,230 | 120 | 11 (1,510) | 1,322 km² (Los Padres NF 1,175 km²) |
| Camp Fire, 40 km around 39.76, −121.6, 2018-11-08..25 | 5,398 | 70,137 | 35 | 0 (nearest 6.1 km) | 378 km² (Plumas NF 212 km²) |

## Anomaly map ("where is fire activity unusual?")

- **Cells.** Fire cell-days (0.01° cell × UTC day, the same unit as the series) are grouped into map cells of 0.1°, 0.25° (default), 0.5° or 1°.
- **Harmonization per cell.** For the chosen calendar month, each year's value uses the selected area's factors: `k·c·Terra` before Aqua, `k·MODIS` before VIIRS, observed VIIRS after. Provisional months use NRT VIIRS (MODIS NRT × k if VIIRS is missing) with the static-source mask.
- **Baseline.** The same calendar month in complete years (default 2001 to the year before the target, at least 5 years; the target year is excluded).
- **Score.** Robust z = (value − median) / max(1.4826·MAD, √max(median, 1)). The floor is roughly the Poisson spread of the median count. Without it, a cell that almost never burns would score z in the thousands from a few detections.
- **Flag.** z ≥ threshold (default 2) **and** value ≥ 10 cell-days. Cells with no detections in the month or the baseline are not shown.
- **Limits.** One k and c for all cells (not per cell). Cells that rarely burn (median 0) still reach high z with modest counts; the value and median are always shown beside z. 25 baseline years give a coarse rank only. Not a fire-danger index.
- **Check.** September 2020 flags the Creek Fire (37.3°N, 119.4°W) and August Complex (40°N, 123°W) cells first.

## AI explanations

- Optional; on only if `GEMINI_API_KEY` is set on the server. The key is sent only in a request header to Google and never reaches the browser.
- The model (Google Gemini 3.8 Flash, falling back to 3.5 Flash-Lite when busy; temperature 0.2, low thinking) receives only a JSON object of statistics the app computed (the Brief, or one anomaly cell) and a system prompt: use only these facts, no causes or named fires, units are cell-days, no forecasts or safety advice, mention data caveats, under 120 words.
- Every answer is labelled AI-generated, and the facts sent to the model are returned by the API so they can be checked. Answers are cached on disk by a hash of the facts.
- If the key is missing, the service fails or the model declines, the panel says so and shows the rule-based sentence instead. No numbers come from the model.

## Drought and fire-weather context

- **Drought.** U.S. Drought Monitor weekly maps (GeoJSON, non-overlapping D0–D4 polygons, 2000 onward), simplified to 0.005°. A month uses the last weekly map dated in it (maps are for Tuesdays, published Thursdays). Share per category = intersected area / area of interest, both in the area's local equidistant projection. DSCI = sum of cumulative shares D0+ … D4+ in percent (0–500). Cached on disk per week.
- **Fire weather.** Active NWS Red Flag Warnings and Fire Weather Watches; alerts without a geometry are matched through their fire-weather zones. Current only, cached 15 minutes.
- **Use.** Shown beside the fire statistics and added to the AI explanation facts. Not combined into any score, and not a forecast.

## Alerts

- Saved areas (JSON files in `data/alerts/`, git-ignored). Rules: Fire Risk Brief level Moderate or Severe; FIRMS near-real-time detections (nominal or high confidence, last 2 UTC days) inside the area or within X km, where the area is buffered in metres; active Red Flag Warning / Fire Weather Watch over the area.
- Each event has a key (rule + month, latest detection date, or warning onset), so the same alert is stored and sent once.
- Delivery: in-app inbox; optional webhook (JSON with a ready `text` field, https only) and e-mail via the server's SMTP settings.
- Limits: NRT detections are not filtered for industrial sources; a prototype store for one server, not a multi-user notification service.

## Reported fires (NIFC)

- **Source.** NIFC WFIGS Incident Locations (IRWIN), queried live with ArcGIS statistics. One record per reported incident, with point of origin, discovery time, size, cause and name. Wildfires (`WF`) and prescribed fires (`RX`) are counted separately.
- **Coverage.** 2014 onward; earlier years are almost empty, so the app reports "outside coverage" before 2014.
- **Filtering.** Discovery date (UTC) within the window; point of origin inside the area. The whole-region view counts all US incidents.
- **Comparison with satellites.** The monthly chart shows reported wildfires next to harmonized satellite fire cell-days on separate axes. They measure different things: one large fire is one incident but thousands of cell-days, and many small reported fires are never detected from orbit. Compare timing and shape, not size.
- **Caching.** Periods older than 60 days are cached on disk permanently; recent ones refresh hourly.
