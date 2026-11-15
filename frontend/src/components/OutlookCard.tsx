import { useQuery } from '@tanstack/react-query'
import { describeAoi, withAoi, type Aoi } from '../lib/aoi'
import { getJson } from '../lib/api'
import { fmtCompact } from '../lib/color'
import { METRIC, metricQuery, type Metric } from '../lib/metric'
import type { OutlookLead, OutlookResponse } from '../types/api'

// Below / near / above normal: blue, grey, orange (the app's anomaly colours), always labelled in text too.
const CAT = [
  { k: 'below', label: 'Below normal', color: '#3b82f6' },
  { k: 'near', label: 'Near normal', color: '#64748b' },
  { k: 'above', label: 'Above normal', color: '#f97316' },
] as const

/** Experimental 1–3 month statistical outlook of fire activity, with its hindcast skill. */
export default function OutlookCard({ aoi, metric = 'cell_days', asSection = true }: { aoi: Aoi; metric?: Metric; asSection?: boolean }) {
  const q = useQuery({
    queryKey: ['outlook', aoi, metric],
    queryFn: () => getJson<OutlookResponse>(withAoi('/api/outlook', aoi, metricQuery(metric))),
    staleTime: 30 * 60_000,
  })
  const d = q.data
  const unit = METRIC[metric].unit
  const body = (
    <>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="outlook-h" className="text-lg font-medium">Fire activity outlook <span className="ml-1 rounded bg-amber-500/20 px-1.5 py-0.5 align-middle text-xs font-normal text-amber-200">experimental</span></h2>
        <p className="text-xs text-slate-400">{describeAoi(aoi)} · {METRIC[metric].long} · next 3 months</p>
      </div>
      {q.isError && <p className="mt-2 text-sm text-red-300" role="alert">Could not load the outlook: {(q.error as Error).message}</p>}
      {q.isLoading && <p className="mt-2 text-sm text-slate-400" role="status">Fitting the outlook…</p>}
      {d?.status === 'insufficient_history' && <p className="mt-2 text-sm text-slate-400">{d.message}</p>}
      {d?.status === 'ok' && d.leads && (
        <>
          <p className="mt-2 text-base">{d.headline}</p>
          <p className="text-xs text-slate-400">
            Issued from data through {d.issued_from}{d.issued_from_provisional ? ' (provisional)' : ''}. Last 3 months were
            {' '}{(d.persistence_index ?? 0) > 0.3 ? 'above' : (d.persistence_index ?? 0) < -0.3 ? 'below' : 'close to'} normal
            {' '}(anomaly index {d.persistence_index?.toFixed(2)}).
          </p>
          <div className="mt-3 grid gap-3 md:grid-cols-3">
            {d.leads.map((l) => <LeadCard key={l.month} l={l} unit={unit} />)}
          </div>
          <details className="mt-3 text-xs text-slate-400">
            <summary className="cursor-pointer">How is this made, and how good is it?</summary>
            <p className="mt-1">{d.method}</p>
            <p className="mt-1">
              Skill is measured by re-running the outlook for every past year without that year's data
              ({d.climatology_years?.[0]}–{d.climatology_years?.[1]}). <b>Skill score</b> above 0 means it beat simply expecting a typical
              month; <b>hit rate</b> is how often the most likely category was right (33% would be chance).
              Outlooks with no skill are labelled and should be ignored.
            </p>
            <p className="mt-1">{d.caveat} Check the drought and fire-weather context alongside it.</p>
          </details>
        </>
      )}
    </>
  )
  return asSection
    ? <section aria-labelledby="outlook-h" className="rounded-lg bg-slate-900/70 p-4">{body}</section>
    : <div>{body}</div>
}

function LeadCard({ l, unit }: { l: OutlookLead; unit: string }) {
  const best = CAT.find((c) => c.k === l.most_likely)!
  return (
    <div className={`rounded border p-3 ${l.skill.useful ? 'border-slate-700' : 'border-dashed border-slate-700 opacity-75'}`}>
      <div className="flex items-baseline justify-between">
        <h3 className="font-medium">{l.month_name}</h3>
        <span className="text-xs text-slate-400">{l.lead} month{l.lead > 1 ? 's' : ''} ahead</span>
      </div>
      {l.skill.useful
        ? <p className="text-sm"><span style={{ color: best.color }}>■</span> {best.label} most likely ({(l.probabilities[l.most_likely] * 100).toFixed(0)}%)</p>
        : <p className="text-sm text-slate-400">No better than the historical average</p>}
      <div className="mt-2 flex h-3 w-full overflow-hidden rounded" role="img"
           aria-label={CAT.map((c) => `${c.label} ${(l.probabilities[c.k] * 100).toFixed(0)}%`).join(', ')}>
        {CAT.map((c) => <div key={c.k} style={{ width: `${l.probabilities[c.k] * 100}%`, background: c.color }} title={`${c.label}: ${(l.probabilities[c.k] * 100).toFixed(0)}%`} />)}
      </div>
      <div className="mt-0.5 flex justify-between text-[11px] tabular-nums text-slate-400">
        {CAT.map((c) => <span key={c.k}>{c.label.split(' ')[0]} {(l.probabilities[c.k] * 100).toFixed(0)}%</span>)}
      </div>
      <p className="mt-2 text-sm tabular-nums">
        Expected {fmtCompact(l.expected)} {unit} <span className="text-slate-400">(80% range {fmtCompact(l.range[0])}–{fmtCompact(l.range[1])}; typical {fmtCompact(l.typical)})</span>
      </p>
      <p className="mt-1 text-[11px] text-slate-400">
        Skill score {l.skill.msss.toFixed(2)} · hit rate {(l.skill.tercile_hit_rate * 100).toFixed(0)}% ({l.skill.n_hindcasts} past outlooks)
      </p>
    </div>
  )
}
