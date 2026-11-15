import { useState } from 'react'
import AnomalyTab from './components/AnomalyTab'
import CalendarTab from './components/CalendarTab'
import ExploreTab from './components/ExploreTab'
import MethodTab from './components/MethodTab'
import { REGION, type Aoi } from './lib/aoi'

const TABS = [
  { id: 'explore', label: 'Explore' },
  { id: 'history', label: 'Calendar' },
  { id: 'anomalies', label: 'Anomaly map' },
  { id: 'method', label: 'Method & data' },
] as const
type Tab = (typeof TABS)[number]['id']

export default function App() {
  const [aoi, setAoi] = useState<Aoi>(REGION)
  const [tab, setTab] = useState<Tab>('explore')

  return (
    <div className="min-h-screen p-4 sm:p-6">
      <header className="mb-4 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">FIREWATCH 360</h1>
          <p className="text-sm text-slate-400">Harmonized burning-activity calendar from NASA FIRMS MODIS and VIIRS hotspots</p>
        </div>
        <nav role="tablist" aria-label="Views" className="flex gap-1">
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
              className={`rounded px-3 py-1.5 text-sm ${tab === t.id ? 'bg-orange-600 font-medium' : 'border border-slate-700 hover:bg-slate-800'}`}
            >
              {t.label}
            </button>
          ))}
        </nav>
      </header>

      <main role="tabpanel" id="panel" aria-labelledby={`tab-${tab}`}>
        {tab === 'explore' && <ExploreTab aoi={aoi} setAoi={setAoi} />}
        {tab === 'history' && <CalendarTab aoi={aoi} />}
        {tab === 'anomalies' && <AnomalyTab aoi={aoi} />}
        {tab === 'method' && <MethodTab aoi={aoi} />}
      </main>

      <footer className="mt-10 border-t border-slate-800 pt-3 text-xs text-slate-500">
        Prototype for NASA Space Apps 2026 · not an official NASA product · not a forecast · data: NASA FIRMS · all dates UTC
      </footer>
    </div>
  )
}
