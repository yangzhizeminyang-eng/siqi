<script setup lang="ts">
import {
  Activity,
  AlertTriangle,
  ArrowUpRight,
  ClipboardCheck,
  Clock3,
  FileDiff,
  FolderSearch,
  History,
  ListTodo,
  RefreshCw,
  Settings,
  ShieldAlert,
} from '@lucide/vue'
import { computed, ref } from 'vue'
import { useAuthStore } from '../stores/auth'
import { useWorkspaceStore } from '../stores/workspace'
import { statusText } from '../types/domain'
import { getDataSourceMode } from '../services/dataSourceMode'
import { useLanguage } from '../stores/language'
import type { DashboardTrendPoint, Task, TaskStatus } from '../types/domain'

const store = useWorkspaceStore()
const auth = useAuthStore()
const { isEnglish } = useLanguage()
const displayUser = computed(() => auth.displayUser)
const isReal = computed(() => getDataSourceMode() === 'real')
const OVERDUE_MS = 24 * 3600 * 1000
const riskTasks = computed(() => store.tasks.filter((task) => task.risk === 'high').length)
const recentTasks = computed(() => [...store.tasks].sort((left, right) => right.updatedAt.localeCompare(left.updatedAt)))
const highRiskTask = computed(() => store.tasks.find((task) => task.status === 'WAITING_APPROVAL' && task.risk === 'high') ?? store.pendingTasks[0] ?? store.selectedTask)
const failedTask = computed(() => store.tasks.find((task) => task.status === 'FAILED') ?? store.tasks[0])
// 逾期口径：真实模式用 owner 范围审批队列（approver 只看指派给自己的），mock 沿用本地待审批任务
const overdueApprovals = computed<Task[]>(() => {
  const pool = isReal.value ? store.approvalQueue : store.pendingTasks
  const now = Date.now()
  return pool.filter((task) => {
    const plan = store.latestPlanOf(task.id)
    if (!plan || plan.status !== 'WAITING_APPROVAL') return false
    const created = new Date(plan.created_at).getTime()
    return Number.isFinite(created) && now - created > OVERDUE_MS
  })
})
const dashboardPendingApprovals = computed(() => (isReal.value ? store.dashboardSummary.pendingApprovals : store.pendingTasks.length))
const approvalQueueCount = computed(() => (isReal.value ? store.approvalQueue.length : store.pendingTasks.length))
const overdueSuffix = computed(() =>
  overdueApprovals.value.length
    ? isEnglish.value
      ? ` · ${overdueApprovals.value.length} overdue`
      : ` · ${overdueApprovals.value.length} 项已逾期`
    : '',
)
function overdueSentence() {
  if (!overdueApprovals.value.length) return ''
  return isEnglish.value
    ? ` ${overdueApprovals.value.length} approvals have waited over 24 hours; clear them first.`
    : ` 另有 ${overdueApprovals.value.length} 项审批等待超过 24 小时，建议先处理。`
}
const automationLabel = computed(() => store.dashboardSummary.automationRateLabel)
const automationProgress = computed(() => Number.parseInt(automationLabel.value, 10) || 0)
const userAvatar = computed(() => displayUser.value?.name?.slice(0, 2) ?? '—')
const greeting = computed(() => {
  const hour = new Date().getHours()
  if (isEnglish.value) {
    if (hour < 12) return 'Good morning'
    if (hour < 18) return 'Good afternoon'
    return 'Good evening'
  }
  if (hour < 12) return '早上好'
  if (hour < 18) return '下午好'
  return '晚上好'
})

type ActionTone = 'danger' | 'warning' | 'info'
type WorkbenchAction = {
  title: string
  detail: string
  meta: string
  to: string
  icon: typeof AlertTriangle
  tone: ActionTone
  primary: boolean
}
type RoleOperation = {
  label: string
  detail: string
}

function buildEmptyWorkbench(role: string | undefined): { headline: string; focus: string; actions: WorkbenchAction[]; operations: RoleOperation[] } {
  if (isEnglish.value) {
    const copy: Record<string, { headline: string; focus: string }> = {
      admin: { headline: 'Manage projects, repositories, and security policies', focus: 'No tasks are running right now. Configure repositories and check index status first; tasks will appear here once created.' },
      approver: { headline: 'No approvals pending', focus: 'The approval queue is empty. Tasks waiting for approval will show here.' },
      developer: { headline: 'No tasks in progress', focus: 'Create a new task from a Gitee issue; the Agent will run analysis and planning for you.' },
      lead: { headline: 'No tasks to review', focus: 'The task list is empty. Replays and audit records will be produced as tasks move forward.' },
    }
    const fallback: WorkbenchAction = { title: 'View tasks', detail: 'Open the task list to see current status and next steps.', meta: 'Available', to: '/tasks', icon: ListTodo, tone: 'info', primary: false }
    const createAction: WorkbenchAction = { title: 'Create a task', detail: 'Submit project, repository, and Issue ID to start an Agent task.', meta: 'Available', to: '/tasks/new', icon: ListTodo, tone: 'warning', primary: true }
    const adminAction: WorkbenchAction = { title: 'Check project settings', detail: 'Maintain repositories, models, test commands, and security policies.', meta: 'Admin responsibility', to: '/settings', icon: Settings, tone: 'info', primary: true }
    const copyForRole = copy[role ?? ''] ?? { headline: 'No tasks in progress', focus: 'Created tasks will show here with status, risk, and next steps.' }
    const action = role === 'admin' ? adminAction : role === 'developer' ? createAction : fallback
    return {
      headline: copyForRole.headline,
      focus: copyForRole.focus,
      actions: [action],
      operations: [{ label: 'Create a task', detail: 'Submit project, repository, and Issue ID' }],
    }
  }
  const copy: Record<string, { headline: string; focus: string }> = {
    admin: { headline: '管理项目、仓库与安全策略', focus: '当前没有进行中的任务。先核对项目配置与仓库索引，任务创建后会出现在这里。' },
    approver: { headline: '当前没有待审批任务', focus: '审批队列为空。任务进入待审批后会显示在这里。' },
    developer: { headline: '当前没有进行中的任务', focus: '可以创建一个新任务：从 Gitee Issue 发起，AI 会自动完成分析、计划与后续流转。' },
    lead: { headline: '当前没有可复盘的任务', focus: '任务列表为空。任务推进后会产生回放时间线与审批、失败审计记录。' },
  }
  const fallback: WorkbenchAction = { title: '查看任务列表', detail: '查看当前任务状态与下一步动作。', meta: '可用', to: '/tasks', icon: ListTodo, tone: 'info', primary: false }
  const createAction: WorkbenchAction = { title: '创建新任务', detail: '提交项目、仓库与 Issue ID，Agent 开始自动分析。', meta: '可用', to: '/tasks/new', icon: ListTodo, tone: 'warning', primary: true }
  const adminAction: WorkbenchAction = { title: '检查项目配置', detail: '维护仓库、模型、测试命令与安全策略。', meta: '管理员职责', to: '/settings', icon: Settings, tone: 'info', primary: true }
  const copyForRole = copy[role ?? ''] ?? { headline: '当前没有进行中的任务', focus: '创建的任务会在这里展示状态、风险与下一步。' }
  const action = role === 'admin' ? adminAction : role === 'developer' ? createAction : fallback
  return {
    headline: copyForRole.headline,
    focus: copyForRole.focus,
    actions: [action],
    operations: [{ label: '创建任务', detail: '提交项目、仓库和 Issue ID' }],
  }
}

