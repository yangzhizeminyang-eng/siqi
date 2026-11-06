<script setup lang="ts">
import { Ban, CheckCircle2, ChevronDown, FileDiff, FlaskConical, MessageSquareText, RotateCcw, Send, ShieldAlert } from '@lucide/vue'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useRoute, useRouter } from 'vue-router'
import { ApiError, apiRequest } from '../services/api'
import EmptyPanel from '../components/EmptyPanel.vue'
import CodeContextPanel from '../components/CodeContextPanel.vue'
import ModificationPlanPanel from '../components/ModificationPlanPanel.vue'
import TaskCommentsPanel from '../components/TaskCommentsPanel.vue'
import HistoricalTasksPanel from '../components/HistoricalTasksPanel.vue'
import { useAuthStore } from '../stores/auth'
import { useWorkspaceStore } from '../stores/workspace'
import StatusTag from '../components/StatusTag.vue'
import type { AgentTask, AnalysisQuestion, AnalysisRelevance, AnalysisSourceType, TaskStatus } from '../types/domain'
import { statusText } from '../types/domain'
import { connectRealtime, subscribeRealtime } from '../services/realtime'
import { getDataSourceMode } from '../services/dataSourceMode'
import { summarizeFailureReason } from '../utils/failureReason'

const phaseSteps = ['Issue', '检索', '分析', '审批', 'Diff', '测试', '完成']
const diffReadyStatuses: TaskStatus[] = ['APPROVED', 'DIFF_GENERATED', 'TEST_RUNNING', 'TEST_FAILED', 'TESTED', 'COMPLETED']
const phaseIndexMap: Record<TaskStatus, number> = {
  CREATED: 0,
  ISSUE_FETCHED: 0,
  CONTEXT_INDEXED: 1,
  ANALYSIS_QUEUED: 2,
  ANALYZED: 2,
  PLAN_GENERATED: 2,
  WAITING_APPROVAL: 3,
  APPROVED: 3,
  DIFF_GENERATED: 4,
  TEST_RUNNING: 5,
  TEST_FAILED: 5,
  TESTED: 5,
  COMPLETED: 6,
  FAILED: 1,
  CANCELLED: 3,
}

const store = useWorkspaceStore()
const auth = useAuthStore()
const route = useRoute()
const router = useRouter()
const routeTaskId = computed(() => {
  const value = route.params.id
  return Array.isArray(value) ? value[0] : value
})
const task = computed(() => (routeTaskId.value ? store.taskById(routeTaskId.value) ?? null : null))
const artifact = computed(() => store.artifactFor(task.value ? task.value.id : ''))
// 真实 API 任务由 mapAgentTask 写入 retryCount；本地 mock 任务没有该字段，用于区分展示口径
const isApiTask = computed(() => Boolean(task.value && task.value.retryCount !== undefined))
const loadErrorKind = ref<'' | 'missing' | 'unauthorized'>('')
const loadError = computed(() => {
  if (loadErrorKind.value === 'unauthorized') {
    return { title: '登录状态已失效', description: '请重新登录后再查看任务详情。' }
  }
  if (loadErrorKind.value === 'missing') {
    return { title: '任务不存在或无权查看', description: '任务可能已被删除，或当前账号没有查看权限。' }
  }
  return null
})
const showEmptyState = computed(() => {
  const current = task.value
  if (!current) return false
  return isApiTask.value
    ? current.status === 'CREATED'
    : ['CREATED', 'ISSUE_FETCHED', 'CONTEXT_INDEXED', 'ANALYSIS_QUEUED'].includes(current.status) || (artifact.value.isPlaceholder && current.status !== 'FAILED')
})
const recentEvents = computed(() => artifact.value.timeline.slice(-4))
const answer = ref('')
const questionSaved = ref(false)
const questionSubmittedAt = ref('')
const answerDrafts = ref<Record<string, string>>({})
const submitting = ref(false)
const syncing = ref(false)
const analysisExpanded = ref(getDataSourceMode() === 'mock')
const secondarySourcesExpanded = ref(false)
const phaseIndex = computed(() => {
  const current = task.value
  if (!current) return 0
  return phaseIndexMap[current.status] ?? 0
})
const currentPhase = computed(() => phaseSteps[phaseIndex.value] ?? 'Issue')
const hasRealAnalysis = computed(() => Boolean(artifact.value.decisionSummary?.length))
const analysisQuestions = computed(() => artifact.value.questions ?? [])
const pendingBlockingQuestions = computed(() => analysisQuestions.value.filter((question) => question.blocking && !question.answered))
const questionPanelTitle = computed(() => (pendingBlockingQuestions.value.length ? '存在待确认问题' : '问题确认'))
const questionPanelStatusText = computed(() => {
  if (!analysisQuestions.value.length) return '已全部确认'
  if (pendingBlockingQuestions.value.length) return '待确认'
  return '已确认'
})
const taskLead = computed(() => {
  const current = task.value
  if (!current) return '正在加载任务信息…'
  if (current.status === 'FAILED') return '任务在自动化流程中断，需要先处理失败节点，再决定是否重新进入队列。'
  if (current.manualRequired) return '任务已暂停自动流转，当前需要人工处理后才能继续。'
  if (current.risk === 'high') return '高风险任务优先人工确认，AI 会继续保留上下文与产物供审计追踪。'
  return '任务已进入自动化处理流程，重点关注异常点和后续产物。'
})
const workflowNote = computed(() => {
  const current = task.value
  if (!current) return '加载中'
  if (current.status === 'FAILED') return '流程已暂停'
  if (current.status === 'WAITING_APPROVAL') return '等待人工确认'
  if (current.status === 'CANCELLED') return '已取消'
  if (current.automationState === 'completed') return '自动化完成'
  return '自动推进中'
})
const detailFacts = computed(() => {
  const current = task.value
  if (!current) return []
  const facts = [
    { label: '当前状态', value: statusText[current.status] },
    { label: '当前阶段', value: currentPhase.value },
    { label: '负责人', value: current.owner },
    { label: '审批人', value: current.approver && current.approver !== '未指定' ? current.approver : '未指派' },
    { label: '工作分支', value: current.branch },
  ]
  // DEV-016-C：从平台需求派生的任务展示当前基线 Commit
  if (current.baseCommitSha) {
    facts.push({ label: '当前 Commit', value: current.baseCommitSha.slice(0, 12) })
  }
  return facts
})
const riskDisplay = computed(() => {
  const current = task.value
  if (!current) return '待分析'
  const map: Record<string, string> = {
    pending: '待分析',
    high: '高风险',
    medium: '中风险',
    low: '低风险',
  }
  return map[current.risk] ?? current.risk
})
const verificationSummary = computed(() => {
  const latest = latestTestRun.value
  const current = task.value
  if (!current) return '加载中'
  if (latest) return TEST_STATUS_LABEL[latest.status] ?? latest.status
  if (['DIFF_GENERATED', 'TEST_RUNNING', 'TEST_FAILED', 'TESTED', 'COMPLETED'].includes(current.status)) return '等待测试记录'
  return '等待 Diff'
})
const nextStepSummary = computed(() => {
  const current = task.value
  if (!current) return '读取任务'
  if (current.status === 'FAILED') return '处理失败后重试'
  if (current.manualRequired) return manualActionText.value
  if (primaryAction.value) return primaryAction.value.label
  if (current.status === 'COMPLETED') return '查看交付结果'
  return workflowNote.value
})
const taskVerdictTitle = computed(() => {
  const current = task.value
  if (!current) return '任务状态读取中'
  if (current.status === 'FAILED') return '任务已暂停，需要先恢复执行'
  if (current.manualRequired) return '当前需要人工介入'
  if (current.status === 'WAITING_APPROVAL') return '修改计划等待审批'
  if (current.status === 'TESTED' || current.status === 'COMPLETED') return '验证已有结论，可进入交付核对'
  return '自动流程正在推进'
})
const taskVerdictBody = computed(() => {
  const current = task.value
  if (!current) return '正在同步任务、分析、测试与交付产物。'
  if (current.status === 'FAILED') return failureAdvice.value
  if (current.manualRequired) {
    if (current.manualReason === 'test_failed' || current.manualReason === 'command_blocked') return testManualBody.value
    return artifact.value.questionBody
  }
  if (artifact.value.decisionSummary?.length) return artifact.value.decisionSummary[0]
  return taskLead.value
})
const decisionSignals = computed(() => [
  { label: '当前阶段', value: currentPhase.value },
  { label: '风险判定', value: riskDisplay.value },
  { label: '下一步', value: nextStepSummary.value },
  { label: '验证状态', value: verificationSummary.value },
])
const decisionBullets = computed(() => {
  const summary = artifact.value.decisionSummary ?? []
  if (summary.length) return summary.slice(0, 3)
  return [taskLead.value]
})
function stripDecisionPrefix(line: string) {
  return line.replace(/^[^：:]{1,14}[：:]\s*/, '').trim()
}

