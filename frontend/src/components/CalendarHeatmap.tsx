import { useMemo, useState } from 'react'
import { countScale, fmtCompact, textOn, zScale, Z_LIMIT, type SeqRamp } from '../lib/color'
import type { CalendarYear, MonthRow } from '../types/api'

export type CalView = 'counts' | 'anomaly'
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
const n0 = (v: number) => Math.round(v).toLocaleString()
const SOURCE_LABEL: Record<MonthRow['source'], string> = {
  VIIRS: 'VIIRS observed',
  MODIS_scaled: 'MODIS Terra + Aqua × k',
  MODIS_terra_scaled: 'MODIS Terra only × c × k',
  VIIRS_provisional: 'VIIRS near-real-time, provisional',
}

interface Props {
  years: CalendarYear[]
  view: CalView
  ramp: SeqRamp
  altDiverging: boolean
  showValues: boolean
  switchMonth: string
  aquaMonth: string | null
  zThreshold: number
  selectedYear: number | null
  onSelectYear: (y: number) => void
}

interface Tip { row: MonthRow; x: number; y: number }

export default function CalendarHeatmap(p: Props) {
  const [tip, setTip] = useState<Tip | null>(null)
  const [min, max] = useMemo(() => {
    const v = p.years.flatMap((y) => y.months.map((m) => m?.harmonized ?? 0)).filter((x) => x > 0)
    return v.length ? [Math.min(...v), Math.max(...v)] : [1, 10]
  }, [p.years])
  const cs = countScale(min, max, p.ramp)
  const zs = zScale(p.altDiverging)
  const switchYear = Number(p.switchMonth.slice(0, 4))
  const aquaYear = p.aquaMonth ? Number(p.aquaMonth.slice(0, 4)) : -1

  const cellStyle = (m: MonthRow | null) => {
    if (!m) return {}
    if (p.view === 'counts') {
      const rgb = cs.rgb(m.harmonized)
      return { background: `rgb(${rgb.join(' ')})`, color: textOn(rgb) }
    }
    if (m.z == null) return { background: '#334155', color: '#cbd5e1' }
    const rgb = zs.rgb(m.z)
    return { background: `rgb(${rgb.join(' ')})`, color: textOn(rgb) }
  }
  const cellText = (m: MonthRow) =>
    p.view === 'counts' ? fmtCompact(m.harmonized) : m.z == null ? '–' : m.z.toFixed(1)

  const show = (row: MonthRow, el: HTMLElement) => {
    const r = el.getBoundingClientRect()
    setTip({ row, x: r.left + r.width / 2, y: r.top })
  }

  return (
    <div>
      <div className="overflow-x-auto pb-2" onMouseLeave={() => setTip(null)}>
        <table className="w-full min-w-[40rem] border-separate border-spacing-[2px] text-xs tabular-nums" aria-label="Burning activity calendar: harmonized monthly fire cell-days by year and month">
          <thead>
            <tr>
              <th scope="col" className="w-16 text-left font-normal text-slate-400">Year</th>
              <th scope="col" className="w-16 text-left font-normal text-slate-400">Source</th>
              {MONTHS.map((m) => <th key={m} scope="col" className="font-normal text-slate-400">{m}</th>)}
              <th scope="col" className="w-16 text-right font-normal text-slate-400">Total</th>
            </tr>
          </thead>
          <tbody>
            {p.years.map((y) => {
              const sel = y.year === p.selectedYear
              const hasProv = y.months.some((m) => m?.provisional)
              const src = hasProv ? 'VIIRS + NRT' : y.year < aquaYear ? 'Terra × c·k' : y.year === aquaYear ? 'mixed' : y.year < switchYear ? 'MODIS × k' : y.year === switchYear ? 'mixed' : 'VIIRS'
              return (
                <tr key={y.year} className={y.year === switchYear ? '[&>*]:border-t-2 [&>*]:border-t-slate-300' : y.year === aquaYear ? '[&>*]:border-t [&>*]:border-dashed [&>*]:border-t-slate-400' : ''}>
                  <th scope="row" className="text-left font-normal">
                    <button
                      onClick={() => p.onSelectYear(y.year)}
                      aria-pressed={sel}
                      aria-label={`Select ${y.year} for the comparison chart`}
                      className={`w-full rounded px-1 text-left ${sel ? 'bg-orange-600 font-medium text-white' : 'hover:bg-slate-800'}`}
                    >
                      {y.year}
                    </button>
                  </th>
                  <td className={`whitespace-nowrap px-1 ${y.year < switchYear ? 'italic text-amber-200/80' : 'text-sky-200/80'}`}>{src}</td>
                  {y.months.map((m, i) => (
                    <td
                      key={i}
                      style={cellStyle(m)}
                      className={`h-6 rounded-sm text-center ${m ? 'cursor-default' : 'bg-[repeating-linear-gradient(45deg,#1e293b_0_3px,transparent_3px_6px)]'} ${sel ? 'outline outline-1 outline-orange-400' : ''} ${m?.anomaly && p.view === 'counts' ? 'shadow-[inset_0_0_0_2px_#f8fafc]' : ''} ${m?.provisional ? 'outline-2 outline-dashed -outline-offset-2 outline-slate-200' : ''}`}
                      aria-label={m ? describe(m) : `${MONTHS[i]} ${y.year}: no data`}
                      tabIndex={m ? 0 : -1}
                      onMouseEnter={(e) => m && show(m, e.currentTarget)}
                      onFocus={(e) => m && show(m, e.currentTarget)}
                      onBlur={() => setTip(null)}
                    >
                      {m && p.showValues ? cellText(m) : ''}
                    </td>
                  ))}
                  <td className={`pr-1 text-right ${y.complete ? 'text-slate-300' : 'text-slate-500'}`} title={y.complete ? undefined : 'Partial year'}>
                    {fmtCompact(y.total)}{y.complete ? '' : '*'}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      {tip && (
        <div role="tooltip" className="pointer-events-none fixed z-50 -translate-x-1/2 -translate-y-full rounded border border-slate-600 bg-slate-950/95 px-2 py-1 text-xs shadow-lg"
             style={{ left: tip.x, top: tip.y - 6 }}>
          <div className="font-medium">{monthLabel(tip.row.month)}{tip.row.provisional && <span className="ml-1 rounded bg-amber-500/20 px-1 text-amber-200">provisional</span>}</div>
          <div>Harmonized: <b>{n0(tip.row.harmonized)}</b> cell-days <span className="text-slate-400">({SOURCE_LABEL[tip.row.source]})</span></div>
          <div className="text-slate-300">Raw MODIS {n0(tip.row.modis)}{tip.row.source === 'MODIS_terra_scaled' ? ` (Terra only ${n0(tip.row.modis_terra)})` : ''} · raw VIIRS {tip.row.viirs == null ? '—' : n0(tip.row.viirs)}</div>
          {tip.row.clim_median != null && (
            <div className="text-slate-300">
              Median {n0(tip.row.clim_median)}
              {tip.row.pct_vs_median != null && ` · ${tip.row.pct_vs_median > 0 ? '+' : ''}${tip.row.pct_vs_median.toFixed(0)}%`}
              {tip.row.z != null && ` · z ${tip.row.z.toFixed(2)}`}
              {tip.row.anomaly && <b className={tip.row.anomaly === 'high' ? ' text-orange-300' : ' text-sky-300'}> · anomaly ({tip.row.anomaly})</b>}
            </div>
          )}
        </div>
      )}

      <Legend {...p} min={min} max={max} />
    </div>
  )
}

function Legend({ view, ramp, altDiverging, min, max, zThreshold, switchMonth, aquaMonth }: Props & { min: number; max: number }) {
  const cs = countScale(min, max, ramp)
  const zs = zScale(altDiverging)
  return (
    <div className="mt-2 flex flex-wrap items-start gap-x-8 gap-y-3 text-xs text-slate-300" aria-label="Legend">
      <div className="w-72 max-w-full">
        {view === 'counts' ? (
          <>
            <div className="mb-1">Fire cell-days per month (log scale; zero = darkest)</div>
            <div className="h-3 rounded-sm" style={{ background: cs.gradient() }} />
            <div className="relative h-4">
              {cs.ticks().map((t) => (
                <span key={t} className="absolute -translate-x-1/2 tabular-nums" style={{ left: `${cs.position(t) * 100}%` }}>{fmtCompact(t)}</span>
              ))}
            </div>
          </>
        ) : (
          <>
            <div className="mb-1">Robust z-score vs baseline median (clipped at ±{Z_LIMIT})</div>
            <div className="h-3 rounded-sm" style={{ background: zs.gradient() }} />
            <div className="relative h-4">
              {[-4, -2, 0, 2, 4].map((t) => (
                <span key={t} className="absolute -translate-x-1/2 tabular-nums" style={{ left: `${zs.position(t) * 100}%` }}>{t > 0 ? `+${t}` : t}</span>
              ))}
            </div>
            <div className="flex justify-between text-slate-400"><span>below normal</span><span>above normal</span></div>
          </>
        )}
      </div>
      <ul className="space-y-1">
        {view === 'counts' && <li><span className="mr-1 inline-block h-3 w-4 rounded-sm align-middle shadow-[inset_0_0_0_2px_#f8fafc]" /> anomalous month (|z| ≥ {zThreshold})</li>}
        {view === 'anomaly' && <li><span className="mr-1 inline-block h-3 w-4 rounded-sm bg-slate-700 align-middle" /> z undefined (no spread or short history)</li>}
        <li><span className="mr-1 inline-block h-3 w-4 rounded-sm bg-[repeating-linear-gradient(45deg,#475569_0_3px,transparent_3px_6px)] align-middle" /> no data</li>
        <li><span className="mr-1 inline-block w-4 border-t-2 border-slate-300 align-middle" /> VIIRS observed from {switchMonth}; MODIS (Terra + Aqua) × k before</li>
        {aquaMonth && <li><span className="mr-1 inline-block w-4 border-t border-dashed border-slate-400 align-middle" /> before {aquaMonth} only Terra flew: Terra × c × k</li>}
        <li><span className="mr-1 inline-block h-3 w-4 rounded-sm align-middle outline-2 outline-dashed -outline-offset-2 outline-slate-200" /> near-real-time, provisional (static-source mask)</li>
        <li>* partial year</li>
      </ul>
    </div>
  )
}

function monthLabel(m: string) {
  return `${MONTHS[Number(m.slice(5, 7)) - 1]} ${m.slice(0, 4)}`
}

function describe(m: MonthRow) {
  let s = `${monthLabel(m.month)}: ${n0(m.harmonized)} fire cell-days (${SOURCE_LABEL[m.source]})`
  if (m.clim_median != null) s += `, median ${n0(m.clim_median)}`
  if (m.z != null) s += `, z ${m.z.toFixed(1)}`
  if (m.anomaly) s += `, ${m.anomaly} anomaly`
  return s
}
