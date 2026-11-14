import { Area, CartesianGrid, ComposedChart, Legend, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import type { ClimMonth, MonthRow } from '../types/api'

interface Props {
  year: number
  months: ClimMonth[]
  rows: MonthRow[]
}

const n0 = (v: unknown): string => (typeof v === 'number' ? Math.round(v).toLocaleString() : Array.isArray(v) ? v.map(n0).join(' – ') : String(v))

/** Selected year vs the baseline monthly median, with the P25–P75 band. */
export default function YearChart({ year, months, rows }: Props) {
  const byMonth = new Map(rows.map((r) => [Number(r.month.slice(5, 7)), r]))
  const data = months.map((c) => {
    const r = byMonth.get(c.month)
    return {
      name: c.name,
      band: c.p25 != null && c.p75 != null ? [c.p25, c.p75] : null,
      median: c.median,
      value: r ? r.harmonized : null,
      anomaly: r?.anomaly ?? null,
    }
  })
  return (
    <div className="h-72" role="img" aria-label={`Line chart of ${year} monthly fire cell-days against the baseline median and interquartile range`}>
      <ResponsiveContainer>
        <ComposedChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: 4 }}>
          <CartesianGrid stroke="#1f2937" />
          <XAxis dataKey="name" stroke="#94a3b8" fontSize={11} />
          <YAxis stroke="#94a3b8" fontSize={11} tickFormatter={(v: number) => (v >= 1000 ? `${v / 1000}k` : String(v))} />
          <Tooltip contentStyle={{ background: '#0f172a', border: '1px solid #334155' }} formatter={(v) => n0(v)} />
          <Legend />
          <Area dataKey="band" name="Baseline P25–P75" stroke="none" fill="#64748b" fillOpacity={0.35} isAnimationActive={false} />
          <Line dataKey="median" name="Baseline median" stroke="#cbd5e1" strokeDasharray="5 4" dot={false} strokeWidth={1.5} isAnimationActive={false} />
          <Line
            dataKey="value" name={String(year)} stroke="#f97316" strokeWidth={2.5} isAnimationActive={false} connectNulls={false}
            dot={(props: { cx?: number; cy?: number; index?: number; payload?: { anomaly: string | null } }) => {
              const { cx, cy, index, payload } = props
              if (cx == null || cy == null || payload?.anomaly == null) return <g key={index} />
              // Anomalous months: larger marker with a ring (shape cue, not colour alone)
              return <circle key={index} cx={cx} cy={cy} r={6} fill="#f97316" stroke="#f8fafc" strokeWidth={2} />
            }}
          />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  )
}
