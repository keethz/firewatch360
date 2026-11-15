import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { getJson } from '../lib/api'
import type { ExplainResponse } from '../types/api'

/** On-demand AI explanation of statistics the server computed. Falls back to `fallback` text. */
export default function AiExplanation({ url, fallback }: { url: string; fallback?: string }) {
  const [asked, setAsked] = useState<string | null>(null)
  const active = asked === url
  const q = useQuery({ queryKey: ['explain', url], queryFn: () => getJson<ExplainResponse>(url), enabled: active, staleTime: Infinity, retry: false })
  if (!active)
    return (
      <button onClick={() => setAsked(url)} className="rounded border border-violet-400/60 px-3 py-1 text-sm text-violet-200 hover:bg-violet-500/10">
        ✦ Explain with AI
      </button>
    )
  return (
    <div className="rounded border border-violet-400/40 bg-violet-500/5 p-3 text-sm" aria-live="polite">
      <p className="mb-1 text-xs font-medium uppercase tracking-wide text-violet-300">✦ AI explanation</p>
      {q.isPending && <p className="text-slate-400">Writing an explanation from the statistics…</p>}
      {q.isError && <p className="text-red-300">Could not get an explanation: {(q.error as Error).message}</p>}
      {q.data?.status === 'ok' && (
        <>
          <p className="leading-relaxed text-slate-100">{q.data.text}</p>
          <p className="mt-2 text-xs text-slate-500">{q.data.label} Model: {q.data.model}.</p>
        </>
      )}
      {q.data && q.data.status !== 'ok' && (
        <>
          <p className="text-amber-200">{q.data.message}</p>
          {fallback && <p className="mt-2 text-slate-300"><span className="text-xs text-slate-500">Rule-based summary: </span>{fallback}</p>}
        </>
      )}
    </div>
  )
}
