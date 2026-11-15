import { useQuery } from '@tanstack/react-query'
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import type { Aoi } from '../lib/aoi'
import { notFuture } from '../lib/api'
import { smokeQuery } from '../lib/hazards'
import { SMOKE_COLORS } from '../lib/overlays'

/** NOAA HMS satellite smoke over the area: one day plus the previous two weeks. */
export default function SmokePanel({ aoi, date, setDate }: { aoi: Aoi; date: string; setDate: (d: string) => void }) {
  const q = useQuery(smokeQuery(aoi, date))
  const d = q.data
  const series = (d?.series ?? []).map((s) => ({
    day: s.date.slice(5), Light: s.pct_covered?.Light ?? 0, Medium: s.pct_covered?.Medium ?? 0, Heavy: s.pct_covered?.Heavy ?? 0,
  }))
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h3 className="font-medium">Smoke from satellites</h3>
          <p className="text-xs text-slate-400">NOAA Hazard Mapping System, analyst-drawn smoke plumes · turn on “Smoke plumes” to see them on the map</p>
        </div>
        <label className="flex flex-col text-sm">
          <span className="text-xs text-slate-400">Date (UTC; blank = latest)</span>
          <input type="date" className="rounded border border-slate-600 bg-slate-800 px-2 py-1" value={date} min="2005-08-05"
                 onChange={(e) => setDate(notFuture(e.target.value))} />
        </label>
      </div>
      {q.isError && <p className="text-sm text-red-300" role="alert">Could not load smoke: {(q.error as Error).message}</p>}
      {q.isLoading && <p className="text-sm text-slate-400" role="status">Loading smoke analyses…</p>}
      {d?.status === 'unavailable' && <p className="text-sm text-slate-400">{d.message}</p>}
      {d?.status === 'ok' && d.today && (
        <div className={q.isFetching ? 'opacity-60' : ''}>
          <p className="text-base">{d.headline}</p>
          <div className="mt-2 grid gap-2 sm:grid-cols-4">
            {(['Light', 'Medium', 'Heavy'] as const).map((k) => (
              <div key={k} className="rounded border border-slate-700 p-2">
                <p className="flex items-center gap-1.5 text-xs text-slate-400"><span className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: SMOKE_COLORS[k] }} /> {k} smoke</p>
                <p className="text-xl tabular-nums">{d.today!.pct_covered![k].toFixed(0)}%</p>
                <p className="text-[11px] text-slate-500">of the area · ~{{ Light: 5, Medium: 16, Heavy: 27 }[k]} µg/m³ PM2.5</p>
              </div>
            ))}
            <div className="rounded border border-slate-700 p-2">
              <p className="text-xs text-slate-400">Smoke days, last {d.days_analysed}</p>
              <p className="text-xl tabular-nums">{d.days_with_smoke}</p>
              <p className="text-[11px] text-slate-500">{d.days_with_heavy_smoke} with heavy smoke (≥10% of area)</p>
            </div>
          </div>
          <div className="mt-3 h-52" role="img" aria-label={`Daily share of the area under light, medium and heavy smoke for the ${series.length} days to ${d.date}`}>
            <ResponsiveContainer>
              <BarChart data={series} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
                <CartesianGrid stroke="#1f2937" vertical={false} />
                <XAxis dataKey="day" stroke="#94a3b8" fontSize={11} />
                <YAxis stroke="#94a3b8" fontSize={11} domain={[0, 100]} unit="%" width={44} />
                <Tooltip contentStyle={{ background: '#0f172a', border: '1px solid #334155' }} formatter={(v) => `${Number(v).toFixed(0)}% of area`} />
                <Legend />
                <Bar dataKey="Light" stackId="s" fill={SMOKE_COLORS.Light} isAnimationActive={false} />
                <Bar dataKey="Medium" stackId="s" fill={SMOKE_COLORS.Medium} isAnimationActive={false} />
                <Bar dataKey="Heavy" stackId="s" fill={SMOKE_COLORS.Heavy} isAnimationActive={false} />
              </BarChart>
            </ResponsiveContainer>
          </div>
          <p className="mt-1 text-xs text-slate-500">{d.note} Source: {d.source}.</p>
        </div>
      )}
    </div>
  )
}
