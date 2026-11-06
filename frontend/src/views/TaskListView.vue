<script setup lang="ts">
import { Filter, Plus, RefreshCw, Search, X } from '@lucide/vue'
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { useRouter } from 'vue-router'
import { apiRequest } from '../services/api'
import { getDataSourceMode } from '../services/dataSourceMode'
import type { TaskListQuery } from '../services/taskData'
import { useAuthStore } from '../stores/auth'
import { useLanguage } from '../stores/language'
import { useWorkspaceStore } from '../stores/workspace'
import StatusTag from '../components/StatusTag.vue'
import TaskFilterSelect from '../components/TaskFilterSelect.vue'
import type { TaskFilterOption } from '../components/TaskFilterSelect.vue'
import type { Project, RiskLevel, Task, TaskStatus } from '../types/domain'

const store = useWorkspaceStore()
const auth = useAuthStore()
const { isEnglish } = useLanguage()
const router = useRouter()
const keyword = ref('')
const selectedId = ref(store.selectedTaskId)
const status = ref('all')
const risk = ref('all')
const project = ref('all')
const projectTouched = ref(false)
watch(project, () => { projectTouched.value = true })
const timeRange = ref('all')
const manualOnly = ref(false)
const owner = ref('all')
const repository = ref('all')
const drawerOpen = ref(false)
const advancedOpen = ref(false)
const loading = ref(false)
const projects = ref<Project[]>([])
const ownerOptions = computed(() => Array.from(new Set(store.tasks.map((task) => task.owner))))
const repositoryOptions = computed(() => Array.from(new Set(store.tasks.map((task) => task.repository))))
const projectOptions = computed(() => Array.from(new Set([
  ...projects.value.map((item) => item.id),
  ...store.tasks.map((task) => task.projectId || store.activeProjectId),
])))
const statusOptions: Array<{ value: 'all' | TaskStatus; zh: string; en: string }> = [
  { value: 'all', zh: '全部状态', en: 'All statuses' },
  { value: 'CREATED', zh: '已创建', en: 'Created' },
  { value: 'ISSUE_FETCHED', zh: 'Issue 已拉取', en: 'Issue fetched' },
  { value: 'CONTEXT_INDEXED', zh: '上下文已索引', en: 'Context indexed' },
  { value: 'ANALYSIS_QUEUED', zh: '分析排队', en: 'Analysis queued' },
  { value: 'ANALYZED', zh: '分析完成', en: 'Analyzed' },
  { value: 'PLAN_GENERATED', zh: '计划已生成', en: 'Plan generated' },
  { value: 'WAITING_APPROVAL', zh: '等待审批', en: 'Waiting approval' },
  { value: 'APPROVED', zh: '审批通过', en: 'Approved' },
  { value: 'DIFF_GENERATED', zh: 'Diff 已生成', en: 'Diff generated' },
  { value: 'TESTED', zh: '测试完成', en: 'Tested' },
  { value: 'COMPLETED', zh: '已完成', en: 'Completed' },
  { value: 'FAILED', zh: '任务失败', en: 'Failed' },
  { value: 'CANCELLED', zh: '已取消', en: 'Cancelled' },
]
const riskOptions: Array<{ value: 'all' | RiskLevel; zh: string; en: string }> = [
  { value: 'all', zh: '全部风险', en: 'All risks' },
  { value: 'pending', zh: '待分析', en: 'Pending' },
  { value: 'high', zh: '高风险', en: 'High risk' },
  { value: 'medium', zh: '中风险', en: 'Medium risk' },
  { value: 'low', zh: '低风险', en: 'Low risk' },
]
const timeOptions = [
  { value: 'all', zh: '全部时间', en: 'All time' },
  { value: 'today', zh: '今天', en: 'Today' },
  { value: '7d', zh: '近 7 天', en: 'Last 7 days' },
  { value: '30d', zh: '近 30 天', en: 'Last 30 days' },
]
const ui = computed(() => ({
  searchPlaceholder: isEnglish.value ? 'Search tasks, issues, or repositories' : '搜索任务、Issue 或仓库',
  allProjects: isEnglish.value ? 'All projects' : '全部项目',
  manualOnly: isEnglish.value ? 'Needs action only' : '只看需处理',
  moreFilters: isEnglish.value ? 'More filters' : '更多筛选',
  selected: isEnglish.value ? 'selected' : '项已选',
  clearSelection: isEnglish.value ? 'Clear selection' : '清空选择',
  refreshing: isEnglish.value ? 'Refreshing' : '刷新中',
  refresh: isEnglish.value ? 'Refresh' : '刷新',
  collapseSummary: isEnglish.value ? 'Collapse summary' : '收起摘要',
  expandSummary: isEnglish.value ? 'Expand summary' : '展开摘要',
  batchApproval: isEnglish.value ? 'Batch approval' : '批量审批',
  createTask: isEnglish.value ? 'Create task' : '创建任务',
  createTaskAria: isEnglish.value ? 'Create a new R&D collaboration task' : '创建新的研发协作任务',
  allOwners: isEnglish.value ? 'All owners' : '全部负责人',
  allRepositories: isEnglish.value ? 'All repositories' : '全部仓库',
  clearFilters: isEnglish.value ? 'Clear filters' : '清空条件',
  totalPrefix: isEnglish.value ? 'Total' : '共',
  totalSuffix: isEnglish.value ? 'tasks' : '个任务',
  priorityHint: isEnglish.value ? 'Manual intervention items appear first' : '优先显示需人工介入任务',
  cancel: isEnglish.value ? 'Cancel' : '取消',
  addVisible: isEnglish.value ? 'Add' : '补选',
  selectAll: isEnglish.value ? 'Select all' : '全选',
  chosen: isEnglish.value ? 'Selected' : '已选',
  select: isEnglish.value ? 'Select' : '选择',
  task: isEnglish.value ? 'Task' : '任务',
  repository: isEnglish.value ? 'Repository' : '仓库',
  status: isEnglish.value ? 'Status' : '状态',
  risk: isEnglish.value ? 'Risk' : '风险',
  processingState: isEnglish.value ? 'Processing state' : '处理状态',
  action: isEnglish.value ? 'Action' : '操作',
  view: isEnglish.value ? 'View' : '查看',
  taskSummary: isEnglish.value ? 'Task summary' : '任务摘要',
  close: isEnglish.value ? 'Close' : '关闭',
  currentStage: isEnglish.value ? 'Current stage' : '当前阶段',
  riskLevel: isEnglish.value ? 'Risk level' : '风险等级',
  updatedAt: isEnglish.value ? 'Updated' : '更新时间',
  taskInfo: isEnglish.value ? 'Task info' : '任务信息',
  branch: isEnglish.value ? 'Work branch' : '工作分支',
  approver: isEnglish.value ? 'Approver' : '审批人',
  recentEvents: isEnglish.value ? 'Recent events' : '最近事件',
  planGenerated: isEnglish.value ? 'Plan v1 generated' : '计划 v1 已生成',
  waitingApproval: isEnglish.value ? 'Waiting approval' : '等待审批',
  retry: isEnglish.value ? 'Retry' : '一键重试',
  enterDetail: isEnglish.value ? 'Open task detail' : '进入任务详情',
  clearFiltersAction: isEnglish.value ? 'Clear filters' : '清空筛选',
}))
const filteredTasks = computed(() => store.tasks
  .filter((task) => {
    const matchesKeyword = `${task.title}${task.issue}${task.repository}${task.projectName ?? ''}`.toLowerCase().includes(keyword.value.toLowerCase())
    const matchesStatus = status.value === 'all' || task.status === status.value
    const matchesRisk = risk.value === 'all' || task.risk === risk.value
    const matchesProject = project.value === 'all' || (task.projectId || store.activeProjectId) === project.value
    const matchesOwner = owner.value === 'all' || task.owner === owner.value
    const matchesRepository = repository.value === 'all' || task.repository === repository.value
    const matchesManual = !manualOnly.value || needsManual(task)
    const matchesTime = matchesTimeRange(task)
    return matchesKeyword && matchesStatus && matchesRisk && matchesProject && matchesOwner && matchesRepository && matchesManual && matchesTime
  })
  .sort(taskPriority))
