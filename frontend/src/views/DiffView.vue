<script setup lang="ts">
import { CheckCircle2, ClipboardCheck, Copy, FileDiff, ListChecks, MessageSquareText, Search, ShieldAlert, TriangleAlert, UserRoundCheck } from '@lucide/vue'
import { computed, nextTick, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { useRoute, useRouter } from 'vue-router'
import EmptyPanel from '../components/EmptyPanel.vue'
import { useWorkspaceStore } from '../stores/workspace'
import { useAuthStore } from '../stores/auth'
import { getDataSourceMode } from '../services/dataSourceMode'
import StatusTag from '../components/StatusTag.vue'
import type { DiffArtifact, PrDescriptionRecord, PrDescriptionStatus } from '../types/domain'

const store = useWorkspaceStore()
const auth = useAuthStore()
const route = useRoute()
const router = useRouter()
// 与后端 TEST_MAX_ATTEMPTS 默认值保持一致（重试达到上限后任务进入 FAILED 终态）
const TEST_MAX_ATTEMPTS = 2
const DIFF_ALLOWED_STATUSES = new Set(['APPROVED', 'DIFF_GENERATED', 'TEST_RUNNING', 'TEST_FAILED', 'TESTED', 'COMPLETED'])
const routeTaskId = computed(() => {
  const value = route.params.id
  return Array.isArray(value) ? value[0] : value
})
const task = computed(() =>
  routeTaskId.value ? store.taskById(routeTaskId.value) ?? null : store.selectedTask ?? null,
)
const artifact = computed(() => store.artifactFor(task.value ? task.value.id : ''))
const isReal = computed(() => getDataSourceMode() === 'real')
const loadingTask = ref(false)
const loadErrorKind = ref<'' | 'missing' | 'unauthorized' | 'unexpected'>('')
const loadError = computed(() => {
  if (loadErrorKind.value === 'unauthorized') {
    return { title: '登录状态已失效', description: '请重新登录后再查看 Diff。' }
  }
  if (loadErrorKind.value === 'missing') {
    return { title: '任务不存在或无权查看', description: '任务可能已被删除，或当前账号没有查看权限。' }
  }
  if (loadErrorKind.value === 'unexpected') {
    return { title: 'Diff 数据加载失败', description: '请稍后重试；问题持续存在时请联系管理员。' }
  }
  return null
})

interface ViewDiffFile {
  path: string
  change: '新增' | '修改' | '删除'
  additions: number
  deletions: number
  before: string[]
  after: string[]
  patch?: string
}

const CHANGE_LABEL: Record<string, ViewDiffFile['change']> = { add: '新增', modify: '修改', delete: '删除' }

function sortArtifactsDesc(list: DiffArtifact[]) {
  return [...list].sort(
    (left, right) => right.plan_version - left.plan_version || right.generated_at.localeCompare(left.generated_at),
  )
}

const diffs = computed(() => (routeTaskId.value ? store.diffsByTask[routeTaskId.value] ?? [] : []))
// 最新版本优先；同版本取最近一次生成。旧版计划产物不会冒充当前产物
const latestArtifact = computed(() => (diffs.value.length ? sortArtifactsDesc(diffs.value)[0] : null))
const failedArtifact = computed(() => {
  const latest = latestArtifact.value
  return latest?.generation_status === 'FAILED' ? latest : null
})
const displayFiles = computed<ViewDiffFile[]>(() => {
  const latest = latestArtifact.value
  if (latest) {
    return latest.files.map((file) => ({
      path: file.file_path,
      change: CHANGE_LABEL[file.change_type] ?? '修改',
      additions: file.added_lines,
      deletions: file.deleted_lines,
      before: file.before ?? [],
      after: file.after ?? [],
      patch: file.patch,
    }))
  }
  return artifact.value.diffFiles.map((file) => ({ ...file }))
})

const diffAllowed = computed(() => Boolean(task.value && DIFF_ALLOWED_STATUSES.has(task.value.status)))
const diffBlocked = computed(() => Boolean(task.value && !diffAllowed.value))
const canGenerate = computed(
  () =>
    isReal.value &&
    Boolean(task.value) &&
    task.value!.status === 'APPROVED' &&
    auth.hasPermission('task:operate') &&
    !failedArtifact.value,
)
const showGenerateGate = computed(() => canGenerate.value && displayFiles.value.length === 0)
const showEmptyState = computed(() => {
  const current = task.value
  if (!current) return false
  if (!diffAllowed.value) return false
  if (current.status === 'CREATED') return true
  if (!isReal.value && artifact.value.isPlaceholder) return true
  return displayFiles.value.length === 0
})
const emptyTitle = computed(() => (task.value?.status === 'CREATED' ? 'Diff 还没生成' : '当前任务没有可展示的变更'))
const emptyDescription = computed(() => '任务创建后会先完成上下文拉取和计划生成，Diff 会在审批通过后出现。')
const blockedDescription = computed(() => {
  if (task.value?.status === 'FAILED') return '当前任务已失败，不能查看或生成 Diff。请先按失败原因重试重建任务版本。'
  return '当前任务尚未通过审批，不能查看或生成 Diff。请先回到任务详情完成计划审批。'
})
const planLabel = computed(() => {
  const latest = latestArtifact.value
  if (latest) return `计划 v${latest.plan_version}`
  return `计划版本 ${artifact.value.planVersion}`
})
const generatedTime = computed(() => {
  const latest = latestArtifact.value
  if (!latest) return ''
  return new Date(latest.generated_at).toLocaleString('zh-CN', { hour12: false })
})
const selectedPath = ref('')
const fileFilter = ref('')
const comment = ref('')
const filteredFiles = computed(() =>
  displayFiles.value.filter((file) =>
    file.path.toLowerCase().includes(fileFilter.value.trim().toLowerCase()),
  ),
)
const currentFile = computed(() => filteredFiles.value.find((file) => file.path === selectedPath.value) ?? filteredFiles.value[0] ?? displayFiles.value[0])
const summary = computed(() => ({
  fileCount: displayFiles.value.length,
  additions: displayFiles.value.reduce((total, file) => total + file.additions, 0),
  deletions: displayFiles.value.reduce((total, file) => total + file.deletions, 0),
}))

type ReviewTab = 'overview' | 'pr' | 'tests' | 'audit'

const REVIEW_TABS: Array<{ id: ReviewTab; label: string }> = [
  { id: 'overview', label: '合入概览' },
  { id: 'pr', label: 'PR 描述' },
  { id: 'tests', label: '测试证据' },
  { id: 'audit', label: '审计记录' },
]
const activeReviewTab = ref<ReviewTab>('overview')
const backgroundExpanded = ref(false)
const diffWorkspace = ref<HTMLElement | null>(null)

watch(
  task,
  (next) => {
    if (!next) return
    store.selectTask(next.id)
    selectedPath.value = displayFiles.value[0]?.path ?? ''
    fileFilter.value = ''
  },
  { immediate: true },
)

// 真实产物异步到达（loadDiffs）后，保证选中第一个文件
watch(
  () => displayFiles.value.map((file) => file.path).join('|'),
  (paths) => {
    if (!paths.includes(selectedPath.value)) {
      selectedPath.value = displayFiles.value[0]?.path ?? ''
    }
  },
)

let loadSeq = 0

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
    // 统一数据访问层：mock 走本地演示数据（不发请求），real 走后端并 upsert
    await store.ensureTaskLoaded(id)
    if (seq !== loadSeq || routeTaskId.value !== id) return
    if (isReal.value) {
      await store.loadDiffs(id)
      if (seq !== loadSeq || routeTaskId.value !== id) return
      // 测试记录/PR 描述不是 Diff 查看的关键路径：单独加载，失败不阻塞产物展示
      try {
        await store.loadTests(id)
      } catch {
        // 忽略：测试记录可在执行后随按钮动作刷新
      }
      try {
        await store.loadPrDescription(id)
      } catch {
        // 忽略：PR 描述版本可在生成后随动作刷新
      }
    }
    if (seq !== loadSeq || routeTaskId.value !== id) return
  } catch (error) {
    if (seq !== loadSeq) return
    const status = (error as { status?: number }).status
    if (status === 404) {
      loadErrorKind.value = 'missing'
    } else if (status === 401) {
      loadErrorKind.value = 'unauthorized'
    } else {
      loadErrorKind.value = 'unexpected'
      ElMessage.error(error instanceof Error ? error.message : '加载 Diff 失败，请稍后重试')
    }
  } finally {
    if (seq === loadSeq) loadingTask.value = false
  }
}

