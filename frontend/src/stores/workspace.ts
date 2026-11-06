// 文件级注释：工作区 Pinia Store。
// 负责"全站共享状态"：项目列表、任务列表、当前选中任务、待审批角标、健康度卡片等。
// 数据源语义（DEV-009）：
//   1) 任务域数据源分 mock / real 两种模式，由 services/dataSourceMode 决定（默认 real）。
//   2) 视图不感知来源：读取统一走 ensureTaskLoaded / refreshTaskList，模式分支收敛在本文件与 taskData。
//   3) 快照按模式隔离（key 带模式后缀），防止 demo 种子与后端任务跨模式混入。
//   4) mock 演示任务只在本模式可见；real 模式以 API 为准（replace 语义，不保留本地残留）。
import { defineStore } from 'pinia'
import type { AgentTask, DashboardSummary, DashboardTrendDays, DashboardTrendPoint, DiffArtifact, ModificationPlan, PrDescriptionOverview, PrDescriptionRecord, RepositoryIndex, RiskThreshold, Task, TaskStatus, TestRun } from '../types/domain'
import { apiRequest } from '../services/api'
import { getDataSourceMode } from '../services/dataSourceMode'
import { demoTasks } from '../services/mockTaskSource'
import { formatTimestamp, getTaskArtifact, mapAgentTask, registerTaskArtifact } from '../services/taskMapper'
import { getTask, listTasks, TaskNotFoundError, type TaskListQuery } from '../services/taskData'

const ACTIVE_PROJECT_KEY = 'frontend-active-project'
const ACTIVE_PROJECT_ID_KEY = 'frontend-active-project-id'
const SNAPSHOT_KEY_BASE = 'frontend-workspace-snapshot-v1'
const LEGACY_SNAPSHOT_KEY = 'frontend-workspace-snapshot-v1'
const SNAPSHOT_VERSION = 1

function snapshotKey() {
  return `${SNAPSHOT_KEY_BASE}-${getDataSourceMode()}`
}

const dashboardSummary: DashboardSummary = {
  runningTasks: 3,
  pendingApprovals: 1,
  completedThisWeek: 26,
  savedHours: 126,
  automationRateLabel: '78%',
  overdueApprovals: 1,
  weeklyGrowthLabel: '+18%',
  trendDays: 7,
  generatedAt: '2026-09-15T09:30:00Z',
  trend: [
    { day: '周三', date: '2026-09-09', created: 6, done: 3, failed: 1, successRate: 75 },
    { day: '周四', date: '2026-09-10', created: 7, done: 4, failed: 1, successRate: 80 },
    { day: '周五', date: '2026-09-11', created: 8, done: 5, failed: 0, successRate: 100 },
    { day: '周六', date: '2026-09-12', created: 9, done: 6, failed: 1, successRate: 86 },
    { day: '周日', date: '2026-09-13', created: 8, done: 4, failed: 2, successRate: 67 },
    { day: '周一', date: '2026-09-14', created: 5, done: 2, failed: 0, successRate: 100 },
    { day: '今天', date: '2026-09-15', created: 6, done: 2, failed: 1, successRate: 67 },
  ],
  events: [
    { level: 'success', title: '索引任务已完成', detail: 'tenant-api / main · 12:08' },
    { level: 'warning', title: '审批等待超过 24 小时', detail: 'TASK-8837 · 11:46' },
    { level: 'info', title: '新的分析计划已生成', detail: 'TASK-8842 · 10:42' },
    { level: 'danger', title: 'Gitee 授权验证失败', detail: 'console-web · 09:17' },
  ],
}

