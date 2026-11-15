import { useQuery } from '@tanstack/react-query'
import { useRef, useState } from 'react'
import { aoiQuery, describeAoi, type Aoi } from '../lib/aoi'
import ImageDownload from './ImageDownload'
import { getJson, todayUtc, notFuture } from '../lib/api'
import type { AnomaliesResponse } from '../types/api'
import CurrentCard from './CurrentCard'

const n0 = (v: number) => Math.round(v).toLocaleString()
const sgn = (v: number) => (v > 0 ? '+' : '') + v.toFixed(0)

export default function SummaryPanel({ aoi, extra = '' }: { aoi: Aoi; extra?: string }) {
  const aoiQs = aoiQuery(aoi)
  const q = [aoiQs, extra].filter(Boolean).join('&')
  const { data, error, isFetching } = useQuery({
    queryKey: ['anomalies', q],
    queryFn: () => getJson<AnomaliesResponse>(`/api/anomalies?${q}`),
  })
  const ref = useRef<HTMLElement>(null)
  const [start, setStart] = useState('2025-07-01')
  const [end, setEnd] = useState('2025-07-31')
  const hotspotsUrl = `/api/export/hotspots.csv?${aoiQs}${aoiQs ? '&' : ''}start=${start}&end=${end}`

  return (
    <section ref={ref} aria-labelledby="sum-h" className="mt-8 max-w-5xl rounded-lg bg-slate-900/70 p-4">
      <div className="flex items-start justify-between gap-2">
        <h2 id="sum-h" className="text-lg font-medium">Historical comparison</h2>
        <ImageDownload target={ref} fileName="firewatch360-historical-comparison" caption={`Historical comparison · ${describeAoi(aoi)}`} />
      </div>
      <p className="text-sm text-slate-400">Harmonized monthly fire cell-days, compared with the baseline median for the same calendar month.</p>
      {isFetching && <p className="mt-2 text-sm text-slate-400" role="status">Computing…</p>}
      {error && <p className="mt-3 text-red-300" role="alert">Could not load analytics: {(error as Error).message}</p>}

      {data && (
        <>
          {data.climatology.status !== 'ok' && (
            <p className="mt-3 rounded border border-amber-600/60 bg-amber-900/30 p-2 text-sm text-amber-200" role="note">
              Fewer than 5 baseline years: anomalies are not computed.
            </p>
          )}
          <div className="mt-4 grid gap-3 sm:grid-cols-3">
            <CurrentCard data={data} loading={false} error={false} />
            <Card title="Peak fire season">
              <p className="text-2xl">{data.rankings.peak_season.join(', ') || '—'}</p>
              <p className="text-xs text-slate-500">Months whose median is above the average month.</p>
            </Card>
            <Card title="Annual trend (Theil–Sen)">
              <p className="text-2xl tabular-nums">{data.rankings.trend ? `${sgn(data.rankings.trend.slope_per_year)} / yr` : '—'}</p>
              <p className="text-xs text-slate-500">{data.rankings.trend ? `${data.rankings.trend.n_years} complete years. Descriptive only.` : 'Too few years.'}</p>
            </Card>
          </div>

          <div className="mt-4 grid gap-4 sm:grid-cols-3 text-sm">
            <RankList title="Top months" items={data.rankings.top_months.map((m) => [m.month, n0(m.harmonized)])} />
            <RankList title="Top years (complete)" items={data.rankings.top_years.map((y) => [String(y.year), n0(y.total)])} />
            <div>
              <h3 className="font-medium">Anomalous months (|z| ≥ 2): {data.anomalies.length}</h3>
              <ul className="mt-1 max-h-40 overflow-auto text-slate-300">
                {data.anomalies.slice().reverse().map((a) => (
                  <li key={a.month} className="tabular-nums">
                    {a.month} <span className={a.anomaly === 'high' ? 'text-orange-300' : 'text-sky-300'}>{a.anomaly === 'high' ? '▲' : '▼'} z {a.z!.toFixed(1)}</span>
                  </li>
                ))}
              </ul>
            </div>
          </div>
          <p className="mt-3 text-xs text-slate-500">
            Baseline {data.climatology.baseline[0]}–{data.climatology.baseline[1]} ({data.climatology.baseline_years.length} complete years). {data.method}. k = {data.calibration.k.toFixed(3)} ({data.calibration.scope === 'aoi' ? 'this area' : 'US-wide fallback'}). Window {data.window[0]} → {data.window[1]} UTC. {data.caveat}
          </p>
        </>
      )}

      <div data-noexport className="mt-4 flex flex-wrap items-end gap-3 border-t border-slate-700 pt-4 text-sm">
        <a className="rounded border border-slate-500 px-3 py-1 hover:bg-slate-800" href={`/api/export/calendar.csv?${q}`} download>
          Download calendar CSV
        </a>
        <label className="flex flex-col">
          <span className="text-slate-400">Hotspots from (UTC)</span>
          <input type="date" className="rounded border border-slate-600 bg-slate-800 px-2 py-1" value={start} max={todayUtc()} onChange={(e) => setStart(notFuture(e.target.value))} />
        </label>
        <label className="flex flex-col">
          <span className="text-slate-400">to</span>
          <input type="date" className="rounded border border-slate-600 bg-slate-800 px-2 py-1" value={end} max={todayUtc()} onChange={(e) => setEnd(notFuture(e.target.value))} />
        </label>
        <a className="rounded border border-slate-500 px-3 py-1 hover:bg-slate-800" href={hotspotsUrl} download>
          Download hotspots CSV
        </a>
        <span className="text-xs text-slate-500">All raw detections in the area (max 2,000,000 rows).</span>
      </div>
    </section>
  )
}

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded border border-slate-700 p-3">
      <h3 className="text-xs text-slate-400">{title}</h3>
      {children}
    </div>
  )
}

function RankList({ title, items }: { title: string; items: [string, string][] }) {
  return (
    <div>
      <h3 className="font-medium">{title}</h3>
      <ol className="mt-1 list-decimal pl-5 text-slate-300">
        {items.map(([k, v]) => (
          <li key={k} className="tabular-nums">{k}: {v}</li>
        ))}
      </ol>
    </div>
  )
}
