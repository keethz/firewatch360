// Colour ramps for the calendar. Both are colour-vision-deficiency safe.

type Stop = [number, number, number]

// Viridis, 9 evenly spaced samples (matplotlib).
const VIRIDIS: Stop[] = [
  [68, 1, 84], [71, 44, 122], [59, 81, 139], [44, 113, 142], [33, 144, 141],
  [39, 173, 129], [92, 200, 99], [170, 220, 50], [253, 231, 37],
]
// Cividis, 9 samples (optimised for deuteranomaly / protanomaly).
const CIVIDIS: Stop[] = [
  [0, 34, 78], [18, 53, 112], [59, 73, 108], [87, 92, 109], [112, 113, 115],
  [138, 134, 120], [165, 156, 116], [195, 179, 105], [254, 232, 56],
]
// Diverging blue – near-white – orange (PuOr-like, colourblind safe).
const DIVERGING: Stop[] = [
  [33, 102, 172], [103, 169, 207], [209, 229, 240], [247, 247, 247],
  [253, 219, 199], [244, 165, 130], [214, 96, 77],
]
const DIVERGING_CB: Stop[] = [
  [45, 0, 75], [128, 115, 172], [216, 218, 235], [247, 247, 247],
  [254, 224, 182], [224, 130, 20], [127, 59, 8],
]

export type SeqRamp = 'viridis' | 'cividis'
const SEQ = { viridis: VIRIDIS, cividis: CIVIDIS }

function interp(stops: Stop[], t: number): Stop {
  const x = Math.min(1, Math.max(0, t)) * (stops.length - 1)
  const i = Math.min(stops.length - 2, Math.floor(x))
  const f = x - i
  const a = stops[i], b = stops[i + 1]
  return [0, 1, 2].map((k) => Math.round(a[k] + (b[k] - a[k]) * f)) as Stop
}
const css = ([r, g, b]: Stop) => `rgb(${r} ${g} ${b})`
/** Relative luminance (WCAG) — used to pick readable text over a cell. */
export function textOn([r, g, b]: Stop): string {
  const l = [r, g, b].map((v) => { const c = v / 255; return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4 })
  return 0.2126 * l[0] + 0.7152 * l[1] + 0.0722 * l[2] > 0.18 ? '#0b0f14' : '#f8fafc'
}

/** Log scale from the smallest non-zero value to the max: counts span several orders of
 * magnitude between quiet and extreme months. Zero maps to the bottom of the ramp. */
export function countScale(min: number, max: number, ramp: SeqRamp) {
  const lo = Math.log10(Math.max(1, min))
  const hi = Math.max(lo + 1, Math.log10(Math.max(1, max)))
  const t = (v: number) => (v <= 0 ? 0 : (Math.log10(Math.max(1, v)) - lo) / (hi - lo))
  return {
    rgb: (v: number) => interp(SEQ[ramp], t(v)),
    color: (v: number) => css(interp(SEQ[ramp], t(v))),
    /** 1-2-5 ticks inside the domain, plus the two ends. */
    ticks: (): number[] => {
      const out = new Set<number>([Math.round(10 ** lo)])
      for (let p = Math.floor(lo); p <= Math.ceil(hi); p++)
        for (const m of [1, 2, 5]) {
          const v = m * 10 ** p
          if (v > 10 ** lo * 1.3 && v < 10 ** hi / 1.3) out.add(v)
        }
      out.add(Math.round(10 ** hi))
      const all = [...out].sort((a, b) => a - b)
      return all.length > 7 ? all.filter((v, i) => i === 0 || i === all.length - 1 || ![2, 5].includes(Math.round(v / 10 ** Math.floor(Math.log10(v))))) : all
    },
    gradient: () => `linear-gradient(to right, ${SEQ[ramp].map(css).join(', ')})`,
    position: (v: number) => t(v),
  }
}

export const Z_LIMIT = 4
export function zScale(colorblindAlt = false) {
  const stops = colorblindAlt ? DIVERGING_CB : DIVERGING
  const t = (z: number) => (Math.max(-Z_LIMIT, Math.min(Z_LIMIT, z)) + Z_LIMIT) / (2 * Z_LIMIT)
  return {
    rgb: (z: number) => interp(stops, t(z)),
    color: (z: number) => css(interp(stops, t(z))),
    gradient: () => `linear-gradient(to right, ${stops.map(css).join(', ')})`,
    position: t,
  }
}

export const fmtCompact = (v: number) =>
  v >= 1e4 ? `${Math.round(v / 1e3)}k` : v >= 1e3 ? `${(v / 1e3).toFixed(1)}k` : String(Math.round(v))
