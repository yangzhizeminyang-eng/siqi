<script setup lang="ts">
import { onBeforeUnmount, onMounted } from 'vue'
import { X } from '@lucide/vue'

const props = withDefaults(defineProps<{ open: boolean; title?: string; width?: string }>(), {
  open: false,
  title: '',
  width: 'min(520px, 100%)',
})
const emit = defineEmits<{ (e: 'close'): void }>()

function onKey(e: KeyboardEvent) {
  if (e.key === 'Escape') emit('close')
}
onMounted(() => window.addEventListener('keydown', onKey))
onBeforeUnmount(() => window.removeEventListener('keydown', onKey))
</script>

<template>
  <Teleport to="body">
    <div v-if="open" class="edrawer" @click.self="emit('close')">
      <aside class="edrawer__panel" :style="{ width }" role="dialog" aria-modal="true" :aria-label="title || '详情'">
        <header class="edrawer__head">
          <h2 class="edrawer__title">{{ title }}</h2>
          <button class="edrawer__close" type="button" aria-label="关闭" @click="emit('close')"><X :size="16" /></button>
        </header>
        <div class="edrawer__body">
          <slot />
        </div>
      </aside>
    </div>
  </Teleport>
</template>

<style scoped>
.edrawer { position: fixed; inset: 0; z-index: 80; background: rgba(15, 23, 42, .32); display: flex; justify-content: flex-end; }
.edrawer__panel { height: 100%; background: var(--panel-bg, #fff); box-shadow: var(--shadow-md, 0 10px 24px rgba(0,0,0,.08)); display: flex; flex-direction: column; }
.edrawer__head { display: flex; align-items: center; justify-content: space-between; padding: 14px 16px; border-bottom: 1px solid var(--theme-border, #dbe3ec); }
.edrawer__title { margin: 0; font-size: 15px; font-weight: 800; overflow-wrap: anywhere; }
.edrawer__close { border: 1px solid var(--theme-border, #dbe3ec); background: transparent; border-radius: 6px; width: 28px; height: 28px; display: inline-flex; align-items: center; justify-content: center; cursor: pointer; color: var(--text-soft, #758496); }
.edrawer__close:hover, .edrawer__close:focus-visible { outline: 2px solid var(--brand, #2249d6); }
.edrawer__body { overflow: auto; padding: 16px; }
</style>