watch(routeTaskId, () => {
  void loadTask()
}, { immediate: true })

const generating = ref(false)

async function runGenerate() {
  const id = routeTaskId.value
  if (!id || generating.value) return
  generating.value = true
  try {
    const next = await store.generateDiff(id)
    if (next?.diffs?.length) {
      ElMessage.success(
        next.diffs.some((item) => item.is_demo_fallback) ? 'Diff 已生成（演示兜底产物，仅供流程演示）' : 'Diff 已生成',
      )
    }
    await store.loadDiffs(id)
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '生成 Diff 失败，请稍后重试')
  } finally {
    generating.value = false
  }
}

// ---------- DEV-012 测试执行：记录来自 testsByTask（后端按开始时间倒序） ----------

const tests = computed(() => (routeTaskId.value ? store.testsByTask[routeTaskId.value] ?? [] : []))
const latestTest = computed(() => (tests.value.length ? tests.value[0] : null))
const failedTestCount = computed(() => tests.value.filter((item) => item.status === 'failed').length)
const testExecuting = ref(false)

const TEST_STATUS_META: Record<string, { label: string; tone: string }> = {
  running: { label: '运行中', tone: 'info' },
  passed: { label: '通过', tone: 'success' },
  failed: { label: '失败', tone: 'danger' },
  skipped: { label: '未执行', tone: 'warning' },
}

const TEST_RUNNER_LABEL: Record<string, string> = {
  docker: 'Docker 容器执行',
  mocked_runner: '演示运行',
  unavailable: '执行环境不可用',
}