const selectedTask = computed(() => store.tasks.find((task) => task.id === selectedId.value))
const selectedCount = computed(() => store.selectedTaskIds.length)
const visibleTaskIds = computed(() => filteredTasks.value.map((task) => task.id))
const allVisibleSelected = computed(() => visibleTaskIds.value.length > 0 && visibleTaskIds.value.every((taskId) => store.selectedTaskIds.includes(taskId)))
const someVisibleSelected = computed(() => visibleTaskIds.value.some((taskId) => store.selectedTaskIds.includes(taskId)))
const hasActiveFilters = computed(() => Boolean(
  keyword.value.trim()
  || status.value !== 'all'
  || risk.value !== 'all'
  || project.value !== 'all'
  || owner.value !== 'all'
  || repository.value !== 'all'
  || timeRange.value !== 'all'
  || manualOnly.value,
))
const projectFilterOptions = computed<TaskFilterOption[]>(() => [
  { value: 'all', label: ui.value.allProjects },
  ...projectOptions.value.map((projectId) => {
    const item = projects.value.find((project) => project.id === projectId)
    return { value: projectId, label: displayProjectName(item?.name ?? projectId) }
  }),
])
const statusFilterOptions = computed<TaskFilterOption[]>(() => statusOptions.map((item) => ({ value: item.value, label: optionLabel(item) })))
const riskFilterOptions = computed<TaskFilterOption[]>(() => riskOptions.map((item) => ({ value: item.value, label: optionLabel(item) })))
const ownerFilterOptions = computed<TaskFilterOption[]>(() => [
  { value: 'all', label: ui.value.allOwners },
  ...ownerOptions.value.map((item) => ({ value: item, label: item })),
])
const repositoryFilterOptions = computed<TaskFilterOption[]>(() => [
  { value: 'all', label: ui.value.allRepositories },
  ...repositoryOptions.value.map((item) => ({ value: item, label: item })),
])
const timeFilterOptions = computed<TaskFilterOption[]>(() => timeOptions.map((item) => ({ value: item.value, label: optionLabel(item) })))
const emptyState = computed(() => hasActiveFilters.value
  ? {
      title: isEnglish.value ? 'No matching tasks' : '没有匹配的任务',
      description: isEnglish.value ? 'Try clearing one or more filters to broaden the task list.' : '可以清空部分筛选条件，扩大任务列表的匹配范围。',
    }
  : {
      title: isEnglish.value ? 'No tasks yet' : '暂时没有任务',
      description: isEnglish.value ? 'New collaboration tasks will appear here after they are created.' : '创建新的研发协作任务后，任务会显示在这里。',
    })

