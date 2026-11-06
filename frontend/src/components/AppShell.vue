<script setup lang="ts">
import {
  Bell,
  Check,
  ChevronDown,
  ChevronRight,
  ClipboardCheck,
  FilePlus2,
  FileDiff,
  FileClock,
  FolderSearch,
  Gauge,
  Globe2,
  History,
  LayoutDashboard,
  LoaderCircle,
  ListTodo,
  LogOut,
  Moon,
  Search,
  ServerCog,
  Settings,
  UserCog,
  Workflow,
  X,
} from '@lucide/vue'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import { useLanguage, type LanguageMode } from '../stores/language'
import { useTheme, type ThemeMode } from '../stores/theme'
import { useWorkspaceStore } from '../stores/workspace'
import { useTenancyStore } from '../stores/tenancy'
import { getDataSourceMode } from '../services/dataSourceMode'
import { apiRequest } from '../services/api'
import { hasAccessRequirement, type AccessRequirement } from '../services/accessControl'
import { connectRealtime, disconnectRealtime, subscribeRealtime, type TeamRealtimeEvent } from '../services/realtime'
import type { Project, TaskNotification } from '../types/domain'
import { notificationTypeText } from '../types/domain'
import type { UserRole } from '../types/auth'
import type { EnterpriseRole } from '../types/tenancy'
const route = useRoute()
const router = useRouter()
const store = useWorkspaceStore()
const auth = useAuthStore()
const tenancy = useTenancyStore()
const { themeMode, setThemeMode } = useTheme()
const { languageMode, isEnglish, setLanguageMode } = useLanguage()
const userMenuOpen = ref(false)
const projectMenuOpen = ref(false)
const tenantMenuOpen = ref(false)
const tenantSwitchingId = ref<string | null>(null)
const notificationOpen = ref(false)
const realNotifications = ref<TaskNotification[]>([])
const unreadCount = ref(0)
const loadingNotifications = ref(false)
const globalSearch = ref('')
const notificationWrap = ref<HTMLElement | null>(null)
const tenantWrap = ref<HTMLElement | null>(null)
const notificationModalWrap = ref<HTMLElement | null>(null)
const sideUserWrap = ref<HTMLElement | null>(null)
const projectWrap = ref<HTMLElement | null>(null)
const projects = ref<Project[]>([])
const unreadNotifications = computed(() => realNotifications.value.filter((item) => !item.read_at))
const readNotifications = computed(() => realNotifications.value.filter((item) => Boolean(item.read_at)))
const activeTenantStatus = computed(() => tenancy.tenants.find((tenant) => tenant.id === tenancy.activeTenant?.id)?.status ?? 'active')
type Copy = {
  zh: string
  en: string
}
type ProjectOption = {
  id: string
  name: string
  label: Copy
  detail: Copy
}
const fallbackProjectOptions: ProjectOption[] = [
  { id: 'proj_payment', name: '支付中台', label: { zh: '支付中台', en: 'Payment Hub' }, detail: { zh: '支付回调 / 订单链路', en: 'Payment callbacks / Order flow' } },
  { id: 'proj_tenant', name: '租户平台', label: { zh: '租户平台', en: 'Tenant Platform' }, detail: { zh: '租户隔离 / 权限上下文', en: 'Tenant isolation / Permission context' } },
  { id: 'proj_runtime', name: '智能体运行时', label: { zh: '智能体运行时', en: 'Agent Runtime' }, detail: { zh: '索引 / 回放 / 调度', en: 'Indexing / Replay / Scheduling' } },
]
const themeOptions: Array<{ value: ThemeMode; label: Copy }> = [
  { value: 'dark', label: { zh: '暗色', en: 'Dark' } },
  { value: 'light', label: { zh: '亮色', en: 'Light' } },
  { value: 'system', label: { zh: '跟随系统', en: 'System' } },
]
const languageOptions: Array<{ value: LanguageMode; label: string }> = [
  { value: 'zh', label: '中文' },
  { value: 'en', label: 'English' },
]

type NavItem = {
  label: Copy
  to: string
  match: string
  icon: typeof Gauge
  badge?: boolean
  permission?: AccessRequirement
}

// 任务级页面（详情 / 回放 / Diff）带 :id 时优先取当前路由任务，
// 刷新或直达时侧栏不会停留在快照里的旧选中任务；其余场景跟随选中任务。
const taskContextId = computed(() => {
  const param = route.params.id
  const routeId = (Array.isArray(param) ? param[0] : param ?? '').trim()
  return routeId || store.selectedTaskId || ''
})

const navEntries = computed<NavItem[]>(() => {
  const taskId = taskContextId.value
  return [
    { label: { zh: '工作台', en: 'Dashboard' }, to: '/dashboard', match: '/dashboard', icon: Gauge },
    { label: { zh: '任务列表', en: 'Tasks' }, to: '/tasks', match: '/tasks', icon: ListTodo },
    { label: { zh: '平台需求', en: 'Requests' }, to: '/requests', match: '/requests', icon: FilePlus2, permission: 'request:view' as const },
    { label: { zh: '审批中心', en: 'Approvals' }, to: '/approvals', match: '/approvals', icon: ClipboardCheck, badge: true, permission: 'approval:view' as const },
    { label: { zh: '任务回放', en: 'Replay' }, to: taskId ? `/replay/${taskId}` : '/tasks', match: '/replay', icon: History },
    { label: { zh: 'Diff 审核', en: 'Diff Review' }, to: taskId ? `/diff/${taskId}` : '/tasks', match: '/diff', icon: FileDiff },
    { label: { zh: '索引管理', en: 'Indexes' }, to: '/indexes', match: '/indexes', icon: FolderSearch },
    { label: { zh: '项目配置', en: 'Settings' }, to: '/settings', match: '/settings', icon: Settings, permission: 'project:manage' as const },
    { label: { zh: '账号恢复', en: 'Account Recovery' }, to: '/account-recovery', match: '/account-recovery', icon: UserCog, permission: 'user:view' as const },
    { label: { zh: '审计中心', en: 'Audit center' }, to: '/audit', match: '/audit', icon: FileClock, permission: ['project:manage', 'audit:view'] },
    { label: { zh: '成员管理', en: 'Members' }, to: '/enterprise/members', match: '/enterprise/members', icon: UserCog, permission: ['project:manage', 'member:view'] },
    { label: { zh: '企业审批', en: 'Governance' }, to: '/enterprise/approvals', match: '/enterprise/approvals', icon: ClipboardCheck, permission: ['project:manage', 'approval:view'] },
    { label: { zh: '企业工作台', en: 'Workbench' }, to: '/enterprise/dashboard', match: '/enterprise/dashboard', icon: LayoutDashboard },
    { label: { zh: '企业 RAG', en: 'RAG Console' }, to: '/enterprise/rag', match: '/enterprise/rag', icon: Search, permission: ['rag:search'] },
    { label: { zh: '企业 Runtime', en: 'Runtime' }, to: '/enterprise/runtime', match: '/enterprise/runtime', icon: Workflow, permission: ['rag:search'] },
    { label: { zh: '企业设置', en: 'Ops' }, to: '/enterprise/ops', match: '/enterprise/ops', icon: ServerCog, permission: ['project:manage'] },
  ]
})

