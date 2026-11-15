import { useQuery } from '@tanstack/react-query'
import { withAoi, type Aoi } from '../lib/aoi'
import { getJson } from '../lib/api'
import type { ContextResponse, DroughtInfo } from '../types/api'

// USDM's own colour order (light yellow → dark red); labels are always written too.
const CAT = [
  { k: 'D0', label: 'D0 Abnormally dry', color: '#ffff00' },
  { k: 'D1', label: 'D1 Moderate', color: '#fcd37f' },
  { k: 'D2', label: 'D2 Severe', color: '#ffaa00' },
  { k: 'D3', label: 'D3 Extreme', color: '#e60000' },
  { k: 'D4', label: 'D4 Exceptional', color: '#730000' },
] as const

function DroughtBar({ d, title }: { d?: DroughtInfo; title: string }) {
  if (!d) return null
  if (d.status !== 'ok') return <p className="text-xs text-slate-400">{title}: {d.message}</p>
  const share = d.share!
  return (
    <div>
      <p className="text-xs text-slate-400">{title} · map of {d.week}</p>
      <p className="text-sm">{d.text}. <span className="text-slate-400">DSCI {d.dsci} / 500{d.dsci_percentile != null && `, ${d.dsci_percentile}th percentile for this month`}</span></p>
      <div className="mt-1 flex h-3 w-full overflow-hidden rounded bg-slate-700" role="img"
           aria-label={CAT.map((c) => `${c.label} ${(share[c.k] * 100).toFixed(0)}%`).join(', ')}>
        {CAT.map((c) => <div key={c.k} style={{ width: `${share[c.k] * 100}%`, background: c.color }} title={`${c.label}: ${(share[c.k] * 100).toFixed(1)}%`} />)}
      </div>
    </div>
  )
}

/** Drought (US Drought Monitor) and active NWS fire-weather alerts for an area. Context, not a forecast. */
export default function ContextCard({ aoi, month, compact = false }: { aoi: Aoi; month?: string; compact?: boolean }) {
  const q = useQuery({
    queryKey: ['context', aoi, month],
    queryFn: () => getJson<ContextResponse>(withAoi('/api/context', aoi, month ? `month=${month}` : '')),
    staleTime: 10 * 60_000,
  })
  const d = q.data
  const fw = d?.fire_weather
  return (
    <section aria-labelledby="ctx-h" className={`rounded-lg bg-slate-900/70 ${compact ? 'p-2' : 'p-3'}`}>
      <h3 id="ctx-h" className="mb-2 font-semibold">Drought &amp; fire weather</h3>
      {q.isPending && <p className="text-sm text-slate-400">Loading Drought Monitor and NWS data…</p>}
      {q.isError && <p className="text-sm text-red-300">{(q.error as Error).message}</p>}
      {d && (
        <div className="space-y-3">
          {month && <DroughtBar d={d.drought_month} title={`Drought in ${month}`} />}
          {!compact && <DroughtBar d={d.drought_latest} title="Drought now" />}
          {!compact && (
            <div>
              <p className="text-xs text-slate-400">Fire weather now (NWS){fw?.as_of && ` · ${fw.as_of}`}</p>
              {fw?.status !== 'ok' && <p className="text-sm text-amber-200">{fw?.message}</p>}
              {fw?.status === 'ok' && !fw.active?.length && <p className="text-sm">No Red Flag Warning or Fire Weather Watch over this area.</p>}
              {fw?.active?.map((a, i) => (
                <p key={i} className={`mt-1 rounded border-l-4 px-2 py-1 text-sm ${a.event === 'Red Flag Warning' ? 'border-red-500 bg-red-500/10' : 'border-amber-400 bg-amber-500/10'}`}>
                  <strong>{a.event}</strong>{a.ends && ` until ${new Date(a.ends).toLocaleString()}`}. {a.area}
                </p>
              ))}
            </div>
          )}
          <p className="text-xs text-slate-500">
            Sources: U.S. Drought Monitor (NDMC/USDA/NOAA); NWS. {d.note}{' '}
            <span className="sr-only">Legend:</span>
            {CAT.map((c) => <span key={c.k} className="mr-2 inline-flex items-center gap-1"><span className="inline-block h-2 w-2" style={{ background: c.color }} />{c.k}</span>)}
          </p>
        </div>
      )}
    </section>
  )
}
