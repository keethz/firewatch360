import type { AnomaliesResponse } from '../types/api'

const n0 = (v: number) => Math.round(v).toLocaleString()
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
const pct = (v: number | null) => (v == null ? '' : `${v > 0 ? '+' : ''}${v.toFixed(0)}% vs median`)

/** Current (partial) month vs the same days in history; falls back to the latest full month. */
export default function CurrentCard({ data, loading, error }: { data?: AnomaliesResponse; loading: boolean; error: boolean }) {
  const cur = data?.current_month
  const latest = data?.latest_vs_median
  const box = 'min-w-0 rounded-lg bg-slate-900/70 px-3 py-2'
  if (!data) return <div className={box}><h3 className="text-xs text-slate-400">Current month vs median</h3><p className="text-slate-400">{error ? 'Unavailable' : loading ? 'Loading…' : '—'}</p></div>

  if (cur) {
    const mon = MONTHS[Number(cur.month.slice(5, 7)) - 1]
    return (
      <div className={box}>
        <h3 className="text-xs text-slate-400">
          {mon} {cur.month.slice(0, 4)} so far (1–{cur.days} {mon}) <Badge />
        </h3>
        <p className="text-2xl tabular-nums">
          {n0(cur.value)} {cur.median != null && <span className="text-base text-slate-400">vs median {n0(cur.median)}</span>}
        </p>
        <p className="text-xs text-slate-300">
          {cur.history_status !== 'ok' ? 'Insufficient history for a comparison' : (
            <>{pct(cur.pct_vs_median)}{cur.z != null && ` · z ${cur.z.toFixed(1)}`}{cur.anomaly ? ` · anomaly (${cur.anomaly})` : ' · within normal range'}
              {cur.rank != null && ` · rank ${cur.rank} of ${cur.n_baseline_years + 1}`}</>
          )}
        </p>
        <p className="mt-1 text-xs text-slate-500">
          {cur.value_source} through {cur.through} UTC, vs the same days in {cur.n_baseline_years} baseline years.
        </p>
      </div>
    )
  }
  return (
    <div className={box}>
      <h3 className="text-xs text-slate-400">Latest month {latest ? `(${latest.month})` : ''} vs median {latest?.provisional && <Badge />}</h3>
      {latest && latest.median != null ? (
        <>
          <p className="text-2xl tabular-nums">{n0(latest.value)} <span className="text-base text-slate-400">vs {n0(latest.median)}</span></p>
          <p className="text-xs text-slate-300">{pct(latest.pct_vs_median)}{latest.anomaly ? ` · anomaly (${latest.anomaly})` : ' · within normal range'}</p>
          <p className="mt-1 text-xs text-slate-500">{latest.data_status}</p>
        </>
      ) : <p className="text-slate-400">Insufficient history</p>}
    </div>
  )
}

function Badge() {
  return <span className="ml-1 rounded bg-amber-500/20 px-1 text-[10px] uppercase tracking-wide text-amber-200">provisional</span>
}