function decisionLine(keys: string[]) {
  const summary = artifact.value.decisionSummary ?? []
  return summary.find((line) => keys.some((key) => line.includes(key)))
}

function analysisPointValue(keys: string[]) {
  const point = artifact.value.analysisPoints.find((item) => keys.some((key) => item.label.includes(key)))
  return point?.value
}

const analysisDecisionStatus = computed(() => {
  const current = task.value
  if (!current) return '读取中'
  if (current.status === 'FAILED') return '暂停处理'
  if (current.manualRequired) return '需人工补充'
  if (current.status === 'WAITING_APPROVAL') return '等待审批'
  if (current.risk === 'high') return '需复核'
  if (latestTestRun.value?.status === 'passed') return '可推进'
  return '可继续分析'
})
const analysisDecisionTone = computed(() => {
  const current = task.value
  if (!current) return 'info'
  if (current.status === 'FAILED' || latestTestRun.value?.status === 'failed') return 'danger'
  if (current.manualRequired || current.risk === 'high' || current.status === 'WAITING_APPROVAL') return 'warning'
  if (latestTestRun.value?.status === 'passed' || current.risk === 'low') return 'success'
  return 'info'
})
const analysisObjective = computed(() => {
  const line = decisionLine(['改什么', '目标', '本次'])
  if (line) return stripDecisionPrefix(line)
  return artifact.value.analysisBody || task.value?.title || '等待 AI 分析产出任务目标'
})
const analysisImpact = computed(() => {
  const line = decisionLine(['影响哪里', '影响范围', '影响模块'])
  if (line) return stripDecisionPrefix(line)
  return analysisPointValue(['影响', '范围', '模块']) || '待分析'
})
const analysisRiskPoint = computed(() => {
  const line = decisionLine(['风险点', '风险'])
  if (line) return stripDecisionPrefix(line)
  if (artifact.value.uncertainties?.length) return artifact.value.uncertainties[0]
  return analysisPointValue(['风险']) || riskDisplay.value
})
const analysisVerificationDetail = computed(() => {
  const latest = latestTestRun.value
  if (!latest) return verificationSummary.value
  const status = TEST_STATUS_LABEL[latest.status] ?? latest.status
  const exitText = latest.exit_code !== null ? `，exit ${latest.exit_code}` : ''
  const durationText = Number.isFinite(latest.duration) ? `，耗时 ${Number(latest.duration.toFixed(2))}s` : ''
  return `${status}${exitText}${durationText}`
})
const analysisDecisionRows = computed(() => [
  { label: '状态', value: analysisDecisionStatus.value, tone: analysisDecisionTone.value },
  { label: '风险', value: riskDisplay.value, tone: task.value?.risk === 'high' ? 'danger' : task.value?.risk === 'medium' ? 'warning' : 'success' },
  { label: '验证', value: analysisVerificationDetail.value, tone: latestTestRun.value?.status === 'failed' ? 'danger' : latestTestRun.value?.status === 'passed' ? 'success' : 'info' },
  { label: '下一步', value: nextStepSummary.value, tone: 'info' },
])
const coreAnalysisSources = computed(() => (artifact.value.analysisSources ?? []).slice(0, 3))
const remainingAnalysisSources = computed(() => (artifact.value.analysisSources ?? []).slice(3))
const evidenceSummary = computed(() => {
  const sourceCount = artifact.value.analysisSources?.length ?? 0
  const codeCount = task.value?.codeContexts?.length ?? 0
  const testCount = testRuns.value.length
  const prCount = prRecords.value.length
  return [
    { label: '代码上下文', value: codeCount ? `${codeCount} 段` : sourceCount ? `${sourceCount} 项引用` : '待补齐' },
    { label: '测试记录', value: testCount ? `${testCount} 次` : '暂无' },
    { label: 'PR 描述', value: prCount ? `${prCount} 版` : prSectionVisible.value ? prStatusLabel.value : '等待测试' },
  ]
})
// DEV-016-C：任务来源闭环（平台需求 / Gitee Issue），旧 /api/tasks 手动任务无来源则不展示
const sourceLinks = computed(() => {
  const current = task.value
  if (!current) return []
  const links: { label: string; text: string; to?: string; href?: string }[] = []
  if (current.teamRequestId && current.teamRequestNo) {
    links.push({ label: '平台需求', text: current.teamRequestNo, to: `/requests/${current.teamRequestId}` })
  }
  if (current.giteeIssueUrl) {
    links.push({ label: 'Gitee Issue', text: current.giteeIssueUrl, href: current.giteeIssueUrl })
  }
  return links
})
const primaryAction = computed(() => {
  const current = task.value
  if (!current) return null
  if (current.status === 'WAITING_APPROVAL' && auth.hasPermission('approval:view')) return { label: '进入审批', to: '/approvals' }
  if (diffReadyStatuses.includes(current.status) && auth.hasPermission('diff:view')) {
    return { label: '查看 Diff', to: `/diff/${current.id}` }
  }
  return null
})
const canOperateTask = computed(() => auth.hasPermission('task:operate'))
const canManageProject = computed(() => auth.hasPermission('project:manage'))
const canCancelTask = computed(() => {
  const current = task.value
  return Boolean(current && canOperateTask.value && !['COMPLETED', 'CANCELLED'].includes(current.status))
})
const emptyTitle = computed(() => (task.value?.status === 'CREATED' ? '任务已创建，AI 正在拉取 Issue...' : '任务已进入分析队列'))
const emptyDescription = computed(
  () =>
    (task.value?.status === 'CREATED'
      ? '系统会先拉取 Issue、仓库和测试上下文，再生成分析、计划和审批内容。'
      : 'Issue 快照已经保存，系统正在检查索引复用并等待 AI 分析产出风险等级。'),
)
const questionStatusClass = computed(() => (questionSaved.value ? 'success' : 'warning'))
const questionStatusText = computed(() => (questionSaved.value ? '已确认' : '待回答'))
const manualActionText = computed(() => {
  const map: Record<string, string> = {
    retry_or_cancel: '重试或取消',
    retry_test: '重试测试',
    answer_analysis_question: '补充回答',
    auto_generate_plan: '等待计划生成',
    auto_run_test: '执行测试',
    watch_test_result: '查看测试结果',
    review_plan: '进入审批',
    supplement_context: '补充上下文',
    wait_analysis: '等待分析',
    watch_diff: '查看 Diff',
    watch_tests: '查看测试',
    view_result: '查看结果',
    view_replay: '查看回放',
  }
  return map[task.value?.nextAction ?? ''] ?? '自动处理'
})
const TEST_STATUS_LABEL: Record<string, string> = { running: '运行中', passed: '通过', failed: '失败', skipped: '未执行' }
const testRuns = computed(() => (task.value ? store.testsByTask[task.value.id] ?? [] : []))
const latestTestRun = computed(() => (testRuns.value.length ? testRuns.value[0] : null))
const failureAdvice = computed(() => {
  const current = task.value
  if (!current) return ''
  return current.failureReason
    ? summarizeFailureReason(current.failureReason, latestTestRun.value?.exit_code)
    : current.repository === 'console-web'
      ? '建议先检查仓库授权和 Token 有效期，再重新拉取上下文。'
      : '建议检查当前失败原因，确认权限、分支和上下文后再重试。'
})
const testRunsTitle = computed(() => {
  const latest = latestTestRun.value
  if (!latest) return '暂无测试执行记录'
  return `最近一次：第 ${latest.attempt} 次 · ${TEST_STATUS_LABEL[latest.status] ?? latest.status}`
})

