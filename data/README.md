# Data

Source: NASA FIRMS yearly country files, https://firms.modaps.eosdis.nasa.gov/data/country/
Region: United States. Downloaded 2026-11-13.

- `raw/modis/modis_<YEAR>_United_States.csv` covers 2000–2025 (26 files, 232 MB). MODIS C6.1, 1 km.
- `raw/viirs-snpp/viirs-snpp_<YEAR>_United_States.csv` covers 2012–2025 (14 files, 551 MB). VIIRS S-NPP, 375 m.
- 2026 is not archived yet. Use the FIRMS NRT API (MODIS_NRT / VIIRS_SNPP_NRT) for 2026.

Columns:
- MODIS: latitude, longitude, brightness, scan, track, acq_date, acq_time, satellite, instrument, confidence (0–100), version, bright_t31, frp, daynight, type
- VIIRS: latitude, longitude, bright_ti4, scan, track, acq_date, acq_time, satellite, instrument, confidence (l/n/h), version, bright_ti5, frp, daynight, type