// 只有真实模式且状态可运行测试时展示执行区（mock 演示任务沿用静态产物，不出操作入口）
const showTestRunArea = computed(() => {
  if (!isReal.value || !latestArtifact.value || failedArtifact.value) return false
  const status = task.value?.status
  const runnable = status === 'DIFF_GENERATED' || status === 'TEST_FAILED'
  return Boolean(runnable || tests.value.length)
})

// DIFF_GENERATED 首轮可运行；TEST_FAILED 未达上限可重试（后端超限会直接置 FAILED 终态）
const canRunTest = computed(() => {
  if (!isReal.value || testExecuting.value || !task.value || !latestArtifact.value || failedArtifact.value) return false
  const status = task.value?.status
  if (status !== 'DIFF_GENERATED' && status !== 'TEST_FAILED') return false
  if (!auth.hasPermission('task:operate')) return false
  return failedTestCount.value < TEST_MAX_ATTEMPTS
})

const nextAttemptNumber = computed(() => (latestTest.value ? latestTest.value.attempt + 1 : 1))
const runButtonLabel = computed(() =>
  task.value?.status === 'TEST_FAILED' ? `重试测试（第 ${nextAttemptNumber.value} 次 / 上限 ${TEST_MAX_ATTEMPTS} 次）` : '运行测试',
)

function testStatusTone(status: string) {
  return TEST_STATUS_META[status]?.tone ?? 'info'
}

function testRunnerLabel(mode: string) {
  return TEST_RUNNER_LABEL[mode] ?? mode
}

function testDurationText(duration: number) {
  return `${Number(duration.toFixed(2))}s`
}

function formatRunTime(iso: string | null) {
  if (!iso) return '--'
  return new Date(iso).toLocaleString('zh-CN', { hour12: false })
}

async function runTest() {
  const id = routeTaskId.value
  if (!id || testExecuting.value || !canRunTest.value) return
  testExecuting.value = true
  try {
    const next = await store.runTests(id)
    const records = next?.test_runs ?? []
    const last = records[records.length - 1]
    if (last?.status === 'passed') {
      ElMessage.success(`测试通过（第 ${last.attempt} 次尝试，exit 0）`)
    } else if (last?.status === 'failed') {
      ElMessage.warning(`测试失败（第 ${last.attempt} 次尝试），可查看输出后重试`)
    } else if (last?.status === 'skipped') {
      ElMessage.warning('测试未执行，原因见测试记录')
    }
    await store.loadTests(id)
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '执行测试失败，请稍后重试')
    // 危险命令被拦截等失败场景：记录已落库（skipped），刷新列表让用户看到拦截原因
    try {
      await store.loadTests(id)
    } catch {
      // 忽略刷新失败
    }
  } finally {
    testExecuting.value = false
  }
}

// ---------- DEV-013 PR 描述：记录来自 prDescriptionsByTask（任务详情/生成响应经 createTaskFromApi 吸收） ----------

const PR_DESC_ALLOWED_STATUSES = new Set(['DIFF_GENERATED', 'TEST_FAILED', 'TESTED'])
const PR_STATUS_META: Record<string, { label: string; tone: string }> = {
  PENDING: { label: '待生成', tone: 'info' },
  GENERATING: { label: '生成中', tone: 'info' },
  GENERATED: { label: '已生成', tone: 'success' },
  FAILED: { label: '生成失败', tone: 'danger' },
}
const PR_FIELD_META: Array<{ key: keyof PrDescriptionRecord; label: string }> = [
  { key: 'background', label: '背景' },
  { key: 'change_summary', label: '改动说明' },
  { key: 'impact_scope', label: '影响范围' },
  { key: 'test_result', label: '测试情况' },
  { key: 'risk_notes', label: '风险提示' },
  { key: 'rollback_notes', label: '回滚方案' },
]

