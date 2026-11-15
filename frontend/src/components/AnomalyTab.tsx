import { keepPreviousData, useQuery } from '@tanstack/react-query'
import type { FeatureCollection as FC } from 'geojson'
import * as maplibregl from 'maplibre-gl'
import type { GeoJSONSource } from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?url'
import { useEffect, useRef, useState } from 'react'
import { aoiRing, describeAoi, withAoi, type Aoi } from '../lib/aoi'
import { getJson, todayUtc } from '../lib/api'
import { Z_LIMIT, zScale } from '../lib/color'
import type { AnomalyCell, AnomalyMapResponse } from '../types/api'
import AiExplanation from './AiExplanation'
import ContextCard from './ContextCard'

maplibregl.setWorkerUrl(workerUrl)

const STYLE_URL = 'https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json'
const EMPTY: FC = { type: 'FeatureCollection', features: [] }
const n0 = (v: number) => Math.round(v).toLocaleString()
const zText = (z: number) => (z > 10 ? '> 10' : z.toFixed(1))

const monthName = (m: string) =>
  new Date(`${m}-01T00:00:00Z`).toLocaleDateString('en-US', { month: 'long', year: 'numeric', timeZone: 'UTC' })

/** Rule-based sentence, used when AI is off and as the always-visible summary. */
function cellSentence(c: AnomalyCell, month: string, years: [number, number]): string {
  const vs = c.median === 0
    ? `a median of 0 in the same month of ${years[0]}–${years[1]}`
    : `a median of ${n0(c.median)} in ${years[0]}–${years[1]} (${c.value >= c.median ? '+' : ''}${c.pct_vs_median?.toFixed(0)}%)`
  const lead = c.anomaly
    ? 'Substantially more fire detections than its historical baseline for this month'
    : c.below ? 'Fewer fire detections than usual for this month' : 'Fire activity within the usual range for this month'
  return `${lead}: ${n0(c.value)} fire cell-days in ${monthName(month)} versus ${vs}; ${c.rank === 1 ? 'the highest' : `rank ${c.rank}`} of ${c.of} years.${c.anomaly ? ' Further investigation may be warranted.' : ''}`
}

