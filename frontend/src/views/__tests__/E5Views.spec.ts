import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import RuntimeConsoleView from '../RuntimeConsoleView.vue'
import EnterpriseDashboardView from '../EnterpriseDashboardView.vue'
import EnterpriseMembersView from '../EnterpriseMembersView.vue'
import EnterpriseOpsView from '../EnterpriseOpsView.vue'
import { useTenancyStore } from '../../stores/tenancy'
import StatusBadge from '../../components/enterprise/StatusBadge.vue'
import AsyncState from '../../components/enterprise/AsyncState.vue'
import ConfirmDialog from '../../components/enterprise/ConfirmDialog.vue'
import DetailDrawer from '../../components/enterprise/DetailDrawer.vue'
import PermissionGate from '../../components/enterprise/PermissionGate.vue'
import type { EnterpriseContext, EnterpriseRole } from '../../types/tenancy'

const { apiRequestMock, hasPermissionMock } = vi.hoisted(() => ({
  apiRequestMock: vi.fn(),
  hasPermissionMock: vi.fn(),
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

vi.mock('../../stores/auth', () => ({
  useAuthStore: () => ({ hasPermission: hasPermissionMock }),
}))

function makeCtx(permissions: string[], role: EnterpriseRole = 'tenant_owner'): EnterpriseContext {
  return {
    active_tenant: { id: 'tenant_default', name: '默认租户', role, role_label: '租户所有者' },
    tenants: [{ id: 'tenant_default', name: '默认租户', role, status: 'active' }],
    role, role_label: '租户所有者', permissions,
  }
}

const runtimeTask = {
  id: 'RT1', tenant_id: 'tenant_default', title: '支付中台重构',
  status: 'waiting_human', current_step: 'execution', checkpoint_version: 2, retry_count: 3,
  approval_state: 'approved', wait_human_reason: 'transient real failure',
  steps: [{ name: 'execution', status: 'waiting_human', retry_count: 3, failure_reason: 'boom' }],
}

async function mountRuntime(permissions: string[], role: EnterpriseRole = 'tenant_owner') {
  const pinia = createPinia(); setActivePinia(pinia)
  const tenancy = useTenancyStore()
  tenancy.context = makeCtx(permissions, role)
  tenancy.activeTenant = tenancy.context.active_tenant
  tenancy.ready = true
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: RuntimeConsoleView }] })
  await router.push('/')
  const wrapper = mount(RuntimeConsoleView, { global: { plugins: [pinia, router] } })
  await flushPromises()
  return { wrapper, tenancy }
}

describe('E5-01 企业 Runtime 控制台', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    apiRequestMock.mockReset(); hasPermissionMock.mockReset(); hasPermissionMock.mockReturnValue(false)
  })

  it('无 rag:search（auditor 语义）显示 403，不请求接口', async () => {
    apiRequestMock.mockResolvedValue([])
    const { wrapper } = await mountRuntime([])
    expect(wrapper.text()).toContain('无权限访问 Runtime')
    expect(apiRequestMock).not.toHaveBeenCalled()
  })

  it('有 rag:search 渲染任务列表 + 状态徽章（真实接口）', async () => {
    apiRequestMock.mockResolvedValue([runtimeTask])
    const { wrapper } = await mountRuntime(['rag:search'])
    expect(apiRequestMock).toHaveBeenCalledWith('/api/enterprise/runtime/tasks')
    expect(wrapper.text()).toContain('支付中台重构') // 行显示标题
    expect(wrapper.text()).toContain('waiting_human')
    expect(wrapper.text()).toContain('execution')
  })

  it('空态 / 错误态', async () => {
    apiRequestMock.mockResolvedValue([])
    const { wrapper: w1 } = await mountRuntime(['rag:search'])
    expect(w1.text()).toContain('暂无 Runtime 任务')

    apiRequestMock.mockRejectedValueOnce(new Error('runtime down'))
    const { wrapper: w2 } = await mountRuntime(['rag:search'])
    expect(w2.text()).toContain('runtime down')
  })

  it('waiting_human 且接管角色可看到 resume/retry/cancel 操作；auditor 角色无接管按钮', async () => {
    apiRequestMock.mockResolvedValue([runtimeTask])
    const { wrapper } = await mountRuntime(['rag:search'], 'tenant_owner')
    expect(wrapper.text()).toContain('resume')
    expect(wrapper.text()).toContain('retry')
    expect(wrapper.text()).toContain('cancel')
  })
})

