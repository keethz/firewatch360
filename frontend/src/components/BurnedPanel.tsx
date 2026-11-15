import { useQuery } from '@tanstack/react-query'
import { Bar, CartesianGrid, ComposedChart, Legend, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import type { Aoi } from '../lib/aoi'
import { fmtCompact } from '../lib/color'
import { burnedQuery, postFireQuery, YEARS, type FireRef } from '../lib/hazards'

const n0 = (v: number | null | undefined) => (v == null ? '—' : Math.round(v).toLocaleString())
// MTBS severity colours (its own published ramp), always with text labels.
const SEV_COLORS: Record<string, string> = {
  'Unburned to low': '#006400', Low: '#7fffd4', Moderate: '#ffff00', High: '#ff0000', 'Increased greenness': '#7fff00',
}
const ASSET_LABELS: Record<string, string> = {
  places: 'Population centers', hospitals: 'Hospitals', schools: 'Schools', firestations: 'Fire stations', airports: 'Airports',
}

/** Mapped burned area for a year, the long record, and the post-fire assessment of a selected fire. */
export default function BurnedPanel({ aoi, year, setYear, selected, onSelect }: {
  aoi: Aoi; year: number; setYear: (y: number) => void; selected: FireRef | null; onSelect: (f: FireRef | null) => void
}) {
  const q = useQuery(burnedQuery(aoi, year))
  const d = q.data
  const yearly = (d?.yearly ?? []).map((r) => ({ year: r.year, MTBS: r.mtbs_acres, WFIGS: r.wfigs_acres ?? null }))
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h3 className="font-medium">Burned area</h3>
          <p className="text-xs text-slate-400">Mapped fire perimeters · turn on “Fire perimeters” to see them on the map; click a fire for its post-fire assessment</p>
        </div>
        <label className="flex flex-col text-sm">
          <span className="text-xs text-slate-400">Fire year</span>
          <select className="rounded border border-slate-600 bg-slate-800 px-2 py-1" value={year} onChange={(e) => { setYear(Number(e.target.value)); onSelect(null) }}>
            {YEARS.map((y) => <option key={y} value={y}>{y}</option>)}
          </select>
        </label>
      </div>
      {q.isError && <p className="text-sm text-red-300" role="alert">Could not load burned area: {(q.error as Error).message}</p>}
      {q.isLoading && <p className="text-sm text-slate-400" role="status">Querying fire perimeters…</p>}
      {d && (
        <div className={q.isFetching ? 'opacity-60' : ''}>
          <p className="text-base">{d.headline}</p>
          <div className="mt-2 grid gap-2 sm:grid-cols-4">
            <Tile label="Acres burned inside the area" value={n0(d.acres_in_area)} sub={`${d.pct_of_area.toFixed(2)}% of the area`} />
            <Tile label="Mapped fires" value={n0(d.n_fires)} sub={`${n0(d.n_wildfires)} wildfires · ${n0(d.n_prescribed)} prescribed`} />
            <Tile label="Still burning" value={d.n_active == null ? '—' : n0(d.n_active)} sub={d.n_active == null ? 'reported for the current year only' : 'not yet 100% contained'} />
            <Tile label="Source" value={d.source === 'wfigs' ? 'NIFC' : 'MTBS'} sub={d.source_label} />
          </div>
          <div className="mt-3 h-56" role="img" aria-label="Acres burned per year by fires intersecting the area, MTBS since 1984 and WFIGS since 2020">
            <ResponsiveContainer>
              <ComposedChart data={yearly} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
                <CartesianGrid stroke="#1f2937" vertical={false} />
                <XAxis dataKey="year" stroke="#94a3b8" fontSize={11} />
                <YAxis stroke="#94a3b8" fontSize={11} tickFormatter={(v: number) => fmtCompact(v)} width={44} />
                <Tooltip contentStyle={{ background: '#0f172a', border: '1px solid #334155' }} formatter={(v) => (v == null ? '—' : `${n0(Number(v))} acres`)} />
                <Legend />
                <Bar dataKey="MTBS" name="MTBS large fires (acres)" fill="#ef4444" isAnimationActive={false}
                     onClick={(e: { payload?: { year: number } }) => e?.payload && setYear(e.payload.year)} cursor="pointer" />
                <Line dataKey="WFIGS" name="NIFC all mapped fires (acres)" stroke="#fde047" strokeWidth={2} dot={{ r: 2 }} isAnimationActive={false} />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
          <div className="mt-3 max-h-64 overflow-auto rounded border border-slate-800">
            <table className="w-full text-left text-sm">
              <caption className="sr-only">Largest fires in {year}</caption>
              <thead className="sticky top-0 bg-slate-900 text-xs text-slate-400">
                <tr><th className="p-2 font-normal">Fire</th><th className="p-2 font-normal">Type</th><th className="p-2 text-right font-normal">Acres</th><th className="p-2 font-normal">Started</th><th className="p-2 font-normal">Status</th></tr>
              </thead>
              <tbody>
                {d.fires.map((f) => {
                  const sel = selected?.source === f.source && selected.id === f.id
                  return (
                    <tr key={`${f.source}-${f.id}`} className={`border-t border-slate-800 ${sel ? 'bg-yellow-500/10' : 'hover:bg-slate-800/60'}`}>
                      <td className="p-2"><button className="text-left underline decoration-slate-600 underline-offset-2 hover:decoration-slate-300" onClick={() => onSelect(sel ? null : { source: f.source, id: f.id })}>{f.name}</button></td>
                      <td className="p-2 text-slate-300">{f.type}</td>
                      <td className="p-2 text-right tabular-nums">{n0(f.acres)}</td>
                      <td className="p-2 tabular-nums text-slate-300">{f.start ?? '—'}</td>
                      <td className="p-2 text-slate-300">{d.n_active == null ? '—' : f.percent_contained != null && f.percent_contained < 100 && !f.contained ? `${f.percent_contained}% contained` : 'contained'}</td>
                    </tr>
                  )
                })}
                {!d.fires.length && <tr><td colSpan={5} className="p-2 text-slate-400">No mapped fires.</td></tr>}
              </tbody>
            </table>
          </div>
          <p className="mt-1 text-xs text-slate-500">{d.note}{d.n_fires > d.fires.length && ` Table shows the ${d.fires.length} largest.`}</p>
        </div>
      )}
      {selected && <PostFire fire={selected} onClose={() => onSelect(null)} />}
    </div>
  )
}

