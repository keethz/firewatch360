import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { describeAoi, PRESETS, withAoi, type Aoi } from '../lib/aoi'
import { getJson } from '../lib/api'
import type { ClimatologyResponse } from '../types/api'

// Okabe–Ito pair; lines also differ in dash pattern and the legend names them.
const COLOR_A = '#E69F00'
const COLOR_B = '#56B4E9'
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
const THIS_YEAR = new Date().getUTCFullYear()
const YEARS = Array.from({ length: THIS_YEAR - 2000 }, (_, i) => THIS_YEAR - i)
const n0 = (v: number | null | undefined) => (v == null ? '—' : Math.round(v).toLocaleString())
const pctText = (v: number | null) => (v == null ? '—' : `${v >= 0 ? '+' : '−'}${Math.abs(v).toFixed(0)}%`)

interface Side { label: string; aoi: Aoi; year: number }

function useSide(s: Side) {
  return useQuery({
    queryKey: ['climatology', s.aoi, `year=${s.year}`],
    queryFn: () => getJson<ClimatologyResponse>(withAoi('/api/climatology', s.aoi, `year=${s.year}`)),
  })
}

function summary(d?: ClimatologyResponse) {
  if (!d) return null
  const rows = d.selected_year_rows
  const months = rows.length
  const total = rows.reduce((t, r) => t + r.harmonized, 0)
  // Median for the same months only, so a partial year is compared fairly.
  const med = rows.reduce((t, r) => t + (r.clim_median ?? 0), 0)
  const peak = rows.reduce((p, r) => (r.harmonized > (p?.harmonized ?? -1) ? r : p), rows[0])
  return {
    months, total, med, pct: med > 0 ? (100 * (total - med)) / med : null,
    peak: peak ? MONTHS[Number(peak.month.slice(5, 7)) - 1] : '—',
    unusual: rows.filter((r) => r.anomaly === 'high').length,
    provisional: rows.some((r) => r.provisional),
    baseline: d.climatology.baseline,
  }
}

