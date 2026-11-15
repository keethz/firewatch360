import { useState, type RefObject } from 'react'
import { renderPanel } from '../lib/renderPanel'

/** Download a panel as a PNG, with a footer naming the source, area and date so the image stands alone.
 *  Elements marked data-noexport (buttons, menus) are left out. */
export default function ImageDownload({ target, fileName, caption }: { target: RefObject<HTMLElement | null>; fileName: string; caption: string }) {
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  async function run() {
    const node = target.current
    if (!node) return
    setBusy(true)
    setErr('')
    try {
      const out = await renderPanel(node, caption)
      const a = document.createElement('a')
      a.href = out.toDataURL('image/png')
      a.download = `${fileName}.png`
      a.click()
    } catch (e) {
      setErr(`Could not create the image: ${(e as Error).message}`)
    } finally {
      setBusy(false)
    }
  }

  return (
    <span data-noexport className="inline-flex items-center gap-2">
      <button type="button" onClick={run} disabled={busy}
              className="rounded border border-slate-600 px-2 py-1 text-sm hover:bg-slate-800 disabled:opacity-50">
        {busy ? 'Creating…' : '⤓ PNG'}
      </button>
      {err && <span className="text-xs text-red-300" role="alert">{err}</span>}
    </span>
  )
}