function testRunTone(status: string) {
  const map: Record<string, string> = { running: 'info', passed: 'success', failed: 'danger', skipped: 'warning' }
  return map[status] ?? 'info'
}

const testManualBody = computed(() => {
  const latest = latestTestRun.value
  if (!latest) return '测试尚未成功执行，可到 Diff 页查看执行结果后重试。'
  const reason = latest.failure_reason || latest.blocked_reason
  return reason
    ? `测试未通过（第 ${latest.attempt} 次尝试）：${summarizeFailureReason(reason, latest.exit_code)}`
    : `测试未通过（第 ${latest.attempt} 次尝试），可到 Diff 页查看输出后重试。`
})

// ---------- DEV-013 PR 描述：状态摘要（版本记录随任务接口共享缓存，与 Diff 页同一数据源） ----------

const prRecords = computed(() => (task.value ? store.prDescriptionsByTask[task.value.id] ?? [] : []))
const latestPrRecord = computed(() => {
  const list = [...prRecords.value].sort(
    (left, right) => right.version - left.version || right.generated_at.localeCompare(left.generated_at),
  )
  return list.length ? list[0] : null
})
const prSectionVisible = computed(() => {
  const current = task.value
  if (!current || current.status === 'FAILED' || current.status === 'CANCELLED') return false
  // mock 演示任务不产出 PR 记录，仅真实模式进入生成口径
  if (!isApiTask.value) return prRecords.value.length > 0
  return prRecords.value.length > 0 || current.status === 'TESTED' || current.status === 'TEST_FAILED'
})
const prStatusTone = computed(() => {
  const latest = latestPrRecord.value
  if (!latest) return 'info'
  return latest.generation_status === 'FAILED' ? 'danger' : 'success'
})
const prStatusLabel = computed(() => {
  const latest = latestPrRecord.value
  if (!latest) return '待生成'
  return latest.generation_status === 'FAILED' ? '生成失败' : `已生成 · 第 ${latest.version} 版`
})
const prGeneratedTimeText = computed(() => {
  const latest = latestPrRecord.value
  if (!latest || latest.generation_status !== 'GENERATED') return ''
  return new Date(latest.generated_at).toLocaleString('zh-CN', { hour12: false })
})
const prStaleHintParts = computed(() => {
  const current = task.value
  const latest = latestPrRecord.value
  if (!current || !latest || latest.generation_status !== 'GENERATED') return []
  const parts: string[] = []
  const diffs = store.diffsByTask[current.id] ?? []
  const latestDiff = [...diffs].sort(
    (left, right) => right.plan_version - left.plan_version || right.generated_at.localeCompare(left.generated_at),
  )[0]
  if (latestDiff && (latestDiff.id !== latest.diff_artifact_id || latestDiff.plan_version !== latest.plan_version)) {
    parts.push('Diff 产物或计划版本已更新')
  }
  const latestTest = (store.testsByTask[current.id] ?? []).find((run) => run.status === 'passed' || run.status === 'failed')
  if (latestTest && latestTest.id !== latest.test_run_id) parts.push('测试结果已变化')
  return parts
})
const prSummaryBody = computed(() => {
  const latest = latestPrRecord.value
  if (!latest) return '测试已有结论后即可生成合入说明；请在 Diff 页执行生成。'
  if (latest.generation_status === 'FAILED') return `生成失败：${latest.failure_reason || '未记录原因'}，可在 Diff 页查看详情后重新生成。`
  return `已基于修改计划 v${latest.plan_version}、Diff 产物与测试结果生成合入说明（第 ${latest.version} 版，${latest.generated_by_name || latest.generated_by}，${prGeneratedTimeText.value}）。仅生成文本，不自动创建 Gitee PR。`
})

let unsubscribeRealtime: (() => void) | undefined

