import { useState } from 'react'
import AlertsTab from './components/AlertsTab'
import AnomalyTab from './components/AnomalyTab'
import CalendarTab from './components/CalendarTab'
import CompareTab from './components/CompareTab'
import ExploreTab from './components/ExploreTab'
import InvestigateTab, { type InvestigateTarget } from './components/InvestigateTab'
import MethodTab from './components/MethodTab'
import { REGION, type Aoi } from './lib/aoi'

const TABS = [
  { id: 'explore', label: 'Explore' },
  { id: 'anomalies', label: 'Anomaly map' },
  { id: 'investigate', label: 'Investigate with AI' },
  { id: 'history', label: 'Calendar' },
  { id: 'compare', label: 'Compare' },
  { id: 'alerts', label: 'Alerts' },
  { id: 'method', label: 'Method' },
] as const
type Tab = (typeof TABS)[number]['id']

export default function App() {
  const [aoi, setAoi] = useState<Aoi>(REGION)
  const [tab, setTab] = useState<Tab>('explore')
  const [target, setTarget] = useState<InvestigateTarget | null>(null)

  return (
    <div className="min-h-screen px-4 pb-6 sm:px-6">
      <header className="mb-4 flex flex-wrap items-center gap-x-8 gap-y-1 border-b border-slate-800">
        <div className="py-3">
          <h1 className="text-lg font-semibold tracking-tight"><span className="text-orange-500" aria-hidden="true">◆ </span>FIREWATCH 360</h1>
          <p className="hidden text-xs text-slate-500 md:block">US wildfire activity from NASA MODIS + VIIRS, 2000 → today</p>
        </div>
        <nav role="tablist" aria-label="Views" className="-mb-px flex max-w-full gap-5 self-end overflow-x-auto text-sm">
          {TABS.map((t) => (
            <button
              key={t.id}
              role="tab"
              id={`tab-${t.id}`}
              aria-selected={tab === t.id}
              aria-controls="panel"
              tabIndex={tab === t.id ? 0 : -1}
              onClick={() => setTab(t.id)}
              onKeyDown={(e) => {
                const i = TABS.findIndex((x) => x.id === tab)
                const next = e.key === 'ArrowRight' ? i + 1 : e.key === 'ArrowLeft' ? i - 1 : null
                if (next == null) return
                const t2 = TABS[(next + TABS.length) % TABS.length].id
                setTab(t2)
                document.getElementById(`tab-${t2}`)?.focus()
              }}
              className={`shrink-0 whitespace-nowrap border-b-2 pb-3 pt-1 ${tab === t.id ? 'border-orange-500 font-medium text-slate-100' : 'border-transparent text-slate-400 hover:text-slate-200'}`}
            >
              {t.label}
            </button>
          ))}
        </nav>
      </header>

      <main role="tabpanel" id="panel" aria-labelledby={`tab-${tab}`}>
        {tab === 'explore' && <ExploreTab aoi={aoi} setAoi={setAoi} />}
        {tab === 'history' && <CalendarTab aoi={aoi} />}
        {tab === 'anomalies' && <AnomalyTab aoi={aoi} onInvestigate={(t) => { setTarget({ ...t, nonce: Date.now() }); setTab('investigate') }} />}
        {tab === 'investigate' && <InvestigateTab aoi={aoi} target={target} />}
        {tab === 'compare' && <CompareTab aoi={aoi} />}
        {tab === 'alerts' && <AlertsTab aoi={aoi} />}
        {tab === 'method' && <MethodTab aoi={aoi} />}
      </main>

      <footer className="mt-10 border-t border-slate-800 pt-3 text-xs text-slate-500">
        Prototype for NASA Space Apps 2026 · not an official NASA product · outlooks are experimental statistics, not fire-danger forecasts · data: NASA FIRMS, NIFC, MTBS, NOAA HMS, USGS NLCD · all dates UTC
      </footer>
    </div>
  )
}
