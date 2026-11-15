import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useRef, useState } from 'react'
import { describeAoi, withAoi, type Aoi } from '../lib/aoi'
import { getJson } from '../lib/api'
import type { BriefResponse } from '../types/api'
import AiExplanation from './AiExplanation'

const MONTH_NAMES: Record<string, string> = {
  Jan: 'January', Feb: 'February', Mar: 'March', Apr: 'April', May: 'May', Jun: 'June',
  Jul: 'July', Aug: 'August', Sep: 'September', Oct: 'October', Nov: 'November', Dec: 'December',
}
// Level styling. The level is always written out too, so colour is never the only cue.
const LEVEL = {
  Severe: { border: 'border-l-red-500', chip: 'bg-red-500/20 text-red-200', icon: '▲▲' },
  Moderate: { border: 'border-l-orange-400', chip: 'bg-orange-500/20 text-orange-200', icon: '▲' },
  Normal: { border: 'border-l-emerald-400', chip: 'bg-emerald-500/20 text-emerald-200', icon: '●' },
  'Below normal': { border: 'border-l-sky-400', chip: 'bg-sky-500/20 text-sky-200', icon: '▼' },
  Unknown: { border: 'border-l-slate-500', chip: 'bg-slate-500/20 text-slate-200', icon: '?' },
} as const
const CONF = { High: 'text-emerald-300', Medium: 'text-amber-300', Low: 'text-red-300' } as const
const n0 = (v: number) => Math.round(v).toLocaleString()

function peakText(months: string[]): string {
  if (!months.length) return 'No distinct peak'
  // Group consecutive months (wrapping Dec→Jan) into ranges: "Jul–Oct", "Feb–Apr, Oct–Nov"
  const order = Object.keys(MONTH_NAMES)
  const idx = months.map((m) => order.indexOf(m)).sort((a, b) => a - b)
  const runs: number[][] = []
  for (const i of idx) {
    const last = runs[runs.length - 1]
    if (last && i === last[last.length - 1] + 1) last.push(i)
    else runs.push([i])
  }
  if (runs.length > 1 && runs[0][0] === 0 && runs[runs.length - 1].at(-1) === 11) runs[0] = [...runs.pop()!, ...runs[0]]
  return runs.map((r) => (r.length === 1 ? order[r[0]] : `${order[r[0]]}–${order[r[r.length - 1]]}`)).join(', ')
}

