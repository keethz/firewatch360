import { useEffect, useRef, useState } from 'react'
import { describeAoi, withAoi, type Aoi } from '../lib/aoi'
import { todayUtc } from '../lib/api'
import AllPanelsPdf from './AllPanelsPdf'

export interface InvestigateTarget { aoi: Aoi; month: string; label: string; nonce: number }

interface EvidenceItem { id: string; tool: string; args: Record<string, unknown>; summary: string; data: unknown; ok: boolean; seconds: number }
interface Finding {
  statement: string; kind: string; evidence: string[]; invalid_citations: string[]
  numbers_checked: number; numbers_unmatched: string[]; verified: boolean
}
interface Report {
  headline: string; assessment: string; confidence: string; findings: Finding[]; gaps: string[]; next_steps: string[]
  summary: { text: string; numbers_checked: number; numbers_unmatched: string[]; verified: boolean }
}
interface Done {
  mode: 'agent' | 'rule-based'; model: string | null; report: Report; evidence: EvidenceItem[]; seconds: number
  verification: { findings: number; verified: number; numbers_checked: number }; area: string; month: string
}
type Step =
  | { kind: 'call'; tool: string; args: Record<string, unknown>; why: string; evidence?: EvidenceItem }
  | { kind: 'note'; text: string }

const TOOL: Record<string, { label: string; glyph: string }> = {
  get_fire_activity: { label: 'Fire activity vs history', glyph: '📈' },
  compare_sensors: { label: 'Compare MODIS and VIIRS', glyph: '🛰️' },
  find_anomalous_cells: { label: 'Find anomalous places', glyph: '🗺️' },
  get_reported_incidents: { label: 'NIFC reported incidents', glyph: '📋' },
  get_drought_and_fire_weather: { label: 'Drought and fire weather', glyph: '🌡️' },
  assess_community_assets: { label: 'Community assets exposed', glyph: '🏘️' },
  get_burned_area: { label: 'Burned area', glyph: '🔥' },
  get_smoke: { label: 'Smoke', glyph: '🌫️' },
  get_outlook: { label: 'Outlook', glyph: '🔭' },
}
const ASSESS_STYLE: Record<string, string> = {
  Exceptional: 'bg-red-600/30 text-red-200 border-red-500/60', Unusual: 'bg-orange-600/30 text-orange-200 border-orange-500/60',
  Normal: 'bg-slate-600/30 text-slate-200 border-slate-500/60', 'Below normal': 'bg-sky-600/30 text-sky-200 border-sky-500/60',
  Inconclusive: 'bg-slate-700/40 text-slate-300 border-slate-500/60',
}
const KIND_LABEL: Record<string, string> = { observation: 'Observed', driver: 'Likely driver', impact: 'Exposure', context: 'Context' }
const lastMonth = () => { const d = new Date(); d.setUTCDate(1); d.setUTCMonth(d.getUTCMonth() - 1); return d.toISOString().slice(0, 7) }

function argText(args: Record<string, unknown>): string {
  return Object.entries(args).map(([k, v]) => `${k}: ${Array.isArray(v) ? `[${v.map((x) => (typeof x === 'number' ? x.toFixed(2) : x)).join(', ')}]` : v}`).join(' · ')
}