const navItems = computed(() => navEntries.value.filter((item) => hasAccessRequirement(auth, tenancy, item.permission)))

// 审批中心角标：真实模式走 owner 范围审批队列（approver 只看指派给自己的），mock 沿用本地演示待审批任务
const isRealMode = computed(() => getDataSourceMode() === 'real')
const approvalBadgeCount = computed(() => (isRealMode.value ? store.approvalQueue.length : store.pendingTasks.length))
let dashboardSummaryTimer: ReturnType<typeof setInterval> | undefined
let notificationsTimer: ReturnType<typeof setInterval> | undefined
let realtimeRefreshTimer: ReturnType<typeof setTimeout> | undefined
let realtimeUnsubscribe: (() => void) | undefined
const pendingRealtimeEvents: TeamRealtimeEvent[] = []

const routeTitleMap: Record<string, Copy> = {
  登录: { zh: '登录', en: 'Sign in' },
  找回密码: { zh: '找回密码', en: 'Password recovery' },
  服务协议: { zh: '服务协议', en: 'Terms' },
  隐私政策: { zh: '隐私政策', en: 'Privacy' },
  工作台: { zh: '工作台', en: 'Dashboard' },
  任务列表: { zh: '任务列表', en: 'Tasks' },
  创建任务: { zh: '创建任务', en: 'Create task' },
  任务详情: { zh: '任务详情', en: 'Task detail' },
  审批中心: { zh: '审批中心', en: 'Approvals' },
  任务回放: { zh: '任务回放', en: 'Replay' },
  'Diff 审核': { zh: 'Diff 审核', en: 'Diff Review' },
  索引管理: { zh: '索引管理', en: 'Indexes' },
  项目配置: { zh: '项目配置', en: 'Settings' },
  账号恢复: { zh: '账号恢复', en: 'Account Recovery' },
  审计中心: { zh: '审计中心', en: 'Audit center' },
  页面不存在: { zh: '页面不存在', en: 'Not found' },
}
const title = computed(() => {
  const rawTitle = String(route.meta.title ?? '工作台')
  return localize(routeTitleMap[rawTitle] ?? { zh: rawTitle, en: rawTitle })
})
const currentUser = computed(() => auth.displayUser)
const searchInput = ref<HTMLInputElement | null>(null)

type RoleSummary = {
  scope: string
  actions: string
}

function buildRoleSummary(role: UserRole | undefined): RoleSummary | null {
  switch (role) {
    case 'admin':
      return isEnglish.value
        ? { scope: 'Projects, repositories, models, security policies, all tasks', actions: 'Configure repositories / Update models / Adjust security policies / View all tasks' }
        : { scope: '项目、仓库、模型、安全策略、全量任务', actions: '配置仓库 / 修改模型 / 调整安全策略 / 查看全部任务' }
    case 'approver':
      return isEnglish.value
        ? { scope: 'Pending approvals, approval history, replay summaries', actions: 'Approve / Reject / Request rewrite / Add approval notes' }
        : { scope: '待审批任务、审批历史、回放摘要', actions: '通过 / 拒绝 / 要求重写 / 补充审批意见' }
    case 'developer':
      return isEnglish.value
        ? { scope: 'Projects and tasks within your permissions', actions: 'Create tasks / View analysis, plans, diffs, tests, PR notes' }
        : { scope: '自己有权限的项目和任务', actions: '创建任务 / 查看分析、计划、Diff、测试、PR 描述' }
    case 'lead':
      return isEnglish.value
        ? { scope: 'Task replay, metrics, audit views', actions: 'Read-only view / Export retrospectives / Locate issues' }
        : { scope: '任务回放、统计、审计视图', actions: '只读查看 / 复盘导出 / 问题定位' }
    default:
      return null
  }
}

const roleSummary = computed(() => buildRoleSummary(currentUser.value?.role))
const currentThemeLabel = computed(() => localize(themeOptions.find((option) => option.value === themeMode.value)?.label ?? { zh: '跟随系统', en: 'System' }))
const currentLanguageLabel = computed(() => languageOptions.find((option) => option.value === languageMode.value)?.label ?? '中文')
const projectOptions = computed<ProjectOption[]>(() => {
  if (!projects.value.length) return fallbackProjectOptions
  return projects.value.map((project) => ({
    id: project.id,
    name: project.name,
    label: { zh: project.name, en: project.name },
    detail: {
      zh: project.description || '自定义工作区',
      en: project.description || 'Custom workspace',
    },
  }))
})

function localize(copy: Copy) {
  return isEnglish.value ? copy.en : copy.zh
}

function roleLabel(role: UserRole | undefined, fallback = '') {
  if (!isEnglish.value) return fallback
  switch (role) {
    case 'admin':
      return 'Admin'
    case 'approver':
      return 'Approver'
    case 'developer':
      return 'Developer'
    case 'lead':
      return 'Lead'
    default:
      return fallback
  }
}

