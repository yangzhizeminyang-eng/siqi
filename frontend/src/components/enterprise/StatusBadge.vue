<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(defineProps<{ status?: string; label?: string; size?: 'sm' | 'md' }>(), {
  status: '',
  label: '',
  size: 'md',
})

const TONE: Record<string, string> = {
  verified: 'ok', succeeded: 'ok', success: 'ok', active: 'ok', running: 'ok', executing: 'ok',
  local_only: 'warn', waiting_approval: 'warn', retrying: 'warn', waiting_human: 'warn', pending: 'warn',
  blocked: 'danger', failed: 'danger', rejected: 'danger', cancelled: 'neutral', queued: 'neutral', idle: 'neutral',
  stale: 'warn', paused: 'warn',
}
const tone = computed(() => TONE[props.status] ?? 'neutral')
const label = computed(() => props.label || props.status || '—')
</script>

<template>
  <span class="ebadge" :class="[`ebadge--${tone}`, `ebadge--${size}`]" :title="label">
    {{ label }}
  </span>
</template>

<style scoped>
.ebadge {
  display: inline-flex;
  align-items: center;
  max-width: 100%;
  padding: 2px 8px;
  border: 1px solid var(--theme-border, #dbe3ec);
  border-radius: 6px;
  background: var(--theme-tag-surface, #f1f5f9);
  color: var(--theme-text, #1f2f46);
  font-size: 12px;
  font-weight: 700;
  line-height: 1.5;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.ebadge--sm { font-size: 11px; padding: 1px 6px; }
.ebadge--ok { background: var(--theme-source-surface, #eaf1ff); color: var(--success, #187448); border-color: transparent; }
.ebadge--warn { background: #fff6e5; color: var(--warning, #9b5c03); border-color: transparent; }
.ebadge--danger { background: #ffeae6; color: var(--danger, #b42318); border-color: transparent; }
.ebadge--neutral { color: var(--text-soft, #758496); }
</style>