const dashboardText = computed(() => ({
  workbench: isEnglish.value ? 'Dashboard' : '工作台',
  pendingApproval: isEnglish.value ? 'pending approvals' : '待审批',
  operationsLabel: isEnglish.value ? 'Available operations for current role' : '当前角色可完成操作',
  projectHealth: isEnglish.value ? 'Project health' : '项目健康度',
  automationRate: isEnglish.value ? 'Automation rate' : '自动化率',
  running: isEnglish.value ? 'Running' : '执行中',
  savedHours: isEnglish.value ? 'Hours saved' : '节省工时',
  completedThisWeek: isEnglish.value ? 'Completed this week' : '本周完成',
  humanRiskOnly: isEnglish.value ? 'Human review for risk' : '人工只看风险',
  highRiskFirst: isEnglish.value ? 'High-risk plans surface first' : '高风险计划优先露出',
  approvalQueue: isEnglish.value ? 'Approval queue' : '审批队列',
  overdueHours: isEnglish.value ? 'items over 24 hours' : '项超过 24 小时',
  autoSaved: isEnglish.value ? 'Automation saved' : '自动节省',
  agentHours: isEnglish.value ? 'hours handled by Agent' : '小时已由 Agent 执行',
  weeklyDelivery: isEnglish.value ? 'Weekly delivery' : '本周交付',
  lastWeek: isEnglish.value ? 'vs last week' : '较上周',
  recentTasks: isEnglish.value ? 'Recent tasks' : '最近任务',
  recentSort: isEnglish.value ? 'Sorted by latest update' : '按最近更新时间排序',
  noRecentTasks: isEnglish.value ? 'No recent tasks.' : '暂无最近任务',
  viewAll: isEnglish.value ? 'View all' : '查看全部',
  task: isEnglish.value ? 'Task' : '任务',
  currentStage: isEnglish.value ? 'Current stage' : '当前阶段',
  risk: isEnglish.value ? 'Risk' : '风险',
  approver: isEnglish.value ? 'Approver' : '审批人',
  updatedAt: isEnglish.value ? 'Updated' : '更新时间',
  needsAttention: isEnglish.value ? 'Needs attention' : '需要处理',
  sortByPriority: isEnglish.value ? 'Sorted by priority' : '按优先级排序',
  taskTrend: isEnglish.value ? 'Delivery trend and quality' : '交付趋势与质量',
  sevenDayTrend: isEnglish.value ? 'Created, delivered, failed, and delivery success rate' : '创建、交付、失败与交付成功率',
  created: isEnglish.value ? 'Created' : '创建',
  done: isEnglish.value ? 'Delivered' : '交付',
  failed: isEnglish.value ? 'Failed' : '失败',
  deliverySuccessRate: isEnglish.value ? 'Delivery success rate' : '交付成功率',
  totalCreated: isEnglish.value ? 'Created' : '新增任务',
  totalCompleted: isEnglish.value ? 'Delivered' : '已交付',
  totalFailed: isEnglish.value ? 'Failed' : '失败任务',
  completionRate: isEnglish.value ? 'Delivery success rate' : '交付成功率',
  trendSummary: isEnglish.value ? 'Delivery trend summary' : '交付趋势摘要',
  trendMetrics: isEnglish.value ? 'Trend metrics' : '趋势指标',
  pendingGovernance: isEnglish.value ? 'Governance queue' : '治理队列',
  blockedRisk: isEnglish.value ? 'Blocked / failed' : '阻塞/失败',
  throughputHint: isEnglish.value ? 'Counts use the left axis; delivery success rate uses the right axis. Data is calculated in UTC by project scope.' : '数量使用左轴，交付成功率使用右轴；数据按项目范围和 UTC 日期聚合。',
  sevenDays: isEnglish.value ? '7 days' : '近 7 天',
  thirtyDays: isEnglish.value ? '30 days' : '近 30 天',
  refreshedAt: isEnglish.value ? 'Updated' : '更新于',
  inspectPoint: isEnglish.value ? 'Select a point to inspect the daily delivery record.' : '选择图表节点，查看当天交付记录。',
  noTrendData: isEnglish.value ? 'No delivery events in this period.' : '该时间范围内暂无交付事件。',
  trendLoadError: isEnglish.value ? 'Could not refresh trend data. Try again.' : '趋势数据刷新失败，请重试。',
  executionLog: isEnglish.value ? 'Execution log' : '执行日志',
  latestEvents: isEnglish.value ? 'Latest 4 events' : '最新 4 条事件',
}))

const trendTotalCreated = computed(() => store.dashboardSummary.trend.reduce((total, item) => total + item.created, 0))
const trendTotalDone = computed(() => store.dashboardSummary.trend.reduce((total, item) => total + item.done, 0))
const trendTotalFailed = computed(() => store.dashboardSummary.trend.reduce((total, item) => total + item.failed, 0))
const trendCompletionRate = computed(() => {
  const resolved = trendTotalDone.value + trendTotalFailed.value
  if (!resolved) return '—'
  return `${Math.round((trendTotalDone.value / resolved) * 100)}%`
})
const trendMaxValue = computed(() => {
  const highest = Math.max(0, ...store.dashboardSummary.trend.flatMap((item) => [item.created, item.done, item.failed]))
  return Math.max(5, Math.ceil(highest / 5) * 5)
})
const blockedTasks = computed(() => store.tasks.filter((task) => task.status === 'FAILED' || task.automationState === 'blocked').length)
type TrendSeriesKey = 'created' | 'done' | 'failed' | 'successRate'
const visibleTrendSeries = ref<Record<TrendSeriesKey, boolean>>({
  created: true,
  done: true,
  failed: true,
  successRate: true,
})
const selectedTrendIndex = ref<number | null>(null)
const trendLoading = ref(false)
const trendError = ref('')
const trendChart = { left: 54, right: 946, top: 24, bottom: 222 }
const trendGridRatios = [0, 0.25, 0.5, 0.75, 1]
const trendRightAxisValues = [100, 50, 0]
const selectedTrendPoint = computed<DashboardTrendPoint | null>(() => {
  if (selectedTrendIndex.value === null) return null
  return store.dashboardSummary.trend[selectedTrendIndex.value] ?? null
})
const trendHasData = computed(() => store.dashboardSummary.trend.some((point) => point.created || point.done || point.failed))

