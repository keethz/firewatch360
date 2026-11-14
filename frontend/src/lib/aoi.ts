export type Aoi =
  | { kind: 'region' }
  | { kind: 'bbox'; bbox: [number, number, number, number]; label?: string }
  | { kind: 'circle'; lat: number; lon: number; radius_km: number }
  | { kind: 'polygon'; ring: [number, number][] } // [lon, lat], open ring

export const REGION: Aoi = { kind: 'region' }

export const PRESETS: { label: string; aoi: Aoi }[] = [
  { label: 'United States (all)', aoi: REGION },
  { label: 'California', aoi: { kind: 'bbox', bbox: [-124.5, 32.5, -114.1, 42.0], label: 'California' } },
  { label: 'Pacific Northwest', aoi: { kind: 'bbox', bbox: [-124.8, 42.0, -116.5, 49.0], label: 'Pacific Northwest' } },
  { label: 'Southeast US', aoi: { kind: 'bbox', bbox: [-91.7, 25.0, -75.4, 36.6], label: 'Southeast US' } },
]

const r4 = (v: number) => Math.round(v * 1e4) / 1e4

export function closedRing(ring: [number, number][]): [number, number][] {
  return [...ring.map(([x, y]) => [r4(x), r4(y)] as [number, number]), [r4(ring[0][0]), r4(ring[0][1])]]
}

/** Query-string fragment (no leading ?/&) for the backend AOI parameters. */
export function aoiQuery(aoi: Aoi): string {
  switch (aoi.kind) {
    case 'region':
      return ''
    case 'bbox':
      return `bbox=${aoi.bbox.map(r4).join(',')}`
    case 'circle':
      return `lat=${r4(aoi.lat)}&lon=${r4(aoi.lon)}&radius_km=${aoi.radius_km}`
    case 'polygon':
      return `polygon=${encodeURIComponent(JSON.stringify({ type: 'Polygon', coordinates: [closedRing(aoi.ring)] }))}`
  }
}

export function withAoi(path: string, aoi: Aoi, extra = ''): string {
  const parts = [aoiQuery(aoi), extra].filter(Boolean)
  return parts.length ? `${path}?${parts.join('&')}` : path
}

export function describeAoi(aoi: Aoi): string {
  const f = (v: number) => v.toFixed(2)
  switch (aoi.kind) {
    case 'region':
      return 'United States (all loaded data)'
    case 'bbox':
      return aoi.label ?? `Box ${aoi.bbox.map(f).join(', ')}`
    case 'circle':
      return `${aoi.radius_km} km around ${f(aoi.lat)}, ${f(aoi.lon)}`
    case 'polygon':
      return `Polygon, ${aoi.ring.length} vertices`
  }
}

/** Polygon approximating a geodesic circle, for display. */
export function circleRing(lat: number, lon: number, km: number, steps = 64): [number, number][] {
  const R = 6371.0088
  const d = km / R
  const la = (lat * Math.PI) / 180
  const lo = (lon * Math.PI) / 180
  const out: [number, number][] = []
  for (let i = 0; i <= steps; i++) {
    const b = (2 * Math.PI * i) / steps
    const la2 = Math.asin(Math.sin(la) * Math.cos(d) + Math.cos(la) * Math.sin(d) * Math.cos(b))
    const lo2 = lo + Math.atan2(Math.sin(b) * Math.sin(d) * Math.cos(la), Math.cos(d) - Math.sin(la) * Math.sin(la2))
    out.push([(lo2 * 180) / Math.PI, (la2 * 180) / Math.PI])
  }
  return out
}

export function aoiRing(aoi: Aoi): [number, number][] | null {
  switch (aoi.kind) {
    case 'region':
      return null
    case 'bbox': {
      const [w, s, e, n] = aoi.bbox
      return [[w, s], [e, s], [e, n], [w, n], [w, s]]
    }
    case 'circle':
      return circleRing(aoi.lat, aoi.lon, aoi.radius_km)
    case 'polygon':
      return [...aoi.ring, aoi.ring[0]]
  }
}
