import { keepPreviousData, useQueries, useQuery } from '@tanstack/react-query'
import { useEffect, useState, type FormEvent } from 'react'
import { describeAoi, PRESETS, REGION, withAoi, type Aoi } from '../lib/aoi'
import { getJson } from '../lib/api'
import type { AnomaliesResponse, HotspotsResponse, ImpactResponse, NrtResponse } from '../types/api'
import CurrentCard from './CurrentCard'
import ContextCard from './ContextCard'
import FireRiskBrief from './FireRiskBrief'
import ImpactPanel from './ImpactPanel'
import { OVERLAYS, type OverlayId, type VectorOverlay } from '../lib/overlays'
import MapView, { type DrawMode } from './MapView'

const n0 = (v: number) => Math.round(v).toLocaleString()

interface Props {
  aoi: Aoi
  setAoi: (a: Aoi) => void
}

export default function ExploreTab({ aoi, setAoi }: Props) {
  const [mode, setMode] = useState<DrawMode>('none')
  const [circleKm, setCircleKm] = useState(50)
  const [start, setStart] = useState('2025-08-01')
  const [end, setEnd] = useState('2025-08-31')
  const [sensors, setSensors] = useState({ MODIS: true, VIIRS: true })
  const [minConf, setMinConf] = useState('nominal')
  const [vegOnly, setVegOnly] = useState(true)
  const [showLive, setShowLive] = useState(false)
  const [liveDays, setLiveDays] = useState(3)
  const [bufferKm, setBufferKm] = useState(5)
  const [panel, setPanel] = useState<'brief' | 'impact' | 'context'>('brief')
  const [overlays, setOverlays] = useState<Partial<Record<OverlayId, boolean>>>({ firezone: true, hospitals: true, schools: true, firestations: true })

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setMode('none')
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  const sensorList = (Object.keys(sensors) as (keyof typeof sensors)[]).filter((s) => sensors[s])
  const filters = `start=${start}&end=${end}&sensors=${sensorList.join(',')}&min_confidence=${minConf}&vegetation_only=${vegOnly}`
  const hs = useQuery({
    queryKey: ['hotspots', aoi, filters],
    queryFn: () => getJson<HotspotsResponse>(withAoi('/api/hotspots', aoi, filters)),
    enabled: sensorList.length > 0 && start <= end,
    placeholderData: keepPreviousData,
  })
  const an = useQuery({
    queryKey: ['anomalies', aoi],
    queryFn: () => getJson<AnomaliesResponse>(withAoi('/api/anomalies', aoi)),
  })

  const peak = an.data?.rankings.top_months[0]
  const hasAoi = aoi.kind !== 'region'
  const impactQs = `start=${start}&end=${end}&buffer_km=${bufferKm}&sensors=${sensorList.join(',')}&min_confidence=${minConf}&vegetation_only=${vegOnly}`
  const impactQ = useQuery({
    queryKey: ['impact', aoi, impactQs],
    queryFn: () => getJson<ImpactResponse>(withAoi('/api/impact', aoi, impactQs)),
    enabled: hasAoi && sensorList.length > 0 && start <= end,
    placeholderData: keepPreviousData,
    staleTime: Infinity,
  })
  const vectorIds = OVERLAYS.filter((o) => o.kind === 'vector' && overlays[o.id]).map((o) => o.id as VectorOverlay)
  const featQs = useQueries({
    queries: vectorIds.map((id) => ({
      queryKey: ['impact-features', id, aoi],
      queryFn: () => getJson<unknown>(withAoi('/api/impact/features', aoi, `layer=${id}`)),
      enabled: hasAoi,
      staleTime: Infinity,
    })),
  })
  const impactGeo = Object.fromEntries(vectorIds.map((id, i) => [id, featQs[i]?.data])) as Partial<Record<VectorOverlay, unknown>>
  const liveQ = useQuery({
    queryKey: ['nrt', aoi, liveDays, minConf],
    queryFn: () => getJson<NrtResponse>(withAoi('/api/nrt', aoi, `days=${liveDays}&min_confidence=${minConf}`)),
    enabled: showLive,
    staleTime: 10 * 60 * 1000,
  })

  return (
    <div className="grid gap-4 lg:grid-cols-[17rem_1fr]">
      <aside className="space-y-2 text-sm lg:sticky lg:top-2 lg:max-h-[calc(100vh-1rem)] lg:self-start lg:overflow-y-auto" aria-label="Map controls">
        <Section title="Area" summary={describeAoi(aoi)} open>
          <label className="flex flex-col">
            <span className="text-slate-400">Preset</span>
            <select
              className="rounded border border-slate-600 bg-slate-800 px-2 py-1"
              value={PRESETS.find((p) => JSON.stringify(p.aoi) === JSON.stringify(aoi))?.label ?? ''}
              onChange={(e) => setAoi(PRESETS.find((p) => p.label === e.target.value)?.aoi ?? REGION)}
            >
              <option value="" disabled>Custom area</option>
              {PRESETS.map((p) => <option key={p.label}>{p.label}</option>)}
            </select>
          </label>
          <div className="mt-2 grid grid-cols-3 gap-1" role="group" aria-label="Draw an area">
            {(['rect', 'polygon', 'circle'] as const).map((m) => (
              <button
                key={m}
                aria-pressed={mode === m}
                onClick={() => setMode(mode === m ? 'none' : m)}
                className={`rounded border px-2 py-1 ${mode === m ? 'border-orange-400 bg-orange-600/30' : 'border-slate-600 hover:bg-slate-800'}`}
              >
                {m === 'rect' ? 'Rectangle' : m === 'polygon' ? 'Polygon' : 'Circle'}
              </button>
            ))}
          </div>
          <PointForm km={circleKm} setKm={setCircleKm} onSubmit={(lat, lon) => setAoi({ kind: 'circle', lat, lon, radius_km: circleKm })} />
          {aoi.kind !== 'region' && (
            <button className="mt-2 text-xs text-slate-400 underline" onClick={() => setAoi(REGION)}>Clear area (whole US)</button>
          )}
        </Section>

        <Section title="Dates & filters" summary={`${start} → ${end}`} open>
          <div className="grid grid-cols-2 gap-2">
            <label className="flex flex-col"><span className="text-slate-400">From (UTC)</span>
              <input type="date" className="rounded border border-slate-600 bg-slate-800 px-1 py-1" value={start} onChange={(e) => setStart(e.target.value)} /></label>
            <label className="flex flex-col"><span className="text-slate-400">To (UTC)</span>
              <input type="date" className="rounded border border-slate-600 bg-slate-800 px-1 py-1" value={end} onChange={(e) => setEnd(e.target.value)} /></label>
          </div>
          <div className="mt-2 flex gap-4">
            {(['MODIS', 'VIIRS'] as const).map((s) => (
              <label key={s} className="flex items-center gap-1">
                <input type="checkbox" checked={sensors[s]} onChange={(e) => setSensors({ ...sensors, [s]: e.target.checked })} /> {s}
              </label>
            ))}
          </div>
          <label className="mt-2 flex flex-col"><span className="text-slate-400">Minimum confidence</span>
            <select className="rounded border border-slate-600 bg-slate-800 px-2 py-1" value={minConf} onChange={(e) => setMinConf(e.target.value)}>
              <option value="low">Low (all)</option><option value="nominal">Nominal</option><option value="high">High</option>
            </select></label>
          <label className="mt-2 flex items-center gap-1">
            <input type="checkbox" checked={vegOnly} onChange={(e) => setVegOnly(e.target.checked)} /> Vegetation fires only (type 0)
          </label>
          {start > end && <p className="mt-1 text-red-300" role="alert">Start date is after end date.</p>}
          {sensorList.length === 0 && <p className="mt-1 text-amber-300" role="alert">Select at least one sensor.</p>}
        </Section>

        <Section title="Map layers" summary={`${OVERLAYS.filter((o) => overlays[o.id]).length} on · ${bufferKm} km exposure`}>
          <label className="mb-2 flex items-center gap-2"><span className="text-slate-400">Exposure distance</span>
            <select className="rounded border border-slate-600 bg-slate-800 px-2 py-1" value={bufferKm} onChange={(e) => setBufferKm(Number(e.target.value))}>
              {[1, 2, 5, 10, 20].map((k) => <option key={k} value={k}>{k} km</option>)}
            </select></label>
          <div className="space-y-1">
            {OVERLAYS.map((o) => (
              <label key={o.id} className="flex items-center gap-1.5" title={o.note}>
                <input type="checkbox" checked={!!overlays[o.id]} disabled={!hasAoi && o.kind !== 'raster'}
                       onChange={(e) => setOverlays({ ...overlays, [o.id]: e.target.checked })} />
                <span className="inline-block h-2.5 w-2.5 shrink-0 rounded-sm" style={{ background: o.color }} aria-hidden="true" />
                {o.label}
              </label>
            ))}
          </div>
          {!hasAoi && <p className="mt-2 text-xs text-amber-200">Select an area to load points, roads and the fire exposure zone.</p>}
        </Section>

        <Section title="Live feed" summary={showLive ? `on · last ${liveDays} d` : 'off'}>
          <label className="flex items-center gap-1">
            <input type="checkbox" checked={showLive} onChange={(e) => setShowLive(e.target.checked)} /> Show live near-real-time hotspots
          </label>
          <label className="mt-2 flex items-center gap-2"><span className="text-slate-400">Last</span>
            <select className="rounded border border-slate-600 bg-slate-800 px-2 py-1" value={liveDays} onChange={(e) => setLiveDays(Number(e.target.value))}>
              {[1, 2, 3, 5, 7, 10].map((d) => <option key={d} value={d}>{d} day{d > 1 ? 's' : ''}</option>)}
            </select></label>
          {showLive && (
            <p className="mt-2 text-xs text-slate-300" role="status">
              {liveQ.isFetching ? 'Fetching from FIRMS…' : liveQ.isError ? <span className="text-red-300">{(liveQ.error as Error).message}</span> : liveQ.data ? (
                <>{n0(liveQ.data.total)} detections, {liveQ.data.window[0]} → {liveQ.data.window[1]} UTC
                  {Object.entries(liveQ.data.counts).map(([s, n]) => ` · ${s} ${n0(n)}`).join('')}.
                  {liveQ.data.counts.VIIRS === 0 && ' VIIRS S-NPP NRT has no recent data in FIRMS.'}</>
              ) : null}
            </p>
          )}
          <p className="mt-1 text-xs text-slate-500">Provisional and not filtered by fire type. Shown as rings.</p>
        </Section>
        <a className="block px-1 py-1 text-xs text-slate-400 underline hover:text-slate-200" download
           href={withAoi('/api/export/hotspots.csv', aoi, `start=${start}&end=${end}&sensors=${sensorList.join(',')}`)}>
          ⤓ Download hotspots CSV (all rows, unfiltered)
        </a>
      </aside>

      <div className="min-w-0 space-y-3">
        <div className="grid gap-2 sm:grid-cols-3">
          <Card title={`Hotspots ${start} → ${end}`}>
            {hs.isError ? <p className="text-red-300">{(hs.error as Error).message}</p> : hs.data ? (
              <>
                <p className="text-2xl tabular-nums">{n0(hs.data.total)}</p>
                <p className="text-xs text-slate-400">
                  MODIS {n0(hs.data.counts.MODIS)} · VIIRS {n0(hs.data.counts.VIIRS)}
                  {hs.data.counts.nrt_provisional > 0 && ` · ${n0(hs.data.counts.nrt_provisional)} provisional`}
                </p>
                {hs.data.sampled && <p className="text-xs text-amber-300">Map shows a sample of {n0(hs.data.returned)}.</p>}
              </>
            ) : <p className="text-slate-400">{hs.isFetching ? 'Loading…' : '—'}</p>}
          </Card>
          <Card title="Peak month on record (harmonized)">
            {peak ? <><p className="text-2xl tabular-nums">{peak.month}</p><p className="text-xs text-slate-400">{n0(peak.harmonized)} fire cell-days</p></> : <p className="text-slate-400">{an.isError ? 'Unavailable' : 'Loading…'}</p>}
          </Card>
          <CurrentCard data={an.data} loading={an.isLoading} error={an.isError} />
        </div>
        <MapView aoi={aoi} onAoi={setAoi} mode={mode} onModeDone={() => setMode('none')} circleKm={circleKm} hotspots={hs.data} liveData={showLive ? liveQ.data : undefined}
          overlays={overlays} impactGeo={hasAoi ? impactGeo : {}} fireZone={hasAoi ? impactQ.data?.fire?.zone_geojson : null} />
        <div>
          <div role="tablist" aria-label="Area details" className="flex gap-4 border-b border-slate-800 text-sm">
            {([['brief', 'Fire Risk Brief'], ['impact', 'What could this affect?'], ['context', 'Drought & weather']] as const).map(([id, label]) => (
              <button key={id} role="tab" aria-selected={panel === id} onClick={() => setPanel(id)}
                      className={`-mb-px border-b-2 px-1 pb-2 ${panel === id ? 'border-orange-500 font-medium text-slate-100' : 'border-transparent text-slate-400 hover:text-slate-200'}`}>
                {label}
              </button>
            ))}
          </div>
          <div className="pt-3" role="tabpanel">
            {panel === 'brief' && <FireRiskBrief aoi={aoi} />}
            {panel === 'context' && <ContextCard aoi={aoi} />}
            {panel === 'impact' && <ImpactPanel data={hasAoi ? impactQ.data : ({ status: 'aoi_required', message: 'Select an area (preset, drawn shape or point + radius) to see what nearby fire activity could affect: people, homes in the wildland–urban interface, roads, hospitals, schools, airports, protected land and farmland.' } as ImpactResponse)}
          loading={impactQ.isFetching} error={impactQ.error as Error | null} bufferKm={bufferKm} />}
          </div>
        </div>
        <p className="text-xs text-slate-500">
          Points are raw satellite detections (pixel centres), not fire perimeters. All times UTC. Not a forecast.
          Basemap © CARTO, © OpenStreetMap contributors. Fire data: NASA FIRMS.
          {hs.isFetching && ' Updating…'}
        </p>
      </div>
    </div>
  )
}