function displayProjectName(name: string) {
  return localize(projectOptions.value.find((item) => item.name === name)?.label ?? { zh: name, en: name })
}

async function loadProjects() {
  if (!isRealMode.value) {
    projects.value = []
    return
  }
  try {
    projects.value = await apiRequest<Project[]>('/api/projects')
    reconcileActiveProject()
  } catch {
    projects.value = []
  }
}

function loadTenancyContext() {
  if (auth.currentUser) void tenancy.loadContext()
}

function reconcileActiveProject() {
  if (!projects.value.length) return
  const byName = projects.value.find((item) => item.name === store.activeProject)
  if (byName && byName.id !== store.activeProjectId) {
    store.setActiveProject(byName.id, byName.name)
    return
  }
  const byId = projects.value.find((item) => item.id === store.activeProjectId)
  if (byId) {
    if (byId.name !== store.activeProject) {
      store.setActiveProject(byId.id, byId.name)
    }
    return
  }
  const nextProject = byName ?? projects.value[0]
  store.setActiveProject(nextProject.id, nextProject.name)
}

function runSearch() {
  const keyword = globalSearch.value.trim().toLowerCase()
  if (!keyword) {
    return
  }
  const match = store.tasks.find((task) => `${task.id} ${task.title} ${task.issue} ${task.repository}`.toLowerCase().includes(keyword))
  if (!match) {
    ElMessage.info(isEnglish.value ? 'No matching task found' : '没有找到匹配任务')
    return
  }
  store.selectTask(match.id)
  globalSearch.value = ''
  router.push(`/tasks/${match.id}`)
}

function logout() {
  userMenuOpen.value = false
  void auth.logout().finally(() => router.push('/login'))
}

function chooseTheme(mode: ThemeMode) {
  setThemeMode(mode)
}

function chooseLanguage(mode: LanguageMode) {
  setLanguageMode(mode)
}

function toggleProjectMenu() {
  projectMenuOpen.value = !projectMenuOpen.value
  if (projectMenuOpen.value) {
    userMenuOpen.value = false
    tenantMenuOpen.value = false
    notificationOpen.value = false
  }
}

function toggleUserMenu() {
  userMenuOpen.value = !userMenuOpen.value
  if (userMenuOpen.value) {
    projectMenuOpen.value = false
    tenantMenuOpen.value = false
    notificationOpen.value = false
  }
}

function switchProject(projectId: string) {
  const project = projectOptions.value.find((item) => item.id === projectId)
  if (!project) return
  store.setActiveProject(project.id, project.name)
  projectMenuOpen.value = false
  ElMessage.closeAll()
  ElMessage.success({ message: isEnglish.value ? `Switched to ${displayProjectName(project.name)}` : `已切换到 ${project.name}`, duration: 1500, showClose: false })
}

function toggleTenantMenu() {
  if (tenantSwitchingId.value) return
  tenantMenuOpen.value = !tenantMenuOpen.value
  if (tenantMenuOpen.value) {
    projectMenuOpen.value = false
    userMenuOpen.value = false
    notificationOpen.value = false
  }
}

function tenantRoleLabel(role: EnterpriseRole) {
  const labels: Record<EnterpriseRole, Copy> = {
    tenant_owner: { zh: '租户所有者', en: 'Tenant owner' },
    tenant_admin: { zh: '企业管理员', en: 'Tenant admin' },
    security_admin: { zh: '安全管理员', en: 'Security admin' },
    project_owner: { zh: '项目负责人', en: 'Project owner' },
    developer: { zh: '开发人员', en: 'Developer' },
    reviewer: { zh: '审查人', en: 'Reviewer' },
    auditor: { zh: '审计人员', en: 'Auditor' },
  }
  return localize(labels[role] ?? { zh: role, en: role })
}

function tenantStatusLabel(status: string) {
  const labels: Record<string, Copy> = {
    active: { zh: '已启用', en: 'Enabled' },
    inactive: { zh: '已停用', en: 'Disabled' },
    pending: { zh: '待激活', en: 'Pending' },
  }
  return localize(labels[status] ?? { zh: status || '状态未知', en: status || 'Unknown status' })
}

function tenantStatusTone(status: string) {
  if (status === 'inactive') return 'inactive'
  if (status === 'pending') return 'pending'
  return 'active'
}

async function switchActiveTenant(tenantId: string) {
  if (tenantSwitchingId.value) return
  if (tenantId === tenancy.activeTenant?.id) {
    tenantMenuOpen.value = false
    return
  }
  tenantSwitchingId.value = tenantId
  try {
    await tenancy.switchTenant(tenantId)
  } catch {
    ElMessage.error(isEnglish.value ? 'Failed to switch tenant' : '切换租户失败')
    tenantSwitchingId.value = null
    return
  }
  tenantSwitchingId.value = null
  tenantMenuOpen.value = false
  ElMessage.closeAll()
  ElMessage.success({
    message: isEnglish.value ? `Switched to ${tenancy.displayActiveName}` : `已切换到 ${tenancy.displayActiveName}`,
    duration: 1500,
    showClose: false,
  })
  // 租户切换后刷新业务数据（后端按新租户范围返回）
  void loadProjects()
  syncDashboardSummary()
  if (isRealMode.value) void store.refreshTaskList().catch(() => undefined)
}

// DEV-019 真实站内通知：/api/notifications 是 DB 真源，SSE 只负责提示在线页面立即刷新
async function loadRealNotifications() {
  loadingNotifications.value = true
  try {
    const [list, unreadList, unread] = await Promise.all([
      apiRequest<TaskNotification[]>('/api/notifications'),
      apiRequest<TaskNotification[]>('/api/notifications?unread_only=true'),
      apiRequest<{ count: number }>('/api/notifications/unread-count'),
    ])
    const unreadItems = unreadList ?? []
    const unreadIds = new Set(unreadItems.map((item) => item.id))
    const recentReadItems = (list ?? []).filter((item) => !unreadIds.has(item.id) && Boolean(item.read_at)).slice(0, 6)
    realNotifications.value = [...unreadItems, ...recentReadItems]
    unreadCount.value = unread?.count ?? unreadItems.length
  } catch {
    // 未登录或后端不可达时静默忽略（通知中心保持空态，绝不用示例数据冒充）
  } finally {
    loadingNotifications.value = false
  }
}