export default function FireRiskBrief({ aoi, extra = '' }: { aoi: Aoi; extra?: string }) {
  const [asOf, setAsOf] = useState('') // '' = latest data; otherwise YYYY-MM
  const query = [extra, asOf && `end=${asOf}`].filter(Boolean).join('&')
  const q = useQuery({
    queryKey: ['brief', aoi, query],
    queryFn: () => getJson<BriefResponse>(withAoi('/api/brief', aoi, query)),
    placeholderData: keepPreviousData,
  })
  const ref = useRef<HTMLDivElement>(null)
  const [busy, setBusy] = useState<'png' | 'pdf' | null>(null)
  const [err, setErr] = useState('')
  const b = q.data

  const fileBase = `firewatch360-brief-${(b?.reference.month ?? 'area').replace(/[^0-9-]/g, '')}`

  async function snapshot(kind: 'png' | 'jpeg'): Promise<string> {
    const { toPng, toJpeg } = await import('html-to-image')
    const opts = {
      pixelRatio: 2,
      backgroundColor: '#0b0f14',
      filter: (n: HTMLElement) => !(n instanceof HTMLElement && n.dataset.noexport !== undefined),
    }
    // JPEG keeps the PDF small (an embedded PNG is several MB).
    return kind === 'png' ? toPng(ref.current!, opts) : toJpeg(ref.current!, { ...opts, quality: 0.92 })
  }
  async function download(kind: 'png' | 'pdf') {
    if (!ref.current) return
    setBusy(kind)
    setErr('')
    try {
      const png = await snapshot(kind === 'png' ? 'png' : 'jpeg')
      if (kind === 'png') {
        const a = document.createElement('a')
        a.href = png
        a.download = `${fileBase}.png`
        a.click()
      } else {
        const { jsPDF } = await import('jspdf')
        const img = new Image()
        img.src = png
        await img.decode()
        const pdf = new jsPDF({ orientation: 'landscape', unit: 'pt', format: 'a4' })
        const pw = pdf.internal.pageSize.getWidth(), ph = pdf.internal.pageSize.getHeight(), m = 28
        const scale = Math.min((pw - 2 * m) / img.width, (ph - 2 * m) / img.height)
        pdf.setFillColor(11, 15, 20)
        pdf.rect(0, 0, pw, ph, 'F')
        pdf.addImage(png, 'JPEG', m, m, img.width * scale, img.height * scale)
        pdf.setProperties({ title: `FIREWATCH 360 Fire Risk Brief — ${b?.reference.label ?? ''}` })
        pdf.save(`${fileBase}.pdf`)
      }
    } catch (e) {
      setErr(`Could not create the ${kind.toUpperCase()}: ${(e as Error).message}`)
    } finally {
      setBusy(null)
    }
  }

  if (q.isLoading) return <div className="rounded-lg bg-slate-900/70 p-4 text-slate-400" role="status">Preparing the Fire Risk Brief…</div>
  if (q.isError) return <div className="rounded-lg border border-red-800 bg-red-950/40 p-4 text-red-200" role="alert">Fire Risk Brief unavailable: {(q.error as Error).message}</div>
  if (!b) return null
  const st = LEVEL[b.level]

  return (
    <section aria-labelledby="brief-h" className={`rounded-lg border border-l-8 border-slate-700 ${st.border} bg-slate-900/80 ${q.isFetching ? 'opacity-70' : ''}`}>
      <div ref={ref} className="p-4">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div className="min-w-0">
            <p className="text-xs uppercase tracking-wider text-slate-400">
              Fire Risk Brief · {describeAoi(aoi)} · {b.reference.label}
              {b.reference.provisional && <span className="ml-2 rounded bg-amber-500/20 px-1 text-amber-200">provisional</span>}
            </p>
            <h2 id="brief-h" className="mt-1 text-xl font-semibold leading-snug sm:text-2xl">{b.headline}</h2>
          </div>
          <span className={`shrink-0 rounded px-2 py-1 text-sm font-semibold ${st.chip}`}>
            <span aria-hidden="true">{st.icon} </span>{b.level}
          </span>
        </div>

        <dl className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <Fact label="Current activity" value={b.current_activity.text ?? 'No baseline to compare'}
                note={b.current_activity.median != null ? `${n0(b.current_activity.value)} vs median ${n0(b.current_activity.median)} fire cell-days` : undefined} />
          <Fact label="Historical rank" value={b.rank?.text ?? '—'} note={b.rank ? `${b.rank.rank} of ${b.rank.of} years` : undefined} />
          <Fact label="Anomaly level" value={b.level}
                note={b.current_activity.z != null ? `robust z = ${b.current_activity.z.toFixed(1)}` : 'z undefined'} />
          <Fact label="Sensor agreement" value={b.agreement.label} note={sensorNote(b)} />
          <Fact label="Confidence" value={b.confidence.level} valueClass={CONF[b.confidence.level]}
                note={b.confidence.reasons.length ? b.confidence.reasons.join('; ') : 'Final data, both sensors agree, ample history'} />
          <Fact label="Peak fire season" value={peakText(b.peak_season)} note="Months with above-average baseline medians" />
          <Fact label="Long-term activity" value={b.trend.direction} note={b.trend.text} />
          <Fact label="Baseline" value={b.baseline.years ? `${b.baseline.years[0]}–${b.baseline.years[1]}` : '—'}
                note={`${b.baseline.n_years} years · k = ${b.factors.k.toFixed(2)}, c = ${b.factors.c.toFixed(2)}`} />
        </dl>

        <div className="mt-3" data-noexport>
          <AiExplanation url={withAoi('/api/explain/brief', aoi, query)} fallback={b.headline} />
        </div>

        <p className="mt-3 text-xs text-slate-500">
          Generated {b.generated_utc}. Final data through MODIS {b.data_as_of.final.MODIS}, VIIRS {b.data_as_of.final.VIIRS}; near-real-time through{' '}
          {Object.entries(b.data_as_of.nrt).map(([s, d]) => `${s} ${d}`).join(', ')}. Source: NASA FIRMS (MODIS C6.1, VIIRS S-NPP 375 m), harmonized by FIREWATCH 360. {b.caveat}
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-2 border-t border-slate-800 px-4 py-2 text-sm" data-noexport>
        <button onClick={() => download('pdf')} disabled={!!busy} className="rounded bg-orange-600 px-3 py-1 font-medium hover:bg-orange-500 disabled:opacity-50">
          {busy === 'pdf' ? 'Creating PDF…' : 'Download PDF briefing'}
        </button>
        <button onClick={() => download('png')} disabled={!!busy} className="rounded border border-slate-500 px-3 py-1 hover:bg-slate-800 disabled:opacity-50">
          {busy === 'png' ? 'Creating PNG…' : 'Download PNG'}
        </button>
        <label className="ml-2 flex items-center gap-1 text-xs text-slate-400">
          Brief for
          <select className="rounded border border-slate-600 bg-slate-800 px-1 py-0.5 text-slate-200" value={asOf ? 'month' : 'latest'}
                  onChange={(e) => setAsOf(e.target.value === 'latest' ? '' : b.reference.month)}>
            <option value="latest">latest data</option>
            <option value="month">a past month…</option>
          </select>
          {asOf && (
            <input type="month" aria-label="Month for the brief" min="2001-01" max={b.data_as_of.final.VIIRS.slice(0, 7)}
                   className="rounded border border-slate-600 bg-slate-800 px-1 py-0.5 text-slate-200"
                   value={asOf} onChange={(e) => e.target.value && setAsOf(e.target.value)} />
          )}
        </label>
        <details className="ml-auto text-xs text-slate-400">
          <summary className="cursor-pointer">How is this decided?</summary>
          <ul className="mt-1 max-w-xl list-disc pl-4">
            {Object.entries(b.rules).map(([k, v]) => <li key={k}><b className="text-slate-300">{k.replace('_', ' ')}:</b> {v}</li>)}
          </ul>
        </details>
        {err && <p className="w-full text-red-300" role="alert">{err}</p>}
      </div>
    </section>
  )
}

function sensorNote(b: BriefResponse): string {
  return (['MODIS', 'VIIRS'] as const)
    .map((s) => {
      const x = b.sensors[s]
      if (!x.available) return `${s}: no data for this period`
      if (x.z == null) return `${s}: too little history`
      return `${s} z ${x.z.toFixed(1)}`
    })
    .join(' · ')
}

function Fact({ label, value, note, valueClass = '' }: { label: string; value: string; note?: string; valueClass?: string }) {
  return (
    <div className="min-w-0 rounded border border-slate-700/80 bg-slate-950/40 p-2">
      <dt className="text-xs text-slate-400">{label}</dt>
      <dd className={`text-base font-semibold ${valueClass}`}>{value}</dd>
      {note && <dd className="text-xs text-slate-400">{note}</dd>}
    </div>
  )
}