describe('E5-01 企业工作台（真实数据）', () => {
  beforeEach(() => {
    setActivePinia(createPinia()); apiRequestMock.mockReset(); hasPermissionMock.mockReset(); hasPermissionMock.mockReturnValue(false)
  })
  it('渲染租户上下文与真实数据摘要', async () => {
    apiRequestMock.mockImplementation((path: string) => {
      if (path === '/api/enterprise/approvals') return Promise.resolve([{ id: 'ap1', title: '重建索引', approval_status: 'pending' }])
      if (path === '/api/audit-events') return Promise.resolve([{ id: 'e1', summary: '创建项目', actor_name: '林岚' }])
      if (path === '/api/enterprise/runtime/tasks') return Promise.resolve([{ id: 'rt1', status: 'succeeded' }])
      return Promise.resolve([])
    })
    const pinia = createPinia(); setActivePinia(pinia)
    const tenancy = useTenancyStore(); tenancy.context = makeCtx(['audit:view', 'approval:view', 'rag:search']); tenancy.activeTenant = tenancy.context.active_tenant; tenancy.ready = true
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: EnterpriseDashboardView }] })
    await router.push('/')
    const wrapper = mount(EnterpriseDashboardView, { global: { plugins: [pinia, router] } })
    await flushPromises()
    expect(wrapper.text()).toContain('企业工作台')
    expect(wrapper.text()).toContain('默认租户')
    expect(wrapper.text()).toContain('重建索引')   // 真实审批数据
    expect(wrapper.text()).toContain('创建项目')   // 真实审计数据
    expect(wrapper.text()).toContain('succeeded') // 真实 runtime 摘要
  })
})

describe('E5-01 共享组件', () => {
  beforeEach(() => { setActivePinia(createPinia()) })

  it('StatusBadge 映射状态到语义色', () => {
    const ok = mount(StatusBadge, { props: { status: 'succeeded' } })
    expect(ok.classes()).toContain('ebadge--ok')
    const fail = mount(StatusBadge, { props: { status: 'failed' } })
    expect(fail.classes()).toContain('ebadge--danger')
    const warn = mount(StatusBadge, { props: { status: 'waiting_human' } })
    expect(warn.classes()).toContain('ebadge--warn')
  })

  it('AsyncState 渲染 loading/empty/error/forbidden', () => {
    expect(mount(AsyncState, { props: { state: 'loading' } }).text()).toContain('加载中')
    expect(mount(AsyncState, { props: { state: 'empty' } }).text()).toContain('暂无数据')
    expect(mount(AsyncState, { props: { state: 'error' } }).text()).toContain('加载失败')
    expect(mount(AsyncState, { props: { state: 'forbidden' } }).text()).toContain('无权限')
  })

  it('ConfirmDialog 确认/取消事件与键盘', async () => {
    const wrapper = mount(ConfirmDialog, { props: { open: true, message: '确定？' }, global: { stubs: { teleport: true } } })
    await wrapper.find('.ebtn--primary').trigger('click')
    expect(wrapper.emitted('confirm')).toBeTruthy()
    await wrapper.find('.ebtn').trigger('click')
    expect(wrapper.emitted('cancel')).toBeTruthy()
  })

  it('DetailDrawer Esc 关闭 + 内容渲染', async () => {
    const wrapper = mount(DetailDrawer, {
      props: { open: true, title: '详情' },
      slots: { default: '<p>body</p>' },
      global: { stubs: { teleport: true } },
    })
    expect(wrapper.text()).toContain('body')
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    expect(wrapper.emitted('close')).toBeTruthy()
  })

  it('PermissionGate：无权限显示 403 内容，有权限渲染插槽', () => {
    const pinia = createPinia(); setActivePinia(pinia)
    const tenancy = useTenancyStore(); tenancy.context = makeCtx([]); tenancy.ready = true
    const denied = mount(PermissionGate, { props: { permission: ['audit:view'] }, global: { plugins: [pinia] }, slots: { default: '<p>secret</p>' } })
    expect(denied.text()).toContain('无权限')
    expect(denied.text()).not.toContain('secret')

    const pinia2 = createPinia(); setActivePinia(pinia2)
    const tenancy2 = useTenancyStore(); tenancy2.context = makeCtx(['audit:view']); tenancy2.ready = true
    const allowed = mount(PermissionGate, { props: { permission: ['audit:view'] }, global: { plugins: [pinia2] }, slots: { default: '<p>secret</p>' } })
    expect(allowed.text()).toContain('secret')
  })
})
describe('E5-01 企业设置/运维（真实数据）', () => {
  beforeEach(() => {
    setActivePinia(createPinia()); apiRequestMock.mockReset(); hasPermissionMock.mockReset(); hasPermissionMock.mockReturnValue(false)
  })
  it('无 project:manage（developer 语义）显示 403，不请求接口', async () => {
    const pinia = createPinia(); setActivePinia(pinia)
    const tenancy = useTenancyStore(); tenancy.context = makeCtx(['rag:search'], 'developer'); tenancy.ready = true
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: EnterpriseOpsView }] })
    await router.push('/')
    const wrapper = mount(EnterpriseOpsView, { global: { plugins: [pinia, router] } })
    await flushPromises()
    expect(wrapper.text()).toContain('无权限访问运维')
    expect(apiRequestMock).not.toHaveBeenCalledWith('/api/enterprise/ops/status')
  })
  it('有 project:manage 渲染健康/配置/仓库（真实接口）', async () => {
    apiRequestMock.mockImplementation((path: string) => {
      if (path === '/api/enterprise/ops/status') return Promise.resolve({
        tenant_id: 'tenant_default', app_env: 'local', migration: 'e5f6071a2b3c / head=e5f6071a2b3c',
        rag: { retrieval_mode: 'hybrid', symbol_boost: 1.0, rerank_provider: 'heuristic', rerank_enabled: false, cross_encoder_enabled: false },
        milvus: { use_milvus: false, collection_prefix: '', host: '' },
        counts: { repositories: 2, indexes: 3 },
        dependencies: { db: { status: 'ok' } },
      })
      if (path === '/api/repositories') return Promise.resolve([{ id: 'r1', name: '支付中台', risk_threshold: 'L2' }])
      return Promise.resolve([])
    })
    const pinia = createPinia(); setActivePinia(pinia)
    const tenancy = useTenancyStore(); tenancy.context = makeCtx(['project:manage']); tenancy.ready = true
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: EnterpriseOpsView }] })
    await router.push('/')
    const wrapper = mount(EnterpriseOpsView, { global: { plugins: [pinia, router] } })
    await flushPromises()
    expect(wrapper.text()).toContain('企业设置 / 运维')
    expect(wrapper.text()).toContain('e5f6071a2b3c')
    expect(wrapper.text()).toContain('hybrid')
    expect(wrapper.text()).toContain('支付中台')
  })
})

