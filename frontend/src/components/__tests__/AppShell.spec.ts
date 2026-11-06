import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { nextTick } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import AppShell from '../AppShell.vue'
import { setDataSourceMode } from '../../services/dataSourceMode'
import { useAuthStore } from '../../stores/auth'
import { useTenancyStore } from '../../stores/tenancy'
import { useWorkspaceStore } from '../../stores/workspace'
import { makeAgentTask } from '../../test/fixtures'
import type { AuthUser } from '../../types/auth'
import type { EnterpriseContext } from '../../types/tenancy'

const { apiRequestMock } = vi.hoisted(() => ({
  apiRequestMock: vi.fn(),
}))

vi.mock('../../services/api', () => ({
  apiRequest: apiRequestMock,
  ApiError: class ApiError extends Error {
    status: number
    constructor(message: string, status = 0) {
      super(message)
      this.status = status
    }
  },
}))

const Dummy = { template: '<div />' }

let activePinia: ReturnType<typeof createPinia>
let router: Router
let activeWrapper: VueWrapper | null = null

const routes = [
  { path: '/dashboard', component: Dummy },
  { path: '/tasks', component: Dummy },
  { path: '/tasks/:id', component: Dummy },
  { path: '/approvals', component: Dummy },
  { path: '/replay/:id?', component: Dummy },
  { path: '/diff/:id?', component: Dummy },
  { path: '/indexes', component: Dummy },
  { path: '/audit', component: Dummy },
  { path: '/enterprise/members', component: Dummy },
]

function navHrefs(): Record<string, string> {
  const links = activeWrapper!.findAll('.nav-list .nav-item')
  return Object.fromEntries(links.map((link) => [link.text().trim(), link.attributes('href') ?? '']))
}

async function mountAt(path: string) {
  router = createRouter({ history: createMemoryHistory(), routes })
  await router.push(path)
  activeWrapper = mount(AppShell, { global: { plugins: [activePinia, router] } })
}

// mount 后通过 pinia 注册表拿到与组件同一份 fresh store 实例（绕过 pinia 4 module-level activePinia 跨用例残留）
function mountedStore(): ReturnType<typeof useWorkspaceStore> {
  const registry = (activePinia as unknown as { _s: Map<string, ReturnType<typeof useWorkspaceStore>> })._s
  return registry.get('workspace')!
}

function makeEnterpriseContext(permissions: string[]): EnterpriseContext {
  return {
    active_tenant: { id: 'tenant_default', name: '默认租户', role: 'auditor', role_label: '审计人员' },
    tenants: [{ id: 'tenant_default', name: '默认租户', role: 'auditor', status: 'active' }],
    role: 'auditor',
    role_label: '审计人员',
    permissions,
  }
}

function signInForShell(user: Partial<AuthUser> = {}) {
  const auth = useAuthStore()
  auth.currentUser = {
    id: 'user_observer',
    name: '李雷',
    email: 'observer@example.com',
    role: 'observer',
    role_label: '观察者',
    permissions: [],
    ...user,
  }
  auth.ready = true
}

beforeEach(() => {
  activeWrapper?.unmount()
  activeWrapper = null
  localStorage.clear()
  // mock 模式下 store 以 demo 任务播种（默认选中 TASK-8842），保证侧栏任务级链接断言有稳定上下文
  setDataSourceMode('mock')
  apiRequestMock.mockReset()
  apiRequestMock.mockResolvedValue([])
  activePinia = createPinia()
  setActivePinia(activePinia)
})

afterEach(() => {
  setDataSourceMode('real')
})

describe('AppShell 侧栏任务级链接', () => {
  it('回放页直达/刷新时跟随路由任务，不再指向快照里的旧选中任务', async () => {
    // 快照无任务时默认选中 mock 的 TASK-8842，但当前路由是 /replay/TASK-9002
    await mountAt('/replay/TASK-9002')
    const hrefs = navHrefs()
    expect(hrefs['任务回放']).toBe('/replay/TASK-9002')
    expect(hrefs['Diff 审核']).toBe('/diff/TASK-9002')
    expect(hrefs['任务回放']).not.toContain('TASK-8842')
  })

  it('任务详情页时链接指向详情任务，切换详情任务后响应式更新', async () => {
    await mountAt('/tasks/TASK-9001')
    expect(navHrefs()['任务回放']).toBe('/replay/TASK-9001')

    await router.push('/tasks/TASK-9002')
    await nextTick()
    expect(navHrefs()['任务回放']).toBe('/replay/TASK-9002')
    expect(navHrefs()['Diff 审核']).toBe('/diff/TASK-9002')
  })

  it('非任务页跟随当前选中任务，选中变化后链接更新', async () => {
    await mountAt('/dashboard')
    expect(navHrefs()['任务回放']).toBe('/replay/TASK-8842')

    mountedStore().selectTask('TASK-9002')
    await nextTick()
    expect(navHrefs()['任务回放']).toBe('/replay/TASK-9002')
    expect(navHrefs()['Diff 审核']).toBe('/diff/TASK-9002')
  })

  it('没有任何任务上下文时回退到任务列表，不拼出悬空任务链接', async () => {
    await mountAt('/dashboard')
    mountedStore().selectedTaskId = ''
    await nextTick()
    expect(navHrefs()['任务回放']).toBe('/tasks')
    expect(navHrefs()['Diff 审核']).toBe('/tasks')
  })
})