function toggleNotifications() {
  notificationOpen.value = !notificationOpen.value
  if (notificationOpen.value) {
    projectMenuOpen.value = false
    tenantMenuOpen.value = false
    userMenuOpen.value = false
  }
  if (notificationOpen.value && isRealMode.value) {
    void loadRealNotifications()
  }
}

function syncNotificationScrollLock(open: boolean) {
  document.documentElement.classList.toggle('notification-open', open)
  document.body.classList.toggle('notification-open', open)
}

function openRealNotification(item: TaskNotification) {
  notificationOpen.value = false
  if (!item.read_at) {
    void apiRequest(`/api/notifications/${item.id}/read`, { method: 'POST' })
  }
  router.push(`/tasks/${item.task_id}`)
}

function formatNotificationTime(value: string) {
  const timestamp = Date.parse(value)
  if (Number.isNaN(timestamp)) return value
  const minutes = Math.floor(Math.max(0, Date.now() - timestamp) / 60000)
  if (minutes < 1) return isEnglish.value ? 'Just now' : '刚刚'
  if (minutes < 60) return isEnglish.value ? `${minutes}m ago` : `${minutes}分钟前`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return isEnglish.value ? `${hours}h ago` : `${hours}小时前`
  const days = Math.floor(hours / 24)
  return isEnglish.value ? `${days}d ago` : `${days}天前`
}

function closeMenus(event: MouseEvent) {
  const target = event.target as Node
  if (
    notificationOpen.value &&
    notificationWrap.value &&
    notificationModalWrap.value &&
    !notificationWrap.value.contains(target) &&
    !notificationModalWrap.value.contains(target)
  ) {
    notificationOpen.value = false
  }
  if (sideUserWrap.value && !sideUserWrap.value.contains(target)) {
    userMenuOpen.value = false
  }
  if (projectWrap.value && !projectWrap.value.contains(target)) {
    projectMenuOpen.value = false
  }
  if (tenantWrap.value && !tenantWrap.value.contains(target)) {
    tenantMenuOpen.value = false
  }
}

function focusSearch(event: KeyboardEvent) {
  if (event.key === 'Escape') {
    userMenuOpen.value = false
    projectMenuOpen.value = false
    tenantMenuOpen.value = false
    notificationOpen.value = false
    return
  }
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
    event.preventDefault()
    searchInput.value?.focus()
    searchInput.value?.select()
  }
}

function syncDashboardSummary() {
  void store.refreshDashboardSummary().catch(() => undefined)
}

function scheduleRealtimeRefresh(event: TeamRealtimeEvent) {
  if (event.type === 'realtime.heartbeat' || event.type === 'realtime.unavailable') return
  pendingRealtimeEvents.push(event)
  if (realtimeRefreshTimer) return
  realtimeRefreshTimer = window.setTimeout(() => {
    realtimeRefreshTimer = undefined
    const events = pendingRealtimeEvents.splice(0)
    const taskIds = new Set(events.map((item) => item.task_id).filter((id): id is string => Boolean(id)))
    const hasNotification = events.some((item) => item.resource_type === 'notification' || item.type === 'notification.created')
    const hasTask = events.some((item) => item.resource_type === 'task' || item.type === 'task.updated')
    if (hasNotification) void loadRealNotifications()
    if (hasTask) {
      taskIds.forEach((taskId) => void store.ensureTaskLoaded(taskId).catch(() => undefined))
      if (route.path.startsWith('/tasks')) void store.refreshTaskList().catch(() => undefined)
      if (auth.hasPermission('approval:view')) void store.loadApprovalQueue().catch(() => undefined)
    }
    if (events.some((item) => item.resource_type === 'project' || item.resource_type === 'repository')) {
      void loadProjects()
      syncDashboardSummary()
    }
    if (hasTask || hasNotification) syncDashboardSummary()
  }, 80)
}

function startRealtimeWhenAuthenticated() {
  if (!isRealMode.value || !auth.ready || !auth.currentUser) return
  connectRealtime()
  realtimeUnsubscribe ??= subscribeRealtime(scheduleRealtimeRefresh)
}

onMounted(() => {
  window.addEventListener('keydown', focusSearch)
  document.addEventListener('click', closeMenus)
  void loadProjects()
  loadTenancyContext()
  syncDashboardSummary()
  if (isRealMode.value) {
    if (store.tasks.length === 0) {
      void store.refreshTaskList().catch(() => undefined)
    }
    if (auth.hasPermission('approval:view')) {
      void store.loadApprovalQueue().catch(() => undefined)
    }
    dashboardSummaryTimer = window.setInterval(syncDashboardSummary, 30000)
  }
  if (isRealMode.value) {
    void loadRealNotifications()
    notificationsTimer = window.setInterval(() => void loadRealNotifications(), 30000)
    startRealtimeWhenAuthenticated()
  }
})

watch(
  () => [auth.ready, auth.currentUser?.id, isRealMode.value] as const,
  () => startRealtimeWhenAuthenticated(),
  { immediate: true },
)

watch(
  () => auth.currentUser?.id,
  () => loadTenancyContext(),
)

onBeforeUnmount(() => {
  syncNotificationScrollLock(false)
  window.removeEventListener('keydown', focusSearch)
  document.removeEventListener('click', closeMenus)
  if (dashboardSummaryTimer) {
    clearInterval(dashboardSummaryTimer)
    dashboardSummaryTimer = undefined
  }
  if (notificationsTimer) {
    clearInterval(notificationsTimer)
    notificationsTimer = undefined
  }
  if (realtimeRefreshTimer) {
    clearTimeout(realtimeRefreshTimer)
    realtimeRefreshTimer = undefined
  }
  pendingRealtimeEvents.splice(0)
  realtimeUnsubscribe?.()
  realtimeUnsubscribe = undefined
  disconnectRealtime()
})

watch(
  () => store.activeProjectId,
  () => {
    if (isRealMode.value) {
      syncDashboardSummary()
      void store.refreshTaskList().catch(() => undefined)
    }
  },
)

