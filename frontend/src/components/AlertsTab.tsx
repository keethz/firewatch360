import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { aoiQuery, describeAoi, type Aoi } from '../lib/aoi'
import { getJson } from '../lib/api'
import type { AlertEvent, SavedArea } from '../types/api'

async function send<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, { method, headers: body ? { 'Content-Type': 'application/json' } : undefined, body: body ? JSON.stringify(body) : undefined })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(typeof data?.detail === 'string' ? data.detail : JSON.stringify(data?.detail ?? res.statusText))
  return data as T
}

const RULE_LABEL = { anomaly: 'Unusual activity', nrt: 'New detections', fire_weather: 'Fire weather' } as const

export default function AlertsTab({ aoi }: { aoi: Aoi }) {
  const qc = useQueryClient()
  const [name, setName] = useState('')
  const [anomaly, setAnomaly] = useState(true)
  const [nrtOn, setNrtOn] = useState(true)
  const [nrtKm, setNrtKm] = useState(5)
  const [fireWeather, setFireWeather] = useState(true)
  const [webhook, setWebhook] = useState('')
  const [email, setEmail] = useState('')
  const [msg, setMsg] = useState<string | null>(null)

  const areas = useQuery({ queryKey: ['alert-areas'], queryFn: () => getJson<{ areas: SavedArea[] }>('/api/alerts/areas') })
  const events = useQuery({ queryKey: ['alert-events'], queryFn: () => getJson<{ events: AlertEvent[] }>('/api/alerts/events'), refetchInterval: 60_000 })
  const refresh = () => { qc.invalidateQueries({ queryKey: ['alert-areas'] }); qc.invalidateQueries({ queryKey: ['alert-events'] }) }

  const save = useMutation({
    mutationFn: () => send<SavedArea>('POST', '/api/alerts/areas', {
      name: name.trim() || describeAoi(aoi), aoi: aoiQuery(aoi), anomaly, nrt_km: nrtOn ? nrtKm : null, fire_weather: fireWeather,
      webhook_url: webhook.trim() || null, email: email.trim() || null,
    }),
    onSuccess: (a) => { setMsg(`Saved “${a.name}”. Checking it now…`); setName(''); refresh(); check.mutate(a.id) },
    onError: (e) => setMsg((e as Error).message),
  })
  const check = useMutation({
    mutationFn: (id?: string) => send<{ checked: number; new_events: AlertEvent[] }>('POST', `/api/alerts/check${id ? `?area_id=${id}` : ''}`),
    onSuccess: (r) => { setMsg(`Checked ${r.checked} area${r.checked === 1 ? '' : 's'}: ${r.new_events.length} new alert${r.new_events.length === 1 ? '' : 's'}.`); refresh() },
    onError: (e) => setMsg((e as Error).message),
  })
  const del = useMutation({ mutationFn: (id: string) => send('DELETE', `/api/alerts/areas/${id}`), onSuccess: refresh })

  const isRegion = aoi.kind === 'region'
  const ev = events.data?.events ?? []

  return (
    <div className="grid gap-4 lg:grid-cols-[380px_1fr]">
      <section aria-labelledby="save-h" className="rounded border border-slate-800 p-3">
        <h2 id="save-h" className="mb-1 text-lg font-semibold">Watch an area</h2>
        <p className="mb-3 text-sm text-slate-400">Saves the area selected on the Explore tab: <strong className="text-slate-200">{describeAoi(aoi)}</strong></p>
        {isRegion && <p className="mb-3 rounded bg-amber-500/10 p-2 text-sm text-amber-200">Pick a specific area first (preset, drawn shape or point + radius) on the Explore tab.</p>}
        <form className="space-y-3 text-sm" onSubmit={(e) => { e.preventDefault(); save.mutate() }}>
          <label className="block">Name
            <input className="mt-1 w-full rounded border border-slate-600 bg-slate-800 px-2 py-1" value={name} maxLength={80}
                   placeholder={describeAoi(aoi)} onChange={(e) => setName(e.target.value)} />
          </label>
          <fieldset className="space-y-1">
            <legend className="mb-1 text-slate-300">Alert me when</legend>
            <label className="flex items-center gap-2"><input type="checkbox" checked={anomaly} onChange={(e) => setAnomaly(e.target.checked)} />
              fire activity is unusual (Moderate or Severe)</label>
            <label className="flex flex-wrap items-center gap-2"><input type="checkbox" checked={nrtOn} onChange={(e) => setNrtOn(e.target.checked)} />
              new satellite detections in the area or within
              <input type="number" min={0} max={100} value={nrtKm} disabled={!nrtOn} onChange={(e) => setNrtKm(+e.target.value)}
                     className="w-16 rounded border border-slate-600 bg-slate-800 px-1" aria-label="distance in km" /> km</label>
            <label className="flex items-center gap-2"><input type="checkbox" checked={fireWeather} onChange={(e) => setFireWeather(e.target.checked)} />
              a Red Flag Warning or Fire Weather Watch is issued</label>
          </fieldset>
          <label className="block">Webhook URL <span className="text-slate-500">(optional, https; e.g. Slack or Teams incoming webhook)</span>
            <input type="url" className="mt-1 w-full rounded border border-slate-600 bg-slate-800 px-2 py-1" value={webhook} onChange={(e) => setWebhook(e.target.value)} placeholder="https://…" />
          </label>
          <label className="block">E-mail <span className="text-slate-500">(optional; needs SMTP on the server)</span>
            <input type="email" className="mt-1 w-full rounded border border-slate-600 bg-slate-800 px-2 py-1" value={email} onChange={(e) => setEmail(e.target.value)} />
          </label>
          <button type="submit" disabled={isRegion || save.isPending || (!anomaly && !nrtOn && !fireWeather)}
                  className="rounded bg-orange-600 px-3 py-1.5 font-medium hover:bg-orange-500 disabled:opacity-50">
            {save.isPending ? 'Saving…' : 'Save and watch'}
          </button>
        </form>
        {msg && <p className="mt-3 text-sm text-slate-300" aria-live="polite">{msg}</p>}
        <p className="mt-3 text-xs text-slate-500">
          Areas are checked when you press “Check now” and by the server’s scheduled job (backend/scripts/check_alerts.py, e.g. hourly).
          Each alert is sent once. Prototype: not an official warning system; follow local authorities.
        </p>
      </section>

      <div className="space-y-4">
        <section aria-labelledby="areas-h" className="rounded border border-slate-800 p-3">
          <div className="mb-2 flex items-center justify-between">
            <h2 id="areas-h" className="text-lg font-semibold">Watched areas</h2>
            <button onClick={() => check.mutate(undefined)} disabled={check.isPending || !areas.data?.areas.length}
                    className="rounded border border-slate-500 px-3 py-1 text-sm hover:bg-slate-800 disabled:opacity-50">
              {check.isPending ? 'Checking…' : 'Check all now'}
            </button>
          </div>
          {!areas.data?.areas.length && <p className="text-sm text-slate-400">No watched areas yet.</p>}
          <ul className="divide-y divide-slate-800">
            {areas.data?.areas.map((a) => (
              <li key={a.id} className="flex flex-wrap items-center gap-2 py-2 text-sm">
                <div className="mr-auto">
                  <p className="font-medium">{a.name}</p>
                  <p className="text-xs text-slate-400">
                    {[a.anomaly && 'unusual activity', a.nrt_km != null && `detections within ${a.nrt_km} km`, a.fire_weather && 'fire weather'].filter(Boolean).join(' · ')}
                    {a.webhook_url && ' · webhook'}{a.email && ' · e-mail'} · last checked {a.last_checked_utc ?? 'never'}
                  </p>
                </div>
                <button onClick={() => check.mutate(a.id)} disabled={check.isPending} className="rounded border border-slate-600 px-2 py-0.5 hover:bg-slate-800">Check now</button>
                <button onClick={() => del.mutate(a.id)} className="rounded border border-slate-600 px-2 py-0.5 text-red-300 hover:bg-slate-800" aria-label={`Stop watching ${a.name}`}>Remove</button>
              </li>
            ))}
          </ul>
        </section>

        <section aria-labelledby="inbox-h" className="rounded border border-slate-800 p-3">
          <h2 id="inbox-h" className="mb-2 text-lg font-semibold">Alerts</h2>
          {!ev.length && <p className="text-sm text-slate-400">No alerts yet.</p>}
          <ul className="space-y-2">
            {ev.map((e) => (
              <li key={e.id} className={`rounded border-l-4 bg-slate-900 p-2 text-sm ${e.severity === 'high' ? 'border-red-500' : 'border-amber-400'}`}>
                <p className="text-xs text-slate-400">
                  {e.severity === 'high' ? '▲▲ High' : '▲ Medium'} · {RULE_LABEL[e.rule]} · {e.area_name} · {e.created_utc}
                </p>
                <p className="font-medium">{e.title}</p>
                <p className="text-slate-300">{e.detail}</p>
                {e.note && <p className="text-xs text-slate-500">{e.note}</p>}
                {e.delivery && <p className="text-xs text-slate-500">Delivered: {Object.entries(e.delivery).map(([k, v]) => `${k.replace('_', '-')} ${v === true ? 'yes' : v}`).join(', ')}</p>}
              </li>
            ))}
          </ul>
        </section>
      </div>
    </div>
  )
}