describe('AppShell 企业权限导航', () => {
  it('企业 audit:view/member:view 可以显示审计中心和成员管理入口', async () => {
    signInForShell()
    apiRequestMock.mockImplementation((path: string) => {
      if (path === '/api/enterprise/tenant-context') return Promise.resolve(makeEnterpriseContext(['audit:view', 'member:view']))
      return Promise.resolve([])
    })

    await mountAt('/dashboard')
    await flushPromises()

    const hrefs = navHrefs()
    expect(hrefs['审计中心']).toBe('/audit')
    expect(hrefs['成员管理']).toBe('/enterprise/members')
  })

  it('没有团队或企业权限时隐藏审计中心和成员管理入口', async () => {
    signInForShell()
    const tenancy = useTenancyStore()
    tenancy.context = makeEnterpriseContext([])
    tenancy.activeTenant = tenancy.context.active_tenant
    tenancy.ready = true

    await mountAt('/dashboard')
    await flushPromises()

    const hrefs = navHrefs()
    expect(hrefs['审计中心']).toBeUndefined()
    expect(hrefs['成员管理']).toBeUndefined()
  })
})

describe('AppShell 租户切换器', () => {
  function seedTenants() {
    const tenancy = useTenancyStore()
    tenancy.context = {
      active_tenant: { id: 'tenant_default', name: '默认租户', role: 'auditor', role_label: '审计人员' },
      tenants: [
        { id: 'tenant_default', name: '默认租户', role: 'auditor', status: 'active' },
        { id: 'tenant_lab', name: '研发实验室', role: 'developer', status: 'active' },
      ],
      role: 'auditor',
      role_label: '审计人员',
      permissions: ['tenant:switch'],
    }
    tenancy.activeTenant = tenancy.context.active_tenant
    tenancy.ready = true
    return tenancy
  }

  it('展开时显示租户数量、角色和可读状态，并标记当前租户', async () => {
    seedTenants()
    await mountAt('/dashboard')

    await activeWrapper!.find('.tenant-chip__button').trigger('click')
    await nextTick()

    const menu = activeWrapper!.find('.tenant-chip__menu')
    expect(menu.find('.tenant-chip__menu-count').text()).toBe('2 个租户')
    expect(menu.findAll('.tenant-chip__menu-item')).toHaveLength(2)
    expect(menu.find('.tenant-chip__menu-item.is-active').text()).toContain('审计人员')
    expect(menu.find('.tenant-chip__menu-item.is-active').text()).toContain('已启用')
    expect(menu.find('.tenant-chip__menu-item').text()).not.toContain('active')
    expect(menu.find('.tenant-chip__menu-item[aria-checked="true"]').exists()).toBe(true)
  })

  it('切换租户时锁定重复操作并在成功后关闭菜单', async () => {
    const tenancy = seedTenants()
    let resolveSwitch!: (context: EnterpriseContext) => void
    apiRequestMock.mockImplementation((path: string) => {
      if (path === '/api/enterprise/tenant-context/switch') {
        return new Promise<EnterpriseContext>((resolve) => {
          resolveSwitch = resolve
        })
      }
      return Promise.resolve([])
    })
    await mountAt('/dashboard')

    await activeWrapper!.find('.tenant-chip__button').trigger('click')
    await activeWrapper!.find('.tenant-chip__menu-item:nth-child(3)').trigger('click')
    await nextTick()

    expect(activeWrapper!.find('.tenant-chip__button').attributes('aria-busy')).toBe('true')
    expect(activeWrapper!.find('.tenant-chip__menu-item.is-switching').text()).toContain('切换中')
    expect(activeWrapper!.findAll('.tenant-chip__menu-item[disabled]')).toHaveLength(2)

    resolveSwitch({
      active_tenant: { id: 'tenant_lab', name: '研发实验室', role: 'developer', role_label: '开发人员' },
      tenants: [
        { id: 'tenant_default', name: '默认租户', role: 'auditor', status: 'active' },
        { id: 'tenant_lab', name: '研发实验室', role: 'developer', status: 'active' },
      ],
      role: 'developer',
      role_label: '开发人员',
      permissions: ['tenant:switch'],
    })
    await flushPromises()

    expect(tenancy.displayActiveName).toBe('研发实验室')
    expect(activeWrapper!.find('.tenant-chip__menu').exists()).toBe(false)
    expect(activeWrapper!.find('.tenant-chip__button').attributes('aria-busy')).toBe('false')
  })

  it('没有可切换租户时显示明确空态', async () => {
    const tenancy = useTenancyStore()
    tenancy.context = {
      active_tenant: { id: 'tenant_default', name: '默认租户', role: 'auditor', role_label: '审计人员' },
      tenants: [],
      role: 'auditor',
      role_label: '审计人员',
      permissions: [],
    }
    tenancy.activeTenant = tenancy.context.active_tenant
    tenancy.ready = true
    await mountAt('/dashboard')

    await activeWrapper!.find('.tenant-chip__button').trigger('click')
    await nextTick()

    expect(activeWrapper!.find('.tenant-chip__empty').text()).toContain('暂无可切换租户')
  })

  it('待激活租户使用待处理状态点，而不是显示为已启用', async () => {
    const tenancy = useTenancyStore()
    tenancy.context = {
      active_tenant: { id: 'tenant_pending', name: '待接入租户', role: 'auditor', role_label: '审计人员' },
      tenants: [{ id: 'tenant_pending', name: '待接入租户', role: 'auditor', status: 'pending' }],
      role: 'auditor',
      role_label: '审计人员',
      permissions: [],
    }
    tenancy.activeTenant = tenancy.context.active_tenant
    tenancy.ready = true
    await mountAt('/dashboard')

    expect(activeWrapper!.find('.tenant-chip__button-dot').classes()).toContain('is-pending')
  })
})