onMounted(() => {
  void loadTask()
  connectRealtime()
  unsubscribeRealtime = subscribeRealtime((event) => {
    if (event.task_id === routeTaskId.value || (event.resource_type === 'task' && event.resource_id === routeTaskId.value)) {
      void loadTask()
    }
  })
})

onBeforeUnmount(() => {
  unsubscribeRealtime?.()
  unsubscribeRealtime = undefined
})

watch(
  task,
  (next) => {
    if (next) store.selectTask(next.id)
  },
  { immediate: true },
)

watch(routeTaskId, () => {
  void loadTask()
})

watch(
  () => task.value?.id ?? '',
  () => {
    answer.value = ''
    questionSaved.value = false
    questionSubmittedAt.value = ''
    answerDrafts.value = {}
    analysisExpanded.value = getDataSourceMode() === 'mock'
    secondarySourcesExpanded.value = false
  },
)

function syncSecondarySourcesExpanded(event: Event) {
  const details = event.currentTarget
  secondarySourcesExpanded.value = details instanceof HTMLDetailsElement && details.open
}

function phaseState(index: number) {
  if (index < phaseIndex.value) return 'done'
  if (index === phaseIndex.value) return 'active'
  return 'waiting'
}

function useDefaultAnswer() {
  if (questionSaved.value) return
  answer.value = '可跳过，优先保证新回调的事件幂等。'
}

function saveAnswer() {
  if (questionSaved.value) return
  questionSaved.value = true
  questionSubmittedAt.value = new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
  ElMessage.closeAll()
  ElMessage.success({
    message: '问题确认已保存，计划将基于该回答重新生成',
    duration: 1800,
    grouping: true,
  })
}

function sourceTypeLabel(sourceType: AnalysisSourceType) {
  const map: Record<AnalysisSourceType, string> = {
    issue_title: 'Issue 标题',
    issue_body: 'Issue 正文',
    issue_comment: 'Issue 评论',
    issue_label: 'Issue 标签',
    repository: '仓库配置',
    code_context: '代码上下文',
    history: '历史相似任务（仅参考）',
    project_default: '项目默认规则',
    answered_question: '问题确认记录',
  }
  return map[sourceType] ?? sourceType
}

function relevanceLabel(relevance: AnalysisRelevance) {
  const map: Record<AnalysisRelevance, string> = { high: '高相关', medium: '中相关', low: '低相关' }
  return map[relevance] ?? relevance
}

function useQuestionDefault(question: AnalysisQuestion) {
  answerDrafts.value = { ...answerDrafts.value, [question.id]: question.default_answer ?? '' }
}

function scrollToQuestions() {
  document.querySelector('.question-panel')?.scrollIntoView({ behavior: 'smooth', block: 'center' })
}

// DEV-017：context_missing 时滚动到代码上下文面板补充
function scrollToContext() {
  document.querySelector('.code-context-section')?.scrollIntoView({ behavior: 'smooth', block: 'center' })
}

async function submitQuestion(question: AnalysisQuestion, skip: boolean) {
  if (submitting.value) return
  const current = task.value
  if (!current) return
  const body = skip ? { skip: true } : { answer: (answerDrafts.value[question.id] ?? '').trim() }
  submitting.value = true
  try {
    const next = await apiRequest<AgentTask>(`/api/tasks/${current.id}/analysis/questions/${question.id}/answer`, {
      method: 'POST',
      body: JSON.stringify(body),
    })
    store.createTaskFromApi(next)
    answerDrafts.value = {}
    ElMessage.closeAll()
    ElMessage.success({
      message: skip ? '已跳过该问题' : '回答已提交，分析已基于确认结果更新',
      duration: 1800,
      grouping: true,
    })
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : '提交回答失败，请稍后重试')
  } finally {
    submitting.value = false
  }
}

function backToTasks() {
  router.push('/tasks')
}

async function loadTask() {
  const id = routeTaskId.value
  if (!id || syncing.value) return
  syncing.value = true
  loadErrorKind.value = ''
  try {
    // 统一数据访问层：mock 走本地演示数据（不发请求），real 走后端并 upsert
    await store.ensureTaskLoaded(id)
    // 测试记录摘要：加载失败不阻塞详情主体
    try {
      await store.loadTests(id)
    } catch {
      // 忽略：主体页面正常展示
    }
    // PR 描述版本摘要：与测试记录同样非关键路径
    try {
      await store.loadPrDescription(id)
    } catch {
      // 忽略：主体页面正常展示
    }
  } catch (error) {
    const status = (error as { status?: number }).status
    if (status === 404) {
      loadErrorKind.value = 'missing'
    } else if (status === 401) {
      loadErrorKind.value = 'unauthorized'
    } else {
      ElMessage.error(error instanceof Error ? error.message : '任务加载失败，请稍后重试')
    }
  } finally {
    syncing.value = false
  }
}

async function retryTask() {
  try {
    await store.retryRemote(task.value!.id)
    ElMessage.success('任务已重新进入分析队列')
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '任务重试失败，请稍后重试')
  }
}

async function cancelTask() {
  await ElMessageBox.confirm('取消后将停止后续 Agent 节点，但保留当前日志与产物供审计查看。', '确认取消任务', { confirmButtonText: '取消任务', cancelButtonText: '保留任务', type: 'warning' })
  try {
    await store.cancelRemote(task.value!.id)
    ElMessage.success('任务已取消')
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '任务取消失败，请稍后重试')
  }
}
</script>

