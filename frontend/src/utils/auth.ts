const LEGACY_AUTH_KEY = 'frontend-auth'
const LEGACY_CREDENTIALS_KEY = 'frontend-login-credentials'

export function cleanupLegacyAuthArtifacts() {
  if (typeof window === 'undefined') return
  window.localStorage.removeItem(LEGACY_AUTH_KEY)
  window.localStorage.removeItem(LEGACY_CREDENTIALS_KEY)
}