onMounted(async () => {
  await loadProjects()
  const currentProject = projects.value.find((item) => item.id === store.activeProjectId)
    ?? projects.value.find((item) => item.name === store.activeProject)
  if (currentProject) {
    project.value = currentProject.id
    if (currentProject.name !== store.activeProject || currentProject.id !== store.activeProjectId) {
      store.setActiveProject(currentProject.id, currentProject.name)
    }
  }
  await refreshTasks(false)
})

function needsManual(task: Task) {
  return Boolean(task.manualRequired || task.status === 'FAILED' || task.status === 'WAITING_APPROVAL' || (task.manualReason && task.manualReason !== 'none'))
}

function manualText(task: Task) {
  if (task.status === 'CANCELLED') return isEnglish.value ? 'Cancelled' : '已取消'
  if (task.nextAction === 'wait_index' && task.status === 'ISSUE_FETCHED') return isEnglish.value ? 'Indexing' : '等待索引'
  if (!needsManual(task)) return task.automationState === 'completed' ? (isEnglish.value ? 'Completed' : '已完成') : (isEnglish.value ? 'Auto running' : '自动运行')
  const map: Record<string, { zh: string; en: string }> = {
    high_risk: { zh: '高风险待审批', en: 'High-risk approval' },
    approval_required: { zh: '等待审批', en: 'Waiting approval' },
    context_missing: { zh: '上下文不足', en: 'Missing context' },
    answer_required: { zh: '待补充回答', en: 'Answer required' },
    auth_expired: { zh: '授权过期', en: 'Authorization expired' },
    test_failed: { zh: '测试失败', en: 'Test failed' },
    command_blocked: { zh: '命令受限', en: 'Command blocked' },
    gitee_fetch_failed: { zh: 'Issue 拉取失败', en: 'Issue fetch failed' },
  }
  const copy = map[task.manualReason ?? '']
  if (copy) return isEnglish.value ? copy.en : copy.zh
  if (task.status === 'FAILED') return isEnglish.value ? 'Failed, needs action' : '失败待处理'
  return isEnglish.value ? 'Needs action' : '需要处理'
}

