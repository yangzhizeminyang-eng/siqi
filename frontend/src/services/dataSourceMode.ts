export type DataSourceMode = 'real' | 'mock'

let currentMode: DataSourceMode | null = null

function defaultMode(): DataSourceMode {
  return import.meta.env.VITE_DATA_SOURCE === 'mock' ? 'mock' : 'real'
}

export function getDataSourceMode(): DataSourceMode {
  if (currentMode === null) currentMode = defaultMode()
  return currentMode
}

export function setDataSourceMode(mode: DataSourceMode): void {
  currentMode = mode
}
