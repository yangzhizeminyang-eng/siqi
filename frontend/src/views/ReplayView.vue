<script setup lang="ts">
import { Braces, Check, CheckCircle2, ChevronDown, ChevronRight, Clock3, Copy, FileSearch, Link2, Play, ShieldAlert, TerminalSquare, TriangleAlert } from '@lucide/vue'
import { computed, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import EmptyPanel from '../components/EmptyPanel.vue'
import { useWorkspaceStore } from '../stores/workspace'
import StatusTag from '../components/StatusTag.vue'
import { getDataSourceMode } from '../services/dataSourceMode'
import { apiRequest } from '../services/api'
import { summarizeFailureReason } from '../utils/failureReason'
import type { ExecutionStep, ExecutionStepStatus, ReplayTimelineEvent, TaskReplay, TimelineEvent } from '../types/domain'

const store = useWorkspaceStore()
const route = useRoute()
const router = useRouter()
const routeTaskId = computed(() => {
  const value = route.params.id
  return Array.isArray(value) ? value[0] : value
})
// 带路由 ID 时严格匹配 store 中同 ID 任务，避免直达/刷新时静默落到其他任务
const task = computed(() =>
  routeTaskId.value ? (store.taskById(routeTaskId.value) ?? null) : (store.selectedTask ?? null),
)
const artifact = computed(() => (task.value ? store.artifactFor(task.value.id) : null))

const loadingTask = ref(false)
const loadErrorKind = ref<'' | 'missing' | 'unauthorized' | 'unexpected'>('')
let loadSeq = 0
let disposed = false

const errorView = computed(() => {
  if (loadErrorKind.value === 'unauthorized') {
    return { title: '登录状态已失效', description: '请重新登录后再查看任务回放。' }
  }
  if (loadErrorKind.value === 'missing') {
    return { title: '任务不存在或无权查看', description: '任务可能已被删除，或当前账号没有查看权限。' }
  }
  if (loadErrorKind.value === 'unexpected') {
    return { title: '回放数据加载失败', description: '请稍后重试；问题持续存在时请联系管理员。' }
  }
  return null
})

const showEmptyState = computed(() => {
  const current = task.value
  const currentArtifact = artifact.value
  return (
    !!current &&
    !loadingTask.value &&
    (current.status === 'CREATED' || !currentArtifact || currentArtifact.isPlaceholder || !currentArtifact.timeline.length)
  )
})
const emptyTitle = computed(() => (task.value?.status === 'CREATED' ? '回放还没生成' : '当前任务没有可回放的执行节点'))
const emptyDescription = computed(() => '任务创建后需要先完成上下文拉取和计划生成，回放会在流程推进后逐步出现。')
const activeEvent = ref('')

function pickInitialEventId() {
  return artifact.value?.timeline.find((item) => item.status === 'active')?.id ?? artifact.value?.timeline.at(-1)?.id ?? ''
}

function timelineStatusLabel(status: string) {
  if (status === 'done') return '已完成'
  if (status === 'active') return '进行中'
  if (status === 'failed') return '失败'
  return '待执行'
}

watch(
  task,
  (next) => {
    if (!next) return
    if (store.selectedTaskId !== next.id) store.selectTask(next.id)
    activeEvent.value = pickInitialEventId()
  },
  { immediate: true },
)

const event = computed(() => artifact.value?.timeline.find((item) => item.id === activeEvent.value) ?? artifact.value?.timeline[0])

function stepStatusText(status: ExecutionStepStatus) {
  if (status === 'completed') return '完成'
  if (status === 'running') return '运行中'
  if (status === 'failed') return '失败'
  if (status === 'blocked') return '拦截'
  return status
}

function formatStepDuration(ms: number | null | undefined) {
  if (ms == null) return ''
  return ms < 1000 ? `${Math.round(ms)}ms` : `${(ms / 1000).toFixed(1)}s`
}

const stepsForDisplayedEvent = computed<ExecutionStep[]>(() => {
  const eventId = event.value?.id
  if (!eventId) return []
  return [...(artifact.value?.executionSteps?.[eventId] ?? [])].sort((a, b) => a.created_at.localeCompare(b.created_at))
})

interface ReplayJump {
  to: string
  label: string
}

function eventJumpLink(eventType: string | undefined, taskId: string): ReplayJump | null {
  if (!eventType) return null
  if (eventType === 'PLAN_APPROVED' || eventType === 'PLAN_REJECTED' || eventType === 'PLAN_REWRITTEN' || eventType.startsWith('ANALYSIS_')) {
    return { to: `/tasks/${taskId}`, label: '查看任务详情（计划/审批）' }
  }
  if (eventType === 'DIFF_GENERATED' || eventType === 'DIFF_GENERATION_FAILED') {
    return { to: `/diff/${taskId}`, label: '查看 Diff' }
  }
  if (eventType === 'TEST_RUN_PASSED' || eventType === 'TEST_RUN_FAILED' || eventType === 'TEST_BLOCKED') {
    return { to: `/diff/${taskId}`, label: '查看测试记录' }
  }
  if (eventType === 'PR_DESC_GENERATED' || eventType === 'PR_DESC_FAILED') {
    return { to: `/diff/${taskId}`, label: '查看 PR 描述' }
  }
  return null
}

const jumpLink = computed<ReplayJump | null>(() =>
  event.value && task.value ? eventJumpLink(event.value.eventType, task.value.id) : null,
)

async function loadTask() {
  const id = routeTaskId.value
  const seq = ++loadSeq
  if (!id) {
    loadingTask.value = false
    loadErrorKind.value = ''
    return
  }
  loadingTask.value = true
  loadErrorKind.value = ''
  try {
    // 刷新时先保留 store 里的旧任务画面，并与任务接口并行读取最新回放。
    // 任务接口返回后会用最新任务本地重建临时回放，不重复请求回放接口。
    if (isRealMode.value && task.value) void loadReplay()
    // 统一数据访问层：mock 走本地演示数据（不发请求），real 走后端并 upsert
    await store.ensureTaskLoaded(id)
    // 只允许最新一次请求（且组件未卸载）更新页面，路由切换后的旧响应直接丢弃
    if (disposed || seq !== loadSeq || routeTaskId.value !== id) return
    if (isRealMode.value) {
      if (replayRequestTaskId === id && replay.value?.summary?.source === 'fallback') applyLegacyReplay(id)
      else if (replayRequestTaskId !== id) await loadReplay()
    }
  } catch (error) {
    if (disposed || seq !== loadSeq) return
    const status = (error as { status?: number }).status
    if (status === 404) {
      loadErrorKind.value = 'missing'
    } else if (status === 401) {
      loadErrorKind.value = 'unauthorized'
    } else {
      loadErrorKind.value = 'unexpected'
      ElMessage.error(error instanceof Error ? error.message : '加载任务回放失败，请稍后重试')
    }
  } finally {
    if (seq === loadSeq) loadingTask.value = false
  }
}

onUnmounted(() => {
  disposed = true
})

function backToTask() {
  router.push(`/tasks/${task.value?.id ?? routeTaskId.value}`)
}

// ---------------------------------------------------------------------------
// DEV-020 真实 DB 回放：real 模式下只读调用 /replay 与 /replay/summary（不推进状态、不改审计）
// ---------------------------------------------------------------------------
const isRealMode = computed(() => getDataSourceMode() === 'real')
const replay = ref<TaskReplay | null>(null)
const summaryText = ref('')
const replayLoading = ref(false)
const replayError = ref('')
const replayLoaded = ref(false)
const copied = ref(false)
const activeReplayEventId = ref('')
const replayViewMode = ref<'overview' | 'audit'>('overview')
const replayMetadataExpanded = ref(false)
const auditStageFilter = ref<ReplayStageKey | 'all'>('all')
const auditAnomaliesOnly = ref(false)
const auditManualOnly = ref(false)

let replaySeq = 0
let replayRequestTaskId = ''

function formatReplayTime(value: string) {
  if (!value) return ''
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return value
  return new Intl.DateTimeFormat('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' }).format(d)
}

