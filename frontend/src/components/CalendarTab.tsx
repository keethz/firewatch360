import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { describeAoi, withAoi, type Aoi } from '../lib/aoi'
import { getJson } from '../lib/api'
import type { SeqRamp } from '../lib/color'
import type { CalendarResponse, ClimatologyResponse } from '../types/api'
import CalendarHeatmap, { type CalView } from './CalendarHeatmap'
import FireRiskBrief from './FireRiskBrief'
import SummaryPanel from './SummaryPanel'
import YearChart from './YearChart'

const n0 = (v: number) => Math.round(v).toLocaleString()

export default function CalendarTab({ aoi }: { aoi: Aoi }) {
  const [view, setView] = useState<CalView>('counts')
  const [ramp, setRamp] = useState<SeqRamp>('viridis')
  const [altDiv, setAltDiv] = useState(false)
  const [showValues, setShowValues] = useState(true)
  const [baseline, setBaseline] = useState<[number, number] | null>(null) // null = server default
  const [year, setYear] = useState<number | null>(null)

  const bq = baseline ? `baseline_start=${baseline[0]}&baseline_end=${baseline[1]}` : ''
  const cal = useQuery({
    queryKey: ['calendar', aoi, bq],
    queryFn: () => getJson<CalendarResponse>(withAoi('/api/calendar', aoi, bq)),
    placeholderData: keepPreviousData,
  })
  const years = cal.data?.years ?? []
  const complete = years.filter((y) => y.complete).map((y) => y.year)
  const effBaseline = cal.data?.climatology.baseline
  // Keep the picked year if this area has it; otherwise default to the latest year.
  const selYear = year != null && years.some((y) => y.year === year) ? year : years.length ? years[years.length - 1].year : null

  // The comparison chart uses exactly the calendar's baseline so both views agree.
  const cq = effBaseline && selYear ? `year=${selYear}&baseline_start=${effBaseline[0]}&baseline_end=${effBaseline[1]}` : ''
  const clim = useQuery({
    queryKey: ['climatology', aoi, cq],
    queryFn: () => getJson<ClimatologyResponse>(withAoi('/api/climatology', aoi, cq)),
    enabled: !!cq,
    placeholderData: keepPreviousData,
  })

  const selRow = years.find((y) => y.year === selYear)
  const anomalies = selRow?.months.filter((m) => m?.anomaly).length ?? 0

  return (
    <div className="space-y-6">
      <FireRiskBrief aoi={aoi} extra={bq} />
      <section aria-labelledby="cal-h" className="rounded-lg border border-slate-700 bg-slate-900/60 p-4">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h2 id="cal-h" className="text-lg font-medium">Burning activity calendar</h2>
            <p className="text-sm text-slate-400">{describeAoi(aoi)} · harmonized monthly fire cell-days (VIIRS-equivalent) · UTC months</p>
          </div>
          <div className="flex flex-wrap items-end gap-3 text-sm">
            <div role="radiogroup" aria-label="Colour by" className="flex overflow-hidden rounded border border-slate-600">
              {(['counts', 'anomaly'] as const).map((v) => (
                <button key={v} role="radio" aria-checked={view === v} onClick={() => setView(v)}
                        className={`px-3 py-1 ${view === v ? 'bg-orange-600 font-medium' : 'hover:bg-slate-800'}`}>
                  {v === 'counts' ? 'Counts' : 'Anomaly (z)'}
                </button>
              ))}
            </div>
            {view === 'counts' ? (
              <label className="flex flex-col"><span className="text-xs text-slate-400">Colour ramp</span>
                <select className="rounded border border-slate-600 bg-slate-800 px-2 py-1" value={ramp} onChange={(e) => setRamp(e.target.value as SeqRamp)}>
                  <option value="viridis">Viridis</option><option value="cividis">Cividis</option>
                </select></label>
            ) : (
              <label className="flex flex-col"><span className="text-xs text-slate-400">Colour ramp</span>
                <select className="rounded border border-slate-600 bg-slate-800 px-2 py-1" value={altDiv ? 'po' : 'bo'} onChange={(e) => setAltDiv(e.target.value === 'po')}>
                  <option value="bo">Blue – orange</option><option value="po">Purple – orange</option>
                </select></label>
            )}
            {complete.length > 0 && (
              <fieldset className="flex items-end gap-1">
                <legend className="text-xs text-slate-400">Baseline years</legend>
                <YearSelect label="Baseline start" value={effBaseline?.[0]} years={complete} onChange={(v) => setBaseline([v, Math.max(v, effBaseline?.[1] ?? v)])} />
                <span className="pb-1">–</span>
                <YearSelect label="Baseline end" value={effBaseline?.[1]} years={complete} onChange={(v) => setBaseline([Math.min(v, effBaseline?.[0] ?? v), v])} />
                {baseline && <button className="pb-1 text-xs text-slate-400 underline" onClick={() => setBaseline(null)}>reset</button>}
              </fieldset>
            )}
            <label className="flex items-center gap-1 pb-1"><input type="checkbox" checked={showValues} onChange={(e) => setShowValues(e.target.checked)} /> Values</label>
            <a className="rounded border border-slate-500 px-3 py-1 hover:bg-slate-800" download href={withAoi('/api/export/calendar.csv', aoi, bq)}>
              Download CSV
            </a>
          </div>
        </div>

        {cal.isError && <p className="mt-3 text-red-300" role="alert">Could not load the calendar: {(cal.error as Error).message}</p>}
        {cal.isLoading && <p className="mt-3 text-slate-400" role="status">Building the calendar…</p>}
        {cal.data && (
          <>
            {cal.data.climatology.status !== 'ok' && (
              <p className="mt-3 rounded border border-amber-600/60 bg-amber-900/30 p-2 text-sm text-amber-200" role="note">
                Baseline has fewer than 5 complete years, so anomalies are not computed.
              </p>
            )}
            {cal.data.calibration.scope !== 'aoi' && (
              <p className="mt-3 rounded border border-amber-600/60 bg-amber-900/30 p-2 text-sm text-amber-200" role="note">
                Too little MODIS/VIIRS overlap in this area: pre-2012 values use the US-wide factor k = {cal.data.calibration.k.toFixed(3)}.
              </p>
            )}
            <div className={`mt-3 transition-opacity ${cal.isFetching ? 'opacity-60' : ''}`}>
              <CalendarHeatmap
                years={years} view={view} ramp={ramp} altDiverging={altDiv} showValues={showValues}
                switchMonth={cal.data.windows.switch_month} aquaMonth={cal.data.windows.aqua_month} zThreshold={cal.data.z_threshold}
                selectedYear={selYear} onSelectYear={setYear}
              />
            </div>
            <p className="mt-2 text-xs text-slate-500">
              Baseline {effBaseline?.[0]}–{effBaseline?.[1]} ({cal.data.climatology.baseline_years.length} complete years).
              k = {cal.data.calibration.k.toFixed(3)} (R² {cal.data.calibration.r2?.toFixed(3) ?? '—'}, {cal.data.calibration.scope === 'aoi' ? 'this area' : 'US-wide fallback'}).
              {cal.data.calibration.terra_applies_before && ` Terra-only correction c = ${cal.data.calibration.terra_c.toFixed(3)} before ${cal.data.calibration.terra_applies_before} (${cal.data.calibration.terra_scope === 'aoi' ? 'this area' : 'US-wide fallback'}).`}
              Data as of MODIS {cal.data.windows.data_as_of.MODIS}, VIIRS {cal.data.windows.data_as_of.VIIRS}. {cal.data.caveat}
            </p>
          </>
        )}
      </section>

      <section aria-labelledby="yr-h" className="rounded-lg border border-slate-700 bg-slate-900/60 p-4">
        <div className="flex flex-wrap items-end justify-between gap-2">
          <div>
            <h2 id="yr-h" className="text-lg font-medium">{selYear ?? '—'} vs the baseline</h2>
            <p className="text-sm text-slate-400">Pick a year in the calendar. Ringed points are anomalous months.</p>
          </div>
          {selRow && (
            <p className="text-sm tabular-nums text-slate-300">
              Total {n0(selRow.total)}{selRow.complete ? '' : ' (partial year)'} · {anomalies} anomalous month{anomalies === 1 ? '' : 's'}
            </p>
          )}
        </div>
        {clim.isError && <p className="mt-3 text-red-300" role="alert">Could not load the comparison: {(clim.error as Error).message}</p>}
        {clim.data && selYear && (
          <div className={clim.isFetching ? 'opacity-60' : ''}>
            <YearChart year={selYear} months={clim.data.climatology.months} rows={clim.data.selected_year_rows} />
          </div>
        )}
      </section>

      <SummaryPanel aoi={aoi} extra={bq} />
    </div>
  )
}

function YearSelect({ label, value, years, onChange }: { label: string; value?: number; years: number[]; onChange: (v: number) => void }) {
  return (
    <select aria-label={label} className="rounded border border-slate-600 bg-slate-800 px-1 py-1" value={value ?? ''} onChange={(e) => onChange(Number(e.target.value))}>
      {years.map((y) => <option key={y} value={y}>{y}</option>)}
    </select>
  )
}
