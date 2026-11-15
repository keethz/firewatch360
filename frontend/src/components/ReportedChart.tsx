import { useQuery } from '@tanstack/react-query'
import { useRef, useState } from 'react'
import { Bar, CartesianGrid, ComposedChart, Legend, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { describeAoi, withAoi, type Aoi } from '../lib/aoi'
import { getJson } from '../lib/api'
import type { ReportedMonthly } from '../types/api'
import ImageDownload from './ImageDownload'

const n0 = (v: unknown) => (typeof v === 'number' ? Math.round(v).toLocaleString() : '—')
const THIS_YEAR = new Date().getUTCFullYear()

/** Agency-reported wildfires (NIFC, 2014+) per month next to harmonized satellite fire cell-days. */
export default function ReportedChart({ aoi }: { aoi: Aoi }) {
  const [from, setFrom] = useState(Math.max(2014, THIS_YEAR - 4))
  const ref = useRef<HTMLElement>(null)
  const q = useQuery({
    queryKey: ['reported-monthly', aoi, from],
    queryFn: () => getJson<ReportedMonthly>(withAoi('/api/reported/monthly', aoi, `start=${from}-01`)),
    staleTime: 30 * 60_000,
  })
  const months = q.data?.months ?? []
  const totalR = months.reduce((t, m) => t + m.wildfires, 0)
  const totalS = months.reduce((t, m) => t + (m.satellite_cell_days ?? 0), 0)

  return (
    <section ref={ref} aria-labelledby="rep-h" className="rounded-lg bg-slate-900/70 p-4">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h2 id="rep-h" className="text-lg font-medium">Reported fires vs satellite detections</h2>
          <p className="text-sm text-slate-400">{describeAoi(aoi)} · wildfires reported to NIFC by discovery month (bars) and harmonized satellite fire cell-days (line)</p>
        </div>
        <div className="flex items-end gap-2 text-sm">
          <label data-noexport className="flex flex-col text-xs text-slate-400">From
            <select className="rounded border border-slate-600 bg-slate-800 px-2 py-1 text-sm text-slate-100" value={from} onChange={(e) => setFrom(+e.target.value)}>
              {Array.from({ length: THIS_YEAR - 2013 }, (_, i) => 2014 + i).map((y) => <option key={y}>{y}</option>)}
            </select>
          </label>
          <ImageDownload target={ref} fileName={`firewatch360-reported-vs-detected-${from}`} caption={`Reported fires (NIFC) vs satellite detections · ${describeAoi(aoi)}`} />
        </div>
      </div>
      {q.isFetching && !q.data && <p className="mt-3 text-sm text-slate-400" role="status">Counting reported fires month by month (first load can take ~30 s)…</p>}
      {q.data?.status === 'unavailable' && <p className="mt-3 text-sm text-amber-200">{q.data.message}</p>}
      {q.isError && <p className="mt-3 text-sm text-red-300">{(q.error as Error).message}</p>}
      {months.length > 0 && (
        <>
          <div className="mt-3 h-72">
            <ResponsiveContainer width="100%" height="100%">
              <ComposedChart data={months} margin={{ top: 8, right: 8, left: 8, bottom: 0 }}>
                <CartesianGrid stroke="#1e293b" />
                <XAxis dataKey="month" stroke="#94a3b8" fontSize={11} minTickGap={24} />
                <YAxis yAxisId="r" stroke="#f59e0b" fontSize={11} tickFormatter={n0} label={{ value: 'reported wildfires', angle: -90, position: 'insideLeft', fill: '#f59e0b', fontSize: 11 }} />
                <YAxis yAxisId="s" orientation="right" stroke="#56B4E9" fontSize={11} tickFormatter={n0} label={{ value: 'satellite cell-days', angle: 90, position: 'insideRight', fill: '#56B4E9', fontSize: 11 }} />
                <Tooltip contentStyle={{ background: '#0f172a', border: '1px solid #334155' }} formatter={(v) => n0(v)} />
                <Legend />
                <Bar yAxisId="r" dataKey="wildfires" name="Reported wildfires (NIFC)" fill="#f59e0b" fillOpacity={0.75} />
                <Line yAxisId="s" dataKey="satellite_cell_days" name="Satellite fire cell-days (harmonized)" stroke="#56B4E9" strokeWidth={2} dot={false} />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
          <p className="mt-2 text-sm text-slate-300">
            {months[0].month} → {months[months.length - 1].month}: <strong>{n0(totalR)}</strong> reported wildfires
            ({n0(months.reduce((t, m) => t + m.acres, 0))} acres) and <strong>{n0(totalS)}</strong> satellite fire cell-days.
          </p>
          <p className="mt-1 text-xs text-slate-500">
            {q.data?.note} Source: {q.data?.source}; reports start {q.data?.coverage_start}. Two separate scales; compare the shape, not the height.
          </p>
        </>
      )}
    </section>
  )
}
