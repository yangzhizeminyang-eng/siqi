// 企业版 E2-01 审批中心 store。仅展示与操作；最终授权由后端完成。

import { defineStore } from 'pinia'
import { apiRequest } from '../services/api'
import type {
  ApprovalApplication,
  ApprovalDecision,
  ApprovalStatus,
} from '../types/approvals'

export const useApprovalsStore = defineStore('approvals', {
  state: () => ({
    ready: false,
    loading: false,
    error: '',
    items: [] as ApprovalApplication[],
    detail: null as ApprovalApplication | null,
  }),
  getters: {
    pending(): ApprovalApplication[] {
      return this.items.filter((a) => a.approval_status === 'pending')
    },
  },
  actions: {
    async load(params?: { status?: ApprovalStatus; risk?: string; scope?: string }): Promise<ApprovalApplication[]> {
      this.loading = true
      this.error = ''
      try {
        const query = new URLSearchParams()
        if (params?.status) query.set('status', params.status)
        if (params?.risk) query.set('risk', params.risk)
        if (params?.scope) query.set('scope', params.scope)
        const suffix = query.toString() ? `?${query.toString()}` : ''
        const items = await apiRequest<ApprovalApplication[]>(`/api/enterprise/approvals${suffix}`)
        this.items = items ?? []
        this.ready = true
        return this.items
      } catch (err) {
        this.error = err instanceof Error ? err.message : '加载审批失败'
        throw err
      } finally {
        this.loading = false
      }
    },
    async loadDetail(id: string): Promise<ApprovalApplication> {
      const detail = await apiRequest<ApprovalApplication>(`/api/enterprise/approvals/${id}`)
      this.detail = detail
      return detail
    },
    async submit(payload: {
      operation: string
      resource_type: string
      resource_id: string
      title: string
      request_summary?: Record<string, unknown>
      idempotency_key: string
    }): Promise<ApprovalApplication> {
      const record = await apiRequest<ApprovalApplication>('/api/enterprise/approvals', {
        method: 'POST',
        body: JSON.stringify(payload),
      })
      return record
    },
    async approve(id: string, comment?: string): Promise<ApprovalApplication> {
      return this._action(id, 'approve', { comment })
    },
    async reject(id: string, reason: string): Promise<ApprovalApplication> {
      return this._action(id, 'reject', { reason })
    },
    async cancel(id: string, reason: string): Promise<ApprovalApplication> {
      return this._action(id, 'cancel', { reason })
    },
    async execute(id: string, confirmation = true): Promise<ApprovalApplication> {
      return this._action(id, 'execute', { secondary_confirmation: confirmation })
    },
    async _action(id: string, action: string, body: Record<string, unknown>): Promise<ApprovalApplication> {
      const record = await apiRequest<ApprovalApplication>(`/api/enterprise/approvals/${id}/${action}`, {
        method: 'POST',
        body: JSON.stringify(body),
      })
      const index = this.items.findIndex((a) => a.id === record.id)
      if (index >= 0) this.items[index] = record
      if (this.detail?.id === record.id) this.detail = record
      return record
    },
  },
})

export type { ApprovalDecision, ApprovalStatus }