watch(notificationOpen, syncNotificationScrollLock, { immediate: true })
</script>

<template>
  <main class="app-frame">
    <aside class="side-nav" :class="{ 'side-nav--account-open': userMenuOpen }">
      <RouterLink to="/dashboard" class="brand">
        <span class="brand__mark brand__mark--logo"><img src="/favicon.svg" alt="" aria-hidden="true" /></span>
        <span>{{ isEnglish ? 'Siqi' : '司契 Siqi' }}</span>
      </RouterLink>

      <div ref="projectWrap" class="project-switcher">
        <span class="project-switcher__label">{{ isEnglish ? 'Current project' : '当前项目' }}</span>
        <button
          class="project-switcher__button"
          :class="{ 'project-switcher__button--open': projectMenuOpen }"
          type="button"
          aria-haspopup="menu"
          :aria-expanded="projectMenuOpen"
          @click.stop="toggleProjectMenu"
          @keydown.esc.stop="projectMenuOpen = false"
        >
          <span class="project-switcher__button-mark" aria-hidden="true">{{ store.activeProject.slice(0, 1) }}</span>
          <span class="project-switcher__button-body">
            <strong>{{ displayProjectName(store.activeProject) }}</strong>
            <small>{{ isEnglish ? 'Switch workspace' : '切换工作区' }}</small>
          </span>
          <ChevronDown :size="16" class="project-switcher__chevron" />
        </button>
        <Transition name="project-switcher-pop">
          <div v-if="projectMenuOpen" class="project-switcher__menu" role="menu" :aria-label="isEnglish ? 'Project switcher' : '项目切换器'">
            <div class="project-switcher__menu-header">
              <span>
                <strong>{{ isEnglish ? 'Switch project' : '切换项目' }}</strong>
                <small>{{ isEnglish ? 'Choose a workspace to continue' : '选择一个工作区继续' }}</small>
              </span>
              <em>{{ projectOptions.length }}</em>
            </div>
            <button
              v-for="item in projectOptions"
              :key="item.id"
              type="button"
              role="menuitemradio"
              :aria-checked="item.id === store.activeProjectId"
              class="project-switcher__menu-item"
              :class="{ 'is-active': item.id === store.activeProjectId }"
              @click.stop="switchProject(item.id)"
            >
              <span class="project-switcher__menu-item-icon" aria-hidden="true">{{ item.name.slice(0, 1) }}</span>
              <span class="project-switcher__menu-item-text">
                <strong>{{ localize(item.label) }}</strong>
                <small>{{ localize(item.detail) }}</small>
              </span>
              <span class="project-switcher__menu-item-mark" :class="{ 'is-visible': item.id === store.activeProjectId }">
                <Check :size="15" />
              </span>
            </button>
          </div>
        </Transition>
      </div>

      <nav class="nav-list" :aria-label="isEnglish ? 'Main navigation' : '主导航'">
        <RouterLink
          v-for="item in navItems"
          :key="item.match"
          :to="item.to"
          class="nav-item"
          :class="{ 'nav-item--active': route.path === item.match || route.path.startsWith(`${item.match}/`) }"
        >
          <component :is="item.icon" :size="17" />
          <span>{{ localize(item.label) }}</span>
          <span v-if="item.badge && approvalBadgeCount" class="nav-item__badge">{{ approvalBadgeCount }}</span>
        </RouterLink>
      </nav>

      <div ref="sideUserWrap" class="side-nav__account">
        <Transition name="account-panel">
          <div v-if="userMenuOpen" class="side-account-panel">
            <header class="side-account-panel__header">
              <span class="avatar avatar--large">{{ currentUser?.name?.slice(0, 2) ?? 'WL' }}</span>
              <span>
                <strong>{{ currentUser?.name ?? (isEnglish ? 'Not signed in' : '未登录') }}</strong>
                <small>{{ roleLabel(currentUser?.role, currentUser?.role_label ?? '研发负责人') }}</small>
              </span>
            </header>
            <div class="side-account-panel__menu">
              <button class="side-account-panel__row" type="button" aria-haspopup="true">
                <Globe2 :size="16" />
                <span>{{ isEnglish ? 'Language' : '语言' }}</span>
                <em>{{ currentLanguageLabel }}</em>
                <ChevronRight :size="15" />
              </button>
              <div class="side-account-submenu" role="radiogroup" :aria-label="isEnglish ? 'Language' : '语言'">
                <button
                  v-for="option in languageOptions"
                  :key="option.value"
                  type="button"
                  role="radio"
                  :aria-checked="languageMode === option.value"
                  class="side-account-submenu__item"
                  :class="{ 'is-active': languageMode === option.value }"
                  @click.stop="chooseLanguage(option.value)"
                >
                  <span>{{ option.label }}</span>
                  <Check v-if="languageMode === option.value" :size="15" />
                </button>
              </div>
            </div>
            <div class="side-account-panel__menu">
              <button
                class="side-account-panel__row"
                type="button"
                aria-haspopup="true"
              >
                <Moon :size="16" />
                <span>{{ isEnglish ? 'Theme' : '主题' }}</span>
                <em>{{ currentThemeLabel }}</em>
                <ChevronRight :size="15" />
              </button>
              <div class="side-account-submenu" role="radiogroup" :aria-label="isEnglish ? 'Theme mode' : '主题模式'">
                <button
                  v-for="option in themeOptions"
                  :key="option.value"
                  type="button"
                  role="radio"
                  :aria-checked="themeMode === option.value"
                  class="side-account-submenu__item"
                  :class="{ 'is-active': themeMode === option.value }"
                  @click.stop="chooseTheme(option.value)"
                >
                  <span>{{ localize(option.label) }}</span>
                  <Check v-if="themeMode === option.value" :size="15" />
                </button>
              </div>
            </div>
            <nav class="side-account-panel__actions" :aria-label="isEnglish ? 'Account actions' : '账户操作'">
              <button type="button" @click="logout">
                <LogOut :size="16" />
                <span>{{ isEnglish ? 'Sign out' : '退出登录' }}</span>
              </button>
            </nav>
          </div>
        </Transition>

        <button class="side-nav__footer" type="button" :aria-expanded="userMenuOpen" :aria-label="isEnglish ? 'Open account menu' : '打开账户菜单'" @click.stop="toggleUserMenu">
          <span class="avatar">{{ currentUser?.name?.slice(0, 2) ?? 'WL' }}</span>
          <span>
            <strong>{{ currentUser?.name ?? (isEnglish ? 'Not signed in' : '未登录') }}</strong>
            <small>{{ roleLabel(currentUser?.role, currentUser?.role_label ?? '研发负责人') }}</small>
          </span>
          <ChevronRight class="side-nav__footer-chevron" :size="15" aria-hidden="true" />
        </button>
      </div>
    </aside>

    <section class="workspace">
      <header class="top-bar">
        <span class="top-bar__signal" aria-hidden="true"><i /></span>
        <div>
          <p class="top-bar__eyebrow">{{ isEnglish ? 'R&D Console' : '研发控制台' }} / {{ displayProjectName(store.activeProject) }}</p>
          <h1>{{ title }}</h1>
          <div v-if="roleSummary" class="top-bar__role" :title="roleSummary.actions">
            <span class="top-bar__role-tag">{{ roleLabel(currentUser?.role, currentUser?.role_label ?? '') }}</span>
            <small>{{ roleSummary.scope }}</small>
          </div>
        </div>
        <div class="top-bar__tools">
          <div ref="tenantWrap" class="tenant-chip">
            <button
              class="tenant-chip__button"
              :class="{ 'is-open': tenantMenuOpen, 'is-busy': tenantSwitchingId }"
              type="button"
              :aria-expanded="tenantMenuOpen"
              :aria-busy="Boolean(tenantSwitchingId)"
              aria-haspopup="menu"
              :title="isEnglish ? 'Current tenant' : '当前租户'"
              @click.stop="toggleTenantMenu"
              @keydown.esc.stop="tenantMenuOpen = false"
            >
              <span class="tenant-chip__button-icon" aria-hidden="true"><Globe2 :size="15" /></span>
              <span class="tenant-chip__button-copy">
                <strong>{{ tenancy.displayActiveName }}</strong>
                <small>{{ isEnglish ? 'Tenant context' : '当前租户' }}</small>
              </span>
              <span class="tenant-chip__button-dot" :class="`is-${tenantStatusTone(activeTenantStatus)}`" aria-hidden="true" />
              <ChevronDown :size="14" class="tenant-chip__button-chevron" />
            </button>
            <Transition name="tenant-chip-pop">
              <div v-if="tenantMenuOpen" class="tenant-chip__menu" role="menu" :aria-label="isEnglish ? 'Tenant switcher' : '租户切换器'">
                <header class="tenant-chip__menu-header">
                  <span class="tenant-chip__menu-heading">
                    <span class="tenant-chip__menu-icon" aria-hidden="true"><Globe2 :size="16" /></span>
                    <span>
                      <strong>{{ isEnglish ? 'Switch tenant' : '切换租户' }}</strong>
                      <small>{{ isEnglish ? 'Authorized by the service' : '由服务端授权管理' }}</small>
                    </span>
                  </span>
                  <span class="tenant-chip__menu-count">{{ isEnglish ? `${tenancy.tenants.length} tenants` : `${tenancy.tenants.length} 个租户` }}</span>
                </header>
                <div v-if="!tenancy.tenants.length" class="tenant-chip__empty">
                  <Globe2 :size="18" />
                  <span>{{ isEnglish ? 'No switchable tenants' : '暂无可切换租户' }}</span>
                </div>
                <button
                  v-for="tenant in tenancy.tenants"
                  :key="tenant.id"
                  type="button"
                  role="menuitemradio"
                  :aria-checked="tenant.id === tenancy.activeTenant?.id"
                  :disabled="Boolean(tenantSwitchingId)"
                  class="tenant-chip__menu-item"
                  :class="{
                    'is-active': tenant.id === tenancy.activeTenant?.id,
                    'is-switching': tenantSwitchingId === tenant.id,
                  }"
                  @click.stop="switchActiveTenant(tenant.id)"
                >
                  <span class="tenant-chip__menu-item-avatar" aria-hidden="true">{{ tenant.name.slice(0, 1) }}</span>
                  <span class="tenant-chip__menu-item-name">
                    <strong>{{ tenant.name }}</strong>
                    <small v-if="tenantSwitchingId === tenant.id">{{ isEnglish ? 'Switching…' : '切换中…' }}</small>
                    <small v-else>{{ tenantRoleLabel(tenant.role) }} · {{ tenantStatusLabel(tenant.status) }}</small>
                  </span>
                  <span class="tenant-chip__menu-item-state" aria-hidden="true">
                    <LoaderCircle v-if="tenantSwitchingId === tenant.id" :size="15" class="is-spinning" />
                    <Check v-else-if="tenant.id === tenancy.activeTenant?.id" :size="15" />
                    <i v-else />
                  </span>
                </button>
              </div>
            </Transition>
          </div>
          <label class="global-search">
            <Search :size="16" />
            <input ref="searchInput" v-model="globalSearch" :placeholder="isEnglish ? 'Search tasks, issues, or repositories' : '搜索任务、Issue 或仓库'" @keydown.enter.prevent="runSearch" />
          </label>
          <div ref="notificationWrap" class="notification-wrap">
            <button class="icon-button" :class="{ 'icon-button--active': notificationOpen }" :aria-label="isEnglish ? 'Notifications' : '通知'" :aria-expanded="notificationOpen" @click.stop="toggleNotifications">
              <Bell :size="18" />
              <span v-if="unreadCount > 0" class="notification-badge">{{ unreadCount > 99 ? '99+' : unreadCount }}</span>
            </button>
          </div>
        </div>
      </header>
      <slot />
    </section>

    <Transition name="notification-modal">
      <div
        v-if="notificationOpen"
        ref="notificationModalWrap"
        class="notification-modal"
        @click.self="notificationOpen = false"
      >
        <section class="notification-dialog" role="dialog" aria-modal="true" :aria-label="isEnglish ? 'Announcements' : '公告'">
          <header class="notification-dialog__header">
            <span class="notification-dialog__heading">
              <span class="notification-dialog__icon"><Bell :size="19" /></span>
              <span>
                <strong>{{ isEnglish ? 'Announcements' : '公告' }}</strong>
                <small>{{ isEnglish ? `${unreadCount} unread, pinned first` : `${unreadCount} 条未读，已置顶显示` }}</small>
              </span>
            </span>
            <button class="notification-dialog__close" type="button" :aria-label="isEnglish ? 'Close announcements' : '关闭公告'" @click.stop="notificationOpen = false">
              <X :size="18" />
            </button>
          </header>

          <div v-if="loadingNotifications" class="notification-dialog__loading">
            {{ isEnglish ? 'Loading notifications' : '正在加载通知' }}
          </div>
          <div v-else-if="realNotifications.length" class="notification-dialog__list">
            <section v-if="unreadNotifications.length" class="notification-dialog__section">
              <header class="notification-dialog__section-header">
                <strong>{{ isEnglish ? 'Unread' : '未读' }}</strong>
                <span>{{ unreadNotifications.length }}</span>
              </header>
              <button
                v-for="item in unreadNotifications"
                :key="item.id"
                class="notification-dialog__item is-unread"
                :class="{ 'is-high': item.priority === 'high' }"
                type="button"
                @click="openRealNotification(item)"
              >
                <span class="notification-dialog__item-icon"><Check :size="16" /></span>
                <span class="notification-dialog__item-body">
                  <span class="notification-dialog__item-title">
                    <strong>{{ item.title }}</strong>
                    <em class="notification-dialog__unread-label">{{ isEnglish ? 'Unread' : '未读' }}</em>
                    <em v-if="item.priority === 'high'" class="notification-dialog__priority-label">{{ isEnglish ? 'High' : '高优先级' }}</em>
                  </span>
                  <small>{{ notificationTypeText[item.type] }} · {{ item.body }} · {{ formatNotificationTime(item.created_at) }}</small>
                </span>
                <ChevronRight :size="18" class="notification-dialog__item-arrow" />
              </button>
            </section>

            <section v-if="readNotifications.length" class="notification-dialog__section">
              <header class="notification-dialog__section-header">
                <strong>{{ isEnglish ? 'Recent read' : '最近已读' }}</strong>
                <span>{{ readNotifications.length }}</span>
              </header>
              <button
                v-for="item in readNotifications"
                :key="item.id"
                class="notification-dialog__item"
                :class="{ 'is-high': item.priority === 'high' }"
                type="button"
                @click="openRealNotification(item)"
              >
                <span class="notification-dialog__item-icon"><Check :size="16" /></span>
                <span class="notification-dialog__item-body">
                  <span class="notification-dialog__item-title">
                    <strong>{{ item.title }}</strong>
                    <em v-if="item.priority === 'high'" class="notification-dialog__priority-label">{{ isEnglish ? 'High' : '高优先级' }}</em>
                  </span>
                  <small>{{ notificationTypeText[item.type] }} · {{ item.body }} · {{ formatNotificationTime(item.created_at) }}</small>
                </span>
                <ChevronRight :size="18" class="notification-dialog__item-arrow" />
              </button>
            </section>
          </div>
          <div v-else class="notification-dialog__empty">
            <Bell :size="22" />
            <span>{{ isEnglish ? 'No new notifications' : '暂无新的通知' }}</span>
          </div>
        </section>
      </div>
    </Transition>
  </main>