describe('AppShell 项目切换器', () => {
  it('点击时展开并可通过外部点击收起', async () => {
    await mountAt('/dashboard')
    const button = activeWrapper!.find('.project-switcher__button')

    expect(button.attributes('aria-expanded')).toBe('false')

    await button.trigger('click')
    await nextTick()
    expect(button.attributes('aria-expanded')).toBe('true')
    const menu = activeWrapper!.find('.project-switcher__menu')
    expect(menu.exists()).toBe(true)
    expect(menu.find('.project-switcher__menu-header').text()).toContain('切换项目')
    expect(menu.findAll('[role="menuitemradio"]')).toHaveLength(3)
    expect(menu.find('[role="menuitemradio"][aria-checked="true"]').exists()).toBe(true)

    document.body.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await nextTick()
    expect(button.attributes('aria-expanded')).toBe('false')
    expect(activeWrapper!.find('.project-switcher__menu').exists()).toBe(false)
  })

  it('项目、通知和账户浮层互斥展开', async () => {
    await mountAt('/dashboard')
    const projectButton = activeWrapper!.find('.project-switcher__button')
    const notificationButton = activeWrapper!.find('.notification-wrap .icon-button')
    const accountButton = activeWrapper!.find('.side-nav__footer')

    await projectButton.trigger('click')
    await nextTick()
    expect(activeWrapper!.find('.project-switcher__menu').exists()).toBe(true)

    await notificationButton.trigger('click')
    await nextTick()
    expect(activeWrapper!.find('.project-switcher__menu').exists()).toBe(false)
    const notificationModal = activeWrapper!.find('.notification-modal')
    expect(notificationModal.exists()).toBe(true)
    expect(document.documentElement.classList.contains('notification-open')).toBe(true)
    expect(document.body.classList.contains('notification-open')).toBe(true)
    expect(notificationModal.find('[role="dialog"][aria-modal="true"]').exists()).toBe(true)
    expect(notificationModal.find('.notification-dialog__close').exists()).toBe(true)

    await accountButton.trigger('click')
    await nextTick()
    expect(activeWrapper!.find('.notification-modal').exists()).toBe(false)
    expect(document.documentElement.classList.contains('notification-open')).toBe(false)
    expect(document.body.classList.contains('notification-open')).toBe(false)
    expect(activeWrapper!.find('.side-account-panel').exists()).toBe(true)
    expect(activeWrapper!.find('.side-nav').classes()).toContain('side-nav--account-open')
  })

  it('账户菜单保留语言设置、主题设置和退出登录入口', async () => {
    await mountAt('/dashboard')

    await activeWrapper!.find('.side-nav__footer').trigger('click')
    await nextTick()

    const panel = activeWrapper!.find('.side-account-panel')
    expect(panel.text()).toContain('语言')
    expect(panel.text()).toContain('主题')
    expect(panel.text()).toContain('退出登录')
  })

  it('真实通知将所有未读项置顶分组，并与未读角标数量对应', async () => {
    setDataSourceMode('real')
    const unread = [
      {
        id: 'notif-unread-1',
        task_id: 'TASK-9001',
        type: 'APPROVAL_RESULT',
        title: '任务 #TASK-9001 审批结果：计划已通过',
        body: '计划 v1',
        actor_id: 'user_approver',
        actor_name: '陈晓',
        priority: 'high',
        read_at: null,
        created_at: '2026-09-11T05:30:00Z',
      },
      {
        id: 'notif-unread-2',
        task_id: 'TASK-9002',
        type: 'MENTION',
        title: '任务 #TASK-9002 有新的协作评论',
        body: '请确认测试范围',
        actor_id: 'user_developer',
        actor_name: '张伟',
        priority: 'normal',
        read_at: null,
        created_at: '2026-09-11T05:20:00Z',
      },
    ]
    const read = {
      id: 'notif-read-1',
      task_id: 'TASK-8999',
      type: 'APPROVAL_RESULT',
      title: '任务 #TASK-8999 审批结果：计划已通过',
      body: '计划 v1',
      actor_id: 'user_approver',
      actor_name: '陈晓',
      priority: 'normal',
      read_at: '2026-09-10T05:20:00Z',
      created_at: '2026-09-10T05:10:00Z',
    }
    apiRequestMock.mockImplementation((path: string) => {
      if (path === '/api/projects') return Promise.resolve([])
      if (path === '/api/notifications?unread_only=true') return Promise.resolve(unread)
      if (path === '/api/notifications') return Promise.resolve([read, ...unread])
      if (path === '/api/notifications/unread-count') return Promise.resolve({ count: 2 })
      return Promise.resolve([])
    })

    await mountAt('/dashboard')
    await flushPromises()
    await activeWrapper!.find('.notification-wrap .icon-button').trigger('click')
    await flushPromises()

    const sections = activeWrapper!.findAll('.notification-dialog__section')
    expect(sections).toHaveLength(2)
    expect(sections[0].text()).toContain('未读')
    expect(sections[0].findAll('.notification-dialog__item.is-unread')).toHaveLength(2)
    expect(sections[0].text()).toContain('任务 #TASK-9001 审批结果：计划已通过')
    expect(sections[1].text()).toContain('最近已读')
    expect(sections[1].findAll('.notification-dialog__item.is-unread')).toHaveLength(0)
    expect(activeWrapper!.find('.notification-dialog__heading').text()).toContain('2 条未读，已置顶显示')
  })

  it('真实项目名称和缓存 id 不一致时按后端项目纠正勾选状态', async () => {
    setDataSourceMode('real')
    apiRequestMock.mockImplementation((path: string) => {
      if (path === '/api/projects') {
        return Promise.resolve([
          { id: 'proj_health', name: '健康平台', description: '中年健康中台', created_at: '', updated_at: '' },
          { id: 'proj_payment', name: '支付中台', description: '支付回调', created_at: '', updated_at: '' },
        ])
      }
      if (path === '/api/notifications') return Promise.resolve([])
      if (path === '/api/notifications/unread-count') return Promise.resolve({ count: 0 })
      return Promise.resolve([])
    })
    activePinia = createPinia()
    setActivePinia(activePinia)
    router = createRouter({ history: createMemoryHistory(), routes })
    await router.push('/dashboard')

    const store = useWorkspaceStore()
    store.setActiveProject('proj_payment', '健康平台')
    activeWrapper = mount(AppShell, { global: { plugins: [activePinia, router] } })
    await flushPromises()

    expect(store.activeProjectId).toBe('proj_health')
    expect(activeWrapper.find('.project-switcher__button').text()).toContain('健康平台')

    await activeWrapper.find('.project-switcher__button').trigger('click')
    await nextTick()

    const checked = activeWrapper.find('[role="menuitemradio"][aria-checked="true"]')
    expect(checked.text()).toContain('健康平台')
    expect(activeWrapper.find('.project-switcher__menu').text()).toContain('支付中台')
  })
})

describe('AppShell 工作台摘要同步', () => {
  it('真实模式挂载时会立即刷新项目健康度摘要', async () => {
    setDataSourceMode('real')
    activePinia = createPinia()
    setActivePinia(activePinia)
    router = createRouter({ history: createMemoryHistory(), routes })
    await router.push('/dashboard')

    const store = useWorkspaceStore()
    store.createTaskFromApi(makeAgentTask())
    const summarySpy = vi.spyOn(store, 'refreshDashboardSummary').mockResolvedValue(store.dashboardSummary)

    activeWrapper = mount(AppShell, { global: { plugins: [activePinia, router] } })
    await nextTick()

    expect(summarySpy).toHaveBeenCalledTimes(1)
  })
})
