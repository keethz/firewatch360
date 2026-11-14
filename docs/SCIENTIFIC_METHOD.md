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
  - only one sensor available
  - sensors disagree
  - baseline median < 20 cell-days (two levels if < 5)
  - fewer than 10 baseline years
  - month-to-date covering fewer than 10 days
  - US-wide fallback calibration
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