</template>

<style scoped>
/* 企业版 E1-01：顶栏租户切换组件。 */
.tenant-chip {
  position: relative;
  display: inline-flex;
  flex: 0 0 auto;
  min-width: 0;
}
.tenant-chip__button {
  display: flex;
  align-items: center;
  width: 172px;
  min-width: 0;
  height: 40px;
  gap: 8px;
  padding: 0 10px 0 8px;
  border-color: var(--panel-border);
  border-radius: 10px;
  color: var(--text-strong);
  background: var(--control-bg, var(--panel-bg));
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, .7), var(--shadow-sm);
  line-height: normal;
  text-align: left;
  transition: border-color .18s ease, background-color .18s ease, box-shadow .18s ease, transform .18s ease;
}

.tenant-chip__button:hover,
.tenant-chip__button.is-open {
  border-color: #78b9b8;
  background: var(--surface-hover, var(--panel-bg));
  box-shadow: 0 0 0 3px rgba(28, 196, 189, .1), var(--shadow-sm);
}

.tenant-chip__button:active {
  transform: translateY(1px);
}

.tenant-chip__button:focus-visible,
.tenant-chip__menu-item:focus-visible {
  outline: 0;
  box-shadow: 0 0 0 3px rgba(28, 196, 189, .22);
}