function trendX(index: number) {
  const total = store.dashboardSummary.trend.length
  if (total <= 1) return (trendChart.left + trendChart.right) / 2
  return trendChart.left + (index / (total - 1)) * (trendChart.right - trendChart.left)
}

function countY(value: number) {
  const height = trendChart.bottom - trendChart.top
  return trendChart.bottom - (Math.max(0, value) / trendMaxValue.value) * height
}

function rateY(rate: number | null) {
  const height = trendChart.bottom - trendChart.top
  return trendChart.bottom - ((rate ?? 0) / 100) * height
}

function trendSeriesValue(point: DashboardTrendPoint, key: TrendSeriesKey) {
  if (key === 'successRate') return point.successRate
  return point[key]
}

function trendSeriesPath(key: TrendSeriesKey) {
  let path = ''
  let pendingMove = true
  store.dashboardSummary.trend.forEach((point, index) => {
    const value = trendSeriesValue(point, key)
    if (value === null) {
      pendingMove = true
      return
    }
    const y = key === 'successRate' ? rateY(value) : countY(value)
    path += `${pendingMove ? 'M' : 'L'} ${trendX(index).toFixed(1)} ${y.toFixed(1)} `
    pendingMove = false
  })
  return path.trim()
}

function trendAxisCount(ratio: number) {
  return Math.round(trendMaxValue.value * (1 - ratio))
}

function shouldShowTrendLabel(index: number) {
  const total = store.dashboardSummary.trend.length
  return total <= 7 || index === 0 || index === total - 1 || index % 5 === 0
}

function displayTrendDate(point: DashboardTrendPoint) {
  if (!point.date) return displayTrendDay(point.day)
  return point.date.slice(5).replace('-', '/')
}

function formatGeneratedAt(value: string) {
  const timestamp = new Date(value)
  if (!Number.isFinite(timestamp.getTime())) return '—'
  return new Intl.DateTimeFormat(isEnglish.value ? 'en-GB' : 'zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
  }).format(timestamp)
}

function selectTrendPoint(index: number) {
  selectedTrendIndex.value = index
}

function toggleTrendSeries(key: TrendSeriesKey) {
  visibleTrendSeries.value[key] = !visibleTrendSeries.value[key]
}

async function setTrendRange(days: 7 | 30) {
  if (trendLoading.value || store.dashboardSummary.trendDays === days) return
  trendLoading.value = true
  trendError.value = ''
  selectedTrendIndex.value = null
  try {
    await store.refreshDashboardSummary(days)
  } catch {
    trendError.value = dashboardText.value.trendLoadError
  } finally {
    trendLoading.value = false
  }
}

function displayProjectName(name: string) {
  if (!isEnglish.value) return name
  const projectMap: Record<string, string> = {
    支付中台: 'Payment Hub',
    租户平台: 'Tenant Platform',
    智能体运行时: 'Agent Runtime',
  }
  return projectMap[name] ?? name
}

function formatStatus(status: TaskStatus) {
  if (!isEnglish.value) return statusText[status]
  const statusMap: Record<TaskStatus, string> = {
    CREATED: 'Created',
    ISSUE_FETCHED: 'Issue fetched',
    CONTEXT_INDEXED: 'Context indexed',
    ANALYSIS_QUEUED: 'Analysis queued',
    ANALYZED: 'AI analysis complete',
    PLAN_GENERATED: 'Plan generated',
    WAITING_APPROVAL: 'Waiting for approval',
    APPROVED: 'Approved',
    DIFF_GENERATED: 'Diff generated',
    TEST_RUNNING: 'Test running',
    TEST_FAILED: 'Test failed',
    TESTED: 'Tested',
    COMPLETED: 'Completed',
    FAILED: 'Failed',
    CANCELLED: 'Cancelled',
  }
  return statusMap[status]
}

function formatRisk(risk: 'pending' | 'low' | 'medium' | 'high') {
  if (!isEnglish.value) return { pending: '待分析', low: '低风险', medium: '中风险', high: '高风险' }[risk]
  return { pending: 'Pending', low: 'Low', medium: 'Medium', high: 'High' }[risk]
}

function statusTone(status: TaskStatus) {
  if (status === 'FAILED' || status === 'CANCELLED') return 'danger'
  if (status === 'WAITING_APPROVAL') return 'warning'
  if (status === 'APPROVED' || status === 'COMPLETED' || status === 'TESTED') return 'success'
  return 'info'
}

function riskTone(risk: 'pending' | 'low' | 'medium' | 'high') {
  return risk === 'pending' ? 'info' : `risk-${risk}`
}

function displayTrendDay(day: string) {
  if (!isEnglish.value) return day
  const map: Record<string, string> = {
    周一: 'Mon',
    周二: 'Tue',
    周三: 'Wed',
    周四: 'Thu',
    周五: 'Fri',
    周六: 'Sat',
    周日: 'Sun',
    今天: 'Today',
  }
  return map[day] ?? day
}

function displayEventTitle(title: string) {
  if (!isEnglish.value) return title
  const map: Record<string, string> = {
    '索引任务已完成': 'Indexing completed',
    '审批等待超过 24 小时': 'Approval waiting over 24 hours',
    '新的分析计划已生成': 'New analysis plan generated',
    'Gitee 授权验证失败': 'Gitee authorization failed',
  }
  return map[title] ?? title
}

function roleLabel() {
  if (!isEnglish.value) return displayUser.value?.role_label ?? ''
  switch (displayUser.value?.role) {
    case 'admin':
      return 'Admin'
    case 'approver':
      return 'Approver'
    case 'developer':
      return 'Developer'
    case 'lead':
      return 'Lead'
    default:
      return displayUser.value?.role_label ?? ''
  }
}

