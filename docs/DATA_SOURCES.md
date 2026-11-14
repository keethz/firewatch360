# Data Sources

## NASA FIRMS yearly country archive (historical)
- URL: https://firms.modaps.eosdis.nasa.gov/data/country/
- Region: United States. Downloaded 2026-11-13. Processed into Parquet 2026-11-14 (see `data/processed/coverage.json`).
- **MODIS Collection 6.1** (Terra and Aqua), 1 km nominal pixel. Covers 2000-11-01 to 2025-12-31. 3,063,355 hotspots.
- **VIIRS S-NPP 375 m**. Covers 2012-01-20 to 2025-12-31. 7,150,394 hotspots.
- Variables used: latitude, longitude, acq_date, acq_time (UTC, HHMM), confidence, frp, daynight, type, brightness channels, satellite, scan/track.
- Access: public download, no key needed.

### Limitations
- 2000 covers November and December only. VIIRS 2012 starts on 20 January. Treat both as partial years.
- Confidence scales differ. MODIS uses 0–100, which is mapped to low (<30), nominal (30–79) and high (≥80). VIIRS uses l/n/h. The raw value is kept.
- `type`: 0 is a presumed vegetation fire, 1 an active volcano, 2 another static land source, 3 offshore. Analyses default to type 0.
- 2026 is not in the archive yet. Use the NRT API for it.

## NASA FIRMS Area API (near-real-time)
- URL: https://firms.modaps.eosdis.nasa.gov/api/
- Requires `FIRMS_MAP_KEY`, used on the server only.
- Live proxy `/api/nrt` (Phase 5): sources `VIIRS_SNPP_NRT` and `MODIS_NRT`, last 1–10 days, 5-day chunks, cached in `data/cache/nrt/`.
- Availability on 2026-11-14: `VIIRS_SNPP_NRT` 2026-08-01 → 2026-11-02 (feed ended); `MODIS_NRT` 2026-09-01 → 2026-11-14; `VIIRS_NOAA20_NRT` and `VIIRS_NOAA21_NRT` to 2026-11-14 (not used).

## Processed row counts by year
Generated from `data/processed/coverage.json`. Rows are all hotspots. "Veg" is hotspots with `type` = 0.

| Year | MODIS rows | MODIS veg | VIIRS rows | VIIRS veg |
|---|---|---|---|---|
| 2000 | 3,781 | 3,550 | — | — |
| 2001 | 44,941 | 41,822 | — | — |
| 2002 | 79,715 | 75,368 | — | — |
| 2003 | 114,471 | 108,394 | — | — |
| 2004 | 158,385 | 153,038 | — | — |
| 2005 | 171,160 | 163,401 | — | — |
| 2006 | 126,737 | 121,972 | — | — |
| 2007 | 142,420 | 136,179 | — | — |
| 2008 | 119,797 | 115,573 | — | — |
| 2009 | 115,174 | 112,655 | — | — |
| 2010 | 98,322 | 94,062 | — | — |
| 2011 | 123,867 | 119,959 | — | — |
| 2012 | 137,295 | 131,606 | 536,477 | 439,689 |
| 2013 | 102,348 | 95,938 | 404,761 | 313,027 |
| 2014 | 98,871 | 92,394 | 377,204 | 294,820 |
| 2015 | 146,984 | 139,536 | 581,042 | 490,975 |
| 2016 | 112,736 | 106,797 | 432,975 | 341,845 |
| 2017 | 152,702 | 145,069 | 596,168 | 499,471 |
| 2018 | 130,986 | 119,196 | 556,244 | 471,601 |
| 2019 | 107,213 | 101,971 | 453,400 | 370,646 |
| 2020 | 153,848 | 148,412 | 672,822 | 595,898 |
| 2021 | 163,687 | 156,655 | 664,995 | 582,653 |
| 2022 | 130,358 | 121,943 | 531,089 | 448,706 |
| 2023 | 94,000 | 87,715 | 388,672 | 303,347 |
| 2024 | 123,712 | 118,241 | 484,736 | 406,794 |
| 2025 | 109,845 | 101,782 | 469,809 | 376,029 |

## 2026 (FIRMS area API, fetched 2026-11-14)
- Script: `backend/scripts/fetch_2026.py`. Raw responses are cached in `data/raw/api/<SOURCE>/`.
- Requests use three bounding boxes (lower 48, Alaska, Hawaii) and 5-day chunks, which is the API maximum.
- Rows are clipped to the Natural Earth 10m US boundary with a 0.02° buffer. Applied to the US-only 2025 archive, this boundary keeps 99.996% of points.

| Sensor | Level | Dates | Fetched | Kept (US) |
|---|---|---|---|---|
| MODIS | standard (MODIS_SP) | 2026-01-01 → 08-31 | 104,143 | 92,655 |
| MODIS | nrt (MODIS_NRT) | 2026-09-01 → 11-14 | 14,813 | 13,732 |
| VIIRS | standard (VIIRS_SNPP_SP) | 2026-01-01 → 07-31 | 326,970 | 298,555 |
| VIIRS | nrt (VIIRS_SNPP_NRT) | 2026-08-01 → 11-02 | 189,047 | 167,795 |

NRT rows are provisional and have no `type` value (NULL), so they can't be filtered to vegetation fires. The UI must label NRT months as provisional.

## Basemap
CARTO Dark Matter vector style (https://basemaps.cartocdn.com), © CARTO, © OpenStreetMap contributors. Loaded by the browser only. If it can't be reached, the map shows a plain background and still works.
