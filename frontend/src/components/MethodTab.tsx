import { useQuery } from '@tanstack/react-query'
import { describeAoi, withAoi, type Aoi } from '../lib/aoi'
import { getJson } from '../lib/api'
import type { CalibrationResponse, Coverage } from '../types/api'
import CalibrationPanel from './CalibrationPanel'

const f3 = (v: number | null | undefined) => (v == null ? '—' : v.toFixed(3))

export default function MethodTab({ aoi }: { aoi: Aoi }) {
  const cal = useQuery({ queryKey: ['calibration', withAoi('', aoi)], queryFn: () => getJson<CalibrationResponse>(withAoi('/api/calibration', aoi)) })
  const cov = useQuery({ queryKey: ['coverage'], queryFn: () => getJson<Coverage>('/api/coverage') })
  const d = cal.data
  const w = d?.windows
  const params = d?.parameters as Record<string, unknown> | undefined

  return (
    <div className="max-w-5xl space-y-8">
      <p className="text-sm text-slate-300">Area: {describeAoi(aoi)}</p>

      <section aria-labelledby="how-h">
        <h2 id="how-h" className="text-lg font-medium">How the harmonized record is built</h2>
        <ol className="mt-2 list-decimal space-y-2 pl-5 text-sm text-slate-300">
          <li><b>Grid and count.</b> Every detection is snapped to a {String(params?.grid_deg ?? 0.01)}° grid (about 1 km). The unit is a <i>fire cell-day</i>: one grid cell with at least one detection on one UTC day. This removes most of the resolution difference between VIIRS (375 m pixels) and MODIS (1 km), and counts Terra and Aqua overpasses on the same day once.</li>
          <li><b>Filter.</b> Vegetation fires only (FIRMS type 0) and {String(params?.min_confidence ?? 'nominal')} or higher confidence.</li>
          <li><b>MODIS → VIIRS.</b> On {d?.calibration.overlap_window.join(' → ') ?? 'the overlap years'}, fit VIIRS ≈ k × MODIS. For this area k = {f3(d?.calibration.k)} (R² {f3(d?.calibration.r2)}).</li>
          {w?.aqua_month && <li><b>Terra-only years.</b> Before {w.aqua_month} only Terra flew. On {d?.terra_correction.fit_window?.join(' → ')}, fit MODIS(Terra + Aqua) ≈ c × MODIS(Terra). Here c = {f3(d?.terra_correction.c)}.</li>}
          <li><b>One series.</b> {w?.aqua_month && <>From {w.series_start} until {w.aqua_month}: Terra × c × k. </>}Then MODIS × k until {w?.switch_month}. From {w?.switch_month}: VIIRS observed. All values are VIIRS-equivalent fire cell-days.</li>
          <li><b>Compare.</b> For each calendar month, the median and spread over complete baseline years. Anomaly = robust z = (value − median) / (1.4826 × MAD), flagged at |z| ≥ 2.</li>
          <li><b>Near-real-time.</b> After the final data, provisional months use FIRMS NRT. NRT has no fire-type field, so detections in grid cells ever flagged as static sources (industry, volcano, offshore) in the archive are dropped. Checked on months where the type is known: within −2% to −7% of the true VIIRS count, versus +14% to +44% without the mask. The current month is compared with the same days of that month in past years.</li>
        </ol>
      </section>

      <CalibrationPanel aoi={aoi} />

      <section aria-labelledby="param-h">
        <h2 id="param-h" className="text-lg font-medium">Parameters</h2>
        <p className="text-sm text-slate-400">Prototype defaults that need scientific validation. All are API parameters.</p>
        <table className="mt-2 w-full text-left text-sm">
          <thead className="text-slate-400"><tr><th className="py-1 font-normal">Parameter</th><th className="font-normal">Value</th><th className="font-normal">Meaning</th></tr></thead>
          <tbody className="divide-y divide-slate-800 text-slate-300">
            <Row k="grid_deg" v={params?.grid_deg} m="Grid cell size in degrees" />
            <Row k="min_confidence" v={params?.min_confidence} m="Lowest detection confidence kept (MODIS 0–100 mapped to low < 30, nominal 30–79, high ≥ 80)" />
            <Row k="vegetation_only" v={params?.vegetation_only} m="Keep FIRMS type 0 only" />
            <Row k="min_overlap_months" v={params?.min_overlap_months} m="Fewer usable months in the area → use the US-wide factor" />
            <Row k="min_monthly_cell_days" v={params?.min_monthly_cell_days} m="A month enters a fit only with at least this many cell-days" />
            <Row k="z_threshold" v={2} m="Anomaly flag threshold on the robust z-score" />
            <Row k="baseline" v="complete years before the selected year" m="At least 5 years, otherwise no anomalies" />
          </tbody>
        </table>
      </section>

      <section aria-labelledby="src-h">
        <h2 id="src-h" className="text-lg font-medium">Data and as-of dates</h2>
        {cov.isError && <p className="text-red-300" role="alert">Could not load coverage: {(cov.error as Error).message}</p>}
        <div className="mt-2 grid gap-4 sm:grid-cols-2">
          {cov.data && (['MODIS', 'VIIRS'] as const).map((s) => (
            <div key={s} className="min-w-0 break-words rounded-lg border border-slate-700 bg-slate-900/60 p-4">
              <h3 className="font-medium">{s}</h3>
              <p className="text-xs text-slate-400">{cov.data.products[s]}</p>
              <p className="mt-2 text-xl tabular-nums">{cov.data.sensors[s].rows.toLocaleString()} hotspots</p>
              <p className="text-sm text-slate-400">{cov.data.sensors[s].first_date} → {cov.data.sensors[s].last_date} (UTC)</p>
              {w && <p className="mt-1 text-xs text-slate-400">Final data through {w.data_as_of[s]} · NRT through {w.nrt_as_of[s] ?? '—'}</p>}
            </div>
          ))}
        </div>
        {w && (
          <ul className="mt-3 list-disc space-y-1 pl-5 text-sm text-slate-300">
            <li>Harmonized final series: {w.series_start} → {w.series_end}.</li>
            {w.provisional_end && <li>Provisional (NRT) months: after {w.series_end} through {w.provisional_end}.</li>}
            {w.current && <li>Current month {w.current.month}: {w.current.sensor === 'VIIRS' ? 'VIIRS NRT' : 'MODIS NRT × k'} through {w.current.through} (the latest NRT day is left out because it is usually still being filled).</li>}
            <li>The live map layer queries the FIRMS area API on request (cached on the server for 3 hours).</li>
          </ul>
        )}
        <p className="mt-2 text-xs text-slate-500">Sources: NASA FIRMS yearly country archive (MODIS C6.1, VIIRS S-NPP 375 m), FIRMS area API (standard and NRT). Region: United States.</p>
      </section>

      <section aria-labelledby="lim-h">
        <h2 id="lim-h" className="text-lg font-medium">Limitations</h2>
        <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-slate-300">
          <li>Hotspots are thermal anomalies at satellite overpass times, not fire perimeters, burned area or fire counts. Clouds and smoke hide fires.</li>
          <li>One k and one c per area: the sensor relationship also varies by season, fire type and year (orbit drift, Terra's later overpass since about 2020).</li>
          <li>VIIRS detects more small and cool fires, so the relationship is not strictly proportional.</li>
          <li>Pre-Aqua (2000–2002) values assume the Terra/Aqua relationship of 2002–2011.</li>
          <li>Near-real-time data are provisional and are replaced by standard processing later. The static-source mask slightly under-counts.</li>
          <li>VIIRS S-NPP NRT in FIRMS ends on 2026-11-02. NOAA-20/21 VIIRS are not used, because they would need their own calibration.</li>
          <li>Only United States data are loaded.</li>
        </ul>
      </section>

      <section aria-labelledby="att-h" className="rounded-lg border border-slate-700 p-4 text-sm text-slate-300">
        <h2 id="att-h" className="font-medium">Attribution and use</h2>
        <p className="mt-1">
          Fire data: NASA Fire Information for Resource Management System (FIRMS), <a className="underline" href="https://firms.modaps.eosdis.nasa.gov/" target="_blank" rel="noreferrer">firms.modaps.eosdis.nasa.gov</a>.
          MODIS Collection 6.1 and VIIRS 375 m active fire products. Basemap © CARTO, © OpenStreetMap contributors.
        </p>
        <p className="mt-2 text-slate-400">
          FIREWATCH 360 is a hackathon prototype (NASA Space Apps 2026). It is not an official NASA product, not an operational emergency tool, and not a forecast. For current fire information, use official agency sources.
        </p>
      </section>
    </div>
  )
}

function Row({ k, v, m }: { k: string; v: unknown; m: string }) {
  return (
    <tr>
      <td className="py-1 pr-3 font-mono text-xs">{k}</td>
      <td className="pr-3 tabular-nums">{v == null ? '—' : String(v)}</td>
      <td className="text-slate-400">{m}</td>
    </tr>
  )
}