const roleWorkbench = computed((): { headline: string; focus: string; actions: WorkbenchAction[]; operations: RoleOperation[] } => {
  const role = displayUser.value?.role
  const task = highRiskTask.value
  if (!task) return buildEmptyWorkbench(role)
  const taskStage = formatStatus(task.status)
  switch (role) {
    case 'admin':
      if (isEnglish.value) {
        return {
          headline: 'Manage projects, repositories, and security policies',
          focus: `${displayProjectName(store.activeProject)} has ${store.tasks.length} tasks. You configure repositories, models, test commands, and security policies to keep delivery moving.`,
          actions: [
            {
              title: 'Review project runtime settings',
              detail: 'Configure repositories, update models, tune test commands, and adjust security policies.',
              meta: 'Admin responsibility',
              to: '/settings',
              icon: Settings,
              tone: 'info',
              primary: true,
            },
            {
              title: 'Review tasks and indexes',
              detail: `${failedTask.value.id} has an authorization issue. Check tasks, repository indexes, and recovery entry points first.`,
              meta: `${store.tasks.length} tasks visible`,
              to: '/tasks',
              icon: FolderSearch,
              tone: 'warning',
              primary: false,
            },
            {
              title: 'Handle account recovery',
              detail: 'Set temporary passwords for members and keep a handling record.',
              meta: 'Admin responsibility',
              to: '/account-recovery',
              icon: ShieldAlert,
              tone: 'warning',
              primary: false,
            },
          ],
          operations: [
            { label: 'Configure repositories', detail: 'Maintain repository URLs, branches, and credentials' },
            { label: 'Update models', detail: 'Switch providers and execution models' },
            { label: 'Adjust security policies', detail: 'Maintain file scope, command allowlist, and risk threshold' },
            { label: 'View all tasks', detail: 'Track all tasks and index status' },
            { label: 'Handle account recovery', detail: 'Reset member passwords through internal channels' },
          ],
        }
      }
      return {
        headline: '管理项目、仓库与安全策略',
        focus: `当前项目 ${store.activeProject} 有 ${store.tasks.length} 项任务。你负责配置仓库、模型、测试命令和安全策略，保障任务继续流转。`,
        actions: [
          {
            title: '检查项目运行配置',
            detail: '配置仓库、修改模型、调整测试命令和安全策略。',
            meta: '管理员职责',
            to: '/settings',
            icon: Settings,
            tone: 'info',
            primary: true,
          },
          {
            title: '查看全量任务与索引',
            detail: `${failedTask.value.id} 授权异常，先核对任务、仓库索引和恢复入口。`,
            meta: `${store.tasks.length} 项任务可见`,
            to: '/tasks',
            icon: FolderSearch,
            tone: 'warning',
            primary: false,
          },
          {
            title: '处理账号恢复申请',
            detail: '为忘记密码的成员设置临时密码并留下处理记录。',
            meta: '管理员职责',
            to: '/account-recovery',
            icon: ShieldAlert,
            tone: 'warning',
            primary: false,
          },
        ],
        operations: [
          { label: '配置仓库', detail: '维护仓库地址、分支和授权凭据' },
          { label: '修改模型', detail: '切换 Provider 与执行模型' },
          { label: '调整安全策略', detail: '维护文件范围、命令白名单和风险阈值' },
          { label: '查看全部任务', detail: '跟踪全量任务与索引状态' },
          { label: '处理账号恢复', detail: '重置成员密码并通过企业内部渠道通知' },
        ],
      }
    case 'approver':
      if (isEnglish.value) {
        return {
          headline: `Review high-risk approval for ${task.id}`,
          focus: `${task.title} is currently in "${taskStage}". After approval, the developer will see follow-up diffs, tests, and PR actions.${overdueSentence()}`,
          actions: [
            {
              title: `Review ${task.id}`,
              detail: 'Confirm risk boundaries, then approve, reject, or request a plan rewrite.',
              meta: `${approvalQueueCount.value} pending${overdueSuffix.value}`,
              to: '/approvals',
              icon: ClipboardCheck,
              tone: 'danger',
              primary: true,
            },
            {
              title: 'Add approval notes',
              detail: 'High-risk plans need a decision, risk requirements, or extra test notes.',
              meta: 'Notes required',
              to: '/approvals',
              icon: AlertTriangle,
              tone: 'warning',
              primary: false,
            },
          ],
          operations: [
            { label: 'Approve', detail: 'Release the plan into diff and test flow' },
            { label: 'Reject', detail: 'Stop the current plan version and keep the record' },
            { label: 'Request rewrite', detail: 'Send the plan back for a new version' },
            { label: 'Add approval notes', detail: 'Record reasoning and risk requirements' },
          ],
        }
      }
      return {
        headline: `处理 ${task.id} 的高风险审批`,
        focus: `${task.title} 当前处于「${taskStage}」。审批完成后，开发者会同步看到 Diff、测试和 PR 后续动作。${overdueSentence()}`,
        actions: [
          {
            title: `处理 ${task.id}`,
            detail: '确认风险边界后，通过、拒绝或要求重写计划。',
            meta: `${approvalQueueCount.value} 项待处理${overdueSuffix.value}`,
            to: '/approvals',
            icon: ClipboardCheck,
            tone: 'danger',
            primary: true,
          },
          {
            title: '补充审批意见',
            detail: '高风险计划必须留下审批结论、风险要求或补充测试说明。',
            meta: '审批意见必填',
            to: '/approvals',
            icon: AlertTriangle,
            tone: 'warning',
            primary: false,
          },
        ],
        operations: [
          { label: '通过', detail: '放行计划，任务进入 Diff 和测试链路' },
          { label: '拒绝', detail: '终止当前计划版本，保留审批记录' },
          { label: '要求重写', detail: '退回计划生成，等待新版方案' },
          { label: '补充审批意见', detail: '记录判断依据和风险要求' },
        ],
      }
    case 'developer':
      if (isEnglish.value) {
        return {
          headline: `Review execution results for ${task.id}`,
          focus: `${task.title} is currently in "${taskStage}". You create tasks and follow analysis, plans, diffs, tests, and PR descriptions.`,
          actions: [
            {
              title: `Review ${task.id} execution`,
              detail: 'Inspect AI analysis, change plans, diffs, test results, and PR description.',
              meta: 'Developer follow-up',
              to: `/tasks/${task.id}`,
              icon: FileDiff,
              tone: 'info',
              primary: true,
            },
            {
              title: 'Create task',
              detail: 'Start a new Agent task from a Gitee issue; branch and test commands are filled automatically.',
              meta: 'Available',
              to: '/tasks/new',
              icon: ListTodo,
              tone: 'warning',
              primary: false,
            },
          ],
          operations: [
            { label: 'Create task', detail: 'Submit project, repository, and Issue ID' },
            { label: 'View analysis', detail: 'Confirm impact scope and risk points' },
            { label: 'View plan', detail: 'Understand suggested change steps' },
            { label: 'View diff', detail: 'Check whether code changes match expectations' },
            { label: 'View tests', detail: 'Confirm automated test results' },
            { label: 'View PR notes', detail: 'Review the final delivery summary' },
          ],
        }
      }
      return {
        headline: `查看 ${task.id} 执行结果`,
        focus: `${task.title} 当前处于「${taskStage}」。你负责创建任务，并跟进分析、计划、Diff、测试和 PR 描述。`,
        actions: [
          {
            title: `查看 ${task.id} 执行结果`,
            detail: '查看 AI 分析、修改计划、Diff、测试结果和 PR 描述。',
            meta: '开发者跟进',
            to: `/tasks/${task.id}`,
            icon: FileDiff,
            tone: 'info',
            primary: true,
          },
          {
            title: '创建新任务',
            detail: '从 Gitee Issue 发起新的 Agent 任务，系统自动补全分支和测试命令。',
            meta: '可创建',
            to: '/tasks/new',
            icon: ListTodo,
            tone: 'warning',
            primary: false,
          },
        ],
        operations: [
          { label: '创建任务', detail: '提交项目、仓库和 Issue ID' },
          { label: '查看分析', detail: '确认影响范围和风险点' },
          { label: '查看计划', detail: '理解 AI 建议的修改步骤' },
          { label: '查看 Diff', detail: '检查代码变更是否符合预期' },
          { label: '查看测试', detail: '确认自动化测试结果' },
          { label: '查看 PR 描述', detail: '复核最终交付说明' },
        ],
      }
    case 'lead':
      if (isEnglish.value) {
        return {
          headline: `Review process quality for ${task.id}`,
          focus: `${task.title} is currently in "${taskStage}". You review replay, metrics, and audits to locate handoff blockers.`,
          actions: [
            {
              title: `Replay ${task.id}`,
              detail: 'Inspect task timeline, execution events, approval records, and failure points.',
              meta: 'Read-only replay',
              to: `/replay/${task.id}`,
              icon: History,
              tone: 'info',
              primary: true,
            },
            {
              title: 'Locate blocked tasks',
              detail: `${failedTask.value.id} is still failed; open task detail to inspect the reason.`,
              meta: 'Issue diagnosis',
              to: `/tasks/${failedTask.value.id}`,
              icon: RefreshCw,
              tone: 'warning',
              primary: false,
            },
          ],
          operations: [
            { label: 'Task replay', detail: 'Review every execution node by timeline' },
            { label: 'Metrics', detail: 'Watch automation rate, pending items, and saved hours' },
            { label: 'Audit view', detail: 'Check approvals, failures, and config changes' },
            { label: 'Export retrospectives', detail: 'Capture process issues and improvements' },
            { label: 'Issue diagnosis', detail: 'Trace failed nodes and root causes' },
          ],
        }
      }
      return {
        headline: `复盘 ${task.id} 的过程质量`,
        focus: `${task.title} 当前处于「${taskStage}」。你负责看回放、统计和审计，定位角色接力中的阻塞点。`,
        actions: [
          {
            title: `复盘 ${task.id}`,
            detail: '查看任务时间线、执行事件、审批记录和失败节点。',
            meta: '只读复盘',
            to: `/replay/${task.id}`,
            icon: History,
            tone: 'info',
            primary: true,
          },
          {
            title: '定位阻塞任务',
            detail: `${failedTask.value.id} 仍处于失败状态，可进入任务详情查看原因。`,
            meta: '问题定位',
            to: `/tasks/${failedTask.value.id}`,
            icon: RefreshCw,
            tone: 'warning',
            primary: false,
          },
        ],
        operations: [
          { label: '任务回放', detail: '按时间线查看每个执行节点' },
          { label: '统计', detail: '关注自动化率、待处理和节省工时' },
          { label: '审计视图', detail: '核对审批、失败和配置变更记录' },
          { label: '复盘导出', detail: '沉淀过程问题和改进结论' },
          { label: '问题定位', detail: '追踪失败节点和异常原因' },
        ],
      }
    default:
      if (isEnglish.value) {
        return {
          headline: `Prioritize high-risk approval for ${task.id}`,
          focus: `${displayProjectName(store.activeProject)} is active. Low-risk tasks flow automatically; failed tasks and index exceptions are highlighted separately.`,
          actions: [
            {
              title: `View ${task.id}`,
              detail: 'Check the current stage, risk, and recommended next step.',
              meta: 'Task follow-up',
              to: `/tasks/${task.id}`,
              icon: ClipboardCheck,
              tone: 'info',
              primary: true,
            },
          ],
          operations: [
            { label: 'View tasks', detail: 'Confirm current status and next step' },
            { label: 'Await assignment', detail: 'Show entries based on role permissions' },
          ],
        }
      }
      return {
        headline: `今天优先处理 ${task.id} 的高风险审批`,
        focus: `当前项目 ${store.activeProject}。低风险任务自动流转，失败任务和索引异常会单独提醒你。`,
        actions: [
          {
            title: `查看 ${task.id}`,
            detail: '查看当前任务阶段、风险和处理建议。',
            meta: '任务跟进',
            to: `/tasks/${task.id}`,
            icon: ClipboardCheck,
            tone: 'info',
            primary: true,
          },
        ],
        operations: [
          { label: '查看任务', detail: '确认当前任务状态与下一步' },
          { label: '等待分配', detail: '根据角色权限显示可操作入口' },
        ],
      }
  }
})

