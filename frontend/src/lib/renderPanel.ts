const BG = '#0b0f14'

/** Render a panel to a canvas with the standard FIREWATCH 360 footer. Shared by PNG and PDF exports. */
export async function renderPanel(node: HTMLElement, caption: string): Promise<HTMLCanvasElement> {
  const { toCanvas } = await import('html-to-image')
  const shot = await toCanvas(node, {
    pixelRatio: 2,
    backgroundColor: BG,
    width: node.scrollWidth,
    filter: (n: HTMLElement) => !(n instanceof HTMLElement && n.dataset.noexport !== undefined),
  })
  const pad = 24 * 2, footer = 44 * 2
  const out = document.createElement('canvas')
  out.width = shot.width + pad * 2
  out.height = shot.height + pad + footer
  const ctx = out.getContext('2d')!
  ctx.fillStyle = BG
  ctx.fillRect(0, 0, out.width, out.height)
  ctx.drawImage(shot, pad, pad)
  ctx.fillStyle = '#94a3b8'
  ctx.font = `${12 * 2}px system-ui, -apple-system, Segoe UI, sans-serif`
  const generated = new Date().toISOString().slice(0, 16).replace('T', ' ') + ' UTC'
  ctx.fillText(`FIREWATCH 360 · ${caption} · generated ${generated}`, pad, shot.height + pad + 26 * 2)
  ctx.fillText('Data: NASA FIRMS (MODIS C6.1, VIIRS S-NPP 375 m), harmonized by FIREWATCH 360. Prototype, not an official NASA product.', pad, shot.height + pad + 40 * 2)
  return out
}