const prRecords = computed(() => (routeTaskId.value ? store.prDescriptionsByTask[routeTaskId.value] ?? [] : []))
const prRecordsDesc = computed(() => [...prRecords.value].sort((left, right) => right.version - left.version))
const latestPrRecord = computed(() => prRecordsDesc.value[0] ?? null)
const prSelectedId = ref('')
const prGenerating = ref(false)
const prStatus = computed<PrDescriptionStatus>(() => {
  if (prGenerating.value) return 'GENERATING'
  const record = latestPrRecord.value
  if (!record) return 'PENDING'
  return record.generation_status
})
const prDisplayRecord = computed(() => prRecordsDesc.value.find((record) => record.id === prSelectedId.value) ?? latestPrRecord.value)
// 后端按开始时间倒序返回测试记录：最新有结论（通过/失败）的是第一条
const latestFinishedTest = computed(() => tests.value.find((item) => item.status === 'passed' || item.status === 'failed') ?? null)
// 过期判定与后端一致：计划版本/Diff 产物/测试记录任一变化都需重新生成
const prStale = computed(() => {
  const record = latestPrRecord.value
  const artifact = latestArtifact.value
  if (!record || record.generation_status !== 'GENERATED' || !artifact) return false
  if (record.plan_version !== artifact.plan_version || record.diff_artifact_id !== artifact.id) return true
  const latestTest = latestFinishedTest.value
  return Boolean(latestTest && record.test_run_id !== latestTest.id)
})
const prStaleHint = computed(() => {
  if (!prStale.value) return ''
  const record = latestPrRecord.value
  const artifact = latestArtifact.value
  const parts: string[] = []
  if (record && artifact && (record.plan_version !== artifact.plan_version || record.diff_artifact_id !== artifact.id)) {
    parts.push('Diff 产物/计划版本已更新')
  }
  const latestTest = latestFinishedTest.value
  if (record && latestTest && record.test_run_id !== latestTest.id) {
    parts.push('测试结果已变化')
  }
  return parts.join('；')
})
const showPrPanel = computed(() => {
  if (!isReal.value || !latestArtifact.value || failedArtifact.value) return false
  if (prRecords.value.length) return true
  return PR_DESC_ALLOWED_STATUSES.has(task.value?.status ?? '')
})
const canGeneratePr = computed(() => {
  if (!isReal.value || prGenerating.value || !task.value || !latestArtifact.value || failedArtifact.value) return false
  const status = task.value.status
  if (status !== 'TESTED' && status !== 'TEST_FAILED') return false
  return auth.hasPermission('task:operate')
})
const showPrGenerateButton = computed(() => {
  if (!canGeneratePr.value) return false
  if (prStatus.value === 'GENERATED') return prStale.value
  return prStatus.value === 'PENDING' || prStatus.value === 'FAILED'
})
const latestGeneratedPr = computed(() =>
  prDisplayRecord.value?.generation_status === 'GENERATED' ? prDisplayRecord.value : null,
)
const latestTestMeta = computed(() => {
  const record = latestTest.value
  if (!record) return { label: '测试待执行', tone: 'pending' }
  if (record.status === 'passed') return { label: '测试通过', tone: 'ready' }
  if (record.status === 'failed') return { label: '测试失败', tone: 'blocked' }
  if (record.status === 'running') return { label: '测试运行中', tone: 'pending' }
  return { label: '测试未执行', tone: 'pending' }
})
const mergeDecision = computed(() => {
  const record = latestTest.value
  if (record?.status === 'failed') {
    return {
      label: '存在阻塞项',
      description: '最新测试未通过，需要修复失败项并重新验证后再进入人工复核。',
      tone: 'blocked',
    }
  }
  if (record?.status === 'passed') {
    return {
      label: '可进入人工复核',
      description: 'Diff 已生成且已有通过记录，审阅人可结合风险与变更内容决定是否合入。',
      tone: 'ready',
    }
  }
  return {
    label: '需补齐证据',
    description: 'Diff 已生成，但尚缺少可用于合入判断的通过记录。',
    tone: 'pending',
  }
})
const riskLabel = computed(() => {
  const labels: Record<string, string> = { low: '低风险', medium: '中风险', high: '高风险', pending: '风险待评估' }
  return labels[task.value?.risk ?? 'pending'] ?? task.value?.risk ?? '风险待评估'
})
const changeConclusion = computed(() => {
  if (!displayFiles.value.length) return '尚无文件变更可供审阅。'
  const fileNames = displayFiles.value.slice(0, 2).map((file) => file.path).join('、')
  const remainder = displayFiles.value.length > 2 ? `等 ${displayFiles.value.length} 个文件` : ''
  return `变更覆盖 ${fileNames}${remainder}，共 +${summary.value.additions} / -${summary.value.deletions} 行。`
})
const riskConclusion = computed(() => {
  const fallback = latestArtifact.value?.is_demo_fallback ? '；当前为演示兜底产物，需在真实执行环境复核' : ''
  return `${riskLabel.value}，审阅时重点核对核心链路、边界条件与回滚可行性${fallback}。`
})
const testConclusion = computed(() => {
  const record = latestTest.value
  if (!record) return '尚未产生测试记录，当前证据不足以支持合入判断。'
  if (record.status === 'passed') {
    return `第 ${record.attempt} 次测试通过，exit ${record.exit_code ?? 0}，耗时 ${testDurationText(record.duration)}。`
  }
  if (record.status === 'failed') {
    return `第 ${record.attempt} 次测试失败：${record.failure_reason || '失败原因未记录'}。`
  }
  if (record.status === 'skipped') {
    return `测试未执行：${record.blocked_reason || '执行环境或命令未满足运行条件'}。`
  }
  return `第 ${record.attempt} 次测试正在运行，完成后再进行合入判断。`
})
const nextStep = computed(() => {
  const record = latestTest.value
  if (record?.status === 'failed') {
    return { owner: task.value?.owner || '任务开发者', action: '修复失败项并重试测试' }
  }
  if (record?.status === 'passed') {
    return { owner: task.value?.approver || '代码审阅人', action: '核对 Diff、风险与回滚方案，给出合入结论' }
  }
  return { owner: task.value?.owner || '任务开发者', action: '运行测试并补齐可追溯证据' }
})

// 新记录到达或切换任务时，选中版本默认跟随最新版
watch(
  prRecordsDesc,
  (records) => {
    const latest = records[0]
    if (latest && (!prSelectedId.value || !records.some((record) => record.id === prSelectedId.value))) {
      prSelectedId.value = latest.id
    }
  },
  { immediate: true },
)

watch(routeTaskId, () => {
  activeReviewTab.value = 'overview'
  backgroundExpanded.value = false
})

watch(prSelectedId, () => {
  backgroundExpanded.value = false
})

