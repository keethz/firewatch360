/** What a calendar month measures. Both go through the same MODIS → VIIRS harmonization (k, c). */
export type Metric = 'cell_days' | 'frp'

export const METRIC: Record<Metric, { label: string; unit: string; long: string; legend: string; hint: string }> = {
  cell_days: {
    label: 'Activity',
    unit: 'cell-days',
    long: 'fire cell-days',
    legend: 'Fire cell-days per month',
    hint: 'How much burned: unique ~1 km grid cells with a fire detection, counted once per day.',
  },
  frp: {
    label: 'Intensity (FRP)',
    unit: 'MW',
    long: 'MW of fire radiative power',
    legend: 'Total fire radiative power per month, MW',
    hint: 'How hard it burned: fire radiative power (MW) summed over every detection. Tracks fuel consumption, so a few intense fires can outweigh many small ones.',
  },
}

/** Query-string fragment for the API ('' for the default measure). */
export const metricQuery = (m: Metric) => (m === 'frp' ? 'metric=frp' : '')
