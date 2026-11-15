import type { FeatureCollection as FC } from 'geojson'
import * as maplibregl from 'maplibre-gl'
import type { GeoJSONSource, MapMouseEvent } from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
// Vite pre-bundles maplibre-gl, which breaks its relative worker URL; point it at the real file.
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?url'
import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { aoiRing, circleRing, type Aoi } from '../lib/aoi'
import { OVERLAYS, RASTER_TILES, type OverlayId, type RasterOverlay, type VectorOverlay } from '../lib/overlays'
import type { HotspotsResponse, NrtResponse } from '../types/api'

maplibregl.setWorkerUrl(workerUrl)

export type DrawMode = 'none' | 'rect' | 'polygon' | 'circle'

// Okabe–Ito colours; sensors also differ in size and the legend names them.
const SENSOR_COLOR = { MODIS: '#E69F00', VIIRS: '#56B4E9' } as const
const STYLE_URL = 'https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json'
const FALLBACK_STYLE: maplibregl.StyleSpecification = {
  version: 8,
  sources: {},
  layers: [{ id: 'bg', type: 'background', paint: { 'background-color': '#0b0f14' } }],
}
const US_BOUNDS: [number, number, number, number] = [-125, 24, -66, 50]
const EMPTY: FC = { type: 'FeatureCollection', features: [] }

const polygonFC = (ring: [number, number][] | null): FC =>
  ring ? { type: 'FeatureCollection', features: [{ type: 'Feature', properties: {}, geometry: { type: 'Polygon', coordinates: [ring] } }] } : EMPTY

const lineFC = (pts: [number, number][]): FC =>
  pts.length < 2
    ? { type: 'FeatureCollection', features: pts.map((p) => ({ type: 'Feature', properties: {}, geometry: { type: 'Point', coordinates: p } })) }
    : { type: 'FeatureCollection', features: [{ type: 'Feature', properties: {}, geometry: { type: 'LineString', coordinates: pts } }] }

interface Props {
  aoi: Aoi
  onAoi: (a: Aoi) => void
  mode: DrawMode
  onModeDone: () => void
  circleKm: number
  hotspots: HotspotsResponse | undefined
  liveData?: NrtResponse
  overlays?: Partial<Record<OverlayId, boolean>>
  impactGeo?: Partial<Record<VectorOverlay, unknown>>
  fireZone?: unknown | null
}

