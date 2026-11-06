<script setup lang="ts">
defineProps<{ title: string; subtitle?: string; tenantName?: string }>()
</script>

<template>
  <header class="eheader">
    <div class="eheader__text">
      <h1 class="eheader__title">{{ title }}</h1>
      <p v-if="subtitle" class="eheader__subtitle">{{ subtitle }}</p>
    </div>
    <div class="eheader__side">
      <span v-if="tenantName" class="eheader__tenant" title="当前租户">租户：{{ tenantName }}</span>
      <slot name="actions" />
    </div>
  </header>
</template>

<style scoped>
.eheader {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 16px;
  min-width: 0;
  flex-wrap: wrap;
  padding-bottom: 16px;
  border-bottom: 1px solid var(--theme-border, #dbe3ec);
  margin-bottom: 16px;
}
.eheader__text { min-width: 0; flex: 1 1 420px; }
.eheader__title { margin: 0; font-size: 22px; font-weight: 800; line-height: 1.25; color: var(--theme-text, #1f2f46); overflow-wrap: anywhere; }
.eheader__subtitle { margin: 6px 0 0; color: var(--text-soft, #758496); font-size: 13px; line-height: 1.55; max-width: 70ch; }
.eheader__side { display: flex; align-items: center; justify-content: flex-end; gap: 10px; min-width: 0; flex: 0 1 auto; flex-wrap: wrap; }
.eheader__tenant { display: inline-flex; align-items: center; min-height: 28px; padding: 0 9px; border: 1px solid var(--theme-border, #dbe3ec); border-radius: 7px; color: var(--text-soft, #758496); background: var(--theme-surface-subtle, #f7fafc); font-size: 12px; font-weight: 700; white-space: nowrap; }

@media (max-width: 680px) {
  .eheader__title { font-size: 20px; }
  .eheader__side { width: 100%; justify-content: flex-start; }
  .eheader__tenant { max-width: 100%; overflow: hidden; text-overflow: ellipsis; }
}
</style>