function prStatusTone(status: string) {
  return PR_STATUS_META[status]?.tone ?? 'info'
}

function prFieldText(key: keyof PrDescriptionRecord) {
  const record = prDisplayRecord.value
  return record ? (record[key] as string) || '' : ''
}

function formatPrTime(iso: string) {
  return iso ? new Date(iso).toLocaleString('zh-CN', { hour12: false }) : '--'
}

async function generatePr() {
  const id = routeTaskId.value
  if (!id || prGenerating.value || !canGeneratePr.value) return
  prGenerating.value = true
  try {
    const next = await store.generatePrDescription(id)
    const records = next?.pr_descriptions ?? []
    const latest = records.length ? [...records].sort((left, right) => right.version - left.version)[0] : null
    if (latest?.generation_status === 'GENERATED') {
      ElMessage.success(`PR 描述已生成（第 ${latest.version} 版），可复制全文`)
    } else if (latest?.generation_status === 'FAILED') {
      ElMessage.error('PR 描述生成失败，原因见下方面板')
    }
    prSelectedId.value = latest?.id ?? ''
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '生成 PR 描述失败，请稍后重试')
  } finally {
    prGenerating.value = false
  }
}

function copyPrFullText() {
  const record = prDisplayRecord.value
  if (!record) return
  const text = [record.background, record.change_summary, record.impact_scope, record.test_result, record.risk_notes, record.rollback_notes].join('\n\n')
  navigator.clipboard?.writeText(text)
  ElMessage.success(`PR 描述全文（第 ${record.version} 版）已复制`)
}

function selectReviewTab(tab: ReviewTab) {
  activeReviewTab.value = tab
}

function handleTabKey(event: KeyboardEvent) {
  const currentIndex = REVIEW_TABS.findIndex((tab) => tab.id === activeReviewTab.value)
  let nextIndex = currentIndex
  if (event.key === 'ArrowRight') nextIndex = (currentIndex + 1) % REVIEW_TABS.length
  else if (event.key === 'ArrowLeft') nextIndex = (currentIndex - 1 + REVIEW_TABS.length) % REVIEW_TABS.length
  else if (event.key === 'Home') nextIndex = 0
  else if (event.key === 'End') nextIndex = REVIEW_TABS.length - 1
  else return
  event.preventDefault()
  activeReviewTab.value = REVIEW_TABS[nextIndex].id
  const tabList = (event.currentTarget as HTMLElement).parentElement
  const buttons = tabList?.querySelectorAll<HTMLButtonElement>('[role="tab"]')
  buttons?.[nextIndex]?.focus()
}

function showTestEvidence() {
  selectReviewTab('tests')
  void nextTick(() => document.getElementById('diff-review-tabs')?.scrollIntoView({ behavior: 'smooth', block: 'start' }))
}

function scrollToDiff() {
  void nextTick(() => diffWorkspace.value?.scrollIntoView({ behavior: 'smooth', block: 'start' }))
}

function copyPatch() {
  const file = currentFile.value
  if (!file) return
  const patch = file.patch?.trim() ? file.patch : [...file.before, ...file.after].join('\n')
  navigator.clipboard?.writeText(patch)
  ElMessage.success('当前文件 Patch 已复制')
}

function submitComment() {
  if (!comment.value.trim()) {
    ElMessage.warning('先输入一条审阅意见')
    return
  }
  ElMessage.success('审阅意见已提交')
  comment.value = ''
}

function backToTask() {
  router.push(`/tasks/${task.value?.id ?? routeTaskId.value ?? ''}`)
}
</script>

