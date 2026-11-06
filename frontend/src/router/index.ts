import { createRouter, createWebHistory } from 'vue-router'
import { ElMessage } from 'element-plus'
import { useAuthStore } from '../stores/auth'
import { useTenancyStore } from '../stores/tenancy'
import { hasAccessRequirement } from '../services/accessControl'

// 业务页面按路由懒加载，避免登录页首屏捆绑整个工作台和详情模块。
const ApprovalView = () => import('../views/ApprovalView.vue')
const CreateTaskView = () => import('../views/CreateTaskView.vue')
const DashboardView = () => import('../views/DashboardView.vue')
const DiffView = () => import('../views/DiffView.vue')
const ForgotPasswordView = () => import('../views/ForgotPasswordView.vue')
const IndexView = () => import('../views/IndexView.vue')
const LegalDocumentView = () => import('../views/LegalDocumentView.vue')
const LoginView = () => import('../views/LoginView.vue')
const ReplayView = () => import('../views/ReplayView.vue')
const NotFoundView = () => import('../views/NotFoundView.vue')
const AccountRecoveryView = () => import('../views/AccountRecoveryView.vue')
const AuditCenterView = () => import('../views/AuditCenterView.vue')
const RequestCreateView = () => import('../views/RequestCreateView.vue')
const RequestDetailView = () => import('../views/RequestDetailView.vue')
const RequestListView = () => import('../views/RequestListView.vue')
const SettingsView = () => import('../views/SettingsView.vue')
const EnterpriseMembersView = () => import('../views/EnterpriseMembersView.vue')
const EnterpriseApprovalsView = () => import('../views/EnterpriseApprovalsView.vue')
const TaskDetailView = () => import('../views/TaskDetailView.vue')
const RagConsoleView = () => import('../views/RagConsoleView.vue')
const EnterpriseDashboardView = () => import('../views/EnterpriseDashboardView.vue')
const RuntimeConsoleView = () => import('../views/RuntimeConsoleView.vue')
const EnterpriseOpsView = () => import('../views/EnterpriseOpsView.vue')
const TaskListView = () => import('../views/TaskListView.vue')

const router = createRouter({
  history: createWebHistory(),
  scrollBehavior: () => ({ top: 0 }),
  routes: [
    { path: '/', redirect: '/dashboard' },
    { path: '/login', component: LoginView, meta: { public: true, title: '登录' } },
    { path: '/forgot-password', component: ForgotPasswordView, meta: { public: true, title: '找回密码' } },
    { path: '/terms', component: LegalDocumentView, props: { kind: 'terms' }, meta: { public: true, title: '服务协议' } },
    { path: '/privacy', component: LegalDocumentView, props: { kind: 'privacy' }, meta: { public: true, title: '隐私政策' } },
    { path: '/dashboard', component: DashboardView, meta: { title: '工作台', permission: 'dashboard:view' } },
    { path: '/tasks', component: TaskListView, meta: { title: '任务列表', permission: 'task:view' } },
    { path: '/tasks/new', component: CreateTaskView, meta: { title: '创建任务', permission: 'task:create' } },
    { path: '/tasks/:id', component: TaskDetailView, meta: { title: '任务详情', permission: 'task:view' } },
    { path: '/requests', component: RequestListView, meta: { title: '平台需求', permission: 'request:view' } },
    { path: '/requests/new', component: RequestCreateView, meta: { title: '创建需求', permission: 'request:create' } },
    { path: '/requests/:id', component: RequestDetailView, meta: { title: '需求详情', permission: 'request:view' } },
    { path: '/approvals', component: ApprovalView, meta: { title: '审批中心', permission: 'approval:view' } },
    { path: '/replay/:id?', component: ReplayView, meta: { title: '任务回放', permission: 'replay:view' } },
    { path: '/diff/:id?', component: DiffView, meta: { title: 'Diff 审核', permission: 'diff:view' } },
    { path: '/indexes', component: IndexView, meta: { title: '索引管理', permission: 'index:view' } },
    { path: '/settings', component: SettingsView, meta: { title: '项目配置', permission: 'project:manage' } },
    { path: '/account-recovery', component: AccountRecoveryView, meta: { title: '账号恢复', permission: 'user:view' } },
    { path: '/audit', component: AuditCenterView, meta: { title: '审计中心', permission: ['project:manage', 'audit:view'] } },
    { path: '/enterprise/members', component: EnterpriseMembersView, meta: { title: '成员管理', permission: ['project:manage', 'member:view'] } },
    { path: '/enterprise/approvals', component: EnterpriseApprovalsView, meta: { title: '审批中心', permission: ['project:manage', 'approval:view'] } },
    { path: '/enterprise/rag', component: RagConsoleView, meta: { title: '企业 RAG', permission: ['rag:search'] } },
    { path: '/enterprise/dashboard', component: EnterpriseDashboardView, meta: { title: '企业工作台' } },
    { path: '/enterprise/runtime', component: RuntimeConsoleView, meta: { title: '企业 Runtime', permission: ['rag:search'] } },
    { path: '/enterprise/ops', component: EnterpriseOpsView, meta: { title: '企业设置', permission: ['project:manage', 'org:manage'] } },
    { path: '/:pathMatch(.*)*', component: NotFoundView, meta: { title: '页面不存在', layout: 'blank' } },
  ],
})

router.beforeEach(async (to) => {
  const auth = useAuthStore()
  if (!auth.ready) {
    await auth.bootstrap()
  }
  if (to.meta.public) {
    return to.path === '/login' && auth.isAuthenticated ? { path: '/dashboard' } : true
  }
  if (!auth.isAuthenticated) {
    return { path: '/login', query: { redirect: to.fullPath } }
  }
  const permission = to.meta.permission
  const tenancy = useTenancyStore()
  if (permission && !hasAccessRequirement(auth, tenancy, permission) && !tenancy.ready) {
    await tenancy.loadContext()
  }
  if (permission && !hasAccessRequirement(auth, tenancy, permission)) {
    ElMessage.warning(`您暂无权限访问「${String(to.meta.title ?? '该页面')}」`)
    return { path: '/dashboard' }
  }
  return true
})

export default router
