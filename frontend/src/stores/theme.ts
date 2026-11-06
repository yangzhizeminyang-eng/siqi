import { computed, ref } from 'vue'

export type ThemeMode = 'system' | 'light' | 'dark'
type ResolvedTheme = 'light' | 'dark'

const THEME_KEY = 'frontend-theme-mode'
const themeMode = ref<ThemeMode>('system')
const systemPrefersDark = ref(false)
let initialized = false
let mediaQuery: MediaQueryList | null = null

const resolvedTheme = computed<ResolvedTheme>(() => {
  if (themeMode.value === 'system') {
    return systemPrefersDark.value ? 'dark' : 'light'
  }
  return themeMode.value
})

function applyTheme() {
  if (typeof document === 'undefined') return
  const resolved = resolvedTheme.value
  const root = document.documentElement
  root.dataset.theme = resolved
  root.style.colorScheme = resolved
}

function readStoredMode(): ThemeMode {
  if (typeof window === 'undefined') return 'system'
  const stored = window.localStorage.getItem(THEME_KEY)
  if (stored === 'light' || stored === 'dark' || stored === 'system') return stored
  return 'system'
}

export function initTheme() {
  if (initialized || typeof window === 'undefined') return
  initialized = true
  themeMode.value = readStoredMode()
  mediaQuery = window.matchMedia('(prefers-color-scheme: dark)')
  systemPrefersDark.value = mediaQuery.matches
  const updateSystemTheme = () => {
    systemPrefersDark.value = mediaQuery?.matches ?? false
    if (themeMode.value === 'system') {
      applyTheme()
    }
  }

  if (mediaQuery.addEventListener) {
    mediaQuery.addEventListener('change', updateSystemTheme)
  } else {
    mediaQuery.addListener(updateSystemTheme)
  }

  applyTheme()
}

export function setThemeMode(mode: ThemeMode) {
  themeMode.value = mode
  if (typeof window !== 'undefined') {
    window.localStorage.setItem(THEME_KEY, mode)
  }
  applyTheme()
}

export function useTheme() {
  return {
    themeMode,
    resolvedTheme,
    setThemeMode,
  }
}