<template>
  <section class="page-content task-detail">
    <EmptyPanel v-if="loadError" :title="loadError.title" :description="loadError.description" action="返回任务列表" @action="backToTasks" />
    <EmptyPanel v-else-if="!task" title="任务加载中" description="正在读取任务信息，请稍候。" />
    <template v-else>
    <div class="task-detail__hero task-command">
      <div class="task-command__main">
        <div class="task-command__identity">
          <span class="task-command__id mono">{{ task.id }}</span>
          <span class="task-command__repo">{{ task.repository }}</span>
          <StatusTag :status="task.status" />
          <StatusTag :risk="task.risk" />
          <StatusTag v-if="task.isDemo" demo />
        </div>
        <h2>{{ task.title }}</h2>
        <section class="task-command__verdict" aria-label="任务推进判断">
          <div>
            <strong>{{ taskVerdictTitle }}</strong>
            <p>{{ taskVerdictBody }}</p>
          </div>
          <ul v-if="decisionBullets.length > 1" class="task-command__bullets">
            <li v-for="line in decisionBullets" :key="line">{{ line }}</li>
          </ul>
        </section>
      </div>

      <aside class="task-command__side" aria-label="任务关键操作">
        <div class="task-detail__actions">
          <button v-if="task.status === 'FAILED' && canOperateTask" class="button button--primary" @click="retryTask"><RotateCcw :size="16" /> 重试任务</button>
          <RouterLink v-else-if="primaryAction" :to="primaryAction.to" class="button button--primary">
            <CheckCircle2 :size="16" /> {{ primaryAction.label }}
          </RouterLink>
          <button v-if="canCancelTask" class="button button--ghost" @click="cancelTask"><Ban :size="16" /> 取消任务</button>
        </div>
        <dl class="task-command__signals">
          <div v-for="signal in decisionSignals" :key="signal.label">
            <dt>{{ signal.label }}</dt>
            <dd>{{ signal.value }}</dd>
          </div>
        </dl>
      </aside>

      <div class="task-command__meta">
        <dl class="task-detail__facts">
          <div v-for="fact in detailFacts" :key="fact.label">
            <dt>{{ fact.label }}</dt>
            <dd>{{ fact.value }}</dd>
          </div>
        </dl>
        <div v-if="sourceLinks.length" class="task-detail__sources">
          <span v-for="link in sourceLinks" :key="link.label" class="task-detail__source">
            <em>{{ link.label }}</em>
            <RouterLink v-if="link.to" :to="link.to">{{ link.text }}</RouterLink>
            <a v-else-if="link.href" :href="link.href" target="_blank" rel="noopener">{{ link.text }}</a>
          </span>
        </div>
      </div>
    </div>

    <div v-if="task.status === 'FAILED' || task.manualRequired" class="page-notice" :class="task.status === 'FAILED' ? 'page-notice--danger' : 'page-notice--info'">
      <span class="page-notice__icon"><RotateCcw :size="16" /></span>
      <div class="page-notice__body">
        <strong>{{ task.status === 'FAILED' ? `任务已暂停：${task.failureNode || 'Agent 执行失败'}` : `需要人工处理：${manualActionText}` }}</strong>
        <p>{{ task.status === 'FAILED' ? failureAdvice : task.manualReason === 'test_failed' || task.manualReason === 'command_blocked' ? testManualBody : artifact.questionBody }}</p>
      </div>
      <RouterLink v-if="task.manualReason === 'auth_expired' && canManageProject" to="/settings" class="button button--secondary">去项目配置</RouterLink>
      <RouterLink v-else-if="task.nextAction === 'review_plan'" to="/approvals" class="button button--secondary">进入审批</RouterLink>
      <button v-else-if="task.nextAction === 'answer_analysis_question'" class="button button--secondary" @click="scrollToQuestions">去回答</button>
      <RouterLink v-else-if="task.nextAction === 'retry_test'" :to="`/diff/${task.id}`" class="button button--secondary">去重试测试</RouterLink>
      <button v-else-if="task.status === 'FAILED' && canOperateTask" class="button button--secondary" @click="retryTask">
        <RotateCcw :size="15" /> <span class="visually-hidden">一键重试</span>
      </button>
      <button v-else-if="task.manualReason === 'context_missing' || task.nextAction === 'supplement_context'" class="button button--secondary" @click="scrollToContext">去补充上下文</button>
    </div>

    <div v-else-if="task.status === 'WAITING_APPROVAL' && task.risk === 'high'" class="page-notice page-notice--warning">
      <span class="page-notice__icon"><ShieldAlert :size="16" /></span>
      <div class="page-notice__body">
        <strong>高风险变更等待审批</strong>
        <p>该任务修改风险较高，修改计划已在审批中心排队，建议尽快由指派审批人人工核对文件范围与风险判定。</p>
      </div>
    </div>

    <EmptyPanel
      v-if="showEmptyState"
      class="task-detail__empty-state"
      :title="emptyTitle"
      :description="emptyDescription"
      action="返回任务列表"
      @action="backToTasks"
    />

    <template v-else>
    <section class="task-detail__workflow">
      <header class="task-detail__workflow-head">
        <div>
          <h3>流程进度 · {{ currentPhase }}</h3>
        </div>
        <span class="task-detail__workflow-note">
          {{ workflowNote }}
        </span>
      </header>
      <div class="phase-rail" aria-label="任务阶段">
        <template v-for="(step, index) in phaseSteps" :key="step">
          <span :class="`is-${phaseState(index)}`">{{ index + 1 }} · {{ step }}</span>
          <i v-if="index < phaseSteps.length - 1" :class="`is-${phaseState(index)}`" />
        </template>
      </div>
      <dl class="task-command__evidence">
        <div v-for="item in evidenceSummary" :key="item.label">
          <dt>{{ item.label }}</dt>
          <dd>{{ item.value }}</dd>
        </div>
      </dl>
    </section>

    <div class="detail-grid">
      <main class="detail-column detail-column--main">
        <section class="detail-section detail-section--issue">
          <header>
            <div>
              <p class="section-kicker">需求上下文</p>
              <h3>Issue 信息</h3>
            </div>
            <span class="mono">{{ task.issue }}</span>
          </header>
          <p class="issue-title">{{ artifact.issueSummary }}</p>
          <details class="task-detail__issue-evidence">
            <summary><span>查看完整 Issue</span><small>正文、分支与发起人</small></summary>
            <p class="issue-body">{{ artifact.issueBody }}</p>
            <dl class="compact-dl">
              <div><dt>基线分支</dt><dd class="mono">main</dd></div>
              <div><dt>工作分支</dt><dd class="mono">{{ task.branch }}</dd></div>
              <div><dt>任务发起人</dt><dd>{{ task.owner }}</dd></div>
            </dl>
          </details>
          <div class="task-detail__issue-tags">
            <span class="section-kicker">关联标签</span>
            <div class="tag-row">
              <span class="outline-tag">Bug</span>
              <span class="outline-tag">{{ task.repository }}</span>
              <span class="outline-tag">{{ task.risk === 'pending' ? '待分析' : task.risk === 'high' ? '高风险' : task.risk === 'medium' ? '中风险' : '低风险' }}</span>
            </div>
          </div>
        </section>

        <section class="detail-section detail-section--analysis" :class="{ 'detail-section--failure': task.status === 'FAILED' }">
          <header>
            <div>
              <p class="section-kicker">分析结论</p>
              <h3>{{ analysisDecisionStatus }}</h3>
            </div>
          </header>
          <dl class="analysis-decision-strip">
            <div v-for="row in analysisDecisionRows" :key="row.label" :class="`is-${row.tone}`">
              <dt>{{ row.label }}</dt>
              <dd>{{ row.value }}</dd>
            </div>
          </dl>
          <div class="analysis-brief">
            <article>
              <span>本次目标</span>
              <p>{{ analysisObjective }}</p>
            </article>
            <article>
              <span>影响范围</span>
              <p>{{ analysisImpact }}</p>
            </article>
            <article>
              <span>关键风险</span>
              <p>{{ analysisRiskPoint }}</p>
            </article>
          </div>
          <div class="analysis-next-action">
            <div>
              <span>建议动作</span>
              <strong>{{ nextStepSummary }}</strong>
              <p>{{ taskVerdictBody }}</p>
            </div>
            <RouterLink v-if="primaryAction" :to="primaryAction.to" class="analysis-next-action__button">
              {{ primaryAction.label }}
            </RouterLink>
          </div>
          <div v-if="artifact.promptInjectionFlags?.length" class="analysis-alert analysis-alert--danger">
            <ShieldAlert :size="16" />
            <span>Issue 内容包含疑似指令注入片段（{{ artifact.promptInjectionFlags.join('、') }}），已按数据分析处理，未作为指令执行。</span>
          </div>
          <div v-if="task.status !== 'FAILED' && (artifact.decisionSummary?.length || artifact.analysisBody || artifact.analysisPoints.length || artifact.findings?.length || artifact.affectedAreas?.length || artifact.uncertainties?.length)" class="analysis-detail-block">
            <button type="button" class="analysis-expand-toggle" :aria-expanded="analysisExpanded" @click="analysisExpanded = !analysisExpanded">
              {{ analysisExpanded ? '收起完整分析' : '展开完整分析' }}
              <ChevronDown :size="14" :class="{ 'is-collapsed': !analysisExpanded }" />
            </button>
            <div v-show="analysisExpanded" class="analysis-detail-content">
              <div v-if="artifact.decisionSummary?.length" class="decision-summary">
                <p class="section-kicker">决策摘要 · 第 {{ artifact.analysisRevision ?? 1 }} 版</p>
                <ol>
                  <li v-for="line in artifact.decisionSummary" :key="line">{{ line }}</li>
                </ol>
              </div>
              <div v-if="artifact.analysisBody" class="analysis-findings">
                <p class="section-kicker">原始分析</p>
                <p>{{ artifact.analysisBody }}</p>
              </div>
              <div v-if="artifact.analysisPoints.length" class="analysis-points">
                <span v-for="point in artifact.analysisPoints" :key="point.label"><b>{{ point.label }}</b> {{ point.value }}</span>
              </div>
              <div v-if="artifact.findings?.length" class="analysis-findings">
                <p class="section-kicker">关键发现</p>
                <ul>
                  <li v-for="item in artifact.findings" :key="item">{{ item }}</li>
                </ul>
              </div>
              <div v-if="artifact.affectedAreas?.length" class="analysis-areas">
                <p class="section-kicker">影响范围</p>
                <div class="tag-row">
                  <span v-for="area in artifact.affectedAreas" :key="area" class="outline-tag">{{ area }}</span>
                </div>
              </div>
              <div v-if="artifact.uncertainties?.length" class="analysis-uncertainties">
                <p class="section-kicker">不确定点</p>
                <ul>
                  <li v-for="item in artifact.uncertainties" :key="item">{{ item }}</li>
                </ul>
              </div>
            </div>
          </div>
        </section>
        <section v-if="analysisQuestions.length && task.status !== 'FAILED'" class="detail-section question-panel">
          <header>
            <div>
              <p class="section-kicker">问题确认</p>
              <h3>{{ questionPanelTitle }}</h3>
            </div>
            <span :class="['status-tag', pendingBlockingQuestions.length ? 'warning' : 'success']">{{ questionPanelStatusText }}</span>
          </header>
          <article v-for="question in analysisQuestions" :key="question.id" class="question-item" :class="{ 'question-item--blocking': question.blocking }">
            <header class="question-item__head">
              <div>
                <span class="question-item__badge" :class="question.blocking ? 'is-blocking' : 'is-optional'">{{ question.blocking ? '阻塞问题' : '可选问题' }}</span>
                <strong>{{ question.question }}</strong>
              </div>
              <span v-if="question.answered" class="status-tag success">已确认</span>
            </header>
            <p class="question-item__reason">{{ question.reason }}</p>
            <p v-if="question.default_answer && !question.answered" class="question-item__default">建议回答：{{ question.default_answer }}</p>
            <template v-if="!question.answered">
              <textarea
                v-if="canOperateTask"
                v-model="answerDrafts[question.id]"
                :placeholder="question.blocking ? '阻塞问题建议填写回答内容；如无需补充，可选择跳过。' : '可填写补充回答；如无需补充，可选择跳过。'"
              />
              <p v-else class="form-subtitle">您当前可以查看问题内容，提交回答需由具有任务操作权限的成员处理。</p>
              <footer v-if="canOperateTask" class="question-panel__actions">
                <button class="button question-panel__button question-panel__button--ghost" :disabled="submitting || !question.default_answer" @click="useQuestionDefault(question)">使用建议回答</button>
                <button class="button question-panel__button question-panel__button--ghost" :disabled="submitting" @click="submitQuestion(question, true)">跳过</button>
                <button class="button question-panel__button question-panel__button--primary" :disabled="submitting || !answerDrafts[question.id]?.trim()" @click="submitQuestion(question, false)">
                  <Send :size="15" /> {{ submitting ? '提交中' : '提交回答' }}
                </button>
              </footer>
            </template>
            <div v-else class="question-panel__saved-state" aria-live="polite">
              <CheckCircle2 :size="16" />
              <span>{{ question.answer === '跳过' ? '已跳过该问题。' : question.answer }}</span>
            </div>
          </article>
        </section>
        <section v-else-if="hasRealAnalysis && task.status !== 'FAILED'" class="detail-section question-panel">
          <header>
            <div>
              <p class="section-kicker">问题确认</p>
              <h3>所有问题已确认</h3>
            </div>
            <span class="status-tag success">已全部确认</span>
          </header>
          <p>分析已根据确认结果重新生成，任务恢复自动流转，后续由计划生成模块继续推进。</p>
          <div class="question-panel__saved-state" aria-live="polite">
            <CheckCircle2 :size="16" />
            <span>确认记录已作为「问题确认记录」纳入分析来源，可在左侧分析来源中查看。</span>
          </div>
        </section>
        <section v-else-if="task.status !== 'FAILED' && canOperateTask" class="detail-section question-panel">
          <header>
            <div>
              <p class="section-kicker">问题确认</p>
              <h3>{{ artifact.questionTitle }}</h3>
            </div>
            <span :class="['status-tag', questionStatusClass]">{{ questionStatusText }}</span>
          </header>
          <p>{{ artifact.questionBody }}</p>
          <textarea v-model="answer" :readonly="questionSaved" placeholder="输入补充信息，或说明可跳过此问题。" />
          <footer class="question-panel__actions">
            <button class="button question-panel__button question-panel__button--ghost" :disabled="questionSaved" @click="useDefaultAnswer">使用默认回答</button>
            <button class="button question-panel__button question-panel__button--primary" :disabled="questionSaved" @click="saveAnswer">
              <CheckCircle2 v-if="questionSaved" :size="15" />
              <Send v-else :size="15" />
              {{ questionSaved ? '已提交计划更新' : '保存并重新生成计划' }}
            </button>
          </footer>
          <div v-if="questionSaved" class="question-panel__saved-state" aria-live="polite">
            <CheckCircle2 :size="16" />
            <span>已记录本次确认，计划会基于该回答重新生成。</span>
            <time v-if="questionSubmittedAt">{{ questionSubmittedAt }}</time>
          </div>
        </section>
        <section v-else-if="task.status !== 'FAILED'" class="detail-section">
          <header>
            <div>
              <p class="section-kicker">问题确认</p>
              <h3>{{ artifact.questionTitle }}</h3>
            </div>
            <span class="status-tag">只读</span>
          </header>
          <p>{{ artifact.questionBody }}</p>
          <p class="form-subtitle">您当前可以查看问题确认内容，提交回答需由开发者处理。</p>
        </section>
        <ModificationPlanPanel v-if="task.status !== 'FAILED'" :task-id="task.id" />
      </main>

      <aside class="detail-column detail-column--context">
        <CodeContextPanel v-if="isApiTask && task.repositoryId" :task="task" @refresh="loadTask" />
        <section v-if="artifact.analysisSources?.length || !isApiTask" class="detail-section">
          <details class="task-detail__source-evidence">
            <summary v-if="artifact.analysisSources?.length"><span>分析来源</span><small>引用 {{ artifact.analysisSources.length }} 项</small></summary>
            <summary v-else><span>相关代码</span><small>查看全部</small></summary>
            <ul v-if="artifact.analysisSources?.length" class="context-list">
              <li v-for="source in coreAnalysisSources" :key="source.source_ref + source.source_type" class="context-source-item">
                <div class="context-source-head">
                  <span :class="['context-score', `context-score--${source.relevance}`]">{{ relevanceLabel(source.relevance) }}</span>
                  <span class="context-source-type">{{ sourceTypeLabel(source.source_type) }}</span>
                </div>
                <div class="context-source-body">
                  <strong class="mono context-source-ref">{{ source.source_ref }}</strong>
                  <small class="context-source-summary">{{ source.content }}</small>
                </div>
              </li>
            </ul>
            <details v-if="remainingAnalysisSources.length" class="source-evidence-more" @toggle="syncSecondarySourcesExpanded">
              <summary><span>{{ secondarySourcesExpanded ? '收起' : '展开' }}其余 {{ remainingAnalysisSources.length }} 项来源</span></summary>
              <ul class="context-list">
                <li v-for="source in remainingAnalysisSources" :key="source.source_ref + source.source_type" class="context-source-item">
                  <div class="context-source-head">
                    <span :class="['context-score', `context-score--${source.relevance}`]">{{ relevanceLabel(source.relevance) }}</span>
                    <span class="context-source-type">{{ sourceTypeLabel(source.source_type) }}</span>
                  </div>
                  <div class="context-source-body">
                    <strong class="mono context-source-ref">{{ source.source_ref }}</strong>
                    <small class="context-source-summary">{{ source.content }}</small>
                  </div>
                </li>
              </ul>
            </details>
            <ul v-else-if="!isApiTask" class="context-list">
              <li v-for="item in artifact.relatedCode" :key="item.path">
                <span class="context-score">{{ item.score }}</span>
                <div>
                  <strong class="mono">{{ item.path }}</strong>
                  <small>{{ item.range }} · {{ item.revision }}</small>
                </div>
              </li>
            </ul>
          </details>
        </section>
        <section v-if="testRuns.length" class="detail-section detail-section--tests">
          <header>
            <div>
              <p class="section-kicker">测试执行记录</p>
              <h3>{{ testRunsTitle }}</h3>
            </div>
            <RouterLink :to="`/diff/${task.id}`" class="text-link">在 Diff 页查看输出</RouterLink>
          </header>
          <div class="tag-row">
            <span v-for="run in testRuns" :key="run.id" class="status-tag" :class="testRunTone(run.status)">
              第 {{ run.attempt }} 次 · {{ TEST_STATUS_LABEL[run.status] ?? run.status }}
              <template v-if="run.exit_code !== null"> · exit {{ run.exit_code }}</template>
            </span>
          </div>
          <p v-if="latestTestRun?.failure_reason" class="detail-test-note detail-test-note--failed">失败原因：{{ summarizeFailureReason(latestTestRun.failure_reason, latestTestRun.exit_code) }}</p>
          <p v-else-if="latestTestRun?.blocked_reason" class="detail-test-note">未执行原因：{{ latestTestRun.blocked_reason }}</p>
          <p v-else-if="latestTestRun && latestTestRun.status === 'passed'" class="detail-test-note detail-test-note--passed">
            测试通过（耗时 {{ Number(latestTestRun.duration.toFixed(2)) }}s，模式：{{ latestTestRun.runner_mode }}），Diff 产物未受影响。
          </p>
        </section>
        <section v-if="prSectionVisible" class="detail-section detail-section--pr">
          <header>
            <div>
              <p class="section-kicker">交付产物</p>
              <h3>PR 描述</h3>
            </div>
            <RouterLink :to="`/diff/${task.id}`" class="text-link">在 Diff 页查看全文与生成</RouterLink>
          </header>
          <div class="tag-row">
            <span class="status-tag" :class="prStatusTone">{{ prStatusLabel }}</span>
            <span v-if="prStaleHintParts.length" class="status-tag status-tag--warning" :title="prStaleHintParts.join('；')">需重新生成</span>
            <span v-if="latestPrRecord && latestPrRecord.generation_status === 'GENERATED'" class="status-tag">
              计划 v{{ latestPrRecord.plan_version }} · 生成于 {{ prGeneratedTimeText }}
            </span>
          </div>
          <p class="detail-test-note" :class="{ 'detail-test-note--failed': latestPrRecord?.generation_status === 'FAILED' }">
            {{ prSummaryBody }}
          </p>
        </section>
        <section class="detail-section">
          <header><h3>最近事件</h3><RouterLink :to="`/replay/${task.id}`" class="text-link">回放</RouterLink></header>
          <ol class="mini-timeline">
            <li
              v-for="item in recentEvents"
              :key="item.id"
              :class="{ 'is-active': item.status === 'active', 'is-failed': item.status === 'failed' }"
            >
              <i />
              <span>{{ item.title }}</span>
              <time>{{ item.time }}</time>
              <small v-if="item.summary" class="mini-timeline__summary">{{ item.summary }}</small>
            </li>
          </ol>
        </section>
        <section class="detail-section artifact-links">
          <header><h3>产物入口</h3></header>
          <RouterLink v-if="diffReadyStatuses.includes(task.status)" :to="`/diff/${task.id}`"><FileDiff :size="16" /> Diff 审核 <span>审批通过后可用</span></RouterLink>
          <button v-else disabled><FileDiff :size="16" /> Diff 审核 <span>{{ task.status === 'FAILED' ? '重试成功后可用' : '计划生成后可用' }}</span></button>
          <RouterLink v-if="['DIFF_GENERATED', 'TEST_RUNNING', 'TEST_FAILED', 'TESTED', 'COMPLETED'].includes(task.status)" :to="`/diff/${task.id}`"><FlaskConical :size="16" /> 测试记录 <span>在 Diff 页查看执行结果</span></RouterLink>
          <button v-else disabled><FlaskConical :size="16" /> 测试记录 <span>等待 Diff</span></button>
          <RouterLink v-if="prSectionVisible" :to="`/diff/${task.id}`"><MessageSquareText :size="16" /> PR 描述 <span>在 Diff 页查看全文与生成</span></RouterLink>
          <button v-else disabled><MessageSquareText :size="16" /> PR 描述 <span>等待测试</span></button>
        </section>
      </aside>
    </div>
    <div class="task-detail__collaboration-stack">
      <TaskCommentsPanel v-if="task && routeTaskId" :task-id="routeTaskId" :task-status="task.status" />
      <HistoricalTasksPanel v-if="task && isApiTask && routeTaskId" :task-id="routeTaskId" :query="task.title" />
    </div>
    </template>
    </template>
  </section>
