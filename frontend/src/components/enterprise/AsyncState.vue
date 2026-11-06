<script setup lang="ts">
import { LoaderCircle, AlertTriangle, ShieldX, Inbox } from '@lucide/vue'

withDefaults(defineProps<{
  state?: 'loading' | 'empty' | 'error' | 'forbidden' | 'ready'
  message?: string
}>(), { state: 'ready', message: '' })
</script>

<template>
  <div v-if="state === 'loading'" class="estate" role="status" aria-live="polite">
    <LoaderCircle class="spin" :size="18" /><span>{{ message || '加载中…' }}</span>
  </div>
  <div v-else-if="state === 'empty'" class="estate estate--muted">
    <Inbox :size="18" /><span>{{ message || '暂无数据' }}</span>
  </div>
  <div v-else-if="state === 'error'" class="estate estate--danger" role="alert">
    <AlertTriangle :size="18" /><span>{{ message || '加载失败，请稍后重试' }}</span>
  </div>
  <div v-else-if="state === 'forbidden'" class="estate estate--danger">
    <ShieldX :size="18" /><span>{{ message || '无权限访问（403）：审计等受限能力不可越权访问' }}</span>
  </div>
  <slot v-else />
</template>

<style scoped>
.estate { display: flex; align-items: center; gap: 8px; padding: 22px 12px; color: var(--text-soft, #758496); font-size: 13px; }
.estate--danger { color: var(--danger, #b42318); }
.estate--muted { color: var(--text-soft, #758496); }
.spin { animation: easpin 1s linear infinite; }
@keyframes easpin { to { transform: rotate(360deg); } }
</style>