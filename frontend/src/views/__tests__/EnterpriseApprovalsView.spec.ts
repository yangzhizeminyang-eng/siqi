import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import EnterpriseApprovalsView from '../EnterpriseApprovalsView.vue'
import { useAuthStore } from '../../stores/auth'
import { useTenancyStore } from '../../stores/tenancy'
import type { ApprovalApplication } from '../../types/approvals'
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

vi.mock('element-plus', () => ({
  ElMessage: {
    error: vi.fn(),
    success: vi.fn(),
    warning: vi.fn(),
  },
  ElMessageBox: {
    confirm: vi.fn(),
  },
}))

const approvals: ApprovalApplication[] = [
  {
    id: 'appr-1',
    tenant_id: 'tenant_default',
    actor_id: 'user_1',
    actor_name: '陈晓',
    resource_type: 'repository',
    resource_id: 'repo_payment_service',
    operation: 'repository.index.rebuild',
    title: '重建支付服务检索索引',
    request_summary: {},
    risk_level: 'L2',
    decision: 'approval_required',
    approval_status: 'pending',
    reason: '索引重建需要审批',
    idempotency_key: 'key-1',
    created_at: '2026-09-16T08:30:00Z',
    updated_at: '2026-09-16T08:30:00Z',
    expires_at: null,
    approved_by_id: null,
    approved_by_name: null,
    approved_at: null,
    executed_at: null,
    executed_by_id: null,
    executor_result: {},
    approval_mode: 'single',
    secondary_confirmation: false,
    version: 1,
  },
  {
    id: 'appr-2',
    tenant_id: 'tenant_default',
    actor_id: 'user_2',
    actor_name: '王琳',
    resource_type: 'team_request',
    resource_id: 'REQ-1002',
    operation: 'team_request.sync_gitee',
    title: '同步高优先级平台需求',
    request_summary: {},
    risk_level: 'L3',
    decision: 'approval_required',
    approval_status: 'approved',
    reason: '外部同步前需确认',
    idempotency_key: 'key-2',
    created_at: '2026-09-15T16:20:00Z',
    updated_at: '2026-09-15T16:30:00Z',
    expires_at: '2026-09-18T16:20:00Z',
    approved_by_id: 'user_admin',
    approved_by_name: '小泽',
    approved_at: '2026-09-15T16:30:00Z',
    executed_at: null,
    executed_by_id: null,
    executor_result: {},
    approval_mode: 'dual',
    secondary_confirmation: false,
    version: 2,
  },
]

function enterpriseContext(): EnterpriseContext {
  return {
    active_tenant: {
      id: 'tenant_default',
      name: '默认租户',
      role: 'tenant_admin',
      role_label: '企业管理员',
    },
    tenants: [{
      id: 'tenant_default',
      name: '默认租户',
      role: 'tenant_admin',
      status: 'active',
    }],
    role: 'tenant_admin',
    role_label: '企业管理员',
    permissions: ['approval:view', 'approval:approve', 'approval:manage'],
  }
}

async function mountApprovals(items = approvals) {
  const pinia = createPinia()
  setActivePinia(pinia)

  const tenancy = useTenancyStore()
  tenancy.context = enterpriseContext()
  tenancy.activeTenant = tenancy.context.active_tenant
  tenancy.ready = true

  const auth = useAuthStore()
  auth.currentUser = {
    id: 'user_admin',
    name: '小泽',
    email: 'admin@example.com',
    role: 'admin',
    role_label: '管理员',
    permissions: ['approval:view', 'approval:manage'],
  }
  auth.ready = true

  apiRequestMock.mockImplementation((url: string) => {
    if (url.startsWith('/api/enterprise/approvals?') || url === '/api/enterprise/approvals') {
      return Promise.resolve(items)
    }
    const id = url.split('/').at(-1)
    return Promise.resolve(items.find((item) => item.id === id) ?? items[0])
  })

  const wrapper = mount(EnterpriseApprovalsView, {
    global: {
      plugins: [pinia],
    },
  })
  await flushPromises()
  return wrapper
}

describe('EnterpriseApprovalsView 企业审批操作台', () => {
  beforeEach(() => {
    apiRequestMock.mockReset()
  })

  it('以队列概览和可扫描表格展示审批申请', async () => {
    const wrapper = await mountApprovals()

    expect(wrapper.find('.approval-governance__summary').exists()).toBe(true)
    expect(wrapper.text()).toContain('待审批')
    expect(wrapper.text()).toContain('高风险')
    expect(wrapper.findAll('.approval-governance__row')).toHaveLength(2)
    expect(wrapper.text()).toContain('重建支付服务检索索引')
    expect(wrapper.text()).toContain('同步高优先级平台需求')
  })

  it('使用可访问的自定义筛选器并在选择后重新加载队列', async () => {
    const wrapper = await mountApprovals()

    const statusFilter = wrapper.find('button[aria-label="审批状态"]')
    expect(statusFilter.exists()).toBe(true)
    await statusFilter.trigger('click')
    await wrapper.find('[role="option"]').trigger('click')
    await flushPromises()

    expect(apiRequestMock).toHaveBeenLastCalledWith('/api/enterprise/approvals?scope=all')
  })

  it('点击队列记录打开审批详情侧栏', async () => {
    const wrapper = await mountApprovals()

    await wrapper.find('[data-approval-id="appr-1"]').trigger('click')
    await flushPromises()

    expect(wrapper.find('.approval-governance__drawer').exists()).toBe(true)
    expect(wrapper.text()).toContain('审批详情')
    expect(wrapper.text()).toContain('索引重建需要审批')
  })

  it('筛选结果为空时显示明确的队列空态', async () => {
    const wrapper = await mountApprovals([])

    expect(wrapper.find('.approval-governance__empty').exists()).toBe(true)
    expect(wrapper.text()).toContain('当前筛选条件下没有审批项')
  })
})
