import { useQuery } from '@tanstack/react-query'
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { aoiQuery, type Aoi } from '../lib/aoi'
import { getJson } from '../lib/api'
import type { CalibrationResponse } from '../types/api'

// Okabe–Ito colours (colourblind safe); series also differ by dash pattern.
const C = { modis: '#E69F00', viirs: '#56B4E9', scaled: '#F0E442' }

const fmt = (v: number | null | undefined, d = 2) => (v == null ? '—' : v.toFixed(d))

export default function CalibrationPanel({ aoi }: { aoi: Aoi }) {
  const qs = aoiQuery(aoi) ? `?${aoiQuery(aoi)}` : ''
  const { data, error, isFetching } = useQuery({
    queryKey: ['calibration', qs],
    queryFn: () => getJson<CalibrationResponse>(`/api/calibration${qs}`),
  })
  const cal = data?.calibration

  return (
    <section aria-labelledby="cal-h" className="mt-8 max-w-5xl rounded-lg bg-slate-900/70 p-4">
      <h2 id="cal-h" className="text-lg font-medium">MODIS → VIIRS harmonization</h2>
      <p className="text-sm text-slate-400">
        Monthly unique fire cell-days (0.01° grid, vegetation fires, nominal+high confidence). Fit: VIIRS ≈ k × MODIS on the overlap period.
      </p>

      {isFetching && <p className="mt-2 text-sm text-slate-400" role="status">Computing…</p>}

      {error && <p className="mt-3 text-red-300" role="alert">Calibration failed: {(error as Error).message}</p>}

      {data && cal && (
        <>
          {cal.status === 'insufficient_overlap' && (
            <p className="mt-3 rounded border border-amber-600/60 bg-amber-900/30 p-2 text-sm text-amber-200" role="note">
              Only {cal.aoi_usable_months} overlap months in this area had enough MODIS detections (need 24).
              Using the US-wide factor instead.
            </p>
          )}
          <dl className="mt-4 grid grid-cols-2 gap-3 text-sm sm:grid-cols-5">
            <Stat label="k (VIIRS / MODIS)" value={fmt(cal.k, 3)} />
            <Stat label="R²" value={fmt(cal.r2, 3)} />
            <Stat label="Months in fit" value={String(cal.n_months)} />
            <Stat label="LOYO annual error" value={`${fmt(cal.validation.mean_annual_error_pct, 1)}%`} />
            <Stat label="LOYO monthly MAPE" value={`${fmt(cal.validation.mean_monthly_mape_pct, 1)}%`} />
          </dl>
          <p className="mt-1 text-xs text-slate-500">
            Factor scope: {cal.scope === 'aoi' ? 'this area' : 'US-wide fallback'}. Overlap {cal.overlap_window[0]} → {cal.overlap_window[1]} (UTC).
            {cal.linear && ` Linear check: VIIRS = ${cal.linear.a.toFixed(0)} + ${cal.linear.b.toFixed(3)} × MODIS (R² ${fmt(cal.linear.r2, 3)}).`}
          </p>

          <div className="mt-4 h-80" role="img" aria-label="Line chart of monthly raw MODIS, raw VIIRS and scaled MODIS fire cell-days over the overlap period">
            <ResponsiveContainer>
              <LineChart data={data.series} margin={{ top: 5, right: 16, bottom: 5, left: 8 }}>
                <CartesianGrid stroke="#1f2937" />
                <XAxis dataKey="month" stroke="#94a3b8" fontSize={11} minTickGap={40} />
                <YAxis stroke="#94a3b8" fontSize={11} />
                <Tooltip
                  contentStyle={{ background: '#0f172a', border: '1px solid #334155' }}
                  formatter={(v) => (typeof v === 'number' ? Math.round(v).toLocaleString() : v)}
                />
                <Legend />
                <Line dataKey="modis" name="MODIS raw" stroke={C.modis} dot={false} strokeWidth={1.5} strokeDasharray="4 3" />
                <Line dataKey="viirs" name="VIIRS raw" stroke={C.viirs} dot={false} strokeWidth={2} />
                <Line dataKey="modis_scaled" name="MODIS × k" stroke={C.scaled} dot={false} strokeWidth={1.5} strokeDasharray="1 3" />
              </LineChart>
            </ResponsiveContainer>
          </div>
          {data.terra_correction.status !== 'not_applicable' && (
            <div className="mt-4 border-t border-slate-700 pt-3">
              <h3 className="font-medium">Terra-only correction (before {data.terra_correction.applies_before})</h3>
              <p className="text-sm text-slate-400">
                Aqua MODIS started in July 2002. Before that only Terra observed, so MODIS saw fewer fires. Fit on {data.terra_correction.fit_window?.[0]} → {data.terra_correction.fit_window?.[1]}: MODIS(Terra + Aqua) ≈ c × MODIS(Terra only). Pre-Aqua months use Terra × c × k.
              </p>
              {data.terra_correction.status === 'insufficient_overlap' && (
                <p className="mt-2 text-sm text-amber-200" role="note">Too few months in this area; using the US-wide c.</p>
              )}
              <dl className="mt-2 grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
                <Stat label="c (Terra+Aqua / Terra)" value={fmt(data.terra_correction.c, 3)} />
                <Stat label="R²" value={fmt(data.terra_correction.r2, 3)} />
                <Stat label="Months in fit" value={String(data.terra_correction.n_months ?? '—')} />
                <Stat label="LOYO annual error" value={`${fmt(data.terra_correction.validation?.mean_annual_error_pct, 1)}%`} />
              </dl>
            </div>
          )}
          <p className="mt-2 text-xs text-slate-500">
            {data.caveat} Data as of MODIS {data.windows.data_as_of.MODIS}, VIIRS {data.windows.data_as_of.VIIRS}. Source: NASA FIRMS.
          </p>
        </>
      )}
    </section>
  )
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded border border-slate-700 p-2">
      <dt className="text-xs text-slate-400">{label}</dt>
      <dd className="text-lg tabular-nums">{value}</dd>
    </div>
  )
}
