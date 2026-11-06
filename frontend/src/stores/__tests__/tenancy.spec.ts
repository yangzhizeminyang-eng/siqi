import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useTenancyStore } from '../tenancy'
import type { EnterpriseContext, EnterpriseMember } from '../../types/tenancy'

const { apiRequestMock } = vi.hoisted(() => ({ apiRequestMock: vi.fn() }))

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

function makeContext(overrides: Partial<EnterpriseContext> = {}): EnterpriseContext {
  return {
    active_tenant: { id: 'tenant_default', name: '默认租户', role: 'tenant_owner', role_label: '租户所有者' },
    tenants: [{ id: 'tenant_default', name: '默认租户', role: 'tenant_owner', status: 'active' }],
    role: 'tenant_owner',
    role_label: '租户所有者',
    permissions: ['tenant:context', 'tenant:switch', 'member:view', 'member:manage'],
    ...overrides,
  }
}

function makeMember(overrides: Partial<EnterpriseMember> = {}): EnterpriseMember {
  return {
    id: 'mem_user_developer_default',
    tenant_id: 'tenant_default',
    user_id: 'user_developer',
    user_name: '周航',
    organization_id: 'org_default',
    role: 'developer',
    role_label: '开发人员',
    status: 'active',
    is_active: true,
    ...overrides,
  }
}

describe('tenancy store：企业版 E1-01 租户上下文与成员管理', () => {
  beforeEach(() => {
    localStorage.clear()
    setActivePinia(createPinia())
    apiRequestMock.mockReset()
  })

  afterEach(() => {
    localStorage.clear()
  })

  it('loadContext 拉取租户上下文并写入活跃租户', async () => {
    apiRequestMock.mockResolvedValueOnce(makeContext())
    const store = useTenancyStore()
    await store.loadContext()

    expect(apiRequestMock).toHaveBeenCalledWith('/api/enterprise/tenant-context')
    expect(store.ready).toBe(true)
    expect(store.activeTenant?.id).toBe('tenant_default')
    expect(store.displayActiveName).toBe('默认租户')
    expect(store.canManageMembers).toBe(true)
  })

  it('切租户通过服务端接口，成功后更新活跃租户与可切换列表', async () => {
    apiRequestMock.mockResolvedValueOnce(
      makeContext({
        active_tenant: { id: 'tenant_b', name: 'TenantB', role: 'developer', role_label: '开发人员' },
        role: 'developer',
      }),
    )
    const store = useTenancyStore()
    await store.switchTenant('tenant_b')

    expect(apiRequestMock).toHaveBeenCalledWith('/api/enterprise/tenant-context/switch', {
      method: 'POST',
      body: JSON.stringify({ tenant_id: 'tenant_b' }),
    })
    expect(store.activeTenant?.id).toBe('tenant_b')
    expect(localStorage.getItem('frontend-active-tenant')).toContain('tenant_b')
  })

  it('loadMembers 拉取当前租户成员；updateMember 就地更新列表并可降级权限', async () => {
    apiRequestMock.mockResolvedValueOnce([makeMember()])
    const store = useTenancyStore()
    await store.loadMembers()
    expect(store.members).toHaveLength(1)

    apiRequestMock.mockResolvedValueOnce(makeMember({ status: 'suspended', is_active: false }))
    await store.updateMember('mem_user_developer_default', { enabled: false })
    expect(store.members[0].status).toBe('suspended')

    apiRequestMock.mockResolvedValueOnce(makeMember({ role: 'reviewer', role_label: '审查人' }))
    await store.updateMember('mem_user_developer_default', { role: 'reviewer' })
    expect(store.members[0].role).toBe('reviewer')
  })

  it('loadContext 失败时保持未就绪且不写本地快照', async () => {
    apiRequestMock.mockRejectedValueOnce(new Error('network'))
    const store = useTenancyStore()
    await store.loadContext()
    expect(store.ready).toBe(false)
    expect(store.tenants).toEqual([])
    expect(localStorage.getItem('frontend-active-tenant')).toBeNull()
  })
})