const nextActions = computed(() => roleWorkbench.value.actions)

const focusItems = computed(() => {
  const items: Array<{ title: string; detail: string; to: string; icon: typeof AlertTriangle; tone: 'danger' | 'warning' | 'info' }> = []
  for (const overdue of overdueApprovals.value) {
    items.push(
      isEnglish.value
        ? { title: 'Approval waiting over 24 hours', detail: `${overdue.id} · ${overdue.title}`, to: '/approvals', icon: AlertTriangle, tone: 'danger' }
        : { title: '审批等待超过 24 小时', detail: `${overdue.id} · ${overdue.title}`, to: '/approvals', icon: AlertTriangle, tone: 'danger' },
    )
  }
  if (!highRiskTask.value) {
    if (items.length) return items
    return [
      {
        title: isEnglish.value ? 'No tasks need attention' : '暂无需要处理的事项',
        detail: isEnglish.value
          ? 'Create a task or wait for new items to arrive; they will be listed here.'
          : '创建新任务或等待新事项进入，需要处理的内容会显示在这里。',
        to: '/tasks',
        icon: ListTodo,
        tone: 'info',
      },
    ]
  }
  switch (displayUser.value?.role) {
    case 'admin':
      items.push(
        isEnglish.value
          ? { title: 'Configure repositories and models', detail: `${displayProjectName(store.activeProject)} · Security policy pending review`, to: '/settings', icon: Settings, tone: 'info' }
          : { title: '配置仓库与模型', detail: `${store.activeProject} · 安全策略待核对`, to: '/settings', icon: Settings, tone: 'info' },
        isEnglish.value
          ? { title: 'Handle account recovery', detail: 'Review password recovery requests from members', to: '/account-recovery', icon: ShieldAlert, tone: 'warning' }
          : { title: '处理账号恢复', detail: '查看成员提交的找回申请', to: '/account-recovery', icon: ShieldAlert, tone: 'warning' },
        isEnglish.value
          ? { title: 'View all tasks', detail: `${store.tasks.length} tasks available`, to: '/tasks', icon: ListTodo, tone: 'warning' }
          : { title: '查看全部任务', detail: `${store.tasks.length} 项任务可查看`, to: '/tasks', icon: ListTodo, tone: 'warning' },
        isEnglish.value
          ? { title: 'Check index status', detail: `${displayProjectName(store.activeProject)} · main`, to: '/indexes', icon: FolderSearch, tone: 'info' }
          : { title: '检查索引状态', detail: `${store.activeProject} · main`, to: '/indexes', icon: FolderSearch, tone: 'info' },
      )
      break
    case 'approver':
      items.push(
        isEnglish.value
          ? { title: 'High-risk plan pending', detail: `${highRiskTask.value.id} · ${highRiskTask.value.title}`, to: '/approvals', icon: AlertTriangle, tone: 'danger' }
          : { title: '高风险计划待审批', detail: `${highRiskTask.value.id} · ${highRiskTask.value.title}`, to: '/approvals', icon: AlertTriangle, tone: 'danger' },
        isEnglish.value
          ? { title: 'Add approval notes', detail: 'Clarify risk judgment before approving, rejecting, or rewriting', to: '/approvals', icon: ClipboardCheck, tone: 'warning' }
          : { title: '补充审批意见', detail: '通过、拒绝、重写前先写清风险判断', to: '/approvals', icon: ClipboardCheck, tone: 'warning' },
        isEnglish.value
          ? { title: 'Approval history', detail: 'Review plan versions and approval records', to: '/approvals', icon: History, tone: 'info' }
          : { title: '审批历史', detail: '查看计划版本和审批记录', to: '/approvals', icon: History, tone: 'info' },
      )
      break
    case 'developer':
      items.push(
        isEnglish.value
          ? { title: 'Review execution result', detail: `${highRiskTask.value.id} · ${formatStatus(highRiskTask.value.status)}`, to: `/tasks/${highRiskTask.value.id}`, icon: FileDiff, tone: 'info' }
          : { title: '查看执行结果', detail: `${highRiskTask.value.id} · ${statusText[highRiskTask.value.status]}`, to: `/tasks/${highRiskTask.value.id}`, icon: FileDiff, tone: 'info' },
        isEnglish.value
          ? { title: 'Create task', detail: 'Start an Agent task from a Gitee issue', to: '/tasks/new', icon: ListTodo, tone: 'warning' }
          : { title: '创建新任务', detail: '从 Gitee Issue 发起 Agent 任务', to: '/tasks/new', icon: ListTodo, tone: 'warning' },
        isEnglish.value
          ? { title: 'Follow failed item', detail: `${failedTask.value.id} · ${failedTask.value.title}`, to: `/tasks/${failedTask.value.id}`, icon: RefreshCw, tone: 'danger' }
          : { title: '跟进失败项', detail: `${failedTask.value.id} · ${failedTask.value.title}`, to: `/tasks/${failedTask.value.id}`, icon: RefreshCw, tone: 'danger' },
      )
      break
    case 'lead':
      items.push(
        isEnglish.value
          ? { title: 'Replay task flow', detail: `${highRiskTask.value.id} · ${highRiskTask.value.title}`, to: `/replay/${highRiskTask.value.id}`, icon: History, tone: 'info' }
          : { title: '复盘任务回放', detail: `${highRiskTask.value.id} · ${highRiskTask.value.title}`, to: `/replay/${highRiskTask.value.id}`, icon: History, tone: 'info' },
        isEnglish.value
          ? { title: 'View approval records', detail: 'Read-only view of approval flow and plan versions', to: '/approvals', icon: ClipboardCheck, tone: 'warning' }
          : { title: '查看审批记录', detail: '只读查看审批链路和计划版本', to: '/approvals', icon: ClipboardCheck, tone: 'warning' },
        isEnglish.value
          ? { title: 'Diagnose failure reason', detail: `${failedTask.value.id} · Authorization status abnormal`, to: `/tasks/${failedTask.value.id}`, icon: RefreshCw, tone: 'danger' }
          : { title: '定位失败原因', detail: `${failedTask.value.id} · 授权状态异常`, to: `/tasks/${failedTask.value.id}`, icon: RefreshCw, tone: 'danger' },
      )
      break
    default:
      items.push(
        isEnglish.value
          ? { title: 'View tasks', detail: `${store.tasks.length} tasks available`, to: '/tasks', icon: ListTodo, tone: 'info' }
          : { title: '查看任务列表', detail: `${store.tasks.length} 项任务可浏览`, to: '/tasks', icon: ListTodo, tone: 'info' },
      )
  }
  return items
})
</script>

