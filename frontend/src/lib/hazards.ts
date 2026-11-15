import { keepPreviousData } from '@tanstack/react-query'
import { withAoi, type Aoi } from './aoi'
import { getJson } from './api'
import type { BurnedResponse, PostFireResponse, SmokeResponse } from '../types/api'

// Query definitions shared by the panels and the map (same keys, so data is fetched once).

const THIS_YEAR = new Date().getUTCFullYear()
export const YEARS = Array.from({ length: THIS_YEAR - 1984 + 1 }, (_, i) => THIS_YEAR - i)
export type FireRef = { source: string; id: number }

export const burnedQuery = (aoi: Aoi, year: number) => ({
  queryKey: ['burned', aoi, year],
  queryFn: () => getJson<BurnedResponse>(withAoi('/api/burned', aoi, `year=${year}`)),
  staleTime: 30 * 60_000,
  placeholderData: keepPreviousData,
})
export const postFireQuery = (f: FireRef | null) => ({
  queryKey: ['postfire', f?.source, f?.id],
  queryFn: () => getJson<PostFireResponse>(`/api/burned/fire?source=${f!.source}&id=${f!.id}`),
  enabled: !!f,
  staleTime: Infinity,
})

export const SMOKE_DAYS = 14
export const smokeQuery = (aoi: Aoi, date: string) => ({
  queryKey: ['smoke', aoi, date],
  queryFn: () => getJson<SmokeResponse>(withAoi('/api/smoke', aoi, `days=${SMOKE_DAYS}${date ? `&date=${date}` : ''}`)),
  staleTime: 30 * 60_000,
  placeholderData: keepPreviousData,
})