<template>
  <section class="page-content diff-page">
    <EmptyPanel v-if="loadError" :title="loadError.title" :description="loadError.description" action="返回任务列表" @action="router.push('/tasks')" />
    <EmptyPanel
      v-else-if="!task"
      :title="loadingTask ? 'Diff 加载中' : '请先选择一个任务'"
      :description="loadingTask ? '正在从数据源读取任务与变更，请稍候。' : '请先从任务列表进入任务详情，再查看 Diff。'"
      :action="loadingTask ? undefined : '返回任务列表'"
      @action="router.push('/tasks')"
    />
    <template v-else>
    <div class="page-toolbar">
      <div>
        <p class="section-kicker">{{ task.id }} · {{ task.repository }}</p>
        <h2>{{ task.title }}</h2>
      </div>
      <div class="tag-row">
        <StatusTag :status="task.status" />
        <StatusTag :risk="task.risk" />
        <StatusTag v-if="task.isDemo" demo />
        <button v-if="canGenerate && displayFiles.length" class="button button--primary" :disabled="generating" @click="runGenerate">
          <FileDiff :size="15" /> {{ generating ? '生成中…' : '重新生成 Diff' }}
        </button>
      </div>
    </div>
    <div v-if="failedArtifact" class="page-notice page-notice--danger diff-failure-notice">
      <span class="page-notice__icon"><TriangleAlert :size="18" /></span>
      <div class="page-notice__body">
        <strong>Diff 生成失败（计划 v{{ failedArtifact.plan_version }}）</strong>
        <p>
          真实执行失败原因：{{ failedArtifact.real_failure_reason || '未记录' }}
          <template v-if="failedArtifact.fallback_reason">；演示兜底产物也未构造成功（{{ failedArtifact.fallback_reason }}）</template>
          任务已进入失败态，请回到任务详情按失败原因重试重建任务版本。
        </p>
      </div>
      <button class="button button--danger" @click="backToTask">返回任务详情处理</button>
    </div>
    <section v-else-if="showGenerateGate" class="page-notice page-notice--info diff-generate-gate">
      <span class="page-notice__icon"><FileDiff :size="18" /></span>
      <div class="page-notice__body">
        <strong>审批已通过，尚未生成 Diff</strong>
        <p>将基于最新审批通过的计划生成变更产物；成功后任务自动进入测试阶段。MVP 阶段产物为演示兜底内容。</p>
      </div>
      <button class="button button--primary" :disabled="generating" @click="runGenerate">
        {{ generating ? '生成中…' : '生成 Diff' }}
      </button>
    </section>
    <EmptyPanel
      v-else-if="showEmptyState"
      :title="emptyTitle"
      :description="emptyDescription"
      action="返回任务详情"
      @action="backToTask"
    />
    <EmptyPanel
      v-else-if="diffBlocked"
      title="Diff 尚未开放"
      :description="blockedDescription"
      action="返回任务详情"
      @action="backToTask"
    />
    <template v-else>
    <section class="diff-review-console" aria-labelledby="diff-review-title">
      <header class="diff-review-decision">
        <div class="diff-review-decision__main">
          <span class="diff-review-decision__icon" :class="`is-${mergeDecision.tone}`">
            <CheckCircle2 v-if="mergeDecision.tone === 'ready'" :size="20" />
            <ShieldAlert v-else :size="20" />
          </span>
          <div>
            <h2 id="diff-review-title">{{ mergeDecision.label }}</h2>
            <p>{{ mergeDecision.description }}</p>
          </div>
        </div>
        <div class="diff-review-actions">
          <button v-if="latestGeneratedPr" class="button button--primary" @click="copyPrFullText">
            <Copy :size="15" /> 复制 PR 描述
          </button>
          <button class="button button--secondary" @click="scrollToDiff">
            <FileDiff :size="15" /> 查看 Diff
          </button>
          <button class="button button--secondary" @click="showTestEvidence">
            <ListChecks :size="15" /> 查看测试记录
          </button>
        </div>
      </header>

      <div class="diff-review-signals" aria-label="合入判断摘要">
        <span><FileDiff :size="14" /> {{ summary.fileCount }} 个文件</span>
        <span class="diff-add">+{{ summary.additions }}</span>
        <span class="diff-delete">-{{ summary.deletions }}</span>
        <span :class="`is-${latestTestMeta.tone}`">{{ latestTestMeta.label }}</span>
        <span>{{ riskLabel }}</span>
        <span>{{ planLabel }}</span>
      </div>

      <nav id="diff-review-tabs" class="diff-review-tabs" role="tablist" aria-label="Diff 审阅内容">
        <button
          v-for="tab in REVIEW_TABS"
          :id="`diff-tab-${tab.id}`"
          :key="tab.id"
          type="button"
          role="tab"
          :aria-selected="activeReviewTab === tab.id"
          :aria-controls="`diff-panel-${tab.id}`"
          :tabindex="activeReviewTab === tab.id ? 0 : -1"
          :class="{ 'is-active': activeReviewTab === tab.id }"
          @click="selectReviewTab(tab.id)"
          @keydown="handleTabKey"
        >
          {{ tab.label }}
        </button>
      </nav>

      <section
        v-if="activeReviewTab === 'overview'"
        id="diff-panel-overview"
        class="diff-review-panel diff-review-overview"
        role="tabpanel"
        tabindex="0"
        aria-labelledby="diff-tab-overview"
      >
        <ol class="diff-review-findings">
          <li><strong>改了什么</strong><p>{{ changeConclusion }}</p></li>
          <li><strong>风险是什么</strong><p>{{ riskConclusion }}</p></li>
          <li><strong>测试证明了什么</strong><p>{{ testConclusion }}</p></li>
        </ol>
        <div class="diff-review-next">
          <UserRoundCheck :size="18" />
          <span>下一步负责人</span>
          <strong>{{ nextStep.owner }}</strong>
          <p>{{ nextStep.action }}</p>
        </div>
      </section>

      <section
        v-else-if="activeReviewTab === 'pr'"
        id="diff-panel-pr"
        class="diff-review-panel"
        role="tabpanel"
        tabindex="0"
        aria-labelledby="diff-tab-pr"
      >
        <section v-if="showPrPanel" class="pr-panel">
          <header class="pr-panel__head">
            <div>
              <h2>PR 描述</h2>
              <p>基于修改计划、Diff 与测试结果生成，不自动创建 Gitee PR。</p>
            </div>
            <div class="pr-panel__actions">
              <span class="status-tag" :class="prStatusTone(prStatus)">{{ PR_STATUS_META[prStatus]?.label ?? prStatus }}</span>
              <span v-if="prStatus === 'GENERATED' && prStale" class="status-tag status-tag--warning" :title="prStaleHint">需重新生成</span>
              <button v-if="latestGeneratedPr" class="button button--secondary" @click="copyPrFullText">
                <Copy :size="15" /> 复制全文
              </button>
              <button v-if="showPrGenerateButton" class="button button--primary" :disabled="prGenerating" @click="generatePr">
                {{ prGenerating ? '生成中…' : prStatus === 'PENDING' ? '生成 PR 描述' : '重新生成 PR 描述' }}
              </button>
            </div>
          </header>
          <p v-if="!latestPrRecord" class="pr-panel__empty">
            <template v-if="task?.status === 'TESTED' || task?.status === 'TEST_FAILED'">
              尚未生成 PR 描述。生成后可复制六段式合入说明，并核对历史版本。
            </template>
            <template v-else>
              需先产生测试结论后才可生成 PR 描述；当前任务处于 {{ task?.status }}。
            </template>
          </p>
          <div v-else-if="latestPrRecord.generation_status === 'FAILED'" class="pr-panel__failed">
            <strong>PR 描述生成失败（第 {{ latestPrRecord.version }} 版）</strong>
            <p>{{ latestPrRecord.failure_reason || '未记录失败原因' }}；修正输入产物后可重新生成，不影响任务状态。</p>
          </div>
          <template v-else>
            <div v-if="prRecordsDesc.length > 1" class="pr-panel__history">
              <span class="pr-panel__history-label">历史版本</span>
              <button
                v-for="record in prRecordsDesc"
                :key="record.id"
                class="pr-version-chip"
                :class="{ 'is-active': prDisplayRecord?.id === record.id }"
                @click="prSelectedId = record.id"
              >
                v{{ record.version }}
              </button>
            </div>
            <p v-if="prDisplayRecord?.id === latestPrRecord?.id && prStale" class="pr-panel__stale-hint">
              该版本基于旧产物（{{ prStaleHint }}），如需与当前代码一致请重新生成。
            </p>
            <section v-for="field in PR_FIELD_META" :key="field.key" class="pr-field">
              <header><h3>{{ field.label }}</h3></header>
              <pre
                class="pr-field__body"
                :class="{ 'is-collapsed': field.key === 'background' && !backgroundExpanded }"
              >{{ prFieldText(field.key) }}</pre>
              <button
                v-if="field.key === 'background'"
                type="button"
                class="pr-field__toggle"
                :aria-expanded="backgroundExpanded"
                @click="backgroundExpanded = !backgroundExpanded"
              >
                {{ backgroundExpanded ? '收起原始背景' : '展开原始背景' }}
              </button>
            </section>
          </template>
        </section>
        <p v-else class="diff-review-empty">当前任务阶段尚未开放 PR 描述生成与查看。</p>
      </section>

      <section
        v-else-if="activeReviewTab === 'tests'"
        id="diff-panel-tests"
        class="diff-review-panel"
        role="tabpanel"
        tabindex="0"
        aria-labelledby="diff-tab-tests"
      >
        <section v-if="showTestRunArea" class="test-run-panel">
          <header class="test-run-panel__head">
            <div>
              <h2>测试证据</h2>
              <p>按任务配置的测试命令执行，记录按开始时间倒序排列。</p>
            </div>
            <button v-if="canRunTest" class="button button--primary" :disabled="testExecuting" @click="runTest">
              {{ testExecuting ? '测试运行中…' : runButtonLabel }}
            </button>
            <button v-else-if="testExecuting" class="button button--primary" disabled>测试运行中…</button>
          </header>
          <p v-if="!tests.length" class="test-run-panel__empty">
            尚未执行测试。命令来自任务或仓库配置，并在隔离环境中运行。
          </p>
          <ol v-else class="test-run-list">
            <li v-for="record in tests" :key="record.id" class="test-run">
              <details>
                <summary>
                  <span class="test-run__attempt">第 {{ record.attempt }} 次</span>
                  <span class="status-tag" :class="testStatusTone(record.status)">{{ TEST_STATUS_META[record.status]?.label ?? record.status }}</span>
                  <span class="test-run__meta">{{ testRunnerLabel(record.runner_mode) }}</span>
                  <span class="test-run__meta">{{ formatRunTime(record.started_at) }}</span>
                  <span v-if="record.finished_at" class="test-run__meta">耗时 {{ testDurationText(record.duration) }}</span>
                  <span v-if="record.exit_code !== null" class="test-run__meta test-run__exit">exit {{ record.exit_code }}</span>
                </summary>
                <div class="test-run__detail">
                  <dl class="compact-dl">
                    <div><dt>执行命令</dt><dd class="mono">{{ record.command || '未记录' }}</dd></div>
                    <div><dt>关联产物</dt><dd class="mono">{{ record.diff_artifact_id }} · 计划 v{{ record.plan_version }}</dd></div>
                  </dl>
                  <p v-if="record.blocked_reason" class="test-run__reason">跳过/拦截原因：{{ record.blocked_reason }}</p>
                  <p v-if="record.failure_reason" class="test-run__reason test-run__reason--failed">失败原因：{{ record.failure_reason }}</p>
                  <section v-if="record.stdout" class="diff-meta">
                    <header class="panel__header"><div><h3>stdout</h3></div></header>
                    <pre class="diff-meta__body mono">{{ record.stdout }}</pre>
                  </section>
                  <section v-if="record.stderr" class="diff-meta">
                    <header class="panel__header"><div><h3>stderr</h3></div></header>
                    <pre class="diff-meta__body mono">{{ record.stderr }}</pre>
                  </section>
                </div>
              </details>
            </li>
          </ol>
        </section>
        <p v-else class="diff-review-empty">当前任务没有可展示的测试记录。</p>
      </section>

      <section
        v-else
        id="diff-panel-audit"
        class="diff-review-panel"
        role="tabpanel"
        tabindex="0"
        aria-labelledby="diff-tab-audit"
      >
        <dl class="diff-audit-strip" aria-label="交付审计元信息">
          <div><dt>PR 版本</dt><dd>{{ prDisplayRecord ? `v${prDisplayRecord.version}` : '未生成' }}</dd></div>
          <div><dt>生成时间</dt><dd>{{ prDisplayRecord ? formatPrTime(prDisplayRecord.generated_at) : generatedTime || '--' }}</dd></div>
          <div><dt>生成人</dt><dd>{{ prDisplayRecord?.generated_by_name ?? '-' }}</dd></div>
          <div><dt>关联产物</dt><dd class="mono">{{ prDisplayRecord?.diff_artifact_id ?? latestArtifact?.id ?? '-' }}</dd></div>
          <div><dt>计划</dt><dd>{{ planLabel }}</dd></div>
          <div><dt>测试记录</dt><dd class="mono">{{ prDisplayRecord?.test_run_id ?? latestTest?.id ?? '-' }}</dd></div>
        </dl>
        <div
          v-if="latestArtifact"
          class="diff-artifact-note"
          :class="latestArtifact.is_demo_fallback ? 'is-warning' : 'is-success'"
        >
          <ClipboardCheck :size="18" />
          <div>
            <strong>{{ latestArtifact.is_demo_fallback ? '演示兜底产物' : '真实执行产物' }} · {{ latestArtifact.execution_step || '执行步骤未记录' }}</strong>
            <p v-if="latestArtifact.is_demo_fallback">
              {{ latestArtifact.fallback_reason || '构造原因未记录' }}
              <template v-if="latestArtifact.real_failure_reason">；真实执行失败原因：{{ latestArtifact.real_failure_reason }}</template>
            </p>
            <p v-else>由真实执行环境产出，可用于人工复核与测试阶段。</p>
          </div>
        </div>
        <section v-if="latestArtifact?.test_summary" class="diff-audit-block">
          <h3>产物测试摘要</h3>
          <pre class="mono">{{ latestArtifact.test_summary }}</pre>
        </section>
        <section
          v-if="latestArtifact?.pr_description_draft && !prRecords.length && !latestFinishedTest"
          class="diff-audit-block"
        >
          <h3>PR 描述草稿（演示兜底）</h3>
          <pre class="mono">{{ latestArtifact.pr_description_draft }}</pre>
        </section>
      </section>
    </section>

    <div ref="diffWorkspace" class="diff-layout diff-workspace">
      <aside class="panel diff-files">
        <header>
          <h2>文件变更</h2>
          <label class="search-field"><Search :size="15" /><input v-model="fileFilter" placeholder="筛选文件" /></label>
        </header>
        <button
          v-for="file in filteredFiles"
          :key="file.path"
          class="diff-file-item"
          :class="{ 'is-selected': file.path === selectedPath }"
          @click="selectedPath = file.path"
        >
          <FileDiff :size="16" />
          <span>
            <strong class="mono" :title="file.path">{{ file.path }}</strong>
            <small>{{ file.change }}</small>
          </span>
          <em><i>+{{ file.additions }}</i><b>-{{ file.deletions }}</b></em>
        </button>
      </aside>
      <main class="panel diff-viewer">
        <header class="diff-viewer__header">
          <div>
            <strong class="mono" :title="currentFile.path">{{ currentFile.path }}</strong>
            <small>{{ currentFile.change }} · {{ currentFile.additions }} 行新增，{{ currentFile.deletions }} 行删除</small>
          </div>
          <button class="button button--secondary" @click="copyPatch"><Copy :size="15" /> 复制 Patch</button>
        </header>
        <div class="diff-code">
          <div class="diff-code__column">
            <header>修改前</header>
            <code v-for="(line, index) in currentFile.before" :key="`before-${index}`" class="diff-line diff-line--delete"><i>{{ index + 1 }}</i>{{ line }}</code>
            <p v-if="!currentFile.before.length" class="diff-empty">新增文件，没有原始内容</p>
          </div>
          <div class="diff-code__column">
            <header>修改后</header>
            <code v-for="(line, index) in currentFile.after" :key="`after-${index}`" class="diff-line diff-line--add"><i>{{ index + 1 }}</i>{{ line }}</code>
          </div>
        </div>
      </main>
    </div>
    <section class="review-bar">
      <MessageSquareText :size="18" />
      <input v-model="comment" placeholder="输入审阅意见，团队成员会在任务详情中看到。" />
      <button class="button button--primary" @click="submitComment">提交审阅意见</button>
    </section>
    </template>
    </template>
  </section>
</template>
