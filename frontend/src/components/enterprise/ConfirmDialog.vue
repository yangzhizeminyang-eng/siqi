<script setup lang="ts">
import { onBeforeUnmount, onMounted } from 'vue'
import { AlertTriangle } from '@lucide/vue'

const props = withDefaults(defineProps<{
  open: boolean
  title?: string
  message?: string
  confirmText?: string
  cancelText?: string
  danger?: boolean
  loading?: boolean
}>(), { title: '确认操作', message: '', confirmText: '确认', cancelText: '取消', danger: false, loading: false })
const emit = defineEmits<{ (e: 'confirm'): void; (e: 'cancel'): void }>()

function onKey(e: KeyboardEvent) {
  if (!props.open) return
  if (e.key === 'Escape') emit('cancel')
  if (e.key === 'Enter') emit('confirm')
}
onMounted(() => window.addEventListener('keydown', onKey))
onBeforeUnmount(() => window.removeEventListener('keydown', onKey))
</script>

<template>
  <Teleport to="body">
    <div v-if="open" class="edialog" @click.self="emit('cancel')" role="alertdialog" aria-modal="true" :aria-label="title">
      <div class="edialog__box">
        <header class="edialog__head"><AlertTriangle :size="16" :class="danger ? 'edialog__danger' : 'edialog__warn'" /><h2>{{ title }}</h2></header>
        <div class="edialog__body">{{ message }}</div>
        <footer class="edialog__foot">
          <button class="btn ebtn" type="button" :disabled="loading" @click="emit('cancel')">{{ cancelText }}</button>
          <button class="btn ebtn" :class="danger ? 'ebtn--danger' : 'ebtn--primary'" type="button" :disabled="loading" @click="emit('confirm')">
            {{ loading ? '处理中…' : confirmText }}
          </button>
        </footer>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.edialog { position: fixed; inset: 0; z-index: 90; background: rgba(15, 23, 42, .38); display: grid; place-items: center; padding: 16px; }
.edialog__box { width: min(420px, 100%); background: var(--panel-bg, #fff); border-radius: 8px; box-shadow: var(--shadow-md, 0 10px 24px rgba(0,0,0,.08)); }
.edialog__head { display: flex; align-items: center; gap: 8px; padding: 14px 16px; border-bottom: 1px solid var(--theme-border, #dbe3ec); font-weight: 800; }
.edialog__danger { color: var(--danger, #b42318); }
.edialog__warn { color: var(--warning, #9b5c03); }
.edialog__body { padding: 14px 16px; color: var(--text-base, #425166); overflow-wrap: anywhere; }
.edialog__foot { display: flex; justify-content: flex-end; gap: 8px; padding: 12px 16px; border-top: 1px solid var(--theme-border, #dbe3ec); }
.ebtn { border: 1px solid var(--theme-border, #dbe3ec); background: var(--panel-bg, #fff); color: var(--theme-text, #1f2f46); }
.ebtn--primary { background: var(--brand, #2249d6); color: #fff; border-color: transparent; }
.ebtn--danger { background: var(--danger, #b42318); color: #fff; border-color: transparent; }
.ebtn:focus-visible { outline: 2px solid var(--brand, #2249d6); outline-offset: 1px; }
</style>