export default function AnomalyTab({ aoi }: { aoi: Aoi }) {
  const [month, setMonth] = useState('')
  const [cellDeg, setCellDeg] = useState(0.25)
  const [z, setZ] = useState(2)
  const [cbAlt, setCbAlt] = useState(false)
  // Display range on the z-score scale; the ends (±Z_LIMIT) are open-ended.
  const [zLo, setZLo] = useState(-Z_LIMIT)
  const [zHi, setZHi] = useState(Z_LIMIT)
  const [picked, setSel] = useState<{ c: AnomalyCell; ctx: string } | null>(null)
  const params = [month && `month=${month}`, `cell_deg=${cellDeg}`, `z_threshold=${z}`].filter(Boolean).join('&')
  const q = useQuery({
    queryKey: ['anomaly-map', aoi, params],
    queryFn: () => getJson<AnomalyMapResponse>(withAoi('/api/anomaly-map', aoi, params)),
    placeholderData: keepPreviousData,
  })
  const d = q.data
  const ctx = `${JSON.stringify(aoi)}|${params}`
  const sel = picked && picked.ctx === ctx ? picked.c : null // selection resets when the query changes
  const scale = zScale(cbAlt)

  // ---- map
  const el = useRef<HTMLDivElement>(null)
  const mapRef = useRef<maplibregl.Map | null>(null)
  const [ready, setReady] = useState(false)
  const cellsRef = useRef<AnomalyCell[]>([])
  const ctxRef = useRef(ctx)
  useEffect(() => { ctxRef.current = ctx }, [ctx])
  useEffect(() => {
    if (!el.current) return
    const map = new maplibregl.Map({ container: el.current, style: STYLE_URL, bounds: [-125, 24, -66, 50], attributionControl: { compact: true } })
    mapRef.current = map
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right')
    map.on('load', () => {
      map.addSource('cells', { type: 'geojson', data: EMPTY })
      map.addSource('aoi', { type: 'geojson', data: EMPTY })
      map.addLayer({ id: 'cells-fill', type: 'fill', source: 'cells', paint: { 'fill-color': ['get', 'color'], 'fill-opacity': 0.75 } })
      map.addLayer({ id: 'cells-anom', type: 'line', source: 'cells', filter: ['==', ['get', 'anomaly'], true],
                     paint: { 'line-color': '#fff', 'line-width': 1.2 } })
      map.addLayer({ id: 'cells-sel', type: 'line', source: 'cells', filter: ['==', ['get', 'key'], ''],
                     paint: { 'line-color': '#facc15', 'line-width': 3 } })
      map.addLayer({ id: 'aoi-line', type: 'line', source: 'aoi', paint: { 'line-color': '#94a3b8', 'line-dasharray': [2, 2] } })
      map.on('click', 'cells-fill', (e) => {
        const key = e.features?.[0]?.properties?.key as string | undefined
        const c = cellsRef.current.find((x) => x.cell.join(',') === key)
        if (c) setSel({ c, ctx: ctxRef.current })
      })
      map.on('mouseenter', 'cells-fill', () => (map.getCanvas().style.cursor = 'pointer'))
      map.on('mouseleave', 'cells-fill', () => (map.getCanvas().style.cursor = ''))
      setReady(true)
    })
    return () => map.remove()
  }, [])

  useEffect(() => {
    const map = mapRef.current
    if (!map || !ready || !d) return
    cellsRef.current = d.geojson.features.map((f) => f.properties)
    const fc = {
      type: 'FeatureCollection',
      features: d.geojson.features.map((f) => ({ ...f, properties: { ...f.properties, key: f.properties.cell.join(','), color: scale.color(f.properties.z) } })),
    } as unknown as FC
    ;(map.getSource('cells') as GeoJSONSource).setData(fc)
    const ring = aoiRing(aoi)
    ;(map.getSource('aoi') as GeoJSONSource).setData(ring ? ({ type: 'FeatureCollection', features: [{ type: 'Feature', properties: {}, geometry: { type: 'Polygon', coordinates: [ring] } }] } as FC) : EMPTY)
  }, [d, ready, aoi, cbAlt]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const map = mapRef.current
    if (!map || !ready) return
    map.setFilter('cells-sel', ['==', ['get', 'key'], sel ? sel.cell.join(',') : ''])
  }, [sel, ready])

  const inRange = (z: number) => (zLo <= -Z_LIMIT || z >= zLo) && (zHi >= Z_LIMIT || z <= zHi)
  useEffect(() => {
    const map = mapRef.current
    if (!map || !ready) return
    const conds: unknown[] = []
    if (zLo > -Z_LIMIT) conds.push(['>=', ['get', 'z'], zLo])
    if (zHi < Z_LIMIT) conds.push(['<=', ['get', 'z'], zHi])
    const range = conds.length ? ['all', ...conds] : null
    map.setFilter('cells-fill', range as maplibregl.FilterSpecification | null)
    map.setFilter('cells-anom', (range ? ['all', ['==', ['get', 'anomaly'], true], ...conds] : ['==', ['get', 'anomaly'], true]) as maplibregl.FilterSpecification)
  }, [zLo, zHi, ready])
  const shown = d ? d.geojson.features.filter((f) => inRange(f.properties.z)).length : 0
  const zLabel = (v: number, end: 'lo' | 'hi') =>
    end === 'lo' && v <= -Z_LIMIT ? 'any' : end === 'hi' && v >= Z_LIMIT ? 'any' : `${v > 0 ? '+' : v < 0 ? '−' : ''}${Math.abs(v)}`
  const preset = (lo: number, hi: number) => { setZLo(lo); setZHi(hi) }

  const zoomTo = (c: AnomalyCell) => {
    setSel({ c, ctx })
    const [w, s, e, n] = c.bbox
    mapRef.current?.fitBounds([w - 1, s - 1, e + 1, n + 1], { duration: 600 })
  }

  const explainUrl = sel ? withAoi('/api/explain/cell', aoi, `${params}&cy=${sel.cell[0]}&cx=${sel.cell[1]}`) : ''

  return (
    <div className="grid gap-4 lg:grid-cols-[1fr_380px]">
      <section aria-labelledby="amap-h" className="min-w-0">
        <div className="mb-2 flex flex-wrap items-end gap-3 text-sm">
          <h2 id="amap-h" className="mr-auto text-lg font-semibold">Where is fire activity unusual?</h2>
          <label className="flex flex-col text-xs text-slate-400">Month
            <input type="month" className="rounded border border-slate-600 bg-slate-800 px-2 py-1 text-sm text-slate-100"
                   value={month || d?.month || ''} min="2001-01" max={todayUtc().slice(0, 7)} onChange={(e) => setMonth(e.target.value && e.target.value > todayUtc().slice(0, 7) ? todayUtc().slice(0, 7) : e.target.value)} />
          </label>
          <label className="flex flex-col text-xs text-slate-400">Map cell
            <select className="rounded border border-slate-600 bg-slate-800 px-2 py-1 text-sm text-slate-100" value={cellDeg} onChange={(e) => setCellDeg(+e.target.value)}>
              <option value={0.1}>0.1° (~11 km)</option>
              <option value={0.25}>0.25° (~28 km)</option>
              <option value={0.5}>0.5° (~55 km)</option>
              <option value={1}>1° (~110 km)</option>
            </select>
          </label>
          <label className="flex flex-col text-xs text-slate-400">Flag at z ≥
            <select className="rounded border border-slate-600 bg-slate-800 px-2 py-1 text-sm text-slate-100" value={z} onChange={(e) => setZ(+e.target.value)}>
              {[2, 2.5, 3, 4].map((v) => <option key={v} value={v}>{v}</option>)}
            </select>
          </label>
          <label className="flex items-center gap-1 text-xs text-slate-400">
            <input type="checkbox" checked={cbAlt} onChange={(e) => setCbAlt(e.target.checked)} /> purple–orange
          </label>
        </div>
        <div className="relative h-[60vh] min-h-[360px] overflow-hidden rounded border border-slate-800">
          <div ref={el} className="h-full w-full" />
          {q.isFetching && <div className="absolute left-2 top-2 rounded bg-slate-900/90 px-2 py-1 text-xs">Computing anomalies…</div>}
          <fieldset className="absolute bottom-2 left-2 w-64 rounded bg-slate-900/90 p-2 text-xs">
            <legend className="sr-only">Show cells by robust z-score</legend>
            <div className="mb-1 flex justify-between gap-2">
              <span>Robust z-score vs the same month in past years</span>
            </div>
            {/* Colour bar; the part outside the chosen range is dimmed */}
            <div className="relative h-2.5 rounded" style={{ background: scale.gradient() }}>
              <div className="absolute inset-y-0 left-0 rounded-l bg-slate-900/80" style={{ width: `${scale.position(zLo) * 100}%` }} />
              <div className="absolute inset-y-0 right-0 rounded-r bg-slate-900/80" style={{ width: `${(1 - scale.position(zHi)) * 100}%` }} />
            </div>
            <div className="zrange relative h-4">
              <input type="range" min={-Z_LIMIT} max={Z_LIMIT} step={0.5} value={zLo} aria-label="Lowest z-score shown"
                     onChange={(e) => setZLo(Math.min(+e.target.value, zHi - 0.5))} />
              <input type="range" min={-Z_LIMIT} max={Z_LIMIT} step={0.5} value={zHi} aria-label="Highest z-score shown"
                     onChange={(e) => setZHi(Math.max(+e.target.value, zLo + 0.5))} />
            </div>
            <div className="flex justify-between text-slate-400"><span>≤ −{Z_LIMIT} fewer</span><span>0</span><span>≥ +{Z_LIMIT} more</span></div>
            <p className="mt-1 text-slate-200" aria-live="polite">
              Showing z from <strong>{zLabel(zLo, 'lo')}</strong> to <strong>{zLabel(zHi, 'hi')}</strong>: {n0(shown)} of {n0(d?.n_cells ?? 0)} cells
            </p>
            <div className="mt-1 flex flex-wrap gap-1">
              {([['All', -Z_LIMIT, Z_LIMIT], ['More than usual', 2, Z_LIMIT], ['Fewer than usual', -Z_LIMIT, -2], ['Near normal', -2, 2]] as const).map(([label, lo, hi]) => (
                <button key={label} type="button" onClick={() => preset(lo, hi)} aria-pressed={zLo === lo && zHi === hi}
                        className={`rounded border px-1.5 py-0.5 ${zLo === lo && zHi === hi ? 'border-orange-400 bg-orange-600/30' : 'border-slate-600 hover:bg-slate-800'}`}>
                  {label}
                </button>
              ))}
            </div>
            <div className="mt-1 flex items-center gap-1 text-slate-300"><span className="inline-block h-3 w-3 border border-white" /> flagged unusual</div>
          </fieldset>
        </div>
        {q.isError && <p className="mt-2 text-sm text-red-300">{(q.error as Error).message}</p>}
        {d && (
          <p className="mt-2 text-sm text-slate-300">
            <strong>{monthName(d.month)}</strong>{d.provisional && ' (provisional near-real-time data)'} in {describeAoi(aoi)}:{' '}
            <strong>{n0(d.n_anomalous)}</strong> of {n0(d.n_cells)} map cells with fire activity are flagged unusual
            (z ≥ {d.z_threshold} and at least {d.min_value} fire cell-days), against {d.baseline_years[0]}–{d.baseline_years[1]} ({d.n_baseline_years} years).
          </p>
        )}
      </section>

      <aside className="space-y-4">
        <section aria-labelledby="sel-h" className="rounded-lg bg-slate-900/70 p-3">
          <h3 id="sel-h" className="mb-2 font-semibold">Selected cell</h3>
          {!sel && <p className="text-sm text-slate-400">Click a cell on the map or a row below.</p>}
          {sel && d && (
            <div className="space-y-2 text-sm">
              <p className="text-xs text-slate-400">
                {((sel.bbox[1] + sel.bbox[3]) / 2).toFixed(2)}°N, {Math.abs((sel.bbox[0] + sel.bbox[2]) / 2).toFixed(2)}°W · {d.cell_deg}° cell
              </p>
              <dl className="grid grid-cols-2 gap-2">
                <div><dt className="text-xs text-slate-400">This month</dt><dd className="text-lg font-semibold">{n0(sel.value)}</dd></div>
                <div><dt className="text-xs text-slate-400">Baseline median</dt><dd className="text-lg font-semibold">{n0(sel.median)}</dd></div>
                <div><dt className="text-xs text-slate-400">Robust z</dt><dd className="font-semibold">{zText(sel.z)}{sel.anomaly && ' · unusual'}</dd></div>
                <div><dt className="text-xs text-slate-400">Rank</dt><dd className="font-semibold">{sel.rank} of {sel.of}</dd></div>
              </dl>
              <p className="text-slate-300">{cellSentence(sel, d.month, d.baseline_years)}</p>
              <ContextCard compact month={d.month} aoi={{ kind: 'bbox', bbox: sel.bbox }} />
              <AiExplanation url={explainUrl} />
            </div>
          )}
        </section>

        <section aria-labelledby="top-h" className="rounded-lg bg-slate-900/70 p-3">
          <h3 id="top-h" className="mb-2 font-semibold">Most unusual cells</h3>
          <table className="w-full text-left text-sm">
            <thead className="text-xs text-slate-400"><tr><th className="py-1">Location</th><th>Value</th><th>Median</th><th>z</th></tr></thead>
            <tbody>
              {d?.top.filter((c) => c.anomaly && inRange(c.z)).slice(0, 10).map((c) => (
                <tr key={c.cell.join(',')} className={`cursor-pointer border-t border-slate-800 hover:bg-slate-800 ${sel?.cell.join(',') === c.cell.join(',') ? 'bg-slate-800' : ''}`}
                    onClick={() => zoomTo(c)} tabIndex={0} onKeyDown={(e) => e.key === 'Enter' && zoomTo(c)}>
                  <td className="py-1">{((c.bbox[1] + c.bbox[3]) / 2).toFixed(2)}, {((c.bbox[0] + c.bbox[2]) / 2).toFixed(2)}</td>
                  <td>{n0(c.value)}</td><td>{n0(c.median)}</td><td>{zText(c.z)}</td>
                </tr>
              ))}
              {d && d.n_anomalous === 0 && <tr><td colSpan={4} className="py-2 text-slate-400">No unusual cells this month.</td></tr>}
            </tbody>
          </table>
          <p className="mt-2 text-xs text-slate-500">
            Units: fire cell-days (≈1 km cell with a detection on a day), harmonized to VIIRS (k = {d?.k.toFixed(2)}, c = {d?.c.toFixed(2)}).
            z = (value − median) / max(1.4826·MAD, √median); the floor stops rarely-burning cells from scoring huge values on a few detections.
          </p>
        </section>
      </aside>
    </div>
  )
}
