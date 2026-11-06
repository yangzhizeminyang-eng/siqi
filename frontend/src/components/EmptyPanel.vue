<script setup lang="ts">
import { ClipboardCheck } from '@lucide/vue'

defineProps<{
  title: string
  description: string
  action?: string
}>()

defineEmits<{ action: [] }>()
</script>

<template>
  <section class="empty-panel">
    <span class="empty-panel__icon"><ClipboardCheck :size="30" /></span>
    <div class="empty-panel__body">
      <h2>{{ title }}</h2>
      <p>{{ description }}</p>
    </div>
    <button v-if="action" class="button button--primary" @click="$emit('action')">{{ action }}</button>
  </section>
</template>