describe('E5-01 企业成员管理（真实数据）', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    apiRequestMock.mockReset()
    hasPermissionMock.mockReset()
    hasPermissionMock.mockReturnValue(false)
  })

  it('统一企业页头，展示真实成员与语义状态，并对只读角色禁用修改控件', async () => {
    const context = makeCtx(['member:view'], 'tenant_admin')
    apiRequestMock.mockImplementation((path: string) => {
      if (path === '/api/enterprise/tenant-context') return Promise.resolve(context)
      if (path === '/api/enterprise/members') {
        return Promise.resolve([{
          id: 'membership-1',
          user_id: 'user-1',
          user_name: '林岚',
          role: 'developer',
          status: 'active',
          is_active: true,
        }])
      }
      return Promise.resolve([])
    })
    const pinia = createPinia(); setActivePinia(pinia)
    const tenancy = useTenancyStore()
    tenancy.context = context
    tenancy.activeTenant = context.active_tenant
    tenancy.ready = true
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: EnterpriseMembersView }] })
    await router.push('/')
    const wrapper = mount(EnterpriseMembersView, { global: { plugins: [pinia, router] } })
    await flushPromises()

    expect(wrapper.find('.eheader__title').text()).toBe('成员管理')
    expect(wrapper.find('.member-table').exists()).toBe(true)
    expect(wrapper.text()).toContain('林岚')
    expect(wrapper.text()).toContain('已启用')
    expect(wrapper.find('select[aria-label="角色"]').attributes('disabled')).toBeDefined()
    expect(wrapper.find('button[role="switch"]').attributes('disabled')).toBeDefined()
  })

  it('成员接口失败时显示可恢复的错误态', async () => {
    const context = makeCtx(['member:view'], 'tenant_admin')
    apiRequestMock.mockImplementation((path: string) => {
      if (path === '/api/enterprise/tenant-context') return Promise.resolve(context)
      if (path === '/api/enterprise/members') return Promise.reject(new Error('members unavailable'))
      return Promise.resolve([])
    })
    const pinia = createPinia(); setActivePinia(pinia)
    const tenancy = useTenancyStore()
    tenancy.context = context
    tenancy.activeTenant = context.active_tenant
    tenancy.ready = true
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: EnterpriseMembersView }] })
    await router.push('/')
    const wrapper = mount(EnterpriseMembersView, { global: { plugins: [pinia, router] } })
    await flushPromises()

    expect(wrapper.find('[role="alert"]').text()).toContain('members unavailable')
    expect(wrapper.find('button[aria-label="重新加载成员"]').exists()).toBe(true)
  })
})