function PointForm({ km, setKm, onSubmit }: { km: number; setKm: (v: number) => void; onSubmit: (lat: number, lon: number) => void }) {
  const [lat, setLat] = useState('')
  const [lon, setLon] = useState('')
  const [err, setErr] = useState('')
  const submit = (e: FormEvent) => {
    e.preventDefault()
    const a = Number(lat), o = Number(lon)
    if (!lat || !lon || !(a >= -90 && a <= 90) || !(o >= -180 && o <= 180)) return setErr('Enter a valid latitude and longitude.')
    if (!(km > 0 && km <= 1000)) return setErr('Radius must be 1–1000 km.')
    setErr('')
    onSubmit(a, o)
  }
  return (
    <form onSubmit={submit} className="mt-3 grid grid-cols-3 gap-1" aria-label="Circle from coordinates">
      <label className="flex flex-col"><span className="text-xs text-slate-400">Lat</span>
        <input inputMode="decimal" className="min-w-0 rounded border border-slate-600 bg-slate-800 px-1 py-1" value={lat} onChange={(e) => setLat(e.target.value)} placeholder="34.05" /></label>
      <label className="flex flex-col"><span className="text-xs text-slate-400">Lon</span>
        <input inputMode="decimal" className="min-w-0 rounded border border-slate-600 bg-slate-800 px-1 py-1" value={lon} onChange={(e) => setLon(e.target.value)} placeholder="-118.25" /></label>
      <label className="flex flex-col"><span className="text-xs text-slate-400">Radius km</span>
        <input type="number" min={1} max={1000} className="min-w-0 rounded border border-slate-600 bg-slate-800 px-1 py-1" value={km} onChange={(e) => setKm(Number(e.target.value))} /></label>
      <button className="col-span-3 mt-1 rounded border border-slate-600 px-2 py-1 hover:bg-slate-800">Use point + radius</button>
      {err && <p className="col-span-3 text-xs text-red-300" role="alert">{err}</p>}
    </form>
  )
}

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="min-w-0 rounded-lg bg-slate-900/70 px-3 py-2">
      <h3 className="text-xs text-slate-400">{title}</h3>
      {children}
    </div>
  )
}

function Section({ title, summary, open = false, children }: { title: string; summary?: string; open?: boolean; children: React.ReactNode }) {
  return (
    <details open={open} className="group rounded-lg bg-slate-900/70">
      <summary className="flex cursor-pointer list-none items-baseline justify-between gap-2 px-3 py-2 [&::-webkit-details-marker]:hidden">
        <span className="font-medium">{title}</span>
        <span className="min-w-0 truncate text-xs text-slate-400 group-open:hidden">{summary}</span>
        <span className="text-slate-500 transition-transform group-open:rotate-90" aria-hidden="true">›</span>
      </summary>
      <div className="px-3 pb-3">{children}</div>
    </details>
  )
}