</template>

<style scoped>
.analysis-decision-strip {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 0;
  margin: 4px 0 14px;
  overflow: hidden;
  border: 1px solid var(--task-border-soft);
  border-radius: 8px;
  background: var(--task-surface-subtle);
}

.analysis-decision-strip div {
  min-width: 0;
  padding: 11px 12px;
  border-right: 1px solid var(--task-border-soft);
}

.analysis-decision-strip div:last-child {
  border-right: 0;
}

.analysis-decision-strip dt,
.analysis-brief span,
.analysis-next-action span {
  color: var(--task-faint);
  font-size: 11px;
  font-weight: 700;
}

.analysis-decision-strip dd {
  margin: 4px 0 0;
  color: var(--task-text);
  font-size: 13px;
  font-weight: 800;
  line-height: 18px;
}

.analysis-decision-strip .is-success dd {
  color: var(--theme-status-success);
}

.analysis-decision-strip .is-warning dd {
  color: var(--theme-status-warning);
}

.analysis-decision-strip .is-danger dd {
  color: var(--theme-status-danger);
}

.analysis-brief {
  display: grid;
  gap: 10px;
  margin: 0 0 12px;
}

.analysis-brief article {
  display: grid;
  gap: 4px;
  padding: 12px 13px;
  border: 1px solid var(--task-border-soft);
  border-radius: 8px;
  background: var(--task-surface-subtle);
}