/** Autonomous investigation: the agent calls the app's own analysis tools and returns an evidence-backed report. */
export default function InvestigateTab({ aoi, target }: { aoi: Aoi; target: InvestigateTarget | null }) {
  const [area, setArea] = useState<{ aoi: Aoi; label: string }>({ aoi, label: describeAoi(aoi) })
  const [month, setMonth] = useState('')
  const [useAi, setUseAi] = useState(true)
  const [running, setRunning] = useState(false)
  const [status, setStatus] = useState('')
  const [steps, setSteps] = useState<Step[]>([])
  const [done, setDone] = useState<Done | null>(null)
  const [error, setError] = useState('')
  const [focus, setFocus] = useState<string | null>(null)
  const es = useRef<EventSource | null>(null)
  const pdfRef = useRef<HTMLDivElement>(null)

  // Follow the app-wide area unless an investigation was launched for a specific place.
  useEffect(() => { if (!target) setArea({ aoi, label: describeAoi(aoi) }) }, [aoi, target])
  useEffect(() => () => es.current?.close(), [])

  function start(a = area, m = month) {
    es.current?.close()
    setSteps([]); setDone(null); setError(''); setFocus(null); setRunning(true); setStatus('Starting…')
    const qs = [m && `month=${m}`, `label=${encodeURIComponent(a.label)}`, !useAi && 'mode=rules'].filter(Boolean).join('&')
    const src = new EventSource(withAoi('/api/investigate', a.aoi, qs))
    es.current = src
    src.onmessage = (msg) => {
      const ev = JSON.parse(msg.data)
      if (ev.type === 'status') setStatus(ev.text)
      else if (ev.type === 'note') setSteps((s) => [...s, { kind: 'note', text: ev.text }])
      else if (ev.type === 'tool_call') {
        setStatus(`Running ${TOOL[ev.tool]?.label ?? ev.tool}…`)
        setSteps((s) => [...s, { kind: 'call', tool: ev.tool, args: ev.args, why: ev.why }])
      } else if (ev.type === 'evidence') {
        setSteps((s) => {
          const out = [...s]
          for (let i = out.length - 1; i >= 0; i--) {
            const st = out[i]
            if (st.kind === 'call' && !st.evidence && st.tool === ev.evidence.tool) { out[i] = { ...st, evidence: ev.evidence }; break }
          }
          return out
        })
        setStatus('Agent is deciding what to check next…')
      } else if (ev.type === 'done') {
        setDone(ev); setRunning(false); setStatus(''); src.close()
      } else if (ev.type === 'error') {
        setError(ev.text); setRunning(false); src.close()
      }
    }
    src.onerror = () => {
      if (src.readyState === EventSource.CLOSED || es.current === src) {
        src.close()
        setRunning((r) => { if (r) setError('The connection to the server was lost.'); return false })
      }
    }
  }

  // Launched from an anomaly cell: set the place and month, then start.
  useEffect(() => {
    if (!target) return
    const a = { aoi: target.aoi, label: target.label }
    setArea(a); setMonth(target.month)
    start(a, target.month)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target?.nonce])

  const evidenceById = Object.fromEntries((done?.evidence ?? []).map((e) => [e.id, e]))
  const download = () => {
    const blob = new Blob([JSON.stringify(done, null, 2)], { type: 'application/json' })
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = `firewatch360-investigation-${done!.month}.json`
    a.click()
    URL.revokeObjectURL(a.href)
  }

  return (
    <div ref={pdfRef} className="space-y-4">
      <section aria-labelledby="inv-h" className="rounded-lg bg-slate-900/70 p-4">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h2 id="inv-h" className="text-lg font-medium">Fire investigation agent</h2>
            <p className="text-sm text-slate-400">
              An AI agent investigates <b className="text-slate-200">{area.label}</b> by calling FIREWATCH 360's own analysis tools,
              then writes a report in which every finding cites its evidence and every number is checked against it.
            </p>
          </div>
          <div data-noexport className="flex flex-wrap items-end gap-3 text-sm">
            <label className="flex flex-col"><span className="text-xs text-slate-400">Month (blank = latest)</span>
              <input type="month" className="rounded border border-slate-600 bg-slate-800 px-2 py-1" value={month} min="2000-11" max={todayUtc().slice(0, 7)}
                     onChange={(e) => setMonth(e.target.value)} placeholder={lastMonth()} /></label>
            <label className="flex items-center gap-1 pb-1" title="Off: a fixed, rule-based investigation (no AI)">
              <input type="checkbox" checked={useAi} onChange={(e) => setUseAi(e.target.checked)} /> AI agent
            </label>
            {target && <button className="pb-1 text-xs text-slate-400 underline" onClick={() => setArea({ aoi, label: describeAoi(aoi) })}>use the app's area instead</button>}
            <button onClick={() => (running ? (es.current?.close(), setRunning(false), setStatus('')) : start())}
                    className={`rounded px-4 py-1.5 font-medium ${running ? 'border border-slate-500 hover:bg-slate-800' : 'bg-orange-600 hover:bg-orange-500'}`}>
              {running ? 'Stop' : done ? 'Investigate again' : 'Investigate'}
            </button>
          </div>
        </div>
        {!steps.length && !running && !done && (
          <p className="mt-3 text-sm text-slate-400">
            Pick an area on Explore (or click “Investigate this cell” on the Anomaly map), choose a month such as 2020-09, and press Investigate.
            The agent decides which tools to call: fire activity vs history, MODIS/VIIRS agreement, anomalous places, NIFC incidents,
            drought and fire weather, exposed community assets, burned area and smoke.
          </p>
        )}
        {error && <p className="mt-3 text-sm text-red-300" role="alert">{error}</p>}
      </section>

      {(steps.length > 0 || running) && (
        <section aria-labelledby="trace-h" className="rounded-lg bg-slate-900/70 p-4">
          <h2 id="trace-h" className="text-lg font-medium">Investigation trace</h2>
          <ol className="mt-3 space-y-2" aria-live="polite">
            {steps.map((s, i) => s.kind === 'note' ? (
              <li key={i} className="rounded border border-amber-600/40 bg-amber-900/20 px-3 py-1.5 text-xs text-amber-200">{s.text}</li>
            ) : (
              <li key={i} id={s.evidence ? `ev-${s.evidence.id}` : undefined}
                  className={`rounded border px-3 py-2 transition-colors ${focus && s.evidence?.id === focus ? 'border-yellow-400 bg-yellow-500/10' : 'border-slate-700'}`}>
                <div className="flex flex-wrap items-baseline gap-x-2">
                  <span aria-hidden="true">{TOOL[s.tool]?.glyph ?? '🔧'}</span>
                  <span className="font-medium">{TOOL[s.tool]?.label ?? s.tool}</span>
                  {s.evidence ? <span className="rounded bg-slate-700 px-1.5 text-xs tabular-nums">{s.evidence.id}</span>
                    : <span className="animate-pulse text-xs text-slate-400">running…</span>}
                  {s.evidence && <span className="text-xs text-slate-500">{s.evidence.seconds.toFixed(1)} s</span>}
                  {Object.keys(s.args).length > 0 && <span className="text-xs text-slate-500">{argText(s.args)}</span>}
                </div>
                {s.why && <p className="mt-0.5 text-xs italic text-slate-400">“{s.why}”</p>}
                {s.evidence && (
                  <p className={`mt-1 text-sm ${s.evidence.ok ? 'text-slate-200' : 'text-red-300'}`}>{s.evidence.summary}</p>
                )}
                {s.evidence && (
                  <details data-noexport className="mt-1 text-xs text-slate-400">
                    <summary className="cursor-pointer">raw evidence</summary>
                    <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap break-all rounded bg-slate-950 p-2">{JSON.stringify(s.evidence.data, null, 1)}</pre>
                  </details>
                )}
              </li>
            ))}
            {running && <li className="animate-pulse px-1 text-sm text-slate-400">{status}</li>}
          </ol>
        </section>
      )}

      {done && (
        <section aria-labelledby="rep-h2" className="rounded-lg bg-slate-900/70 p-4">
          <div className="flex flex-wrap items-start justify-between gap-2">
            <div>
              <h2 id="rep-h2" className="text-lg font-medium">Investigation report · {done.area} · {done.month}</h2>
              <p className="mt-1 text-base">
                <span className={`mr-2 rounded border px-2 py-0.5 text-sm font-semibold ${ASSESS_STYLE[done.report.assessment] ?? ASSESS_STYLE.Inconclusive}`}>{done.report.assessment}</span>
                {done.report.headline}
              </p>
              <p className="mt-1 text-xs text-slate-400">
                Confidence {done.report.confidence} · {done.verification.verified} of {done.verification.findings} findings verified
                ({done.verification.numbers_checked} numbers checked against cited evidence) · {done.evidence.length} tool calls in {done.seconds} s ·
                {' '}{done.mode === 'agent' ? `planned by AI (${done.model})` : 'rule-based investigator (no AI)'}
              </p>
            </div>
            <div data-noexport className="flex flex-wrap gap-2">
              <button onClick={download} className="rounded border border-slate-600 px-2 py-1 text-sm hover:bg-slate-800">⤓ Evidence JSON</button>
              <AllPanelsPdf container={pdfRef} fileName={`firewatch360-investigation-${done.month}`} area={done.area} />
            </div>
          </div>

          <ul className="mt-3 space-y-2">
            {done.report.findings.map((f, i) => (
              <li key={i} className="rounded border border-slate-700 px-3 py-2">
                <div className="flex flex-wrap items-center gap-2 text-xs">
                  <span className="rounded bg-slate-700 px-1.5">{KIND_LABEL[f.kind] ?? f.kind}</span>
                  {f.verified
                    ? <span className="text-emerald-300" title="Every number in this finding appears in the cited evidence">✓ verified</span>
                    : <span className="text-amber-300" title="Some numbers were not found in the cited evidence, or nothing was cited">
                        ⚠ unverified{f.numbers_unmatched.length > 0 && `: ${f.numbers_unmatched.join(', ')} not in evidence`}{!f.evidence.length && ': no valid citation'}
                      </span>}
                  {f.evidence.map((id) => (
                    <button key={id} className="rounded border border-slate-500 px-1.5 tabular-nums hover:bg-slate-700"
                            title={evidenceById[id]?.summary}
                            onClick={() => { setFocus(id); document.getElementById(`ev-${id}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' }) }}>
                      {id}
                    </button>
                  ))}
                </div>
                <p className="mt-1 text-sm">{f.statement}</p>
              </li>
            ))}
          </ul>
          <div className="mt-3 grid gap-3 md:grid-cols-2">
            <div>
              <h3 className="text-sm font-medium">Gaps and caveats</h3>
              <ul className="mt-1 list-disc space-y-0.5 pl-5 text-sm text-slate-300">{done.report.gaps.map((g, i) => <li key={i}>{g}</li>)}</ul>
            </div>
            <div>
              <h3 className="text-sm font-medium">Suggested next steps</h3>
              <ul className="mt-1 list-disc space-y-0.5 pl-5 text-sm text-slate-300">{done.report.next_steps.map((g, i) => <li key={i}>{g}</li>)}</ul>
            </div>
          </div>
          {done.report.summary?.text && (
            <div className="mt-4 rounded border-l-4 border-orange-500 bg-slate-950/60 px-4 py-3">
              <div className="flex flex-wrap items-baseline gap-2">
                <h3 className="text-sm font-medium">Summary</h3>
                {done.report.summary.verified
                  ? <span className="text-xs text-emerald-300" title="Every number in the summary appears in the evidence">✓ {done.report.summary.numbers_checked} numbers match the evidence</span>
                  : <span className="text-xs text-amber-300">⚠ not in evidence: {done.report.summary.numbers_unmatched.join(', ')}</span>}
              </div>
              <p className="mt-1 leading-relaxed text-slate-100">{done.report.summary.text}</p>
            </div>
          )}
          <p className="mt-3 text-xs text-slate-500">
            {done.mode === 'agent' ? 'Findings are written by an AI model from the evidence above; verification checks numbers and citations, not interpretation. ' : ''}
            Describes observed data; not a fire-danger forecast or an official assessment.
          </p>
        </section>
      )}
    </div>
  )
}
