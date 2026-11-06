import { computed, ref, watchEffect } from 'vue'

export type LanguageMode = 'zh' | 'en'

const LANGUAGE_KEY = 'frontend-language-mode'
const languageMode = ref<LanguageMode>(readLanguageMode())

function readLanguageMode(): LanguageMode {
  if (typeof window === 'undefined') return 'zh'
  return window.localStorage.getItem(LANGUAGE_KEY) === 'en' ? 'en' : 'zh'
}

export function useLanguage() {
  const isEnglish = computed(() => languageMode.value === 'en')

  function setLanguageMode(mode: LanguageMode) {
    languageMode.value = mode
    if (typeof window !== 'undefined') {
      window.localStorage.setItem(LANGUAGE_KEY, mode)
    }
  }

  return {
    languageMode,
    isEnglish,
    setLanguageMode,
  }
}

export function initLanguage() {
  watchEffect(() => {
    if (typeof document === 'undefined') return
    document.documentElement.lang = languageMode.value === 'en' ? 'en' : 'zh-CN'
  })
}
