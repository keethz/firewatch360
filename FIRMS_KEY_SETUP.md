# Getting a NASA FIRMS MAP_KEY

1. Open https://firms.modaps.eosdis.nasa.gov/api/map_key/
2. Enter your email address and click **Request MAP_KEY**.
3. Check your inbox (and spam folder) for an email from FIRMS. The key is a 32-character string.
4. Test it in a browser (replace YOUR_KEY):
   https://firms.modaps.eosdis.nasa.gov/mapserver/mapkey_status/?MAP_KEY=YOUR_KEY
   It should show your transaction limit and current usage.
5. Test a real data request (last 1 day of VIIRS over California):
   https://firms.modaps.eosdis.nasa.gov/api/area/csv/YOUR_KEY/VIIRS_SNPP_NRT/-124.5,32.5,-114,42/1
   You should get CSV rows (or just a header if there were no fires).
6. Copy `.env.example` to `.env` in the project root (never commit it) and set:
   ```
   FIRMS_MAP_KEY=YOUR_KEY
   ```
   `.env` is already in `.gitignore`.

Notes
- The key is free. There is a limit on how many requests you can make within a 10-minute window; step 4 shows yours.
- The area API covers recent data only, for a limited number of days per request. Historical data comes from the yearly country files in `data/raw/` (see `data/README.md`).
- Check the API page (https://firms.modaps.eosdis.nasa.gov/api/) for the current source names and limits.
