import type { ImpactItem, ImpactLayer, ImpactResponse } from '../types/api'

const n0 = (v: unknown) => (typeof v === 'number' ? Math.round(v).toLocaleString() : '—')
const km2 = (v: unknown) => (typeof v === 'number' ? `${v >= 100 ? Math.round(v).toLocaleString() : v.toFixed(1)} km²` : '—')
const pct = (v: unknown) => (typeof v === 'number' ? `${(v * 100).toFixed(0)}%` : '—')
const fmtDate = (d: string) => new Date(`${d}T00:00:00Z`).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' })

type L = ImpactLayer & Record<string, any> // eslint-disable-line @typescript-eslint/no-explicit-any

/** Plain-language exposure sentence built only from values that are available. */
function headline(d: ImpactResponse): string {
  const f = d.fire
  if (f.detections === 0) return `No fire detections in this area between ${fmtDate(d.filters.start)} and ${fmtDate(d.filters.end)}, so nothing is within ${f.buffer_km} km of active fire.`
  const L = d.layers as Record<string, L>
  const parts: string[] = []
  if (L.population.status === 'ok' && L.population.near_fire) parts.push(`about ${n0(L.population.near_fire.population)} residents (2020 Census)`)
  if (L.schools.status === 'ok') parts.push(`${n0(L.schools.near_fire)} schools`)
  if (L.hospitals.status === 'ok') parts.push(`${n0(L.hospitals.near_fire)} hospitals${L.hospitals.beds_near_fire ? ` (${n0(L.hospitals.beds_near_fire)} beds)` : ''}`)
  if (L.firestations?.status === 'ok') parts.push(`${n0(L.firestations.near_fire)} fire stations`)
  if (L.airports.status === 'ok' && L.airports.near_fire) parts.push(`${n0(L.airports.near_fire)} airports`)
  if (L.roads.status === 'ok') parts.push(`${n0(L.roads.near_fire_km)} km of major roads`)
  if (L.protected.status === 'ok') parts.push(`${km2(L.protected.near_fire_km2)} of protected land`)
  if (L.agriculture.status === 'ok') parts.push(`${km2(L.agriculture.near_fire_km2)} of farmland`)
  const list = parts.length > 1 ? `${parts.slice(0, -1).join(', ')} and ${parts[parts.length - 1]}` : parts[0] ?? 'no mapped assets'
  return `Within ${f.buffer_km} km of ${n0(f.detections)} fire detections (${fmtDate(d.filters.start)} – ${fmtDate(d.filters.end)}): ${list}.`
}

