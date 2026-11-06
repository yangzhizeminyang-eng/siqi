<script setup lang="ts">
import { useAuthStore } from '../../stores/auth'
import { useTenancyStore } from '../../stores/tenancy'
import { hasAccessRequirement, type AccessRequirement } from '../../services/accessControl'
import AsyncState from './AsyncState.vue'

const props = withDefaults(defineProps<{ permission?: AccessRequirement; message?: string }>(), {
  permission: undefined,
  message: '无权限访问（403）：前端仅展示层，后端为最终安全边界',
})
const auth = useAuthStore()
const tenancy = useTenancyStore()
const allowed = () => hasAccessRequirement(auth, tenancy, props.permission)
</script>

<template>
  <AsyncState v-if="!allowed()" state="forbidden" :message="message" />
  <slot v-else />
</template>