export default function MapView({ aoi, onAoi, mode, onModeDone, circleKm, hotspots, liveData, overlays = {}, impactGeo = {}, fireZone = null }: Props) {
  const el = useRef<HTMLDivElement>(null)
  const mapRef = useRef<maplibregl.Map | null>(null)
  const [ready, setReady] = useState(false)
  const [vertices, setVertices] = useState<[number, number][]>([])
  // Event handlers are bound once; refs give them the latest props.
  const live = useRef({ mode, onAoi, onModeDone, circleKm, vertices })
  const finishPolygonRef = useRef(() => {})
  const finishPolygon = () => {
    // A double click also fires two clicks; drop consecutive duplicates.
    const v = vertices.filter((p, i) => i === 0 || Math.hypot(p[0] - vertices[i - 1][0], p[1] - vertices[i - 1][1]) > 1e-6)
    if (v.length >= 3) {
      onAoi({ kind: 'polygon', ring: v })
      onModeDone()
    }
    setVertices([])
  }
  useLayoutEffect(() => {
    live.current = { mode, onAoi, onModeDone, circleKm, vertices }
    finishPolygonRef.current = finishPolygon
  })

  // ---------------------------------------------------------------- init
  useEffect(() => {
    if (!el.current) return
    const map = new maplibregl.Map({ container: el.current, style: STYLE_URL, bounds: US_BOUNDS, attributionControl: { compact: true } })
    mapRef.current = map
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right')
    map.addControl(new maplibregl.ScaleControl({ unit: 'metric' }), 'bottom-left')
    let fellBack = false
    map.on('error', (e: maplibregl.ErrorEvent) => {
      // Basemap unreachable (offline): keep working on a plain background.
      if (!fellBack && !map.isStyleLoaded() && String(e.error?.message ?? '').match(/style|fetch|Failed/i)) {
        fellBack = true
        map.setStyle(FALLBACK_STYLE)
      }
    })
    map.on('load', () => setReady(true))
    map.on('style.load', () => {
      if (map.getSource('aoi')) return
      map.addSource('aoi', { type: 'geojson', data: EMPTY })
      map.addSource('draft', { type: 'geojson', data: EMPTY })
      map.addSource('hotspots', { type: 'geojson', data: EMPTY })
      map.addSource('live', { type: 'geojson', data: EMPTY })
      // Impact rasters sit under everything else we draw.
      for (const [id, r] of Object.entries(RASTER_TILES) as [RasterOverlay, (typeof RASTER_TILES)[RasterOverlay]][]) {
        map.addSource(`ov-${id}`, { type: 'raster', tiles: r.tiles, tileSize: 256, maxzoom: r.maxzoom, attribution: r.attribution })
        map.addLayer({ id: `ov-${id}`, type: 'raster', source: `ov-${id}`, layout: { visibility: 'none' }, paint: { 'raster-opacity': r.opacity } })
      }
      map.addSource('ov-firezone', { type: 'geojson', data: EMPTY })
      map.addLayer({ id: 'ov-firezone-fill', type: 'fill', source: 'ov-firezone', layout: { visibility: 'none' }, paint: { 'fill-color': '#f43f5e', 'fill-opacity': 0.12 } })
      map.addLayer({ id: 'ov-firezone', type: 'line', source: 'ov-firezone', layout: { visibility: 'none' }, paint: { 'line-color': '#f43f5e', 'line-width': 1.5, 'line-dasharray': [3, 2] } })
      map.addSource('ov-roads', { type: 'geojson', data: EMPTY })
      map.addLayer({ id: 'ov-roads', type: 'line', source: 'ov-roads', layout: { visibility: 'none' },
        paint: { 'line-color': '#facc15', 'line-opacity': 0.8, 'line-width': ['match', ['get', 'type'], 'Major Highway', 2.5, 1.5] } })
      map.addLayer({ id: 'aoi-fill', type: 'fill', source: 'aoi', paint: { 'fill-color': '#f97316', 'fill-opacity': 0.08 } })
      map.addLayer({ id: 'aoi-line', type: 'line', source: 'aoi', paint: { 'line-color': '#f97316', 'line-width': 2 } })
      map.addLayer({
        id: 'hotspots', type: 'circle', source: 'hotspots',
        paint: {
          'circle-color': ['match', ['get', 'sensor'], 'MODIS', SENSOR_COLOR.MODIS, SENSOR_COLOR.VIIRS],
          'circle-radius': ['interpolate', ['linear'], ['zoom'], 3, ['match', ['get', 'sensor'], 'MODIS', 2, 1.4], 10, ['match', ['get', 'sensor'], 'MODIS', 7, 4.5]],
          'circle-opacity': 0.85,
          'circle-stroke-color': '#ffffff',
          'circle-stroke-width': ['case', ['==', ['get', 'level'], 'nrt'], 1, 0],
        },
      })
      // Live NRT: hollow rings in the sensor colour, drawn above the archive points.
      map.addLayer({
        id: 'live', type: 'circle', source: 'live',
        paint: {
          'circle-color': 'rgba(0,0,0,0)',
          'circle-radius': ['interpolate', ['linear'], ['zoom'], 3, 3, 10, 9],
          'circle-stroke-width': 2,
          'circle-stroke-color': ['match', ['get', 'sensor'], 'MODIS', SENSOR_COLOR.MODIS, SENSOR_COLOR.VIIRS],
        },
      })
      // Impact points: coloured circle + letter, so the type never depends on colour alone.
      for (const o of OVERLAYS.filter((x) => x.kind === 'vector' && x.glyph)) {
        map.addSource(`ov-${o.id}`, { type: 'geojson', data: EMPTY })
        map.addLayer({ id: `ov-${o.id}`, type: 'circle', source: `ov-${o.id}`, layout: { visibility: 'none' },
          paint: { 'circle-color': o.color, 'circle-stroke-color': '#0b0f14', 'circle-stroke-width': 1,
                   'circle-radius': o.id === 'reported'
                     ? ['interpolate', ['linear'], ['sqrt', ['max', ['coalesce', ['get', 'acres'], 0], 0]], 0, 4, 100, 6, 1000, 14]
                     : ['interpolate', ['linear'], ['zoom'], 4, 4, 10, 8] } })
        map.addLayer({ id: `ov-${o.id}-label`, type: 'symbol', source: `ov-${o.id}`, layout: { visibility: 'none', 'text-field': o.glyph!,
          'text-size': ['interpolate', ['linear'], ['zoom'], 4, 7, 10, 11], 'text-allow-overlap': true, 'text-font': ['Open Sans Bold', 'Noto Sans Bold'] },
          paint: { 'text-color': '#0b0f14' } })
        map.on('click', `ov-${o.id}`, (e: maplibregl.MapLayerMouseEvent) => {
          const p = e.features?.[0]?.properties as Record<string, unknown> | undefined
          if (!p || live.current.mode !== 'none') return
          const extra = ['date', 'acres', 'cause', 'county', 'city', 'state', 'type', 'beds', 'size', 'iata', 'pop_max'].filter((k) => p[k] != null && p[k] !== '' && p[k] !== 'null')
            .map((k) => `${k === 'pop_max' ? 'population (est.)' : k}: ${typeof p[k] === 'number' ? (p[k] as number).toLocaleString() : p[k]}`).join('<br/>')
          new maplibregl.Popup().setLngLat(e.lngLat).setHTML(`<div style="color:#0f172a;font:12px system-ui"><b>${o.label.replace(/s$/, '')}</b>: ${String(p.name ?? '')}<br/>${extra}</div>`).addTo(map)
        })
      }
      map.addLayer({ id: 'draft-line', type: 'line', source: 'draft', paint: { 'line-color': '#fde68a', 'line-width': 2, 'line-dasharray': [2, 2] } })
      map.addLayer({ id: 'draft-pt', type: 'circle', source: 'draft', filter: ['==', ['geometry-type'], 'Point'], paint: { 'circle-color': '#fde68a', 'circle-radius': 4 } })
      map.addLayer({ id: 'draft-fill', type: 'fill', source: 'draft', filter: ['==', ['geometry-type'], 'Polygon'], paint: { 'fill-color': '#fde68a', 'fill-opacity': 0.1 } })
      setReady(true)
    })

    const popup = (e: maplibregl.MapLayerMouseEvent) => {
      if (live.current.mode !== 'none') return
      const f = e.features?.[0]
      if (!f) return
      const p = f.properties as Record<string, string | number | null>
      const html = `<div style="color:#0f172a;font:12px system-ui">
        <b>${p.sensor}</b>${p.live ? ' <i>(live NRT, provisional)</i>' : p.level === 'nrt' ? ' <i>(near-real-time, provisional)</i>' : ''}<br/>
        ${p.date} ${String(p.time_utc).slice(0, 2)}:${String(p.time_utc).slice(2)} UTC (${p.daynight === 'D' ? 'day' : 'night'})<br/>
        FRP ${p.frp ?? '—'} MW · confidence ${p.confidence}</div>`
      new maplibregl.Popup({ closeButton: true }).setLngLat(e.lngLat).setHTML(html).addTo(map)
    }
    map.on('click', 'hotspots', popup)
    map.on('click', 'live', popup)
    map.on('mouseenter', 'hotspots', () => { if (live.current.mode === 'none') map.getCanvas().style.cursor = 'pointer' })
    map.on('mouseleave', 'hotspots', () => { if (live.current.mode === 'none') map.getCanvas().style.cursor = '' })

    // ---------------------------------------------------------------- drawing
    let rectStart: maplibregl.LngLat | null = null
    const draft = (fc: FC) => (map.getSource('draft') as GeoJSONSource | undefined)?.setData(fc)
    const rectRing = (a: maplibregl.LngLat, b: maplibregl.LngLat): [number, number][] => {
      const [w, e] = [Math.min(a.lng, b.lng), Math.max(a.lng, b.lng)]
      const [s, n] = [Math.min(a.lat, b.lat), Math.max(a.lat, b.lat)]
      return [[w, s], [e, s], [e, n], [w, n], [w, s]]
    }
    map.on('mousedown', (e: MapMouseEvent) => {
      if (live.current.mode !== 'rect') return
      e.preventDefault()
      rectStart = e.lngLat
    })
    map.on('mousemove', (e: MapMouseEvent) => {
      const { mode: m, vertices: v, circleKm: km } = live.current
      if (m === 'rect' && rectStart) draft(polygonFC(rectRing(rectStart, e.lngLat)))
      else if (m === 'polygon' && v.length) draft(lineFC([...v, [e.lngLat.lng, e.lngLat.lat], ...(v.length > 1 ? [v[0]] : [])]))
      else if (m === 'circle') draft(polygonFC(circleRing(e.lngLat.lat, e.lngLat.lng, km)))
    })
    map.on('mouseup', (e: MapMouseEvent) => {
      if (live.current.mode !== 'rect' || !rectStart) return
      const r = rectRing(rectStart, e.lngLat)
      rectStart = null
      draft(EMPTY)
      if (r[1][0] - r[0][0] < 0.01 || r[2][1] - r[1][1] < 0.01) return // ignore a click without a drag
      live.current.onAoi({ kind: 'bbox', bbox: [r[0][0], r[0][1], r[2][0], r[2][1]] })
      live.current.onModeDone()
    })
    map.on('click', (e: MapMouseEvent) => {
      const { mode: m, circleKm: km } = live.current
      if (m === 'polygon') {
        setVertices((v) => [...v, [e.lngLat.lng, e.lngLat.lat]])
      } else if (m === 'circle') {
        draft(EMPTY)
        live.current.onAoi({ kind: 'circle', lat: e.lngLat.lat, lon: e.lngLat.lng, radius_km: km })
        live.current.onModeDone()
      }
    })
    map.on('dblclick', (e: MapMouseEvent) => {
      if (live.current.mode !== 'polygon') return
      e.preventDefault()
      finishPolygonRef.current()
    })
    return () => map.remove()
  }, [])

  useEffect(() => {
    const map = mapRef.current
    if (!map) return
    setVertices([])
    ;(map.getSource('draft') as GeoJSONSource | undefined)?.setData(EMPTY)
    map.getCanvas().style.cursor = mode === 'none' ? '' : 'crosshair'
    if (mode === 'rect') map.dragPan.disable()
    else map.dragPan.enable()
    if (mode === 'polygon') map.doubleClickZoom.disable()
    else map.doubleClickZoom.enable()
  }, [mode])

  useEffect(() => {
    const map = mapRef.current
    if (!map || !ready || mode !== 'polygon') return
    ;(map.getSource('draft') as GeoJSONSource | undefined)?.setData(lineFC(vertices))
  }, [vertices, mode, ready])

  // ---------------------------------------------------------------- data
  useEffect(() => {
    const map = mapRef.current
    if (!map || !ready) return
    const ring = aoiRing(aoi)
    ;(map.getSource('aoi') as GeoJSONSource | undefined)?.setData(polygonFC(ring))
    if (ring) {
      const xs = ring.map((p) => p[0])
      const ys = ring.map((p) => p[1])
      map.fitBounds([Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)], { padding: 40, duration: 600 })
    } else {
      map.fitBounds(US_BOUNDS, { duration: 600 })
    }
  }, [aoi, ready])

  // Impact overlays: visibility and data
  useEffect(() => {
    const map = mapRef.current
    if (!map || !ready) return
    const vis = (id: string, on: boolean) => map.getLayer(id) && map.setLayoutProperty(id, 'visibility', on ? 'visible' : 'none')
    for (const o of OVERLAYS) {
      const on = !!overlays[o.id]
      if (o.id === 'firezone') { vis('ov-firezone', on); vis('ov-firezone-fill', on) }
      else { vis(`ov-${o.id}`, on); vis(`ov-${o.id}-label`, on) }
    }
  }, [overlays, ready])

  useEffect(() => {
    const map = mapRef.current
    if (!map || !ready) return
    for (const id of ['places', 'roads', 'hospitals', 'schools', 'firestations', 'airports', 'reported'] as VectorOverlay[]) {
      ;(map.getSource(`ov-${id}`) as GeoJSONSource | undefined)?.setData((impactGeo[id] as FC) ?? EMPTY)
    }
    const zone = fireZone ? ({ type: 'FeatureCollection', features: [{ type: 'Feature', properties: {}, geometry: fireZone }] } as unknown as FC) : EMPTY
    ;(map.getSource('ov-firezone') as GeoJSONSource | undefined)?.setData(zone)
  }, [impactGeo, fireZone, ready])

  useEffect(() => {
    const map = mapRef.current
    if (!map || !ready) return
    ;(map.getSource('live') as GeoJSONSource | undefined)?.setData((liveData as unknown as FC) ?? EMPTY)
  }, [liveData, ready])

  useEffect(() => {
    const map = mapRef.current
    if (!map || !ready) return
    ;(map.getSource('hotspots') as GeoJSONSource | undefined)?.setData((hotspots as unknown as FC) ?? EMPTY)
  }, [hotspots, ready])

  return (
    <div className="relative h-[62vh] min-h-80 w-full overflow-hidden rounded-lg border border-slate-800">
      <div ref={el} className="h-full w-full" role="application" aria-label="Map of hotspots and area of interest" />
      {mode === 'polygon' && (
        <div className="absolute left-2 top-2 flex items-center gap-2 rounded bg-slate-900/90 px-2 py-1 text-xs">
          <span>Click to add points ({vertices.length}); double-click or</span>
          <button className="rounded bg-orange-600 px-2 py-0.5 disabled:opacity-50" disabled={vertices.length < 3} onClick={finishPolygon}>
            Finish
          </button>
        </div>
      )}
      {mode === 'rect' && <Hint text="Drag on the map to draw a rectangle" />}
      {mode === 'circle' && <Hint text={`Click the map to set the centre (${circleKm} km radius)`} />}
      <div className="absolute bottom-8 right-2 rounded bg-slate-900/90 px-2 py-1 text-xs" aria-label="Legend">
        <LegendDot color={SENSOR_COLOR.MODIS} size={10} label="MODIS (1 km)" />
        <LegendDot color={SENSOR_COLOR.VIIRS} size={7} label="VIIRS (375 m)" />
        <div className="mt-0.5 flex items-center gap-1.5">
          <span className="inline-block h-2 w-2 rounded-full border border-white" /> near-real-time (provisional)
        </div>
        {OVERLAYS.filter((o) => overlays[o.id]).map((o) => (
          <div key={o.id} className="mt-0.5 flex items-center gap-1.5">
            {o.glyph ? (
              <span className="inline-flex h-3 w-3 items-center justify-center rounded-full text-[8px] font-bold text-slate-900" style={{ background: o.color }}>{o.glyph}</span>
            ) : o.kind === 'zone' ? (
              <span className="inline-block h-2.5 w-3 border border-dashed" style={{ borderColor: o.color, background: `${o.color}33` }} />
            ) : o.id === 'roads' ? (
              <span className="inline-block h-0.5 w-3" style={{ background: o.color }} />
            ) : (
              <span className="inline-block h-2.5 w-3 opacity-70" style={{ background: o.color }} />
            )}
            {o.label}
          </div>
        ))}
        {liveData && (
          <div className="mt-0.5 flex items-center gap-1.5">
            <span className="inline-block h-2.5 w-2.5 rounded-full border-2 border-sky-300" /> live NRT ring (last days)
          </div>
        )}
      </div>
    </div>
  )
}

function Hint({ text }: { text: string }) {
  return <div className="absolute left-2 top-2 rounded bg-slate-900/90 px-2 py-1 text-xs">{text} · Esc to cancel</div>
}

function LegendDot({ color, size, label }: { color: string; size: number; label: string }) {
  return (
    <div className="flex items-center gap-1.5">
      <span className="inline-block rounded-full" style={{ background: color, width: size, height: size }} /> {label}
    </div>
  )
}