export default function ImpactPanel({ data, loading, error, bufferKm }: { data?: ImpactResponse; loading: boolean; error?: Error | null; bufferKm: number }) {
  const box = 'rounded-lg border border-slate-700 bg-slate-900/60 p-4'
  if (error) return <section className={box} role="alert"><h2 className="text-lg font-medium">What could this affect?</h2><p className="mt-1 text-red-300">{error.message}</p></section>
  if (!data) return <section className={box}><h2 className="text-lg font-medium">What could this affect?</h2><p className="mt-1 text-slate-400" role="status">{loading ? 'Checking population, infrastructure and land around the fire detections… (first query for a large area can take up to a minute)' : '—'}</p></section>
  if (data.status === 'aoi_required') return <section className={box}><h2 className="text-lg font-medium">What could this affect?</h2><p className="mt-1 text-slate-300">{data.message}</p></section>
  const L = data.layers as Record<string, L>

  return (
    <section aria-labelledby="impact-h" className={`${box} ${loading ? 'opacity-70' : ''}`}>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="impact-h" className="text-lg font-medium">What could this affect?</h2>
        <p className="text-xs text-slate-400">Area {km2(data.area_km2)} · fire exposure zone {km2(data.fire.zone_km2)} (within {bufferKm} km)</p>
      </div>
      <p className="mt-1 text-base text-slate-100">{headline(data)}</p>

      <div className="mt-3 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Tile title="Population" layer={L.population}
              main={L.population.near_fire ? `${n0(L.population.near_fire.population)} near fire` : undefined}
              sub={L.population.in_area ? `${n0(L.population.in_area.population)} residents, ${n0(L.population.in_area.housing_units)} homes in area` : undefined} />
        <Tile title="Wildland–urban interface" layer={L.wui}
              main={L.wui.near_fire ? `${n0(L.wui.near_fire.housing_units)} WUI homes near fire` : undefined}
              sub={L.wui.in_area ? `${pct(L.wui.in_area.share_of_population)} of residents live in WUI (${n0(L.wui.in_area.interface?.population)} interface, ${n0(L.wui.in_area.intermix?.population)} intermix)` : undefined} />
        <Tile title="Population centers" layer={L.places} main={`${n0(L.places.near_fire)} of ${n0(L.places.in_area)} near fire`}
              sub={L.places.items?.length ? L.places.items.slice(0, 3).map((i: ImpactItem) => `${i.name} (${dist(i)})`).join(' · ') : 'None mapped in this area'} />
        <Tile title="Roads" layer={L.roads} main={`${n0(L.roads.near_fire_km)} km near fire`}
              sub={`${n0(L.roads.in_area_km)} km of major/secondary highways in area${L.roads.names?.length ? `: ${L.roads.names.slice(0, 4).join(', ')}` : ''}`} />
        <Tile title="Hospitals" layer={L.hospitals} main={`${n0(L.hospitals.near_fire)} of ${n0(L.hospitals.in_area)} near fire`}
              sub={L.hospitals.beds_near_fire ? `${n0(L.hospitals.beds_near_fire)} beds near fire` : undefined} items={L.hospitals.items} />
        <Tile title="Schools" layer={L.schools} main={`${n0(L.schools.near_fire)} of ${n0(L.schools.in_area)} near fire`} items={L.schools.items} />
        {L.firestations && <Tile title="Fire stations" layer={L.firestations} main={`${n0(L.firestations.near_fire)} of ${n0(L.firestations.in_area)} near fire`}
              sub={L.firestations.items?.[0]?.distance_to_fire_km != null ? `Nearest: ${L.firestations.items[0].name} (${dist(L.firestations.items[0])})` : undefined} items={L.firestations.items} />}
        <Tile title="Airports" layer={L.airports} main={`${n0(L.airports.near_fire)} of ${n0(L.airports.in_area)} near fire`}
              sub={L.airports.by_size ? `large ${L.airports.by_size.large} · medium ${L.airports.by_size.medium} · small ${L.airports.by_size.small}` : undefined} items={L.airports.items} />
        <Tile title="Protected areas" layer={L.protected}
              main={L.protected.status === 'ok' ? `${km2(L.protected.near_fire_km2)} near fire` : undefined}
              sub={L.protected.status === 'ok' ? `${km2(L.protected.in_area_km2)} (${pct(L.protected.share_of_area)} of area), ${L.protected.units_near_fire} of ${L.protected.units_in_area} units near fire` : undefined}
              items={L.protected.items?.map((u: any) => ({ name: `${u.name}${u.manager ? ` (${u.manager})` : ''}`, distance_to_fire_km: null, note: `${km2(u.near_fire_km2)} near fire` }))} />
        <Tile title="Agriculture" layer={L.agriculture}
              main={L.agriculture.status === 'ok' ? `${km2(L.agriculture.near_fire_km2)} near fire` : undefined}
              sub={L.agriculture.status === 'ok' ? `${km2(L.agriculture.in_area_km2)} farmland (${pct(L.agriculture.share_of_area)} of area): crops ${km2(L.agriculture.crops_km2)}, pasture/hay ${km2(L.agriculture.pasture_hay_km2)}` : undefined} />
      </div>

      <p className="mt-3 text-xs text-slate-500">
        {data.note} Sources:{' '}
        {[...new Set(Object.values(L).map((l) => l.source).filter(Boolean))].join('; ')}.
        {L.population.note ? ` ${L.population.note}` : ''}
      </p>
    </section>
  )
}

function dist(i: ImpactItem) {
  return i.distance_to_fire_km == null ? 'no fire nearby' : `${i.distance_to_fire_km} km from fire`
}

function Tile({ title, layer, main, sub, items }: { title: string; layer: L; main?: string; sub?: string; items?: (ImpactItem & { note?: string })[] }) {
  const ok = layer?.status === 'ok'
  return (
    <div className="min-w-0 rounded border border-slate-700/80 bg-slate-950/40 p-2">
      <h3 className="text-xs text-slate-400">{title}</h3>
      {ok ? (
        <>
          {main && <p className="text-base font-semibold">{main}</p>}
          {sub && <p className="text-xs text-slate-400">{sub}</p>}
          {items && items.length > 0 && (
            <details className="mt-1 text-xs">
              <summary className="cursor-pointer text-slate-300">Nearest to fire</summary>
              <ul className="mt-1 space-y-0.5 text-slate-400">
                {items.map((i, k) => <li key={k}>{String(i.name)} — {i.note ?? dist(i)}</li>)}
              </ul>
            </details>
          )}
        </>
      ) : (
        <p className="text-sm text-amber-200">{layer?.status === 'too_large' ? 'Area too large' : layer?.status === 'outside_coverage' ? 'Not covered here' : 'Unavailable'}
          <span className="block text-xs text-slate-400">{layer?.message}</span></p>
      )}
    </div>
  )
}
