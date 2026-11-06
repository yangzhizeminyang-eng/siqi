// 企业版 E2-01 审批申请类型。最终授权由后端完成，前端仅展示服务端返回。

export type ApprovalStatus =
  | 'pending' | 'approved' | 'executing' | 'executed'
  | 'rejected' | 'cancelled' | 'expired'
export type ApprovalDecision = 'allow' | 'deny' | 'approval_required'
export type ApprovalMode = 'single' | 'dual' | 'security'

export interface ApprovalApplication {
  id: string
  tenant_id: string
  actor_id: string
  actor_name: string
  resource_type: string
  resource_id: string
  operation: string
  title: string
  request_summary: Record<string, unknown>
  risk_level: string
  decision: ApprovalDecision
  approval_status: ApprovalStatus
  reason: string
  idempotency_key: string
  created_at: string
  updated_at: string
  expires_at: string | null
  approved_by_id: string | null
  approved_by_name: string | null
  approved_at: string | null
  executed_at: string | null
  executed_by_id: string | null
  executor_result: Record<string, unknown>
  approval_mode: ApprovalMode
  secondary_confirmation: boolean
  version: number
  execution_attempt_id?: string | null
  async_result_id?: string | null
  retry_count?: number
  started_at?: string | null
  finished_at?: string | null
  execution_error?: string | null
}

export const APPROVAL_STATUS_LABELS: Record<ApprovalStatus, string> = {
  pending: '待审批',
  approved: '已通过',
  executing: '执行中',
  executed: '已执行',
  rejected: '已拒绝',
  cancelled: '已取消',
  expired: '已过期',
}