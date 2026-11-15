// Human-impact map overlays. Vector layers come from /api/impact/features; raster layers are
// public tile services from the data providers (no keys).

export type VectorOverlay = 'places' | 'roads' | 'hospitals' | 'schools' | 'firestations' | 'airports' | 'reported'
export type RasterOverlay = 'protected' | 'wui' | 'landcover'
export type AreaOverlay = 'perimeters' | 'smoke'
export type OverlayId = VectorOverlay | RasterOverlay | AreaOverlay | 'firezone'

export const OVERLAYS: { id: OverlayId; label: string; kind: 'vector' | 'raster' | 'zone' | 'area'; color: string; glyph?: string; note: string }[] = [
  { id: 'firezone', label: 'Fire exposure zone', kind: 'zone', color: '#f43f5e', note: 'Within the buffer distance of a fire detection' },
  { id: 'perimeters', label: 'Fire perimeters', kind: 'area', color: '#ef4444', note: 'Mapped burned areas for the Burned area year (NIFC WFIGS 2020+, MTBS before); click one for its post-fire assessment' },
  { id: 'smoke', label: 'Smoke plumes', kind: 'area', color: '#94a3b8', note: 'NOAA HMS smoke for the Smoke date: light, medium, heavy' },
  { id: 'reported', label: 'Reported fires (NIFC)', kind: 'vector', color: '#f59e0b', glyph: 'R', note: 'NIFC WFIGS incidents in the date range, 2014+; size by acres' },
  { id: 'places', label: 'Population centers', kind: 'vector', color: '#e2e8f0', glyph: 'P', note: 'Natural Earth populated places' },
  { id: 'roads', label: 'Major roads', kind: 'vector', color: '#facc15', note: 'Natural Earth highways' },
  { id: 'hospitals', label: 'Hospitals', kind: 'vector', color: '#f472b6', glyph: 'H', note: 'HIFLD, open facilities' },
  { id: 'schools', label: 'Schools', kind: 'vector', color: '#a78bfa', glyph: 'S', note: 'NCES public schools 2022–23' },
  { id: 'firestations', label: 'Fire stations', kind: 'vector', color: '#fb7185', glyph: 'F', note: 'USGS National Structures, fire/EMS stations' },
  { id: 'airports', label: 'Airports', kind: 'vector', color: '#38bdf8', glyph: 'A', note: 'OurAirports' },
  { id: 'protected', label: 'Protected areas', kind: 'raster', color: '#4ade80', note: 'USGS PAD-US 3.0 (tiles to zoom 7)' },
  { id: 'wui', label: 'Wildland–urban interface', kind: 'raster', color: '#fb923c', note: 'USFS SILVIS WUI 2020 (slow tiles)' },
  { id: 'landcover', label: 'Land cover / agriculture', kind: 'raster', color: '#c2a35a', note: 'NLCD 2021; crops are brown, pasture yellow' },
]

export const RASTER_TILES: Record<RasterOverlay, { tiles: string[]; maxzoom: number; attribution: string; opacity: number }> = {
  protected: {
    tiles: ['https://services.arcgis.com/v01gqwM5QqNysAAi/ArcGIS/rest/services/PADUS3_0Fee_Manager/MapServer/tile/{z}/{y}/{x}'],
    maxzoom: 7, attribution: 'PAD-US 3.0, USGS GAP', opacity: 0.55,
  },
  wui: {
    tiles: ['https://apps.fs.usda.gov/arcx/rest/services/EDW/EDW_WUI_2020_01/MapServer/export?bbox={bbox-epsg-3857}&bboxSR=3857&imageSR=3857&size=256,256&format=png32&transparent=true&layers=show:0&f=image'],
    maxzoom: 14, attribution: 'WUI 2020, USFS / SILVIS Lab', opacity: 0.6,
  },
  landcover: {
    tiles: ['https://www.mrlc.gov/geoserver/mrlc_display/NLCD_2021_Land_Cover_L48/wms?service=WMS&request=GetMap&version=1.1.1&layers=NLCD_2021_Land_Cover_L48&styles=&format=image/png&transparent=true&width=256&height=256&srs=EPSG:3857&bbox={bbox-epsg-3857}'],
    maxzoom: 14, attribution: 'NLCD 2021, MRLC', opacity: 0.5,
  },
}

/** HMS smoke densities: light → heavy in increasing opacity and darkness (plus labels in the legend). */
export const SMOKE_COLORS = { Light: '#cbd5e1', Medium: '#94a3b8', Heavy: '#475569' } as const