function replayStatusText(status: string) {
  if (status === 'completed') return '完成'
  if (status === 'failed') return '失败'
  if (status === 'skipped') return '跳过'
  if (status === 'running') return '进行中'
  return status
}

function replayChipClass(status: string) {
  if (status === 'failed') return 'replay-chip--fail'
  if (status === 'skipped') return 'replay-chip--blocked'
  if (status === 'running') return 'replay-chip--running'
  return 'replay-chip--pass'
}

function replayEventChipClass(event: ReplayTimelineEvent) {
  const type = event.event_type.toUpperCase()
  if (type === 'DIFF_GENERATED') return 'replay-chip--diff'
  if (type === 'DIFF_GENERATION_FAILED') return 'replay-chip--fail'
  if (type === 'TEST_RUN_STARTED') return 'replay-chip--running'
  if (type === 'TEST_RUN_PASSED') return 'replay-chip--pass'
  if (type === 'TEST_RUN_FAILED') return 'replay-chip--fail'
  if (type === 'TEST_BLOCKED') return 'replay-chip--blocked'
  if (type === 'PR_DESC_GENERATED') return 'replay-chip--pr'
  if (type === 'PR_DESC_FAILED') return 'replay-chip--fail'
  if (event.status === 'completed') return 'replay-chip--completed'
  return replayChipClass(event.status)
}

function replayEventChipLabel(event: ReplayTimelineEvent) {
  const type = event.event_type.toUpperCase()
  if (type === 'DIFF_GENERATED') return 'Diff 生成'
  if (type === 'DIFF_GENERATION_FAILED') return 'Diff 生成失败'
  if (type === 'TEST_RUN_STARTED') return '测试执行'
  if (type === 'TEST_RUN_PASSED') return '测试通过'
  if (type === 'TEST_RUN_FAILED') return '测试失败'
  if (type === 'TEST_BLOCKED') return '测试拦截'
  if (type === 'PR_DESC_GENERATED') return 'PR 描述生成'
  if (type === 'PR_DESC_FAILED') return 'PR 描述失败'
  return event.status === 'completed' ? '已完成' : replayStatusText(event.status)
}

function replayEventChipBaseClass(event: ReplayTimelineEvent) {
  return event.status === 'completed' && !['DIFF_GENERATED', 'TEST_RUN_PASSED', 'PR_DESC_GENERATED'].includes(event.event_type.toUpperCase())
    ? 'replay-event-status'
    : 'replay-chip'
}

const replayEvents = computed(() => replay.value?.timeline ?? [])
const failedReplayEvents = computed(() => replayEvents.value.filter((ev) => ev.status === 'failed'))
const latestFailedReplayEvent = computed(() => failedReplayEvents.value.at(-1) ?? null)

function replayMetadataValue(event: ReplayTimelineEvent | null, keys: string[]) {
  const metadata = event?.metadata
  if (!metadata) return null
  for (const key of keys) {
    const value = metadata[key]
    if (typeof value === 'string' || typeof value === 'number') return value
  }
  return null
}

const latestFailureExitCode = computed(() => replayMetadataValue(latestFailedReplayEvent.value, ['退出码', 'exit_code', 'exitCode']))
const latestFailureReason = computed(() => summarizeFailureReason(latestFailedReplayEvent.value?.failure_reason || task.value?.failureReason, latestFailureExitCode.value))

const replayStats = computed(() =>
  replayEvents.value.reduce(
    (acc, ev) => {
      acc.total += 1
      acc[ev.status] += 1
      return acc
    },
    { total: 0, completed: 0, failed: 0, running: 0, skipped: 0 } as Record<ReplayTimelineEvent['status'] | 'total', number>,
  ),
)

const replayStageOrder = [
  { key: 'intake', label: '需求接入' },
  { key: 'context', label: '上下文检索' },
  { key: 'approval', label: '计划审批' },
  { key: 'execution', label: '代码执行' },
  { key: 'testing', label: '测试验证' },
] as const

