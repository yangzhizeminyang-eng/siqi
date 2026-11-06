<script setup lang="ts">
import type { ThemeMode } from '../stores/theme'
import { useTheme } from '../stores/theme'

const { themeMode, setThemeMode } = useTheme()

const options: Array<{ value: ThemeMode; label: string }> = [
  { value: 'system', label: '系统' },
  { value: 'light', label: '浅色' },
  { value: 'dark', label: '深色' },
]
</script>

<template>
  <div class="theme-switcher" role="radiogroup" aria-label="主题模式">
    <button
      v-for="option in options"
      :key="option.value"
      type="button"
      class="theme-switcher__item"
      :class="{ 'is-active': themeMode === option.value }"
      :aria-checked="themeMode === option.value"
      role="radio"
      @click="setThemeMode(option.value)"
    >
      {{ option.label }}
    </button>
  </div>
</template>
