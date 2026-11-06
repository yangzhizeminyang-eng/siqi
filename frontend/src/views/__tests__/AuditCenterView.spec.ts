import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import AuditCenterView from '../AuditCenterView.vue'
import { useTenancyStore } from '../../stores/tenancy'
import type { EnterpriseContext } from '../../types/tenancy'

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
  useAuthStore: () => ({
    hasPermission: hasPermissionMock,
  }),
}))

const auditEvents = [
  {
    id: 'audit-1',
    event_type: 'PLAN_APPROVED',
    resource_type: 'task',
    resource_id: 'TASK-9001',
    actor_id: 'user_approver',
    actor_name: '陈晓',
    summary: '修改计划 v1 已通过审批',
    diff_summary: ['风险等级：高风险', '审批人：陈晓', '计划版本：v1'],
    created_at: '2026-09-15T08:30:00Z',
    execution_step_ids: ['step-1'],
  },
  {
    id: 'audit-2',
    event_type: 'REPOSITORY_UPDATED',
    resource_type: 'repository',
    resource_id: 'repo-payment',
    actor_id: 'user_admin',
    actor_name: '小泽',
    summary: '仓库配置已更新',
    diff_summary: ['测试命令：pytest tests/ -q'],
    created_at: '2026-09-14T16:20:00Z',
    execution_step_ids: [],
  },
]

function makeEnterpriseContext(permissions: string[] = []): EnterpriseContext {
  return {
    active_tenant: { id: 'tenant_default', name: '默认租户', role: 'auditor', role_label: '审计人员' },
    tenants: [{ id: 'tenant_default', name: '默认租户', role: 'auditor', status: 'active' }],
    role: 'auditor',
    role_label: '审计人员',
    permissions,
  }
}

async function mountAuditCenter(enterprisePermissions: string[] = []) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const tenancy = useTenancyStore()
  tenancy.context = makeEnterpriseContext(enterprisePermissions)
  tenancy.activeTenant = tenancy.context.active_tenant
  tenancy.ready = true
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: AuditCenterView },
      { path: '/tasks/:id', component: { template: '<div />' } },
    ],
  })
  await router.push('/')
  const wrapper = mount(AuditCenterView, {
    global: { plugins: [pinia, router] },
  })
  await flushPromises()
  return { wrapper, router }
}

describe('AuditCenterView 企业审计中心', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    apiRequestMock.mockReset()
    hasPermissionMock.mockReset()
    hasPermissionMock.mockReturnValue(true)
    apiRequestMock.mockResolvedValue(auditEvents)
  })

  it('加载真实审计事件并显示企业治理摘要', async () => {
    const { wrapper } = await mountAuditCenter()

    expect(apiRequestMock).toHaveBeenCalledWith('/api/audit-events')
    expect(wrapper.find('.audit-center__summary').exists()).toBe(true)
    expect(wrapper.findAll('.audit-metric strong')[0].text()).toBe('2')
    expect(wrapper.text()).toContain('条审计事件')
    expect(wrapper.text()).toContain('修改计划 v1 已通过审批')
    expect(wrapper.text()).toContain('仓库配置已更新')
  })

  it('企业 audit:view 可访问审计中心，不依赖团队版 project:manage', async () => {
    hasPermissionMock.mockReturnValue(false)
    const { wrapper } = await mountAuditCenter(['audit:view'])

    expect(apiRequestMock).toHaveBeenCalledWith('/api/audit-events')
    expect(wrapper.find('.page-notice--warning').exists()).toBe(false)
    expect(wrapper.text()).toContain('修改计划 v1 已通过审批')
  })

  it('没有团队或企业审计权限时不加载审计事件', async () => {
    hasPermissionMock.mockReturnValue(false)
    const { wrapper } = await mountAuditCenter([])

    expect(apiRequestMock).not.toHaveBeenCalled()
    expect(wrapper.find('.page-notice--warning').exists()).toBe(true)
    expect(wrapper.text()).toContain('当前账号没有审计中心权限')
  })

  it('按关键词和资源类型筛选事件', async () => {
    const { wrapper } = await mountAuditCenter()

    await wrapper.find('input[placeholder*="事件"]').setValue('仓库')
    expect(wrapper.findAll('.audit-event-row')).toHaveLength(1)
    expect(wrapper.text()).toContain('仓库配置已更新')
    expect(wrapper.text()).not.toContain('修改计划 v1 已通过审批')

    await wrapper.find('input[placeholder*="事件"]').setValue('')
    await wrapper.findAll('.audit-center__filter-option')[1].trigger('click')
    expect(wrapper.findAll('.audit-event-row')).toHaveLength(1)
    expect(wrapper.find('.audit-event-row').text()).toContain('TASK-9001')
  })

  it('打开事件详情并从任务事件跳转到任务详情', async () => {
    const { wrapper, router } = await mountAuditCenter()

    await wrapper.find('.audit-event-row').trigger('click')
    expect(wrapper.find('.audit-event-detail').exists()).toBe(true)
    expect(wrapper.text()).toContain('step-1')

    await wrapper.find('.audit-event-detail__task-link').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.fullPath).toBe('/tasks/TASK-9001')
  })

  it('接口失败时显示可恢复的错误状态', async () => {
    apiRequestMock.mockRejectedValue(new Error('audit unavailable'))
    const { wrapper } = await mountAuditCenter()

    expect(wrapper.find('.audit-center__error').exists()).toBe(true)
    expect(wrapper.text()).toContain('审计事件加载失败')
    expect(wrapper.find('button').text()).toContain('重新加载')
  })
})