type ReplayStageKey = (typeof replayStageOrder)[number]['key']

const replayClosureLabel = computed(() => {
  if (latestFailedReplayEvent.value || replayStats.value.skipped) return '存在阻塞项'
  if (replayStats.value.running) return '处理中'
  if (replayStats.value.total) return '已闭合'
  return '暂无记录'
})

const replayClosureClass = computed(() => {
  if (latestFailedReplayEvent.value || replayStats.value.skipped) return 'is-failed'
  if (replayStats.value.running) return 'is-running'
  return 'is-completed'
})

const replayVerdictText = computed(() => {
  const failed = latestFailedReplayEvent.value
  if (failed) return `失败停在 ${failed.node || failed.event_type}，建议先处理失败原因，再重新触发对应节点。`
  if (replayStats.value.running) return '已有事件正在执行，等待节点完成后再检查 Diff、测试和 PR 产物。'
  if (!replayStats.value.total) return '后端还没有写入可展示的审计事件。'
  return '关键节点已串联完成，可继续查看原始摘要或完整事件证据。'
})

function replayStageKey(event: ReplayTimelineEvent): ReplayStageKey {
  const value = `${event.event_type} ${event.node} ${event.message}`.toUpperCase()
  if (value.includes('ISSUE') || value.includes('TASK_CREATED') || value.includes('需求') || value.includes('接入')) return 'intake'
  if (value.includes('ANALYSIS') || value.includes('RETRIEVAL') || value.includes('CONTEXT') || value.includes('上下文') || value.includes('检索')) return 'context'
  if (value.includes('PLAN') || value.includes('APPROV') || value.includes('REJECT') || value.includes('审批') || value.includes('计划')) return 'approval'
  if (value.includes('TEST') || value.includes('BLOCK') || value.includes('测试') || value.includes('拦截')) return 'testing'
  if (value.includes('DIFF') || value.includes('EXEC') || value.includes('PR') || value.includes('代码') || value.includes('执行')) return 'execution'
  return 'context'
}

function replayStageText(eventType: string, node: string) {
  const value = `${eventType} ${node}`.toUpperCase()
  if (value.includes('ISSUE') || value.includes('TASK_CREATED')) return '需求接入'
  if (value.includes('ANALYSIS') || value.includes('RETRIEVAL') || value.includes('CONTEXT')) return '上下文检索'
  if (value.includes('PLAN') || value.includes('APPROV')) return '计划审批'
  if (value.includes('TEST') || value.includes('BLOCK')) return '测试验证'
  if (value.includes('DIFF') || value.includes('EXEC') || value.includes('PR')) return '代码执行'
  return '上下文检索'
}

function replayEventType(ev: ReplayTimelineEvent) {
  return ev.event_type.toUpperCase()
}

function isManualReplayEvent(ev: ReplayTimelineEvent) {
  const type = replayEventType(ev)
  const actor = `${ev.actor_type} ${ev.actor_name}`.toLowerCase()
  return actor.includes('user') || actor.includes('human') || actor.includes('manual') || type.includes('APPROV') || type.includes('REJECT') || type.includes('ANSWER') || type.includes('COMMENT')
}

function isReplayAnomaly(ev: ReplayTimelineEvent) {
  const type = replayEventType(ev)
  return ev.status === 'failed' || ev.status === 'skipped' || ev.status === 'running' || type.includes('FAILED') || type.includes('BLOCK') || type.includes('REJECT')
}

function isReplayEventExpanded(ev: ReplayTimelineEvent) {
  return isReplayAnomaly(ev) || isManualReplayEvent(ev) || replayEventType(ev).includes('TEST_RUN_FAILED')
}

function replayEventEvidence(ev: ReplayTimelineEvent | null) {
  if (!ev) return '暂无关键证据'
  if (ev.failure_reason) return summarizeFailureReason(ev.failure_reason, replayMetadataValue(ev, ['退出码', 'exit_code', 'exitCode']))
  if (ev.metadata?.legacy_detail && typeof ev.metadata.legacy_detail === 'string') return ev.metadata.legacy_detail
  const metadata = ev.metadata ?? {}
  const entry = Object.entries(metadata).find(([, value]) => typeof value === 'string' || typeof value === 'number')
  if (entry) return `${entry[0]}：${entry[1]}`
  return ev.message || '事件已记录'
}

function replayStageOutcome(events: ReplayTimelineEvent[]) {
  if (!events.length) return { label: '未发生', className: 'is-empty' }
  if (events.some((event) => event.status === 'failed' || replayEventType(event).includes('FAILED'))) return { label: '存在异常', className: 'is-failed' }
  if (events.some((event) => event.status === 'running')) return { label: '进行中', className: 'is-running' }
  if (events.some((event) => event.status === 'skipped' || replayEventType(event).includes('BLOCK'))) return { label: '已拦截', className: 'is-warning' }
  return { label: '已完成', className: 'is-completed' }
}

const replayStageSummaries = computed(() =>
  replayStageOrder.map((stage) => {
    const events = replayEvents.value.filter((event) => replayStageKey(event) === stage.key)
    const latest = events.at(-1) ?? null
    const outcome = replayStageOutcome(events)
    return {
      ...stage,
      events,
      total: events.length,
      latest,
      latestAt: latest ? formatReplayTime(latest.occurred_at) : '—',
      evidence: replayEventEvidence(latest),
      conclusion: outcome.label,
      outcomeClass: outcome.className,
    }
  }),
)