function PostFire({ fire, onClose }: { fire: FireRef; onClose: () => void }) {
  const q = useQuery(postFireQuery(fire))
  const d = q.data
  const sev = d?.severity.acres_by_class ?? {}
  const sevMapped = Object.entries(sev).filter(([k]) => k !== 'Non-processing area')
  const sevTotal = sevMapped.reduce((a, [, v]) => a + v, 0)
  const daily = d?.detections.daily ?? []
  const maxFrp = Math.max(1, ...daily.map((r) => r.frp_mw))
  return (
    <div className="rounded-lg border border-yellow-500/40 bg-slate-950/60 p-3" aria-live="polite">
      <div className="flex items-start justify-between gap-2">
        <h3 className="font-medium">Post-fire assessment{d ? `: ${d.name}` : ''}</h3>
        <button onClick={onClose} className="rounded border border-slate-600 px-2 text-xs hover:bg-slate-800" aria-label="Close post-fire assessment">Close</button>
      </div>
      {q.isLoading && <p className="text-sm text-slate-400" role="status">Assessing the fire (satellite detections, burn severity, land cover)…</p>}
      {q.isError && <p className="text-sm text-red-300" role="alert">{(q.error as Error).message}</p>}
      {d && (
        <div className="mt-1 space-y-3 text-sm">
          <p>{d.headline}</p>
          <div className="grid gap-2 sm:grid-cols-4">
            <Tile label="Perimeter area" value={`${n0(d.perimeter_km2 * 247.105)} ac`} sub={`${n0(d.perimeter_km2)} km² · start ${d.start}`} />
            <Tile label="Satellite detections inside" value={n0(d.detections.total)} sub={`${d.detections.active_days} active days`} />
            <Tile label="Total fire radiative power" value={`${fmtCompact(d.detections.frp_total_mw)} MW`} sub={`peak pixel ${n0(d.detections.frp_max_mw)} MW`} />
            <Tile label="Most intense day" value={d.detections.peak_day ?? '—'} sub={d.detections.first ? `${d.detections.first} → ${d.detections.last}` : 'no detections'} />
          </div>
          {daily.length > 0 && (
            <div>
              <p className="text-xs text-slate-400">Daily fire radiative power inside the perimeter (fire progression)</p>
              <div className="mt-1 flex h-16 items-end gap-px" role="img" aria-label={`Daily fire radiative power from ${daily[0].date} to ${daily[daily.length - 1].date}, peak on ${d.detections.peak_day}`}>
                {daily.map((r) => <div key={r.date} className="min-w-[2px] flex-1 bg-orange-500" style={{ height: `${Math.max(2, (r.frp_mw / maxFrp) * 100)}%` }} title={`${r.date}: ${n0(r.frp_mw)} MW, ${r.detections} detections`} />)}
              </div>
            </div>
          )}
          <div className="grid gap-3 md:grid-cols-3">
            <div>
              <p className="text-xs text-slate-400">Burn severity (MTBS)</p>
              {d.severity.status === 'ok' ? (
                <>
                  <div className="mt-1 flex h-3 overflow-hidden rounded" role="img" aria-label={sevMapped.map(([k, v]) => `${k} ${((v / sevTotal) * 100).toFixed(0)}%`).join(', ')}>
                    {sevMapped.map(([k, v]) => <div key={k} style={{ width: `${(v / sevTotal) * 100}%`, background: SEV_COLORS[k] ?? '#64748b' }} />)}
                  </div>
                  <ul className="mt-1 space-y-0.5 text-xs">
                    {sevMapped.map(([k, v]) => <li key={k}><span className="mr-1 inline-block h-2 w-2" style={{ background: SEV_COLORS[k] ?? '#64748b' }} />{k}: {n0(v)} ac ({((v / sevTotal) * 100).toFixed(0)}%)</li>)}
                  </ul>
                </>
              ) : <p className="text-xs text-slate-400">{d.severity.message ?? 'No severity data.'}</p>}
            </div>
            <div>
              <p className="text-xs text-slate-400">What burned {d.land_cover.status === 'ok' && `(${d.land_cover.source?.split(' land')[0]})`}</p>
              {d.land_cover.status === 'ok'
                ? <ul className="mt-1 space-y-0.5 text-xs">{Object.entries(d.land_cover.acres_by_class!).slice(0, 6).map(([k, v]) => <li key={k}>{k}: {n0(v)} ac</li>)}</ul>
                : <p className="text-xs text-slate-400">{d.land_cover.message}</p>}
            </div>
            <div>
              <p className="text-xs text-slate-400">Mapped assets inside the perimeter</p>
              <ul className="mt-1 space-y-0.5 text-xs">
                {Object.entries(ASSET_LABELS).map(([k, label]) => {
                  const a = d.assets_inside[k] as { count: number; names: string[] } | undefined
                  return a ? <li key={k} title={a.names.join(', ')}>{label}: {a.count}{a.count ? ` (${a.names.slice(0, 2).join(', ')}${a.count > 2 ? '…' : ''})` : ''}</li> : null
                })}
                {typeof d.assets_inside.roads_km === 'number' && <li>Major roads: {d.assets_inside.roads_km} km</li>}
              </ul>
            </div>
          </div>
          <p className="text-xs text-slate-500">{d.note}</p>
        </div>
      )}
    </div>
  )
}

function Tile({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="min-w-0 rounded border border-slate-700 p-2">
      <p className="text-xs text-slate-400">{label}</p>
      <p className="truncate text-xl tabular-nums">{value}</p>
      {sub && <p className="truncate text-[11px] text-slate-500" title={sub}>{sub}</p>}
    </div>
  )
}