function normalizeTrendPoint(point: Partial<DashboardTrendPoint>, index: number): DashboardTrendPoint {
  const fallback = dashboardSummary.trend[Math.min(index, dashboardSummary.trend.length - 1)]
  return {
    day: point.day ?? fallback.day,
    date: point.date ?? fallback.date,
    created: Number.isFinite(point.created) ? Number(point.created) : 0,
    done: Number.isFinite(point.done) ? Number(point.done) : 0,
    failed: Number.isFinite(point.failed) ? Number(point.failed) : 0,
    successRate: point.successRate === null || point.successRate === undefined
      ? null
      : Number.isFinite(point.successRate) ? Number(point.successRate) : null,
  }
}

function normalizeDashboardSummary(summary?: Partial<DashboardSummary> | null): DashboardSummary {
  return {
    runningTasks: summary?.runningTasks ?? dashboardSummary.runningTasks,
    pendingApprovals: summary?.pendingApprovals ?? dashboardSummary.pendingApprovals,
    completedThisWeek: summary?.completedThisWeek ?? dashboardSummary.completedThisWeek,
    savedHours: summary?.savedHours ?? dashboardSummary.savedHours,
    automationRateLabel: summary?.automationRateLabel ?? dashboardSummary.automationRateLabel,
    overdueApprovals: summary?.overdueApprovals ?? dashboardSummary.overdueApprovals,
    weeklyGrowthLabel: summary?.weeklyGrowthLabel ?? dashboardSummary.weeklyGrowthLabel,
    trendDays: summary?.trendDays === 30 ? 30 : 7,
    generatedAt: summary?.generatedAt ?? dashboardSummary.generatedAt,
    trend: summary?.trend?.map(normalizeTrendPoint) ?? dashboardSummary.trend,
    events: summary?.events ?? dashboardSummary.events,
  }
}

interface WorkspaceSnapshot {
  version: number
  activeProject: string
  activeProjectId?: string
  selectedTaskId: string
  tasks: Task[]
  dashboardSummary: DashboardSummary
}

function readStoredProject() {
  if (typeof window === 'undefined') return '支付中台'
  return localStorage.getItem(ACTIVE_PROJECT_KEY) || '支付中台'
}

function readStoredProjectId() {
  if (typeof window === 'undefined') return 'proj_payment'
  return localStorage.getItem(ACTIVE_PROJECT_ID_KEY) || 'proj_payment'
}

/**
 * 函数级注释：从 localStorage 同步读取当前模式的工作区快照。
 * 入参：无。
 * 返回值：WorkspaceSnapshot | null。读不到/解析失败返回 null。
 * 异常：JSON.parse 失败时返回 null，不抛出。
 */
function readSnapshot(): WorkspaceSnapshot | null {
  if (typeof window === 'undefined') return null
  try {
    const raw = localStorage.getItem(snapshotKey())
    if (!raw) return null
    const parsed = JSON.parse(raw) as WorkspaceSnapshot
    if (!parsed || parsed.version !== SNAPSHOT_VERSION) return null
    if (!Array.isArray(parsed.tasks) || parsed.tasks.length === 0) return null
    return parsed
  } catch {
    return null
  }
}

/**
 * 函数级注释：把工作区当前状态同步写入当前模式的 localStorage 快照。
 * 入参：无（直接读 store state）。
 * 返回值：boolean，是否写入成功。
 * 异常：localStorage 写入失败时（如 quota exceeded）返回 false，不抛出。
 */
function writeSnapshot(state: {
  activeProject: string
  activeProjectId: string
  selectedTaskId: string
  tasks: Task[]
  dashboardSummary: DashboardSummary
}): boolean {
  if (typeof window === 'undefined') return false
  try {
    const payload: WorkspaceSnapshot = {
      version: SNAPSHOT_VERSION,
      activeProject: state.activeProject,
      activeProjectId: state.activeProjectId,
      selectedTaskId: state.selectedTaskId,
      tasks: state.tasks,
      dashboardSummary: state.dashboardSummary,
    }
    localStorage.setItem(snapshotKey(), JSON.stringify(payload))
    return true
  } catch {
    return false
  }
}