const replayStageGroups = computed(() =>
  replayStageSummaries.value
    .map((stage) => {
      const events = stage.events
        .filter((event) => auditStageFilter.value === 'all' || replayStageKey(event) === auditStageFilter.value)
        .filter((event) => !auditAnomaliesOnly.value || isReplayAnomaly(event))
        .filter((event) => !auditManualOnly.value || isManualReplayEvent(event))
      return { ...stage, events }
    })
    .filter((stage) => stage.events.length > 0),
)
const replayStageCount = computed(() => replayStageOrder.length)
const auditReplayEvents = computed(() => replayStageGroups.value.flatMap((stage) => stage.events))
const auditFilterActive = computed(() => auditStageFilter.value !== 'all' || auditAnomaliesOnly.value || auditManualOnly.value)
const auditEventCount = computed(() => auditReplayEvents.value.length)
const replayTestEvent = computed(() =>
  [...replayEvents.value].reverse().find((event) => {
    const type = replayEventType(event)
    return type.includes('TEST') || type.includes('BLOCK') || `${event.node} ${event.message}`.includes('测试')
  }) ?? null,
)
const replayTestConclusion = computed(() => {
  const event = replayTestEvent.value
  if (!event) return { label: '未执行', className: 'is-empty', evidence: '暂无测试记录' }
  const type = replayEventType(event)
  if (type.includes('PASSED')) return { label: '测试通过', className: 'is-completed', evidence: replayEventEvidence(event) }
  if (type.includes('FAILED')) return { label: '测试失败', className: 'is-failed', evidence: replayEventEvidence(event) }
  if (type.includes('BLOCK')) return { label: '测试被拦截', className: 'is-warning', evidence: replayEventEvidence(event) }
  return { label: '测试执行中', className: 'is-running', evidence: replayEventEvidence(event) }
})
const activeReplayEvent = computed(() => replayEvents.value.find((ev) => ev.id === activeReplayEventId.value) ?? latestFailedReplayEvent.value ?? replayEvents.value.at(-1) ?? null)
const stepsForActiveReplayEvent = computed<ExecutionStep[]>(() => {
  const eventId = activeReplayEvent.value?.id
  if (!eventId) return []
  return [...(artifact.value?.executionSteps?.[eventId] ?? [])].sort((a, b) => a.created_at.localeCompare(b.created_at))
})
const replaySummaryMeta = computed(() => {
  const summary = replay.value?.summary
  return summary ? `${summary.source || 'deterministic'} · ${summary.input_fingerprint || '暂无指纹'}` : '暂无摘要元数据'
})
const activeReplayJump = computed<ReplayJump | null>(() =>
  activeReplayEvent.value && task.value ? eventJumpLink(activeReplayEvent.value.event_type, task.value.id) : null,
)
const latestFailureJump = computed<ReplayJump | null>(() =>
  latestFailedReplayEvent.value && task.value ? eventJumpLink(latestFailedReplayEvent.value.event_type, task.value.id) : null,
)

function selectReplayEvent(id: string) {
  activeReplayEventId.value = id
}

function openAudit(stage?: ReplayStageKey) {
  replayViewMode.value = 'audit'
  auditStageFilter.value = stage ?? 'all'
}

function resetAuditFilters() {
  auditStageFilter.value = 'all'
  auditAnomaliesOnly.value = false
  auditManualOnly.value = false
}

function eventMetadataText(ev: ReplayTimelineEvent | null) {
  if (!ev || !ev.metadata || !Object.keys(ev.metadata).length) return ''
  return JSON.stringify(ev.metadata, null, 2)
}

function createLegacyReplayEvent(event: TimelineEvent, taskId: string): ReplayTimelineEvent {
  const status = event.status === 'failed' ? 'failed' : event.status === 'active' ? 'running' : event.status === 'waiting' ? 'skipped' : 'completed'
  return {
    id: event.id,
    event_type: event.eventType || 'TASK_EVENT',
    occurred_at: event.time,
    actor_id: 'system',
    actor_name: 'Agent 自动化',
    actor_type: 'system',
    status,
    node: event.eventType || event.title,
    message: event.title,
    failure_reason: status === 'failed' ? event.detail : null,
    retry_count: 0,
    metadata: {
      legacy_detail: event.detail,
      legacy_summary: event.summary || '',
      demo_fallback: event.demoFallback ?? false,
    },
    relations: { task_id: taskId },
  }
}

function createLegacyReplay(taskId: string): TaskReplay {
  const timeline = (artifact.value?.timeline ?? []).map((event) => createLegacyReplayEvent(event, taskId))
  return {
    task_id: taskId,
    generated_at: new Date().toISOString(),
    timeline,
    artifacts: { plans: [], diffs: [], test_runs: [], pr_descriptions: [], comments: [] },
    summary: {
      text: timeline.map((event) => `${event.message}\n${event.metadata.legacy_detail || ''}`).join('\n\n'),
      source: 'fallback',
      input_fingerprint: 'legacy-task-events',
      generated_at: new Date().toISOString(),
    },
  }
}

function applyLegacyReplay(taskId: string) {
  const fallback = createLegacyReplay(taskId)
  replay.value = fallback
  summaryText.value = fallback.summary.text
  activeReplayEventId.value = fallback.timeline.find((ev) => ev.status === 'failed')?.id ?? fallback.timeline.at(-1)?.id ?? ''
  replayLoaded.value = true
}

async function copySummary() {
  const text = summaryText.value
  if (!text) return
  copied.value = false
  try {
    if (!navigator.clipboard || !navigator.clipboard.writeText) {
      throw new Error('unsupported')
    }
    await navigator.clipboard.writeText(text)
    copied.value = true
    ElMessage.success('回放摘要已复制')
    setTimeout(() => (copied.value = false), 1600)
  } catch {
    // 浏览器不支持 Clipboard API 时提供选区人工复制兜底
    ElMessage.info('当前浏览器不支持自动复制，请长按摘要区域手动选择复制')
  }
}