.analysis-brief p,
.analysis-next-action p {
  margin: 0;
  color: var(--task-text);
  font-size: 13px;
  line-height: 21px;
}

.analysis-next-action {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 13px 14px;
  border: 1px solid var(--task-border-soft);
  border-radius: 8px;
  background: var(--task-surface-subtle);
}

.analysis-next-action strong {
  color: var(--task-accent);
  font-size: 15px;
  line-height: 20px;
}

.analysis-next-action > div {
  display: grid;
  gap: 5px;
  min-width: 0;
}

.analysis-next-action__button {
  display: inline-flex;
  min-height: 34px;
  flex: none;
  align-items: center;
  justify-content: center;
  padding: 0 14px;
  border: 1px solid var(--theme-action-border);
  border-radius: 7px;
  color: var(--theme-action-text);
  background: var(--theme-action-bg);
  font-size: 13px;
  font-weight: 800;
  text-decoration: none;
}

.analysis-next-action__button:hover {
  border-color: var(--theme-action-bg-hover);
  background: var(--theme-action-bg-hover);
}

.analysis-expand-toggle {
  width: auto;
  min-height: 34px;
  margin-top: 12px;
  padding: 0 12px;
  border: 1px solid var(--task-border-soft);
  border-radius: 7px;
  color: var(--task-text);
  background: transparent;
  font-size: 12px;
  font-weight: 800;
}