.tenant-chip__button.is-busy {
  cursor: wait;
}

.tenant-chip__button-icon,
.tenant-chip__menu-icon,
.tenant-chip__menu-item-avatar {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: none;
}

.tenant-chip__button-icon {
  width: 26px;
  height: 26px;
  border: 1px solid rgba(28, 196, 189, .28);
  border-radius: 8px;
  color: #149b96;
  background: rgba(28, 196, 189, .1);
}

.tenant-chip__button-copy {
  display: grid;
  grid-template-rows: auto auto;
  align-content: center;
  min-width: 0;
  flex: 1 1 auto;
  gap: 1px;
}

.tenant-chip__button-copy strong,
.tenant-chip__button-copy small,
.tenant-chip__menu-item-name strong,
.tenant-chip__menu-item-name small {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tenant-chip__button-copy strong,
.tenant-chip__button-copy small {
  display: block;
  min-width: 0;
}

.tenant-chip__button-copy strong {
  color: var(--text-strong);
  font-size: 12px;
  font-weight: 700;
  line-height: 16px;
}

.tenant-chip__button-copy small {
  color: #a3b0c2;
  font-size: 10px;
  line-height: 13px;
}

.tenant-chip__button-dot {
  width: 6px;
  height: 6px;
  flex: none;
  border: 1px solid var(--panel-bg);
  border-radius: 50%;
  background: #39c78a;
  box-shadow: 0 0 0 2px rgba(57, 199, 138, .12);
}

.tenant-chip__button-dot.is-pending {
  background: #d99827;
  box-shadow: 0 0 0 2px rgba(217, 152, 39, .14);
}

.tenant-chip__button-dot.is-inactive {
  background: #9aa7b8;
  box-shadow: 0 0 0 2px rgba(154, 167, 184, .14);
}

.tenant-chip__button-chevron {
  flex: none;
  color: var(--text-faint);
  transition: color .18s ease, transform .22s cubic-bezier(.16, 1, .3, 1);
}

.tenant-chip__button.is-open .tenant-chip__button-chevron {
  color: #149b96;
  transform: rotate(180deg);
}

.tenant-chip__menu {
  position: absolute;
  top: calc(100% + 12px);
  right: 0;
  z-index: 160;
  width: min(320px, calc(100vw - 24px));
  max-height: min(420px, calc(100vh - 88px));
  min-width: 0;
  padding: 6px;
  overflow: auto;
  border: 1px solid color-mix(in srgb, var(--panel-border) 82%, rgba(28, 196, 189, .18));
  border-radius: 14px;
  background: var(--panel-bg);
  box-shadow: 0 20px 44px rgba(0, 0, 0, .28), 0 4px 12px rgba(0, 0, 0, .12);
  transform-origin: top right;
}

.tenant-chip__menu-header {
  display: flex;
  flex-direction: row;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  min-height: 60px;
  padding: 4px 8px 12px;
  border-bottom: 1px solid var(--panel-border-soft);
}

.tenant-chip__menu-heading {
  display: flex;
  align-items: center;
  min-width: 0;
  gap: 9px;
}

.tenant-chip__menu-icon {
  width: 30px;
  height: 30px;
  border: 1px solid rgba(28, 196, 189, .28);
  border-radius: 8px;
  color: #149b96;
  background: rgba(28, 196, 189, .1);
}

.tenant-chip__menu-heading > span:last-child {
  display: grid;
  min-width: 0;
  gap: 2px;
}

.tenant-chip__menu-header strong {
  color: var(--text-strong);
  font-size: 13px;
  line-height: 18px;
}

.tenant-chip__menu-header small {
  color: var(--text-faint);
  font-size: 10px;
  line-height: 14px;
  opacity: 1;
}

.tenant-chip__menu-count {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-height: 22px;
  padding: 0 7px;
  border: 1px solid var(--panel-border-soft);
  border-radius: 999px;
  color: var(--text-faint);
  background: transparent;
  font-size: 10px;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.tenant-chip__empty {
  display: grid;
  place-items: center;
  gap: 7px;
  min-height: 112px;
  padding: 18px 12px;
  color: var(--text-faint);
  font-size: 11px;
  text-align: center;
}

.tenant-chip__empty svg {
  color: var(--text-soft);
}

.tenant-chip__menu-item {
  display: grid;
  grid-template-columns: 32px minmax(0, 1fr) 22px;
  align-items: center;
  gap: 10px;
  width: 100%;
  min-height: 56px;
  margin-top: 6px;
  padding: 8px 10px;
  border: 1px solid transparent;
  border-radius: 10px;
  color: var(--text-strong);
  background: transparent;
  font: inherit;
  text-align: left;
  cursor: pointer;
  transition: background-color .18s ease, border-color .18s ease, transform .18s ease, opacity .18s ease;
}

.tenant-chip__menu-item:hover:not(:disabled),
.tenant-chip__menu-item.is-active {
  border-color: rgba(28, 196, 189, .18);
  background: rgba(28, 196, 189, .055);
}

.tenant-chip__menu-item:hover:not(:disabled) {
  transform: translateY(-1px);
}

.tenant-chip__menu-item:disabled {
  cursor: wait;
  opacity: .78;
}

.tenant-chip__menu-item-avatar {
  width: 32px;
  height: 32px;
  border: 1px solid var(--panel-border);
  border-radius: 9px;
  color: var(--text-base);
  background: var(--surface-subtle, var(--panel-bg));
  font-size: 12px;
  font-weight: 800;
  transition: color .18s ease, border-color .18s ease, background-color .18s ease;
}

.tenant-chip__menu-item.is-active .tenant-chip__menu-item-avatar,
.tenant-chip__menu-item:hover:not(:disabled) .tenant-chip__menu-item-avatar {
  border-color: rgba(28, 196, 189, .28);
  color: #149b96;
  background: rgba(28, 196, 189, .09);
}

.tenant-chip__menu-item-name {
  display: grid;
  min-width: 0;
  gap: 3px;
}

.tenant-chip__menu-item-name strong {
  color: var(--text-strong);
  font-size: 12px;
  line-height: 17px;
}

.tenant-chip__menu-item-name small {
  color: var(--text-faint);
  font-size: 10px;
  line-height: 14px;
}

.tenant-chip__menu-item-state {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  color: #21b879;
}

.tenant-chip__menu-item-state i {
  width: 6px;
  height: 6px;
  border: 1px solid var(--panel-border);
  border-radius: 50%;
  background: var(--text-faint);
}

.tenant-chip-pop-enter-active,
.tenant-chip-pop-leave-active {
  transition: opacity .2s ease, transform .24s cubic-bezier(.16, 1, .3, 1), filter .2s ease;
}

.tenant-chip-pop-enter-from,
.tenant-chip-pop-leave-to {
  opacity: 0;
  filter: blur(2px);
  transform: translateY(-7px) scale(.97);
}

@media (max-width: 720px) {
  .tenant-chip__button {
    min-width: 0;
    width: 42px;
    padding: 0;
    justify-content: center;
  }

  .tenant-chip__button-copy,
  .tenant-chip__button-dot,
  .tenant-chip__button-chevron {
    display: none;
  }

  .tenant-chip__menu {
    left: 0;
    right: auto;
    top: auto;
    bottom: calc(100% + 12px);
    width: min(310px, calc(100vw - 24px));
    transform-origin: bottom left;
  }
}
</style>