async function loadReplay() {
  const id = routeTaskId.value
  const seq = ++replaySeq
  if (!id || !isRealMode.value || !task.value) {
    replayLoaded.value = false
    return
  }
  if (replayRequestTaskId === id) return
  replayRequestTaskId = id
  replayLoading.value = true
  replayError.value = ''
  if (task.value) applyLegacyReplay(id)
  try {
    // 摘要直接使用 /replay 返回的真实 summary（确定性模板），同权限、同真源
    const replayData = await apiRequest<TaskReplay>(`/api/tasks/${id}/replay`)
    if (disposed || seq !== replaySeq || routeTaskId.value !== id) return
    const hasTimeline = Array.isArray((replayData as Partial<TaskReplay> | undefined)?.timeline)
    const normalizedReplay = hasTimeline ? replayData : createLegacyReplay(id)
    replay.value = normalizedReplay
    summaryText.value = normalizedReplay.summary?.text ?? ''
    activeReplayEventId.value = normalizedReplay.timeline.find((ev) => ev.status === 'failed')?.id ?? normalizedReplay.timeline.at(-1)?.id ?? ''
    replayLoaded.value = true
  } catch (error) {
    if (disposed || seq !== replaySeq) return
    replayError.value = error instanceof Error ? error.message : '回放加载失败'
    replayLoaded.value = false
  } finally {
    if (seq === replaySeq) replayLoading.value = false
  }
}

watch(routeTaskId, () => {
  replayRequestTaskId = ''
  replayViewMode.value = 'overview'
  replayMetadataExpanded.value = false
  resetAuditFilters()
  void loadTask()
}, { immediate: true })

</script>