/** 旧版无模式后缀快照 key：一次性清理，防止跨模式数据混入。 */
function removeLegacySnapshot() {
  if (typeof window === 'undefined') return
  if (localStorage.getItem(LEGACY_SNAPSHOT_KEY) !== null) {
    localStorage.removeItem(LEGACY_SNAPSHOT_KEY)
  }
}

export const useWorkspaceStore = defineStore('workspace', {
  // 文件级注释：state() 按数据源模式初始化——real 以 API 为准（快照仅为刷新缓存），
  // mock 无快照时回退 demo 演示任务。快照与模式一一对应，互不污染。
  state: () => {
    removeLegacySnapshot()
    const isMock = getDataSourceMode() === 'mock'
    const snapshot = readSnapshot()
    if (snapshot) {
      return {
        projectName: '司契 Siqi',
        tasks: snapshot.tasks,
        selectedTaskId: snapshot.selectedTaskId || (isMock ? 'TASK-8842' : ''),
        selectedTaskIds: [] as string[],
        activeProject: snapshot.activeProject || readStoredProject(),
        activeProjectId: snapshot.activeProjectId || readStoredProjectId(),
        dashboardSummary: normalizeDashboardSummary(snapshot.dashboardSummary),
        repoIndexes: [] as RepositoryIndex[],
        plansByTask: {} as Record<string, ModificationPlan[]>,
        approvalQueue: [] as Task[],
        diffsByTask: {} as Record<string, DiffArtifact[]>,
        testsByTask: {} as Record<string, TestRun[]>,
        prDescriptionsByTask: {} as Record<string, PrDescriptionRecord[]>,
      }
    }
    return {
      projectName: '司契 Siqi',
      tasks: isMock ? demoTasks.map((task) => ({ ...task })) : ([] as Task[]),
      selectedTaskId: isMock ? 'TASK-8842' : '',
      selectedTaskIds: [] as string[],
      activeProject: readStoredProject(),
      activeProjectId: readStoredProjectId(),
      dashboardSummary,
      repoIndexes: [] as RepositoryIndex[],
      plansByTask: {} as Record<string, ModificationPlan[]>,
      approvalQueue: [] as Task[],
      diffsByTask: {} as Record<string, DiffArtifact[]>,
      testsByTask: {} as Record<string, TestRun[]>,
      prDescriptionsByTask: {} as Record<string, PrDescriptionRecord[]>,
    }
  },
  getters: {
    selectedTask: (state) => state.tasks.find((task) => task.id === state.selectedTaskId) ?? state.tasks[0],
    pendingTasks: (state) => state.tasks.filter((task) => task.status === 'WAITING_APPROVAL'),
    taskById: (state) => (taskId: string) => state.tasks.find((task) => task.id === taskId),
    artifactFor: (state) => (taskId: string) => getTaskArtifact(taskId, state.tasks.find((task) => task.id === taskId)),
    indexById: (state) => (indexId: string) => state.repoIndexes.find((record) => record.id === indexId) ?? null,
    /**
     * 函数级注释：取某任务当前生效（非 SUPERSEDED）版本最高的修改计划。
     * 入参：taskId string。
     * 返回值：ModificationPlan | null；无计划或全部被取代时按版本倒序回退最高版本。
     */
    latestPlanOf: (state) => (taskId: string): ModificationPlan | null => {
      const plans = state.plansByTask[taskId]
      if (!plans?.length) return null
      const active = plans.filter((plan) => plan.status !== 'SUPERSEDED')
      const pool = active.length ? active : plans
      return [...pool].sort((left, right) => right.version - left.version)[0]
    },
  },
  actions: {
    /**
     * 函数级注释：立即把当前 store 状态写入当前模式的 localStorage 快照。
     * 入参：无。
     * 返回值：boolean。
     * 异常：写失败返回 false。
     */
    persistSnapshot(): boolean {
      return writeSnapshot({
        activeProject: this.activeProject,
        activeProjectId: this.activeProjectId,
        selectedTaskId: this.selectedTaskId,
        tasks: this.tasks,
        dashboardSummary: this.dashboardSummary,
      })
    },
    /**
     * 函数级注释：清空当前模式的工作区快照（调试重置时调用）。
     * 入参：无。
     * 返回值：void。
     */
    clearSnapshot(): void {
      if (typeof window === 'undefined') return
      localStorage.removeItem(snapshotKey())
    },
    /**
     * 函数级注释：重置到 mock 演示数据，并清空当前模式快照。
     * 入参：无。
     * 返回值：void。
     */
    resetToMock(): void {
      this.tasks = demoTasks.map((task) => ({ ...task }))
      this.selectedTaskId = 'TASK-8842'
      this.selectedTaskIds = []
      this.activeProject = '支付中台'
      this.activeProjectId = 'proj_payment'
      this.dashboardSummary = dashboardSummary
      this.repoIndexes = []
      this.plansByTask = {}
      this.approvalQueue = []
      this.diffsByTask = {}
      this.testsByTask = {}
      this.prDescriptionsByTask = {}
      this.clearSnapshot()
    },
    /**
     * 函数级注释：合并（upsert）仓库索引记录，供详情页轮询与索引管理页共享。
     * 入参：record RepositoryIndex。
     * 返回值：void。
     */
    mergeRepositoryIndex(record: RepositoryIndex): void {
      const existing = this.repoIndexes.findIndex((item) => item.id === record.id)
      if (existing >= 0) {
        this.repoIndexes.splice(existing, 1, record)
      } else {
        this.repoIndexes.push(record)
      }
    },
    selectTask(taskId: string) {
      this.selectedTaskId = taskId
      this.persistSnapshot()
    },
    setTaskSelection(taskIds: string[]) {
      this.selectedTaskIds = [...new Set(taskIds)]
    },
    toggleTaskSelection(taskId: string) {
      this.selectedTaskIds = this.selectedTaskIds.includes(taskId)
        ? this.selectedTaskIds.filter((id) => id !== taskId)
        : [...this.selectedTaskIds, taskId]
    },
    clearTaskSelection() {
      this.selectedTaskIds = []
    },
    setActiveProject(projectId: string, projectName: string) {
      this.activeProjectId = projectId
      this.activeProject = projectName
      if (typeof window !== 'undefined') {
        localStorage.setItem(ACTIVE_PROJECT_KEY, projectName)
        localStorage.setItem(ACTIVE_PROJECT_ID_KEY, projectId)
      }
      this.persistSnapshot()
    },
    retryTask(taskId: string) {
      const task = this.tasks.find((item) => item.id === taskId)
      if (!task) return
      task.status = 'CREATED'
      task.updatedAt = formatTimestamp()
      this.persistSnapshot()
    },
    setTaskStatus(taskId: string, status: TaskStatus) {
      const task = this.tasks.find((item) => item.id === taskId)
      if (task) {
        task.status = status
        task.updatedAt = formatTimestamp()
      }
      this.selectedTaskIds = this.selectedTaskIds.filter((id) => id !== taskId)
      this.persistSnapshot()
    },
    createTask(input: Pick<Task, 'title' | 'issue' | 'repository' | 'risk'>) {
      const id = `TASK-${8850 + this.tasks.length}`
      const task: Task = {
        id,
        ...input,
        status: 'CREATED',
        approver: '待分配',
        updatedAt: '刚刚',
        branch: `agent/main/issue-${input.issue.replace('#', '')}-v1`,
        owner: '当前用户',
      }
      this.tasks.unshift(task)
      this.selectedTaskId = id
      this.persistSnapshot()
      return task
    },
    createTaskFromApi(agentTask: AgentTask) {
      const task = mapAgentTask(agentTask)
      const existingIndex = this.tasks.findIndex((item) => item.id === task.id)
      if (existingIndex >= 0) {
        this.tasks.splice(existingIndex, 1, task)
      } else {
        this.tasks.unshift(task)
      }
      this.selectedTaskId = task.id
      registerTaskArtifact(agentTask)
      if (agentTask.plans) {
        this.plansByTask[agentTask.id] = [...agentTask.plans]
      }
      if (agentTask.diffs) {
        this.diffsByTask[agentTask.id] = [...agentTask.diffs]
      }
      if (agentTask.test_runs) {
        this.testsByTask[agentTask.id] = [...agentTask.test_runs]
      }
      if (agentTask.pr_descriptions) {
        this.prDescriptionsByTask[agentTask.id] = [...agentTask.pr_descriptions]
      }
      this.persistSnapshot()
      return task
    },
    /**
     * 函数级注释：以 API 结果整体替换任务列表（real 模式刷新语义）。
     * 与旧 mergeTasksFromApi 不同：replace 会清掉本地 demo/残留任务，保证列表只展示当前数据源任务。
     * 入参：tasks Task[]（taskData 已注册对应产物）。
     * 返回值：void。
     */
    replaceTaskList(tasks: Task[]) {
      this.tasks = [...tasks]
      if (!this.taskById(this.selectedTaskId)) {
        this.selectedTaskId = tasks[0]?.id ?? ''
      }
      this.persistSnapshot()
    },
    /**
     * 函数级注释：单任务 upsert（直达详情/回放/Diff 读回时用），不改变当前选中。
     * 入参：task Task。
     * 返回值：void。
     */
    upsertTask(task: Task) {
      const existingIndex = this.tasks.findIndex((item) => item.id === task.id)
      if (existingIndex >= 0) {
        this.tasks.splice(existingIndex, 1, task)
      } else {
        this.tasks.unshift(task)
      }
      this.persistSnapshot()
    },
    /**
     * 函数级注释：列表刷新（TaskListView/AppShell 共用入口）。
     * real = 走后端并按 replace 语义落库；mock = 本地演示数据已在 state，空转不发请求。
     * 入参：query 可选过滤条件。
     * 返回值：Promise<void>；real 网络失败时向上抛（调用方负责 toast）。
     */
    async refreshTaskList(query: TaskListQuery = {}) {
      if (getDataSourceMode() === 'mock') return
      const scopedQuery: TaskListQuery = {
        ...query,
        projectId: query.projectId === '' ? undefined : (query.projectId ?? this.activeProjectId ?? undefined),
      }
      const tasks = await listTasks(scopedQuery)
      this.replaceTaskList(tasks)
    },
    /**
     * 函数级注释：按模式刷新工作台摘要卡片（项目健康度、趋势、事件）。
     * mock = 维持本地演示摘要；real = 走后端总览接口，失败时不覆盖当前摘要。
     * 入参：无。
     * 返回值：Promise<DashboardSummary>；real 失败向上抛。
     */
    async refreshDashboardSummary(days?: DashboardTrendDays) {
      const selectedDays = days ?? this.dashboardSummary.trendDays
      if (getDataSourceMode() === 'mock') {
        this.dashboardSummary = { ...dashboardSummary, trendDays: selectedDays }
        this.persistSnapshot()
        return this.dashboardSummary
      }
      const projectName = encodeURIComponent(this.activeProject)
      const summary = await apiRequest<Partial<DashboardSummary>>(`/api/dashboard/automation-summary?project_name=${projectName}&days=${selectedDays}`)
      this.dashboardSummary = normalizeDashboardSummary(summary)
      this.persistSnapshot()
      return this.dashboardSummary
    },
    /**
     * 函数级注释：按模式刷新审批中心队列（真实模式 owner 范围：approver 只见指派给自己的，admin 全量）。
     * mock = 清空队列（mock 审批中心仍用 pendingTasks 演示数据，不依赖本队列）。
     * 返回值：Promise<void>；real 网络失败向上抛（调用方负责提示）。
     */
    async loadApprovalQueue() {
      if (getDataSourceMode() === 'mock') {
        this.approvalQueue = []
        return
      }
      const items = await apiRequest<AgentTask[]>('/api/approvals/pending')
      const tasks: Task[] = []
      for (const agentTask of items) {
        const task = mapAgentTask(agentTask)
        tasks.push(task)
        registerTaskArtifact(agentTask)
        if (agentTask.plans) {
          this.plansByTask[agentTask.id] = [...agentTask.plans]
        }
        if (agentTask.diffs) {
          this.diffsByTask[agentTask.id] = [...agentTask.diffs]
        }
        // 队列任务同步进任务列表：审批中心聚焦/批量选择按 taskById 读取，不改当前选中
        const existingIndex = this.tasks.findIndex((item) => item.id === task.id)
        if (existingIndex >= 0) {
          this.tasks.splice(existingIndex, 1, task)
        } else {
          this.tasks.push(task)
        }
      }
      this.approvalQueue = tasks
    },
    /**
     * 函数级注释：确保某任务在 store 中可用（直达/刷新进入详情、回放、Diff 的公共取数入口）。
     * mock = 本地已有该任务则返回，否则抛 TaskNotFoundError(404)；
     * real = 走后端 getTask 并 upsert。
     * 入参：taskId string。
     * 返回值：Promise<void>。
     */
    async ensureTaskLoaded(taskId: string): Promise<void> {
      if (getDataSourceMode() === 'mock') {
        if (this.taskById(taskId)) return
        throw new TaskNotFoundError(`演示数据中不存在任务：${taskId}`)
      }
      const task = await getTask(taskId)
      this.upsertTask(task)
    },
    /**
     * 函数级注释：按模式重试任务——real 走后端 retry 接口，mock 走本地状态重置。
     * 入参：taskId string。
     * 返回值：Promise<void>；real 失败向上抛（调用方负责提示）。
     */
    async retryRemote(taskId: string) {
      if (getDataSourceMode() === 'mock') {
        this.retryTask(taskId)
        return
      }
      const next = await apiRequest<AgentTask>(`/api/tasks/${taskId}/retry`, { method: 'POST' })
      this.createTaskFromApi(next)
    },
    /**
     * 函数级注释：按模式取消任务——real 走后端 cancel 接口，mock 走本地状态更新。
     * 入参：taskId string。
     * 返回值：Promise<void>；real 失败向上抛（调用方负责提示）。
     */
    async cancelRemote(taskId: string) {
      if (getDataSourceMode() === 'mock') {
        this.setTaskStatus(taskId, 'CANCELLED')
        return
      }
      const next = await apiRequest<AgentTask>(`/api/tasks/${taskId}/cancel`, { method: 'POST' })
      this.createTaskFromApi(next)
    },
    /**
     * 函数级注释：按模式加载任务的修改计划列表并写入 plansByTask 缓存。
     * mock = 置空缓存；real = GET /plans（后端按版本倒序返回）。
     * 入参：taskId string。
     * 返回值：Promise<void>；real 失败向上抛（调用方负责提示）。
     */
    async loadPlans(taskId: string) {
      if (getDataSourceMode() === 'mock') {
        this.plansByTask[taskId] = []
        return
      }
      const plans = await apiRequest<ModificationPlan[]>(`/api/tasks/${taskId}/plans`)
      this.plansByTask[taskId] = plans
    },
    /**
     * 函数级注释：按模式为任务生成修改计划（仅 real，由详情页 ANALYZED 态按钮触发）。
     * 响应任务（含 plans）经 createTaskFromApi 落库，plans 同步进缓存。
     * 入参：taskId string。
     * 返回值：Promise<AgentTask | null>；mock 返回 null（按钮在 mock 模式不展示）。
     */
    async generatePlan(taskId: string) {
      if (getDataSourceMode() === 'mock') return null
      const next = await apiRequest<AgentTask>(`/api/tasks/${taskId}/plans`, { method: 'POST' })
      this.createTaskFromApi(next)
      return next
    },
    /**
     * 函数级注释：审批通过某个修改计划（可选调整风险等级，调整时必须带原因，后端 422 双保险）。
     * 入参：taskId/planId string；payload 含 comment、risk_level、risk_adjust_reason。
     * 返回值：Promise<AgentTask | null>；mock 返回 null。
     */
    async approvePlan(taskId: string, planId: string, payload: { comment?: string | null; risk_level?: RiskThreshold | null; risk_adjust_reason?: string | null } = {}) {
      if (getDataSourceMode() === 'mock') return null
      const body: Record<string, unknown> = {}
      if (payload.comment) body.comment = payload.comment
      if (payload.risk_level) body.risk_level = payload.risk_level
      if (payload.risk_adjust_reason) body.risk_adjust_reason = payload.risk_adjust_reason
      const next = await apiRequest<AgentTask>(`/api/tasks/${taskId}/plans/${planId}/approve`, { method: 'POST', body: JSON.stringify(body) })
      this.createTaskFromApi(next)
      return next
    },
    /**
     * 函数级注释：审批人要求重写计划，生成新版本并取代当前待审批版本。
     * 入参：taskId/planId string；reason string（必填，后端 422 双保险）。
     * 返回值：Promise<AgentTask | null>；mock 返回 null。
     */
    async rewritePlan(taskId: string, planId: string, reason: string) {
      if (getDataSourceMode() === 'mock') return null
      const next = await apiRequest<AgentTask>(`/api/tasks/${taskId}/plans/${planId}/rewrite`, {
        method: 'POST',
        body: JSON.stringify({ reason }),
      })
      this.createTaskFromApi(next)
      return next
    },
    /**
     * 函数级注释：审批人拒绝计划：计划置 REJECTED，任务 FAILED（可重试重建任务版本）。
     * 入参：taskId/planId string；reason string（必填，后端 422 双保险）。
     * 返回值：Promise<AgentTask | null>；mock 返回 null。
     */
    async rejectPlan(taskId: string, planId: string, reason: string) {
      if (getDataSourceMode() === 'mock') return null
      const next = await apiRequest<AgentTask>(`/api/tasks/${taskId}/plans/${planId}/reject`, {
        method: 'POST',
        body: JSON.stringify({ reason }),
      })
      this.createTaskFromApi(next)
      return next
    },
    /**
     * 函数级注释：指派/改派任务审批人（仅 real；权限 approval:assign=admin/研发负责人）。
     * 入参：taskId、user_id；reason 可选（改派原因会写入审计事件）。
     * 返回值：Promise<AgentTask | null>；mock 返回 null（控件在 mock 模式不展示）。
     */
    async assignApprovalOwner(taskId: string, user_id: string, reason?: string) {
      if (getDataSourceMode() === 'mock') return null
      const body: Record<string, unknown> = { user_id }
      if (reason) body.reason = reason
      const next = await apiRequest<AgentTask>(`/api/tasks/${taskId}/approval-owner`, {
        method: 'POST',
        body: JSON.stringify(body),
      })
      this.createTaskFromApi(next)
      return next
    },
    /**
     * 函数级注释：按模式加载任务的 Diff 产物列表并写入 diffsByTask 缓存。
     * mock = 置空缓存；real = GET /diffs（后端按计划版本倒序返回）。
     * 入参：taskId string。
     * 返回值：Promise<void>；real 失败向上抛（调用方负责提示）。
     */
    async loadDiffs(taskId: string) {
      if (getDataSourceMode() === 'mock') {
        this.diffsByTask[taskId] = []
        return
      }
      const artifacts = await apiRequest<DiffArtifact[]>(`/api/tasks/${taskId}/diffs`)
      this.diffsByTask[taskId] = artifacts
    },
    /**
     * 函数级注释：按模式生成任务 Diff（仅 real，由 Diff 页 APPROVED 态按钮触发）。
     * 响应任务（含 diffs）经 createTaskFromApi 落库，产物同步进缓存。
     * 入参：taskId string。
     * 返回值：Promise<AgentTask | null>；mock 返回 null（按钮在 mock 模式不展示）。
     */
    async generateDiff(taskId: string) {
      if (getDataSourceMode() === 'mock') return null
      const next = await apiRequest<AgentTask>(`/api/tasks/${taskId}/diffs/generate`, { method: 'POST' })
      this.createTaskFromApi(next)
      return next
    },
    /**
     * 函数级注释：按模式加载任务的测试执行记录列表并写入 testsByTask 缓存。
     * mock = 置空缓存；real = GET /tests（后端按开始时间倒序返回）。
     * 入参：taskId string。
     * 返回值：Promise<void>；real 失败向上抛（调用方负责提示）。
     */
    async loadTests(taskId: string) {
      if (getDataSourceMode() === 'mock') {
        this.testsByTask[taskId] = []
        return
      }
      const records = await apiRequest<TestRun[]>(`/api/tasks/${taskId}/tests`)
      this.testsByTask[taskId] = records
    },
    /**
     * 函数级注释：按模式加载任务 PR 描述版本历史（GET 复合接口，versions 写入 prDescriptionsByTask）。
     * mock = 置空缓存；real = GET /pr-description（后端按版本倒序返回）。
     * 入参：taskId string。
     * 返回值：Promise<PrDescriptionOverview | null>；real 失败向上抛（调用方选择忽略或提示）。
     */
    async loadPrDescription(taskId: string): Promise<PrDescriptionOverview | null> {
      if (getDataSourceMode() === 'mock') {
        this.prDescriptionsByTask[taskId] = []
        return null
      }
      const overview = await apiRequest<PrDescriptionOverview>(`/api/tasks/${taskId}/pr-description`)
      this.prDescriptionsByTask[taskId] = overview.versions
      return overview
    },
    /**
     * 函数级注释：按模式执行任务测试（仅 real，由 Diff 页 DIFF_GENERATED/TEST_FAILED 态按钮触发）。
     * 响应任务（含 test_runs）经 createTaskFromApi 落库，记录同步进缓存。
     * 入参：taskId string。
     * 返回值：Promise<AgentTask | null>；mock 返回 null（按钮在 mock 模式不展示）。
     */
    async runTests(taskId: string) {
      if (getDataSourceMode() === 'mock') return null
      const next = await apiRequest<AgentTask>(`/api/tasks/${taskId}/tests/run`, { method: 'POST' })
      // 团队版 celery 异步排队响应为 {task_id,status,queued}，非完整任务，跳过落库避免解析 undefined.title
      if ((next as unknown as { queued?: boolean })?.queued) return next
      this.createTaskFromApi(next)
      return next
    },
    /**
     * 函数级注释：按模式生成/重新生成任务 PR 描述（仅 real，由 Diff 页/详情页 TESTED/TEST_FAILED 态触发）。
     * 响应任务（含 pr_descriptions）经 createTaskFromApi 落库，版本记录同步进缓存。
     * 入参：taskId string。
     * 返回值：Promise<AgentTask | null>；mock 返回 null（按钮在 mock 模式不展示）。
     */
    async generatePrDescription(taskId: string) {
      if (getDataSourceMode() === 'mock') return null
      const next = await apiRequest<AgentTask>(`/api/tasks/${taskId}/pr-description/generate`, { method: 'POST' })
      if ((next as unknown as { queued?: boolean })?.queued) return next
      this.createTaskFromApi(next)
      return next
    },
  },
})