function optionLabel(option: { zh: string; en: string }) {
  return isEnglish.value ? option.en : option.zh
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

function taskPriority(a: Task, b: Task) {
  const priority = (task: Task) => {
    const reasonOrder: Record<string, number> = { auth_expired: 0, test_failed: 0, context_missing: 1, answer_required: 1, high_risk: 2, approval_required: 3 }
    const riskOrder: Record<RiskLevel, number> = { high: 0, medium: 1, low: 2, pending: 3 }
    return [
      needsManual(task) ? 0 : 1,
      task.status === 'FAILED' ? 0 : 1,
      reasonOrder[task.manualReason ?? ''] ?? 8,
      riskOrder[task.risk] ?? 9,
      -taskTime(task),
    ]
  }
  const left = priority(a)
  const right = priority(b)
  for (let index = 0; index < left.length; index += 1) {
    if (left[index] !== right[index]) return left[index] - right[index]
  }
  return 0
}

function taskTime(task: Task) {
  const value = task.createdAt || task.updatedAt
  const time = Date.parse(value)
  return Number.isNaN(time) ? 0 : time
}

function matchesTimeRange(task: Task) {
  if (timeRange.value === 'all') return true
  const time = taskTime(task)
  if (!time) return true
  const now = Date.now()
  if (timeRange.value === 'today') return new Date(time).toDateString() === new Date(now).toDateString()
  if (timeRange.value === '7d') return now - time <= 7 * 24 * 60 * 60 * 1000
  if (timeRange.value === '30d') return now - time <= 30 * 24 * 60 * 60 * 1000
  return true
}

async function loadProjects() {
  if (getDataSourceMode() !== 'real') {
    projects.value = []
    return
  }
  try {
    projects.value = await apiRequest<Project[]>('/api/projects')
  } catch {
    projects.value = []
  }
}

function chooseTask(id: string) {
  selectedId.value = id
  store.selectTask(id)
}

function toggleSelection(taskId: string) {
  store.toggleTaskSelection(taskId)
}

function toggleVisibleSelection() {
  const visibleIds = visibleTaskIds.value
  if (!visibleIds.length) return
  const nextSelection = allVisibleSelected.value
    ? store.selectedTaskIds.filter((taskId) => !visibleIds.includes(taskId))
    : Array.from(new Set([...store.selectedTaskIds, ...visibleIds]))
  store.setTaskSelection(nextSelection)
}

function clearSelection() {
  store.clearTaskSelection()
}

function clearFilters() {
  keyword.value = ''
  status.value = 'all'
  risk.value = 'all'
  project.value = 'all'
  owner.value = 'all'
  repository.value = 'all'
  timeRange.value = 'all'
  manualOnly.value = false
}

async function refreshTasks(showToast = true) {
  loading.value = true
  try {
    const query: TaskListQuery = {}
    query.projectId = projectTouched.value ? (project.value === 'all' ? '' : project.value) : undefined
    if (status.value !== 'all') query.status = status.value as TaskStatus
    if (risk.value !== 'all') query.risk = risk.value as RiskLevel
    if (keyword.value.trim()) query.keyword = keyword.value.trim()
    if (manualOnly.value) query.manualOnly = true
    if (timeRange.value !== 'all') query.timeRange = timeRange.value
    await store.refreshTaskList(query)
    selectedId.value = store.selectedTaskId
    if (showToast) {
      ElMessage.success({
        message: isEnglish.value ? 'Task list refreshed' : '任务列表已刷新',
        grouping: true,
      })
    }
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : isEnglish.value ? 'Failed to refresh task list' : '任务列表刷新失败')
  } finally {
    loading.value = false
  }
}

async function retrySelectedTask() {
  if (!selectedTask.value || selectedTask.value.status !== 'FAILED') return
  try {
    await store.retryRemote(selectedTask.value.id)
    ElMessage.success(isEnglish.value ? 'Task re-entered the analysis queue' : '任务已重新进入分析队列')
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : isEnglish.value ? 'Failed to retry task' : '任务重试失败')
  }
}

function goToApproval() {
  router.push('/approvals')
}
</script>