<template>
  <section class="page-content replay-page">
    <template v-if="errorView">
      <EmptyPanel :title="errorView.title" :description="errorView.description" action="返回任务列表" @action="router.push('/tasks')" />
    </template>
    <template v-else-if="!task">
      <EmptyPanel
        title="回放数据加载中"
        :description="routeTaskId ? '正在从后端读取任务与审计事件，请稍候。' : '请先从任务列表选择一个任务。'"
        :action="routeTaskId ? undefined : '返回任务列表'"
        @action="router.push('/tasks')"
      />
    </template>
    <template v-else>
      <div class="page-toolbar">
        <p><strong>{{ task.id }}</strong> · {{ task.title }}</p>
        <div class="tag-row">
          <StatusTag :status="task.status" />
          <StatusTag :risk="task.risk" />
        </div>
      </div>
      <!-- DEV-020 真实 DB 回放：real 模式只读调用 /replay 与 /replay/summary -->
      <template v-if="isRealMode">
        <section class="panel replay-real">
            <header class="panel__header replay-real__head">
              <div>
                <h2>回放摘要</h2>
              <p v-if="replay">
                确定性模板 · 指纹 {{ replay.summary?.input_fingerprint ?? '暂无记录' }} · 生成于 {{ formatReplayTime(replay.summary?.generated_at ?? replay.generated_at) }}
              </p>
              <p v-else-if="replayLoading">正在从后端读取回放数据…</p>
            </div>
          </header>

          <div v-if="replayLoading && !replayLoaded" class="replay-real__loading">加载中…</div>
          <div v-else-if="replayError" class="replay-real__empty">回放加载失败：{{ replayError }}</div>
          <div v-else-if="!replayLoaded" class="replay-real__empty">暂无回放记录</div>
          <template v-else>
            <div v-if="replayViewMode === 'overview'" class="replay-real__overview-shell">
              <section class="replay-real__decision">
                <div class="replay-real__decision-main">
                  <div class="replay-real__decision-signal" :class="replayClosureClass">
                    <CheckCircle2 v-if="replayClosureClass === 'is-completed'" :size="18" />
                    <TriangleAlert v-else :size="18" />
                  </div>
                  <div>
                    <p class="replay-real__eyebrow">回放结论</p>
                    <h3>{{ replayClosureLabel }}</h3>
                    <p>{{ replayVerdictText }}</p>
                  </div>
                </div>
                <div class="replay-real__decision-actions">
                  <button class="button button--primary button--sm" type="button" @click="openAudit()">
                    查看全部事件 <ChevronRight :size="15" />
                  </button>
                  <button class="button button--secondary button--sm" type="button" :disabled="!summaryText" @click="copySummary">
                    <Copy v-if="!copied" :size="15" /><Check v-else :size="15" /> 复制摘要
                  </button>
                </div>
              </section>

              <section v-if="latestFailedReplayEvent" class="replay-real__failure">
                <div class="replay-real__failure-copy">
                  <p class="replay-real__eyebrow">需要处理</p>
                  <strong>失败节点：{{ latestFailedReplayEvent.node || latestFailedReplayEvent.event_type }}</strong>
                  <p>失败原因：{{ latestFailureReason }}</p>
                </div>
                <div class="replay-real__failure-actions">
                  <div class="replay-real__failure-meta">
                    <span>{{ formatReplayTime(latestFailedReplayEvent.occurred_at) }}</span>
                    <span>{{ latestFailedReplayEvent.actor_name }}（{{ latestFailedReplayEvent.actor_type }}）</span>
                    <span v-if="latestFailedReplayEvent.retry_count">重试 {{ latestFailedReplayEvent.retry_count }}</span>
                  </div>
                  <RouterLink v-if="latestFailureJump" :to="latestFailureJump.to" class="button button--secondary button--sm">
                    {{ latestFailureJump.label }}
                  </RouterLink>
                </div>
              </section>

              <dl class="replay-real__metric-line" aria-label="回放关键指标">
                <div :class="replayClosureClass"><dt>链路状态</dt><dd>{{ replayClosureLabel }}</dd></div>
                <div><dt>失败节点</dt><dd>{{ replayStats.failed }}</dd></div>
                <div><dt>审计事件</dt><dd>{{ replayStats.total }}</dd></div>
                <div><dt>阶段</dt><dd>{{ replayStageCount }}</dd></div>
              </dl>

              <section class="replay-real__test-conclusion">
                <div>
                  <p class="replay-real__eyebrow">最终测试结论</p>
                  <h3 :class="replayTestConclusion.className">{{ replayTestConclusion.label }}</h3>
                </div>
                <p>{{ replayTestConclusion.evidence }}</p>
                <button class="button button--ghost button--sm" type="button" @click="openAudit('testing')">
                  查看测试记录 <ChevronRight :size="14" />
                </button>
              </section>

              <section class="replay-real__stage-summary" aria-labelledby="replay-stage-summary-title">
                <header class="replay-real__section-head">
                  <div>
                    <p class="replay-real__eyebrow">流程阶段</p>
                    <h3 id="replay-stage-summary-title">五阶段交付摘要</h3>
                  </div>
                  <span>按阶段进入审计记录</span>
                </header>
                <div class="replay-real__stage-rows">
                  <button
                    v-for="stage in replayStageSummaries"
                    :key="stage.key"
                    type="button"
                    class="replay-real__stage-row"
                    :class="stage.outcomeClass"
                    @click="openAudit(stage.key)"
                  >
                    <span class="replay-real__stage-mark" />
                    <span class="replay-real__stage-name">{{ stage.label }}</span>
                    <span class="replay-real__stage-conclusion">{{ stage.conclusion }}</span>
                    <span class="replay-real__stage-evidence">{{ stage.evidence }}</span>
                    <span class="replay-real__stage-time mono">{{ stage.latestAt }}</span>
                    <span class="replay-real__stage-count">{{ stage.total }} 条事件</span>
                    <ChevronRight :size="15" class="replay-real__stage-arrow" />
                  </button>
                </div>
              </section>

              <section class="replay-real__metadata">
                <button type="button" class="replay-real__metadata-toggle" :aria-expanded="replayMetadataExpanded" @click="replayMetadataExpanded = !replayMetadataExpanded">
                  <span>
                    <span class="replay-real__eyebrow">任务元信息</span>
                    <strong>{{ task.projectName || '未指定项目' }} · {{ task.repository }} · {{ task.branch || 'main' }}</strong>
                  </span>
                  <ChevronDown :size="16" :class="{ 'is-open': replayMetadataExpanded }" />
                </button>
                <dl v-if="replayMetadataExpanded" class="replay-real__metadata-grid">
                  <div><dt>项目</dt><dd>{{ task.projectName || '未指定项目' }}</dd></div>
                  <div><dt>仓库</dt><dd>{{ task.repository }}</dd></div>
                  <div><dt>工作分支</dt><dd class="mono">{{ task.branch || 'main' }}</dd></div>
                  <div><dt>摘要来源</dt><dd>{{ replaySummaryMeta }}</dd></div>
                </dl>
              </section>
            </div>

            <div v-else class="replay-real__audit">
              <header class="replay-real__audit-head">
                <div>
                  <p class="replay-real__eyebrow">完整审计</p>
                  <h3>全部事件记录</h3>
                  <p>保留节点、操作者、精确时间、事件码与原始元数据；异常和人工操作默认展开。</p>
                </div>
                <div class="replay-real__audit-actions">
                  <button class="button button--ghost button--sm" type="button" @click="replayViewMode = 'overview'">
                    <ChevronDown :size="14" class="replay-real__back-icon" /> 返回概览
                  </button>
                  <button class="button button--secondary button--sm" type="button" :disabled="!summaryText" @click="copySummary">
                    <Copy v-if="!copied" :size="15" /><Check v-else :size="15" /> 复制摘要
                  </button>
                </div>
              </header>

              <div class="replay-real__audit-filters" aria-label="审计筛选">
                <label>
                  <span>阶段</span>
                  <select v-model="auditStageFilter">
                    <option value="all">全部阶段</option>
                    <option v-for="stage in replayStageSummaries" :key="stage.key" :value="stage.key">{{ stage.label }}（{{ stage.total }}）</option>
                  </select>
                </label>
                <button type="button" class="replay-real__filter-toggle" :class="{ 'is-active': auditAnomaliesOnly }" :aria-pressed="auditAnomaliesOnly" @click="auditAnomaliesOnly = !auditAnomaliesOnly">
                  <TriangleAlert :size="14" /> 只看异常
                </button>
                <button type="button" class="replay-real__filter-toggle" :class="{ 'is-active': auditManualOnly }" :aria-pressed="auditManualOnly" @click="auditManualOnly = !auditManualOnly">
                  <CheckCircle2 :size="14" /> 只看人工操作
                </button>
                <button v-if="auditFilterActive" type="button" class="text-button replay-real__filter-reset" @click="resetAuditFilters">清除筛选</button>
                <span class="replay-real__audit-count">显示 {{ auditEventCount }} / {{ replayStats.total }} 条</span>
              </div>

              <div v-if="!auditEventCount" class="replay-real__audit-empty">当前筛选条件下没有事件。</div>
              <section
                v-for="stage in replayStageGroups"
                :id="`replay-stage-${stage.key}`"
                :key="stage.key"
                class="replay-real__stage-group"
              >
                <header class="replay-real__stage-head">
                  <div>
                    <span class="replay-real__stage-mark" :class="stage.outcomeClass" />
                    <h4>{{ stage.label }}</h4>
                  </div>
                  <span>{{ stage.events.length }} 条事件</span>
                </header>
                <ol class="replay-real__timeline">
                  <li v-for="ev in stage.events" :key="ev.id">
                    <details
                      class="replay-real__audit-event"
                      :class="[`is-${ev.status}`, { 'is-selected': ev.id === activeReplayEventId, 'is-manual': isManualReplayEvent(ev), 'is-expanded': isReplayEventExpanded(ev) }]"
                      :open="isReplayEventExpanded(ev)"
                    >
                      <summary class="replay-real__audit-summary" @click="selectReplayEvent(ev.id)">
                        <span class="replay-real__dot" :class="`is-${ev.status}`" />
                        <span :class="[replayEventChipBaseClass(ev), replayEventChipClass(ev)]">{{ replayEventChipLabel(ev) }}</span>
                        <span v-if="isManualReplayEvent(ev)" class="replay-chip replay-chip--manual">人工操作</span>
                        <span v-if="ev.event_type === 'DIFF_GENERATED' && ev.metadata?.demo_fallback" class="replay-chip replay-chip--demo">演示兜底</span>
                        <code class="replay-real__type">{{ ev.event_type }}</code>
                        <strong class="replay-real__msg">{{ ev.message }}</strong>
                        <ChevronDown :size="14" class="replay-real__chev" />
                      </summary>
                      <div class="replay-real__meta mono">
                        <span>{{ formatReplayTime(ev.occurred_at) }}</span>
                        <span>节点：{{ ev.node }}</span>
                        <span>操作者：{{ ev.actor_name }}（{{ ev.actor_type }}）</span>
                        <span v-if="ev.retry_count">重试 {{ ev.retry_count }}</span>
                      </div>
                      <p v-if="ev.metadata?.legacy_detail" class="replay-real__legacy-summary">{{ ev.metadata.legacy_detail }}</p>
                      <div v-if="ev.status === 'failed'" class="replay-real__fail">
                        <p><b>失败原因：</b>{{ ev.failure_reason || '暂无记录' }}</p>
                        <p><b>节点：</b>{{ ev.node }}</p>
                        <p><b>重试次数：</b>{{ ev.retry_count ?? 0 }}</p>
                        <p v-if="ev.metadata?.execution_env"><b>执行环境：</b>{{ ev.metadata.execution_env }}</p>
                        <pre v-if="Array.isArray(ev.metadata?.execution_steps) && ev.metadata.execution_steps.length" class="replay-real__steps">{{ JSON.stringify(ev.metadata.execution_steps, null, 2) }}</pre>
                      </div>
                    </details>
                  </li>
                </ol>
              </section>

              <section v-if="activeReplayEvent" class="panel replay-detail replay-real__detail">
                <header class="panel__header">
                  <div>
                    <p class="section-kicker">事件详情</p>
                      <h2>{{ activeReplayEvent.message }}</h2>
                    </div>
                    <span class="status-tag" :class="activeReplayEvent.status === 'failed' ? 'danger' : activeReplayEvent.status === 'completed' ? 'success' : 'info'">{{ replayStatusText(activeReplayEvent.status) }}</span>
                  </header>
                  <div class="replay-detail__summary"><Clock3 :size="17" /><span>{{ activeReplayEvent.message }}</span></div>
                  <section class="replay-block">
                    <header><ChevronDown :size="16" /> 审计信息</header>
                    <dl class="compact-dl">
                      <div><dt>阶段</dt><dd>{{ replayStageText(activeReplayEvent.event_type, activeReplayEvent.node) }}</dd></div>
                      <div><dt>执行节点</dt><dd class="mono">{{ activeReplayEvent.node }}</dd></div>
                      <div><dt>操作者</dt><dd>{{ activeReplayEvent.actor_name }}（{{ activeReplayEvent.actor_type }}）</dd></div>
                      <div><dt>发生时间</dt><dd class="mono">{{ formatReplayTime(activeReplayEvent.occurred_at) }}</dd></div>
                    </dl>
                  </section>
                  <section v-if="activeReplayEvent.failure_reason" class="replay-block replay-block--failure">
                    <header><ShieldAlert :size="16" /> 失败原因</header>
                    <p class="replay-detail__failure-text">{{ summarizeFailureReason(activeReplayEvent.failure_reason, replayMetadataValue(activeReplayEvent, ['退出码', 'exit_code', 'exitCode'])) }}</p>
                    <details class="replay-failure-evidence">
                      <summary>查看完整失败输出</summary>
                      <p>{{ activeReplayEvent.failure_reason }}</p>
                    </details>
                  </section>
                  <section v-if="eventMetadataText(activeReplayEvent)" class="replay-block">
                    <header><Braces :size="16" /> 事件元数据</header>
                    <pre>{{ eventMetadataText(activeReplayEvent) }}</pre>
                  </section>
                  <section v-if="stepsForActiveReplayEvent.length" class="replay-block replay-block--steps">
                    <header><Play :size="16" /> 执行步骤 <em class="replay-steps-count">{{ stepsForActiveReplayEvent.length }}</em></header>
                    <div v-for="step in stepsForActiveReplayEvent" :key="step.id" class="step-card" :class="`step-card--${step.status}`">
                      <div class="step-card__head">
                        <strong class="step-card__node mono">{{ step.node }}</strong>
                        <span class="step-card__title">{{ step.title }}</span>
                        <span class="step-status" :class="`is-${step.status}`">{{ stepStatusText(step.status) }}</span>
                        <small v-if="formatStepDuration(step.duration_ms)" class="step-card__duration">耗时 {{ formatStepDuration(step.duration_ms) }}</small>
                      </div>
                      <template v-if="step.input">
                        <p class="step-card__label">输入</p>
                        <pre class="step-card__pre">{{ step.input }}</pre>
                      </template>
                      <template v-if="step.output">
                        <p class="step-card__label">输出</p>
                        <pre class="step-card__pre">{{ step.output }}</pre>
                      </template>
                      <p v-if="step.status === 'failed' && step.failure_reason" class="detail-test-note detail-test-note--failed">失败原因：{{ step.failure_reason }}</p>
                      <p v-else-if="step.status === 'blocked' && step.blocked_reason" class="detail-test-note detail-test-note--blocked">拦截原因：{{ step.blocked_reason }}</p>
                    </div>
                  </section>
                  <RouterLink v-if="activeReplayJump" :to="activeReplayJump.to" class="replay-jump">
                    <Link2 :size="15" />{{ activeReplayJump.label }}
                  </RouterLink>
                </section>

                <details class="replay-raw-summary">
                  <summary>
                    <span>原始回放摘要</span>
                    <small>完整 Markdown 原文</small>
                  </summary>
                  <pre class="replay-summary__pre">{{ summaryText || '暂无记录' }}</pre>
                </details>
            </div>
          </template>
        </section>
      </template>

      <template v-else>
        <EmptyPanel
          v-if="showEmptyState"
          :title="emptyTitle"
          :description="emptyDescription"
          action="返回任务详情"
          @action="backToTask"
        />
        <div v-else class="replay-grid">
        <aside class="panel replay-timeline">
          <header class="panel__header">
            <div>
              <h2>执行时间线</h2>
              <p>按节点展开输入与输出</p>
            </div>
            <span class="replay-legend">
              <i class="legend-dot legend-dot--done" />已完成
              <i class="legend-dot legend-dot--active" />进行中
              <i class="legend-dot legend-dot--waiting" />待执行
              <i class="legend-dot legend-dot--failed" />失败
            </span>
          </header>
          <ol class="replay-steps">
            <li v-for="item in artifact?.timeline ?? []" :key="item.id">
              <button
                type="button"
                class="replay-steps__item"
                :class="[`replay-steps__item--${item.status}`, { 'is-selected': item.id === activeEvent }]"
                @click="activeEvent = item.id"
              >
                <span class="replay-steps__badge" aria-hidden="true" />
                <div class="replay-steps__body">
                  <div class="replay-steps__head">
                    <div class="replay-steps__title">
                      <strong :title="item.title">{{ item.title }}</strong>
                      <em v-if="item.eventType === 'DIFF_GENERATED'" class="replay-legacy-chip replay-chip--diff">Diff 生成</em>
                      <em v-else-if="item.eventType === 'DIFF_GENERATION_FAILED'" class="replay-legacy-chip replay-chip--fail">Diff 生成失败</em>
                      <em v-else-if="item.eventType === 'TEST_RUN_STARTED'" class="replay-legacy-chip replay-chip--running">测试执行</em>
                      <em v-else-if="item.eventType === 'TEST_RUN_PASSED'" class="replay-legacy-chip replay-chip--pass">测试通过</em>
                      <em v-else-if="item.eventType === 'TEST_RUN_FAILED'" class="replay-legacy-chip replay-chip--fail">测试失败</em>
                      <em v-else-if="item.eventType === 'TEST_BLOCKED'" class="replay-legacy-chip replay-chip--blocked">测试拦截</em>
                      <em v-else-if="item.eventType === 'PR_DESC_GENERATED'" class="replay-legacy-chip replay-chip--pr">PR 描述生成</em>
                      <em v-else-if="item.eventType === 'PR_DESC_FAILED'" class="replay-legacy-chip replay-chip--fail">PR 描述失败</em>
                      <em v-if="item.demoFallback" class="replay-legacy-chip replay-chip--demo">演示兜底</em>
                    </div>
                    <div class="replay-steps__meta">
                      <span class="replay-steps__state" :class="`is-${item.status}`">{{ timelineStatusLabel(item.status) }}</span>
                      <small class="replay-steps__time">{{ item.time }}</small>
                      <small v-if="item.duration" class="replay-steps__duration">{{ item.duration }}</small>
                    </div>
                  </div>
                  <small v-if="item.summary" class="replay-steps__summary">{{ item.summary }}</small>
                </div>
              </button>
            </li>
          </ol>
        </aside>
        <main v-if="event" class="panel replay-detail">
          <header class="panel__header">
            <div>
              <p class="section-kicker">步骤详情</p>
              <h2>{{ event.title }}</h2>
            </div>
            <span class="status-tag" :class="event.status">{{ event.status === 'done' ? '已完成' : event.status === 'active' ? '进行中' : event.status === 'failed' ? '执行失败' : '等待执行' }}</span>
          </header>
          <div class="replay-detail__summary"><Clock3 :size="17" /><span>{{ event.detail }}</span></div>
          <section v-if="event.input" class="replay-block">
            <header><FileSearch :size="16" /> 检索输入</header>
            <pre>{{ event.input }}</pre>
          </section>
          <section v-if="event.output" class="replay-block">
            <header><Braces :size="16" /> 检索结果</header>
            <div class="retrieval-table">
              <div v-for="item in artifact?.relatedCode.slice(0, 2) ?? []" :key="item.path">
                <code>{{ item.path }}:{{ item.range }}</code>
                <span>相似度 {{ item.score }}</span>
              </div>
            </div>
          </section>
          <section class="replay-block">
            <header><TerminalSquare :size="16" /> 节点输出</header>
            <pre>{{ event.status === 'active' ? artifact?.replayNote : event.output || artifact?.replayResult }}</pre>
          </section>
          <section v-if="stepsForDisplayedEvent.length" class="replay-block replay-block--steps">
            <header><Play :size="16" /> 执行步骤 <em class="replay-steps-count">{{ stepsForDisplayedEvent.length }}</em></header>
            <div v-for="step in stepsForDisplayedEvent" :key="step.id" class="step-card" :class="`step-card--${step.status}`">
              <div class="step-card__head">
                <strong class="step-card__node mono">{{ step.node }}</strong>
                <span class="step-card__title">{{ step.title }}</span>
                <span class="step-status" :class="`is-${step.status}`">{{ stepStatusText(step.status) }}</span>
                <small v-if="formatStepDuration(step.duration_ms)" class="step-card__duration">耗时 {{ formatStepDuration(step.duration_ms) }}</small>
              </div>
              <template v-if="step.input">
                <p class="step-card__label">输入</p>
                <pre class="step-card__pre">{{ step.input }}</pre>
              </template>
              <template v-if="step.output">
                <p class="step-card__label">输出</p>
                <pre class="step-card__pre">{{ step.output }}</pre>
              </template>
              <p v-if="step.status === 'failed' && step.failure_reason" class="detail-test-note detail-test-note--failed">失败原因：{{ step.failure_reason }}</p>
              <p v-else-if="step.status === 'blocked' && step.blocked_reason" class="detail-test-note detail-test-note--blocked">拦截原因：{{ step.blocked_reason }}</p>
            </div>
          </section>
          <section class="replay-block">
            <header><ChevronDown :size="16" /> 审计信息</header>
            <dl class="compact-dl">
              <div><dt>执行节点</dt><dd class="mono">{{ artifact?.auditNode }}</dd></div>
              <div><dt>关联任务</dt><dd class="mono">{{ task.id }}</dd></div>
              <div><dt>执行环境</dt><dd>正式流程</dd></div>
            </dl>
          </section>
          <RouterLink v-if="jumpLink" :to="jumpLink.to" class="replay-jump">
            <Link2 :size="15" />{{ jumpLink.label }}
          </RouterLink>
        </main>
        </div>
      </template>
    </template>
  </section>
</template>
