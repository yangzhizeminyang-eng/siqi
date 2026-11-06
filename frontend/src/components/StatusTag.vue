<script setup lang="ts">
import { computed } from 'vue'
import { useLanguage } from '../stores/language'
import { statusText, type RiskLevel, type TaskStatus } from '../types/domain'

const props = defineProps<{
  status?: TaskStatus
  risk?: RiskLevel
  demo?: boolean
}>()
const { isEnglish } = useLanguage()

const content = computed(() => {
  if (props.demo) return isEnglish.value ? 'Demo fallback' : '演示兜底'
  if (props.risk) {
    return isEnglish.value
      ? { pending: 'Pending', low: 'Low', medium: 'Medium', high: 'High' }[props.risk]
      : { pending: '待分析', low: '低风险', medium: '中风险', high: '高风险' }[props.risk]
  }
  if (props.status && isEnglish.value) {
    const statusMap: Record<TaskStatus, string> = {
      CREATED: 'Created',
      ISSUE_FETCHED: 'Issue fetched',
      CONTEXT_INDEXED: 'Context indexed',
      ANALYSIS_QUEUED: 'Analysis queued',
      ANALYZED: 'AI analysis complete',
      PLAN_GENERATED: 'Plan generated',
      WAITING_APPROVAL: 'Waiting approval',
      APPROVED: 'Approved',
      DIFF_GENERATED: 'Diff generated',
      TEST_RUNNING: 'Test running',
      TEST_FAILED: 'Test failed',
      TESTED: 'Tested',
      COMPLETED: 'Completed',
      FAILED: 'Failed',
      CANCELLED: 'Cancelled',
    }
    return statusMap[props.status]
  }
  return props.status ? statusText[props.status] : ''
})

const tone = computed(() => {
  if (props.demo) return 'demo'
  if (props.risk === 'pending') return 'info'
  if (props.risk) return `risk-${props.risk}`
  if (props.status === 'FAILED' || props.status === 'CANCELLED' || props.status === 'TEST_FAILED') return 'danger'
  if (props.status === 'WAITING_APPROVAL' || props.status === 'TEST_RUNNING') return 'warning'
  if (props.status === 'APPROVED' || props.status === 'COMPLETED' || props.status === 'TESTED') return 'success'
  return 'info'
})
</script>

<template>
  <span class="status-tag" :class="tone">{{ content }}</span>
</template>