<template>
  <section class="page-content task-list-page">
    <div class="page-toolbar page-toolbar--stacked task-list-toolbar">
      <div class="filter-bar task-list-toolbar__filters">
        <label class="search-field"><Search :size="16" /><input v-model="keyword" :placeholder="ui.searchPlaceholder" /></label>
        <div class="task-list-toolbar__selects">
          <TaskFilterSelect v-model="project" :options="projectFilterOptions" :ariaLabel="ui.allProjects" />
          <TaskFilterSelect v-model="status" :options="statusFilterOptions" :ariaLabel="ui.status" />
          <TaskFilterSelect v-model="risk" :options="riskFilterOptions" :ariaLabel="ui.risk" />
        </div>
        <button class="button button--secondary task-list-toggle" :class="{ 'is-active': manualOnly }" @click="manualOnly = !manualOnly">{{ ui.manualOnly }}</button>
        <button class="button button--secondary task-list-advanced-toggle" :class="{ 'is-active': advancedOpen }" @click="advancedOpen = !advancedOpen"><Filter :size="16" /> {{ ui.moreFilters }}</button>
      </div>
      <div class="toolbar-actions task-list-toolbar__actions">
        <span v-if="selectedCount" class="selection-chip">{{ selectedCount }} {{ ui.selected }}</span>
        <button v-if="selectedCount" class="button button--secondary" @click="clearSelection">{{ ui.clearSelection }}</button>
        <button class="button button--secondary" :disabled="loading" @click="refreshTasks()"><RefreshCw :size="16" /> {{ loading ? ui.refreshing : ui.refresh }}</button>
        <button class="button button--ghost" @click="drawerOpen = !drawerOpen">{{ drawerOpen ? ui.collapseSummary : ui.expandSummary }}</button>
        <button v-if="auth.hasPermission('approval:manage')" class="button button--secondary" :disabled="!selectedCount" @click="goToApproval">{{ ui.batchApproval }}</button>
        <RouterLink
          v-if="auth.hasPermission('task:create')"
          to="/tasks/new"
          class="button button--primary task-create-button"
          :aria-label="ui.createTaskAria"
        >
          <span class="task-create-button__icon" aria-hidden="true"><Plus :size="15" stroke-width="2.4" /></span>
          <span>{{ ui.createTask }}</span>
        </RouterLink>
      </div>
    </div>

    <Transition name="task-filter-reveal">
      <div v-if="advancedOpen" class="filter-bar filter-bar--advanced task-list-advanced-filters">
        <TaskFilterSelect v-model="owner" :options="ownerFilterOptions" :ariaLabel="ui.allOwners" />
        <TaskFilterSelect v-model="repository" :options="repositoryFilterOptions" :ariaLabel="ui.allRepositories" />
        <TaskFilterSelect v-model="timeRange" :options="timeFilterOptions" :ariaLabel="ui.clearFilters" />
        <button class="text-button" @click="clearFilters">{{ ui.clearFilters }}</button>
      </div>
    </Transition>

    <div class="task-list-layout" :class="{ 'task-list-layout--drawer-closed': !drawerOpen }">
      <article class="panel table-panel">
        <header class="table-panel__header">
          <div class="table-panel__title">
            <strong>{{ ui.totalPrefix }} {{ filteredTasks.length }} {{ ui.totalSuffix }}</strong>
            <span class="muted">{{ ui.priorityHint }}</span>
          </div>
          <button
            class="task-select-control task-select-control--header"
            :class="{ 'is-checked': allVisibleSelected, 'is-mixed': someVisibleSelected && !allVisibleSelected }"
            type="button"
            :aria-pressed="allVisibleSelected"
            @click="toggleVisibleSelection"
          >
            <span class="task-select-control__dot" aria-hidden="true" />
            <span class="task-select-control__label">{{ allVisibleSelected ? ui.cancel : someVisibleSelected ? ui.addVisible : ui.selectAll }}</span>
          </button>
        </header>
        <div class="table-wrap">
          <table class="data-table data-table--task">
            <colgroup>
              <col class="task-table__select" />
              <col class="task-table__task" />
              <col class="task-table__repo" />
              <col class="task-table__status" />
              <col class="task-table__risk" />
              <col class="task-table__processing" />
              <col class="task-table__action" />
            </colgroup>
            <thead><tr><th>{{ ui.select }}</th><th>{{ ui.task }}</th><th>{{ ui.repository }}</th><th>{{ ui.status }}</th><th>{{ ui.risk }}</th><th>{{ ui.processingState }}</th><th>{{ ui.action }}</th></tr></thead>
            <tbody>
              <tr v-if="!filteredTasks.length" class="task-empty-row">
                <td colspan="7">
                  <div class="task-empty-state">
                    <span class="task-empty-state__icon"><Search :size="18" /></span>
                    <div>
                      <strong>{{ emptyState.title }}</strong>
                      <p>{{ emptyState.description }}</p>
                    </div>
                    <button v-if="hasActiveFilters" class="text-button" @click.stop="clearFilters">{{ ui.clearFiltersAction }}</button>
                  </div>
                </td>
              </tr>
              <tr v-for="task in filteredTasks" :key="task.id" :class="{ 'is-selected': task.id === selectedId }" @click="chooseTask(task.id)">
                <td>
                  <button class="task-select-control" :class="{ 'is-checked': store.selectedTaskIds.includes(task.id) }" type="button" :aria-pressed="store.selectedTaskIds.includes(task.id)" @click.stop="toggleSelection(task.id)">
                    <span class="task-select-control__dot" aria-hidden="true" />
                    <span class="task-select-control__label">{{ store.selectedTaskIds.includes(task.id) ? ui.chosen : '' }}</span>
                  </button>
                </td><td><strong>{{ task.title }}</strong><small class="mono">{{ task.id }} · {{ task.issue }} · {{ displayProjectName(task.projectName || store.activeProject) }}</small></td><td>{{ task.repository }}</td>
                <td><StatusTag :status="task.status" /></td><td><StatusTag :risk="task.risk" /></td>
                <td><span :class="['manual-chip', { 'is-required': needsManual(task) }]">{{ manualText(task) }}</span></td>
                <td><RouterLink :to="`/tasks/${task.id}`" class="text-button" @click.stop>{{ ui.view }}</RouterLink></td>
              </tr>
            </tbody>
          </table>
        </div>
      </article>

      <aside v-if="drawerOpen && selectedTask" class="task-drawer">
        <header><div><p class="section-kicker">{{ ui.taskSummary }}</p><h2>{{ selectedTask.title }}</h2></div><button class="icon-button" :aria-label="ui.close" @click="drawerOpen = false"><X :size="18" /></button></header>
        <div class="drawer-section"><span class="drawer-label">{{ ui.currentStage }}</span><StatusTag :status="selectedTask.status" /><span class="drawer-label">{{ ui.processingState }}</span><span :class="['manual-chip', { 'is-required': needsManual(selectedTask) }]">{{ manualText(selectedTask) }}</span><span class="drawer-label">{{ ui.riskLevel }}</span><StatusTag :risk="selectedTask.risk" /><span class="drawer-label">{{ ui.updatedAt }}</span><span class="mono">{{ selectedTask.updatedAt }}</span></div>
        <div class="drawer-section"><span class="drawer-label">{{ ui.taskInfo }}</span><dl class="compact-dl"><div><dt>Issue</dt><dd class="mono">{{ selectedTask.issue }}</dd></div><div><dt>{{ ui.repository }}</dt><dd>{{ selectedTask.repository }}</dd></div><div><dt>{{ ui.branch }}</dt><dd class="mono">{{ selectedTask.branch }}</dd></div><div><dt>{{ ui.approver }}</dt><dd>{{ selectedTask.approver }}</dd></div></dl></div>
        <div class="drawer-section"><span class="drawer-label">{{ ui.recentEvents }}</span><ol class="mini-timeline"><li><i />{{ ui.planGenerated }} <time>10:39</time></li><li><i />{{ ui.waitingApproval }} <time>10:42</time></li></ol></div>
        <footer class="drawer-footer-actions">
          <button v-if="selectedTask.status === 'FAILED' && auth.hasPermission('task:operate')" class="button button--ghost button--wide" @click="retrySelectedTask">{{ ui.retry }}</button>
          <RouterLink :to="`/tasks/${selectedTask.id}`" class="button button--primary button--wide">{{ ui.enterDetail }}</RouterLink>
        </footer>
      </aside>
    </div>
  </section>
</template>
