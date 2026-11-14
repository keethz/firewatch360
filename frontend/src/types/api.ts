export interface SensorCoverage {
  rows: number
  first_date: string
  last_date: string
  years: number
}

export interface Coverage {
  region: string
  source: string
  products: Record<string, string>
  processed_at_utc: string
  sensors: Record<'MODIS' | 'VIIRS', SensorCoverage>
}

export interface CalibrationPoint {
  month: string
  modis: number
  viirs: number
  modis_scaled: number
  residual: number
  used_in_fit: boolean
}

export interface ValidationYear {
  year: number
  k_train: number
  n_months: number
  annual_error_pct: number
  monthly_mape_pct: number | null
}

export interface CalibrationResponse {
  aoi: { kind: string; bbox?: number[]; lat?: number; lon?: number; radius_km?: number }
  parameters: Record<string, unknown>
  windows: Windows
  units: string
  caveat: string
  calibration: {
    k: number
    r2: number | null
    n_months: number
    rmse: number
    linear: { a: number; b: number; r2: number | null } | null
    status: 'ok' | 'insufficient_overlap'
    scope: 'aoi' | 'region_fallback'
    aoi_usable_months: number
    model: string
    overlap_window: [string, string]
    validation: { per_year: ValidationYear[]; mean_annual_error_pct: number | null; mean_monthly_mape_pct: number | null }
  }
  terra_correction: {
    c: number
    r2: number | null
    n_months?: number
    status: 'ok' | 'insufficient_overlap' | 'not_applicable'
    scope: 'aoi' | 'region_fallback' | 'none'
    aoi_usable_months?: number
    fit_window?: [string, string]
    applies_before: string | null
    validation?: { mean_annual_error_pct: number | null; mean_monthly_mape_pct: number | null }
  }
  series: CalibrationPoint[]
}

export interface MonthRow {
  month: string
  harmonized: number
  modis: number
  viirs: number | null
  source: 'MODIS_terra_scaled' | 'MODIS_scaled' | 'VIIRS' | 'VIIRS_provisional'
  modis_terra: number
  provisional?: boolean
  clim_median: number | null
  pct_vs_median: number | null
  z: number | null
  anomaly: 'high' | 'low' | null
}

export interface AnomaliesResponse {
  window: [string, string]
  windows: Windows
  calibration: { k: number; scope: string; status: string }
  climatology: { baseline: [number, number]; baseline_years: number[]; status: string }
  method: string
  caveat: string
  latest_vs_median: { month: string; value: number; median: number | null; pct_vs_median: number | null; z: number | null; anomaly: string | null; data_status: string; provisional: boolean } | null
  current_month: CurrentMonth | null
  anomalies: MonthRow[]
  rankings: {
    top_months: { month: string; harmonized: number; source: string }[]
    top_years: { year: number; total: number }[]
    peak_season: string[]
    peak_season_rule: string
    trend: { slope_per_year: number; n_years: number } | null
  }
}

export interface HotspotProps {
  sensor: 'MODIS' | 'VIIRS'
  level: 'archive' | 'standard' | 'nrt'
  date: string
  time_utc: string
  frp: number | null
  confidence: string
  daynight: string
  type: number | null
}

export interface HotspotsResponse {
  type: 'FeatureCollection'
  features: { type: 'Feature'; geometry: { type: 'Point'; coordinates: [number, number] }; properties: HotspotProps }[]
  total: number
  returned: number
  sampled: boolean
  counts: { MODIS: number; VIIRS: number; nrt_provisional: number }
  first_date: string | null
  last_date: string | null
  note: string
}

export interface CalendarYear {
  year: number
  total: number
  complete: boolean
  months: (MonthRow | null)[]
}

interface MetaBase {
  window: [string, string]
  windows: Windows
  calibration: { k: number; r2: number | null; n_months: number; status: string; scope: string; terra_c: number; terra_status: string; terra_scope: string; terra_applies_before: string | null }
  z_threshold: number
  units: string
  caveat: string
}

export interface CalendarResponse extends MetaBase {
  climatology: { baseline: [number, number]; baseline_years: number[]; status: string }
  years: CalendarYear[]
}

export interface ClimMonth {
  month: number
  name: string
  n_years: number
  median: number | null
  mean: number | null
  p25: number | null
  p75: number | null
  mad: number | null
}

export interface ClimatologyResponse extends MetaBase {
  selected_year: number
  climatology: { baseline: [number, number]; baseline_years: number[]; status: string; months: ClimMonth[] }
  selected_year_rows: MonthRow[]
}

export interface Windows {
  series_start: string
  switch_month: string
  aqua_month: string | null
  overlap_end?: string
  series_end: string
  data_as_of: Record<string, string>
  provisional_end: string | null
  nrt_as_of: Record<string, string>
  current: { month: string; through: string; sensor: 'MODIS' | 'VIIRS' } | null
}

export interface CurrentMonth {
  month: string
  through: string
  days: number
  sensor: 'MODIS' | 'VIIRS'
  raw_cell_days: number
  value: number
  value_source: string
  status: 'provisional'
  history_status: 'ok' | 'insufficient_history'
  n_baseline_years: number
  median: number | null
  p25: number | null
  p75: number | null
  z: number | null
  pct_vs_median: number | null
  anomaly: 'high' | 'low' | null
  rank?: number
}

export interface NrtResponse extends Omit<HotspotsResponse, 'counts' | 'returned' | 'sampled' | 'first_date' | 'last_date'> {
  counts: Record<string, number>
  by_day: { date: string; sensor: string; n: number }[]
  window: [string, string]
}

export interface SensorSignal {
  available: boolean
  value: number | null
  median: number | null
  n_years: number
  z: number | null
  class: 'high' | 'normal' | 'low' | null
}

export interface BriefResponse {
  headline: string
  level: 'Severe' | 'Moderate' | 'Normal' | 'Below normal' | 'Unknown'
  reference: { label: string; month: string; period: string; provisional: boolean; kind: 'month_to_date' | 'month' }
  current_activity: { value: number; median: number | null; pct_vs_median: number | null; text: string | null; z: number | null; units: string }
  rank: { rank: number; side: 'highest' | 'lowest'; of: number; text: string } | null
  sensors: Record<'MODIS' | 'VIIRS', SensorSignal>
  agreement: { label: string; status: string }
  confidence: { level: 'High' | 'Medium' | 'Low'; score: number; reasons: string[] }
  peak_season: string[]
  trend: { direction: string; text: string; pct_per_year?: number }
  baseline: { years: [number, number] | null; n_years: number }
  factors: { k: number; c: number; k_scope: string; c_scope: string }
  rules: Record<string, string>
  aoi: Record<string, unknown>
  generated_utc: string
  data_as_of: { final: Record<string, string>; nrt: Record<string, string> }
  caveat: string
}