.analysis-expand-toggle:hover {
  border-color: var(--theme-action-border);
  color: var(--task-accent);
  background: var(--theme-accent-soft);
}

.artifact-links a,
.artifact-links button {
  min-height: 48px;
  border-radius: 7px;
  background: transparent;
}

.artifact-links a:hover,
.artifact-links button:hover:not(:disabled) {
  transform: none;
}

.source-evidence-more {
  margin-top: 8px;
  border-top: 1px solid var(--task-border-soft);
  padding-top: 8px;
}

.source-evidence-more summary {
  display: flex;
  min-height: 30px;
  align-items: center;
  justify-content: space-between;
  color: var(--task-accent);
  cursor: pointer;
  font-size: 12px;
  font-weight: 700;
  list-style: none;
}

.source-evidence-more summary::-webkit-details-marker {
  display: none;
}

.source-evidence-more summary::after {
  width: 7px;
  height: 7px;
  border-right: 1.5px solid currentColor;
  border-bottom: 1.5px solid currentColor;
  transform: rotate(225deg);
  transition: transform .16s ease;
  content: "";
}

.source-evidence-more[open] summary::after {
  transform: rotate(45deg);
}

.source-evidence-more .context-list {
  margin-top: 8px;
}

.task-detail__sources {
  display: flex;
  flex-wrap: wrap;
  gap: 14px;
  margin-top: 10px;
}
.task-detail__collaboration-stack {
  display: grid;
  gap: 18px;
  margin-top: 18px;
}
.task-detail__source {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
}
.task-detail__source em {
  font-style: normal;
  color: var(--muted-color);
}
.task-detail__source a {
  color: var(--primary-color);
  text-decoration: none;
  word-break: break-all;
}
.task-detail__source a:hover {
  text-decoration: underline;
}

@media (max-width: 980px) {
  .analysis-decision-strip {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .analysis-decision-strip div:nth-child(2n) {
    border-right: 0;
  }

  .analysis-decision-strip div:nth-last-child(-n + 2) {
    border-top: 1px solid var(--task-border-soft);
  }
}

@media (max-width: 640px) {
  .analysis-next-action {
    align-items: stretch;
    flex-direction: column;
  }

  .analysis-next-action__button {
    width: 100%;
  }

  .analysis-decision-strip {
    grid-template-columns: minmax(0, 1fr);
  }

  .analysis-decision-strip div,
  .analysis-decision-strip div:nth-child(2n) {
    border-right: 0;
    border-bottom: 1px solid var(--task-border-soft);
  }

  .analysis-decision-strip div:last-child {
    border-bottom: 0;
  }
}
</style>