<template>
  <section class="page-content dashboard">
    <section class="workbench-hero">
      <article class="panel workbench-banner">
        <div class="workbench-banner__top">
          <div class="workbench-banner__identity">
            <span class="workbench-banner__avatar">{{ userAvatar }}</span>
            <div>
              <p class="section-kicker">{{ greeting }}{{ isEnglish ? ', ' : '，' }}{{ displayUser?.name ?? '—' }} · {{ roleLabel() }}</p>
              <h2>{{ roleWorkbench.headline }}</h2>
              <p>{{ roleWorkbench.focus }}</p>
            </div>
          </div>
          <div class="workbench-banner__pills">
            <span class="workbench-banner__pill">{{ dashboardText.workbench }}</span>
            <span class="workbench-banner__pill workbench-banner__pill--muted">{{ dashboardPendingApprovals }} {{ dashboardText.pendingApproval }}</span>
          </div>
        </div>

        <div class="workbench-next-actions">
          <RouterLink
            v-for="action in nextActions"
            :key="action.title"
            :to="action.to"
            class="next-action-card"
            :class="[`next-action-card--${action.tone}`, { 'next-action-card--primary': action.primary }]"
          >
            <span class="next-action-card__icon">
              <component :is="action.icon" :size="17" />
            </span>
            <span class="next-action-card__body">
              <small>{{ action.meta }}</small>
              <strong>{{ action.title }}</strong>
              <span>{{ action.detail }}</span>
            </span>
            <ArrowUpRight :size="15" />
          </RouterLink>
        </div>

        <div class="workbench-automation-strip" role="group" :aria-label="dashboardText.operationsLabel">
          <span v-for="operation in roleWorkbench.operations" :key="operation.label">
            <b>{{ operation.label }}</b>
            <small>{{ operation.detail }}</small>
          </span>
        </div>
      </article>

      <article class="panel workbench-summary">
        <header class="panel__header">
          <div>
            <p class="section-kicker">{{ dashboardText.projectHealth }}</p>
            <h2>{{ dashboardText.automationRate }} {{ automationLabel }}</h2>
          </div>
        </header>
        <div class="workbench-summary__body">
          <div class="workbench-summary__ring" :style="{ '--progress': automationProgress }">
            <div class="workbench-summary__ring-inner">
              <strong>{{ automationLabel }}</strong>
              <span>{{ dashboardText.automationRate }}</span>
            </div>
          </div>
          <div class="workbench-summary__stats">
            <div><strong>{{ store.dashboardSummary.runningTasks }}</strong><small>{{ dashboardText.running }}</small></div>
            <div><strong>{{ dashboardPendingApprovals }}</strong><small>{{ dashboardText.pendingApproval }}</small></div>
            <div><strong>{{ store.dashboardSummary.savedHours }}</strong><small>{{ dashboardText.savedHours }}</small></div>
            <div><strong>{{ store.dashboardSummary.completedThisWeek }}</strong><small>{{ dashboardText.completedThisWeek }}</small></div>
          </div>
        </div>
      </article>
    </section>

    <section class="metric-grid">
      <article class="metric-card metric-card--risk">
        <span class="metric-card__icon"><ShieldAlert :size="18" /></span>
        <p>{{ dashboardText.humanRiskOnly }}</p><strong>{{ riskTasks }}</strong><small>{{ dashboardText.highRiskFirst }}</small>
      </article>
      <article class="metric-card metric-card--approval">
        <span class="metric-card__icon"><ClipboardCheck :size="18" /></span>
        <p>{{ dashboardText.approvalQueue }}</p><strong>{{ dashboardPendingApprovals }}</strong><small>{{ store.dashboardSummary.overdueApprovals }} {{ dashboardText.overdueHours }}</small>
      </article>
      <article class="metric-card metric-card--running">
        <span class="metric-card__icon"><Activity :size="18" /></span>
        <p>{{ dashboardText.autoSaved }}</p><strong>{{ store.dashboardSummary.savedHours }}</strong><small>{{ dashboardText.agentHours }}</small>
      </article>
      <article class="metric-card metric-card--done">
        <span class="metric-card__icon"><Clock3 :size="18" /></span>
        <p>{{ dashboardText.weeklyDelivery }}</p><strong>{{ store.dashboardSummary.completedThisWeek }}</strong><small>{{ dashboardText.lastWeek }} {{ store.dashboardSummary.weeklyGrowthLabel }}</small>
      </article>
    </section>

    <section class="dashboard-grid">
      <article class="panel panel--wide">
        <header class="panel__header">
          <div><h2>{{ dashboardText.recentTasks }}</h2><p>{{ dashboardText.recentSort }}</p></div>
          <RouterLink to="/tasks" class="text-link">{{ dashboardText.viewAll }} <ArrowUpRight :size="15" /></RouterLink>
        </header>
        <div class="table-wrap">
          <table class="data-table dashboard-table">
            <thead><tr><th>{{ dashboardText.task }}</th><th>Issue</th><th>{{ dashboardText.currentStage }}</th><th>{{ dashboardText.risk }}</th><th>{{ dashboardText.approver }}</th><th>{{ dashboardText.updatedAt }}</th></tr></thead>
            <tbody>
              <tr v-if="recentTasks.length === 0">
                <td class="dashboard-table__empty muted" colspan="6">{{ dashboardText.noRecentTasks }}</td>
              </tr>
              <tr v-for="task in recentTasks" :key="task.id" @click="store.selectTask(task.id)">
                <td><RouterLink :to="`/tasks/${task.id}`" class="task-cell"><strong>{{ task.title }}</strong><small>{{ task.repository }}</small></RouterLink></td>
                <td class="mono">{{ task.issue }}</td><td><span class="status-tag" :class="statusTone(task.status)">{{ formatStatus(task.status) }}</span></td><td><span class="status-tag" :class="riskTone(task.risk)">{{ formatRisk(task.risk) }}</span></td>
                <td>{{ task.approver }}</td><td class="muted">{{ task.updatedAt }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </article>

      <article class="panel">
        <header class="panel__header"><div><h2>{{ dashboardText.needsAttention }}</h2><p>{{ dashboardText.sortByPriority }}</p></div></header>
        <div class="attention-list">
          <RouterLink
            v-for="item in focusItems"
            :key="item.title"
            :to="item.to"
            class="attention-item attention-item--card"
          >
            <span class="attention-item__icon" :class="`attention-item__icon--${item.tone}`">
              <component :is="item.icon" :size="17" />
            </span>
            <span><strong>{{ item.title }}</strong><small>{{ item.detail }}</small></span>
            <ArrowUpRight :size="16" />
          </RouterLink>
        </div>
      </article>
    </section>

    <section class="dashboard-grid dashboard-grid--lower">
      <article class="panel panel--wide">
        <header class="panel__header delivery-trend__header">
          <div><h2>{{ dashboardText.taskTrend }}</h2><p>{{ dashboardText.sevenDayTrend }}</p></div>
          <div class="delivery-trend__controls" role="group" aria-label="趋势时间范围">
            <button
              v-for="days in [7, 30] as const"
              :key="days"
              type="button"
              class="delivery-trend__range"
              :class="{ 'is-active': store.dashboardSummary.trendDays === days }"
              :disabled="trendLoading"
              :aria-pressed="store.dashboardSummary.trendDays === days"
              :data-testid="`trend-range-${days}`"
              @click="setTrendRange(days)"
            >
              {{ days === 7 ? dashboardText.sevenDays : dashboardText.thirtyDays }}
            </button>
          </div>
        </header>
        <div class="delivery-trend__summary" role="group" :aria-label="dashboardText.trendSummary">
          <span><small>{{ dashboardText.totalCreated }}</small><strong>{{ trendTotalCreated }}</strong></span>
          <span><small>{{ dashboardText.totalCompleted }}</small><strong>{{ trendTotalDone }}</strong></span>
          <span><small>{{ dashboardText.totalFailed }}</small><strong>{{ trendTotalFailed }}</strong></span>
          <span><small>{{ dashboardText.completionRate }}</small><strong>{{ trendCompletionRate }}</strong></span>
        </div>
        <div class="delivery-trend__toolbar">
          <div class="delivery-trend__legend" role="group" :aria-label="dashboardText.trendMetrics">
            <button
              v-for="series in [
                { key: 'created', label: dashboardText.created },
                { key: 'done', label: dashboardText.done },
                { key: 'failed', label: dashboardText.failed },
                { key: 'successRate', label: dashboardText.deliverySuccessRate },
              ] as const"
              :key="series.key"
              type="button"
              class="delivery-trend__legend-item"
              :class="[`delivery-trend__legend-item--${series.key}`, { 'is-muted': !visibleTrendSeries[series.key] }]"
              :aria-pressed="visibleTrendSeries[series.key]"
              :data-testid="`trend-series-${series.key}`"
              @click="toggleTrendSeries(series.key)"
            >
              <i aria-hidden="true" />
              {{ series.label }}
            </button>
          </div>
          <span class="delivery-trend__freshness">{{ dashboardText.refreshedAt }} {{ formatGeneratedAt(store.dashboardSummary.generatedAt) }}</span>
        </div>
        <div class="delivery-trend__chart" :class="{ 'is-loading': trendLoading }">
          <div v-if="trendLoading" class="delivery-trend__loading" aria-live="polite" />
          <p v-if="trendError" class="delivery-trend__error" role="alert">{{ trendError }}</p>
          <div v-else-if="!trendHasData" class="delivery-trend__empty">{{ dashboardText.noTrendData }}</div>
          <svg
            v-else
            data-testid="delivery-trend-svg"
            class="delivery-trend__svg"
            viewBox="0 0 1000 272"
            role="img"
            :aria-label="dashboardText.taskTrend"
          >
            <g class="delivery-trend__grid">
              <template v-for="ratio in trendGridRatios" :key="ratio">
                <line :x1="trendChart.left" :x2="trendChart.right" :y1="trendChart.top + ratio * (trendChart.bottom - trendChart.top)" :y2="trendChart.top + ratio * (trendChart.bottom - trendChart.top)" />
                <text :x="trendChart.left - 12" :y="trendChart.top + ratio * (trendChart.bottom - trendChart.top) + 4" text-anchor="end">{{ trendAxisCount(ratio) }}</text>
              </template>
              <template v-for="value in trendRightAxisValues" :key="value">
                <text
                  class="delivery-trend__right-axis-label"
                  :x="trendChart.right + 12"
                  :y="rateY(value) + 4"
                  text-anchor="start"
                >{{ value }}%</text>
              </template>
            </g>
            <g class="delivery-trend__series">
              <path v-if="visibleTrendSeries.created" class="delivery-trend__line delivery-trend__line--created" :d="trendSeriesPath('created')" />
              <path v-if="visibleTrendSeries.done" class="delivery-trend__line delivery-trend__line--done" :d="trendSeriesPath('done')" />
              <path v-if="visibleTrendSeries.failed" class="delivery-trend__line delivery-trend__line--failed" :d="trendSeriesPath('failed')" />
              <path v-if="visibleTrendSeries.successRate" class="delivery-trend__line delivery-trend__line--success-rate" :d="trendSeriesPath('successRate')" />
              <g v-for="(point, index) in store.dashboardSummary.trend" :key="point.date">
                <template v-for="series in ['created', 'done', 'failed', 'successRate'] as const" :key="series">
                  <circle
                    v-if="visibleTrendSeries[series] && trendSeriesValue(point, series) !== null"
                    class="delivery-trend__point"
                    :class="`delivery-trend__point--${series}`"
                    :cx="trendX(index)"
                    :cy="series === 'successRate' ? rateY(trendSeriesValue(point, series)) : countY(trendSeriesValue(point, series) ?? 0)"
                    r="4.5"
                    tabindex="0"
                    @mouseenter="selectTrendPoint(index)"
                    @focus="selectTrendPoint(index)"
                    @keydown.enter.prevent="selectTrendPoint(index)"
                  >
                    <title>{{ `${displayTrendDate(point)} · ${dashboardText.created} ${point.created} · ${dashboardText.done} ${point.done} · ${dashboardText.failed} ${point.failed} · ${dashboardText.deliverySuccessRate} ${point.successRate ?? '—'}%` }}</title>
                  </circle>
                </template>
                <text
                  v-if="shouldShowTrendLabel(index)"
                  class="delivery-trend__x-label"
                  :x="trendX(index)"
                  y="254"
                  text-anchor="middle"
                >{{ displayTrendDate(point) }}</text>
              </g>
            </g>
          </svg>
        </div>
        <div class="delivery-trend__inspection" :class="{ 'has-selection': selectedTrendPoint }">
          <template v-if="selectedTrendPoint">
            <strong>{{ displayTrendDate(selectedTrendPoint) }}</strong>
            <span>{{ dashboardText.created }} <b>{{ selectedTrendPoint.created }}</b></span>
            <span>{{ dashboardText.done }} <b>{{ selectedTrendPoint.done }}</b></span>
            <span>{{ dashboardText.failed }} <b>{{ selectedTrendPoint.failed }}</b></span>
            <span>{{ dashboardText.deliverySuccessRate }} <b>{{ selectedTrendPoint.successRate === null ? '—' : `${selectedTrendPoint.successRate}%` }}</b></span>
          </template>
          <span v-else>{{ dashboardText.inspectPoint }}</span>
        </div>
        <div class="delivery-trend__footer">
          <span><b>{{ dashboardPendingApprovals }}</b> {{ dashboardText.pendingGovernance }}</span>
          <span><b>{{ blockedTasks }}</b> {{ dashboardText.blockedRisk }}</span>
          <p>{{ dashboardText.throughputHint }}</p>
        </div>
      </article>
      <article class="panel">
        <header class="panel__header"><div><h2>{{ dashboardText.executionLog }}</h2><p>{{ dashboardText.latestEvents }}</p></div></header>
        <ol class="event-list">
          <li v-for="item in store.dashboardSummary.events" :key="`${item.title}-${item.detail}`">
            <span class="event-dot" :class="`event-dot--${item.level}`" />
            <div><strong>{{ displayEventTitle(item.title) }}</strong><p>{{ item.detail }}</p></div>
          </li>
        </ol>
      </article>
    </section>
  </section>
</template>
