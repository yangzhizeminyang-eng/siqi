import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useApprovalsStore } from '../approvals'
import type { ApprovalApplication } from '../../types/approvals'

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

function makeApproval(overrides: Partial<ApprovalApplication> = {}): ApprovalApplication {
  return {
    id: 'appr_1',
    tenant_id: 'tenant_default',
    actor_id: 'user_developer',
    actor_name: '周航',
    resource_type: 'repository',
    resource_id: 'repo_payment_service',
    operation: 'repository.index.rebuild',
    title: 'rebuild index',
    request_summary: {},
    risk_level: 'L2',
    decision: 'approval_required',
    approval_status: 'pending',
    reason: '',
    idempotency_key: 'k1',
    created_at: '2026-09-15T09:00:00Z',
    updated_at: '2026-09-15T09:00:00Z',
    expires_at: '2026-09-16T09:00:00Z',
    approved_by_id: null,
    approved_by_name: null,
    approved_at: null,
    executed_at: null,
    executed_by_id: null,
    executor_result: {},
    approval_mode: 'single',
    secondary_confirmation: false,
    version: 1,
    ...overrides,
  }
}

describe('approvals store：企业版 E2-01 审批中心', () => {
  beforeEach(() => {
    localStorage.clear()
    setActivePinia(createPinia())
    apiRequestMock.mockReset()
  })

  afterEach(() => {
    localStorage.clear()
  })

  it('load 拉取列表并写入 store、过滤参数透传', async () => {
    apiRequestMock.mockResolvedValueOnce([makeApproval()])
    const store = useApprovalsStore()
    await store.load({ status: 'pending', risk: 'L2', scope: 'all' })

    expect(apiRequestMock).toHaveBeenCalledWith('/api/enterprise/approvals?status=pending&risk=L2&scope=all')
    expect(store.items).toHaveLength(1)
    expect(store.pending).toHaveLength(1)
  })

  it('approve 调用后端并就地更新列表与详情', async () => {
    apiRequestMock.mockResolvedValueOnce([makeApproval()])
    const store = useApprovalsStore()
    await store.load()

    apiRequestMock.mockResolvedValueOnce(makeApproval({ approval_status: 'approved' }))
    await store.approve('appr_1', 'ok')
    expect(apiRequestMock).toHaveBeenCalledWith('/api/enterprise/approvals/appr_1/approve', {
      method: 'POST',
      body: JSON.stringify({ comment: 'ok' }),
    })
    expect(store.items[0].approval_status).toBe('approved')
  })

  it('reject 需带原因；execute 默认二次确认', async () => {
    apiRequestMock.mockResolvedValueOnce(makeApproval({ approval_status: 'rejected' }))
    const store = useApprovalsStore()
    await store.reject('appr_1', 'no')
    expect(apiRequestMock).toHaveBeenCalledWith('/api/enterprise/approvals/appr_1/reject', {
      method: 'POST',
      body: JSON.stringify({ reason: 'no' }),
    })

    apiRequestMock.mockResolvedValueOnce(makeApproval({ approval_status: 'executed' }))
    await store.execute('appr_1', true)
    expect(apiRequestMock).toHaveBeenCalledWith('/api/enterprise/approvals/appr_1/execute', {
      method: 'POST',
      body: JSON.stringify({ secondary_confirmation: true }),
    })
  })

  it('load 失败写入 error 并抛出', async () => {
    apiRequestMock.mockRejectedValueOnce(new Error('network'))
    const store = useApprovalsStore()
    await expect(store.load()).rejects.toThrow()
    expect(store.error).toBeTruthy()
  })
})