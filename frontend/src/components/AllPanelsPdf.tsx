import { useState, type RefObject } from 'react'
import { renderPanel } from '../lib/renderPanel'

/** Capture every panel inside `container` (elements marked data-pdf="Title", else top-level <section>s) and save them as one PDF, one panel per A4 landscape page. */
export default function AllPanelsPdf({ container, fileName, area }: { container: RefObject<HTMLElement | null>; fileName: string; area: string }) {
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  async function run() {
    const root = container.current
    if (!root) return
    setBusy(true)
    setErr('')
    try {
      const marked = Array.from(root.querySelectorAll<HTMLElement>('[data-pdf]'))
      const sections = marked.length ? marked : Array.from(root.querySelectorAll<HTMLElement>(':scope > section'))
      if (!sections.length) throw new Error('nothing to export yet')
      const { jsPDF } = await import('jspdf')
      const pdf = new jsPDF({ orientation: 'landscape', unit: 'pt', format: 'a4' })
      const pw = pdf.internal.pageSize.getWidth(), ph = pdf.internal.pageSize.getHeight(), m = 24
      for (const [i, sec] of sections.entries()) {
        const title = sec.dataset.pdf || sec.querySelector('h2')?.textContent?.trim() || `Panel ${i + 1}`
        const canvas = await renderPanel(sec, `${title} · ${area}`)
        const scale = Math.min((pw - 2 * m) / canvas.width, (ph - 2 * m) / canvas.height)
        const w = canvas.width * scale, h = canvas.height * scale
        if (i > 0) pdf.addPage()
        pdf.setFillColor(11, 15, 20)
        pdf.rect(0, 0, pw, ph, 'F')
        pdf.addImage(canvas.toDataURL('image/jpeg', 0.92), 'JPEG', (pw - w) / 2, m, w, h)
      }
      pdf.save(`${fileName}.pdf`)
    } catch (e) {
      setErr(`Could not create the PDF: ${(e as Error).message}`)
    } finally {
      setBusy(false)
    }
  }

  return (
    <span data-noexport className="inline-flex items-center gap-2">
      <button type="button" onClick={run} disabled={busy}
              className="rounded bg-orange-600 px-3 py-1 text-sm font-medium hover:bg-orange-500 disabled:opacity-50">
        {busy ? 'Creating PDF…' : '⤓ Download all as PDF'}
      </button>
      {err && <span className="text-xs text-red-300" role="alert">{err}</span>}
    </span>
  )
}