export default function CompareTab({ aoi }: { aoi: Aoi }) {
  const [mode, setMode] = useState<'years' | 'areas'>('years')
  const [yearA, setYearA] = useState(THIS_YEAR - 1)
  const [yearB, setYearB] = useState(2020)
  const [presetB, setPresetB] = useState(aoi.kind === 'region' ? 1 : 0)
  const [scale, setScale] = useState<'value' | 'pct'>('value')

  const a: Side = { label: mode === 'years' ? `${yearA}` : `${describeAoi(aoi)} ${yearA}`, aoi, year: yearA }
  const b: Side = mode === 'years'
    ? { label: `${yearB}`, aoi, year: yearB }
    : { label: `${PRESETS[presetB].label} ${yearA}`, aoi: PRESETS[presetB].aoi, year: yearA }
  const qa = useSide(a)
  const qb = useSide(b)
  const sa = summary(qa.data)
  const sb = summary(qb.data)

  const val = (d: ClimatologyResponse | undefined, m: number) => {
    const r = d?.selected_year_rows.find((x) => Number(x.month.slice(5, 7)) === m)
    if (!r) return null
    return scale === 'value' ? r.harmonized : r.pct_vs_median
  }
  const data = MONTHS.map((name, i) => ({ name, A: val(qa.data, i + 1), B: val(qb.data, i + 1) }))
  const err = qa.error ?? qb.error

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end gap-3 text-sm">
        <h2 className="mr-auto text-lg font-semibold">Compare</h2>
        <div role="radiogroup" aria-label="Compare what" className="flex gap-1">
          {(['years', 'areas'] as const).map((m) => (
            <button key={m} role="radio" aria-checked={mode === m} onClick={() => { setMode(m); if (m === 'areas') setScale('pct') }}
                    className={`rounded px-3 py-1 ${mode === m ? 'bg-orange-600 font-medium' : 'border border-slate-700 hover:bg-slate-800'}`}>
              {m === 'years' ? 'Two years, this area' : 'Two areas, same year'}
            </button>
          ))}
        </div>
        <label className="flex flex-col text-xs text-slate-400">{mode === 'years' ? 'Year A' : 'Year'}
          <select className="rounded border border-slate-600 bg-slate-800 px-2 py-1 text-sm text-slate-100" value={yearA} onChange={(e) => setYearA(+e.target.value)}>
            {YEARS.map((y) => <option key={y}>{y}</option>)}
          </select>
        </label>
        {mode === 'years' ? (
          <label className="flex flex-col text-xs text-slate-400">Year B
            <select className="rounded border border-slate-600 bg-slate-800 px-2 py-1 text-sm text-slate-100" value={yearB} onChange={(e) => setYearB(+e.target.value)}>
              {YEARS.map((y) => <option key={y}>{y}</option>)}
            </select>
          </label>
        ) : (
          <label className="flex flex-col text-xs text-slate-400">Area B
            <select className="rounded border border-slate-600 bg-slate-800 px-2 py-1 text-sm text-slate-100" value={presetB} onChange={(e) => setPresetB(+e.target.value)}>
              {PRESETS.map((p, i) => <option key={p.label} value={i}>{p.label}</option>)}
            </select>
          </label>
        )}
        <label className="flex flex-col text-xs text-slate-400">Show
          <select className="rounded border border-slate-600 bg-slate-800 px-2 py-1 text-sm text-slate-100" value={scale} onChange={(e) => setScale(e.target.value as 'value' | 'pct')}>
            <option value="value">Fire cell-days</option>
            <option value="pct">% vs own median</option>
          </select>
        </label>
      </div>
      <p className="text-sm text-slate-400">
        {mode === 'years'
          ? <>Area A and B: <strong className="text-slate-200">{describeAoi(aoi)}</strong> (change it on the Explore tab).</>
          : <>Area A: <strong className="text-slate-200">{describeAoi(aoi)}</strong> (Explore tab). Areas of different size are best compared as % vs their own median.</>}
      </p>
      {err && <p className="text-sm text-red-300">{(err as Error).message}</p>}

      <div className="h-80 rounded border border-slate-800 p-2">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 8, right: 16, left: 8, bottom: 0 }}>
            <CartesianGrid stroke="#1e293b" />
            <XAxis dataKey="name" stroke="#94a3b8" fontSize={12} />
            <YAxis stroke="#94a3b8" fontSize={12} tickFormatter={(v) => (scale === 'pct' ? `${v}%` : n0(v))} />
            <Tooltip contentStyle={{ background: '#0f172a', border: '1px solid #334155' }}
                     formatter={(v) => (typeof v === 'number' ? (scale === 'pct' ? pctText(v) : n0(v)) : '—')} />
            <Legend />
            <Line dataKey="A" name={a.label} stroke={COLOR_A} strokeWidth={2.5} dot={{ r: 3 }} connectNulls={false} />
            <Line dataKey="B" name={b.label} stroke={COLOR_B} strokeWidth={2.5} strokeDasharray="6 3" dot={{ r: 3, strokeDasharray: '' }} connectNulls={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>

      <table className="w-full max-w-3xl text-left text-sm">
        <caption className="mb-1 text-left text-xs text-slate-400">Harmonized VIIRS-equivalent fire cell-days. Each side is compared with its own baseline median for the same months.</caption>
        <thead className="text-xs text-slate-400">
          <tr><th className="py-1"></th><th>Total</th><th>Median (same months)</th><th>vs median</th><th>Peak month</th><th>Unusual months</th><th>Baseline</th></tr>
        </thead>
        <tbody>
          {[{ s: a, sum: sa, c: COLOR_A, dash: false }, { s: b, sum: sb, c: COLOR_B, dash: true }].map(({ s, sum, c, dash }) => (
            <tr key={s.label + c} className="border-t border-slate-800">
              <th scope="row" className="py-1 pr-2 font-medium">
                <span className="mr-1 inline-block w-5 border-t-2 align-middle" style={{ borderColor: c, borderStyle: dash ? 'dashed' : 'solid' }} />
                {s.label}{sum?.provisional && ' *'}
              </th>
              <td>{n0(sum?.total)}{sum && sum.months < 12 && <span className="text-xs text-slate-400"> ({sum.months} mo)</span>}</td>
              <td>{n0(sum?.med)}</td>
              <td>{pctText(sum?.pct ?? null)}</td>
              <td>{sum?.peak ?? '—'}</td>
              <td>{sum?.unusual ?? '—'}</td>
              <td className="text-xs text-slate-400">{sum ? `${sum.baseline[0]}–${sum.baseline[1]}` : '…'}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {(sa?.provisional || sb?.provisional) && <p className="text-xs text-slate-500">* includes provisional near-real-time months.</p>}
    </div>
  )
}
