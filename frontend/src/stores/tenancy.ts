// 企业版 E1-01 租户上下文 store（options 风格，仿 workspace store）。
// 仅展示服务端解析到的租户上下文；切换租户由后端校验（服务端权威，前端不可伪造）。

import { defineStore } from 'pinia'
import { apiRequest } from '../services/api'
import type {
  ActiveTenant,
  EnterpriseContext,
  EnterpriseMember,
  EnterpriseRole,
  EnterpriseTenant,
} from '../types/tenancy'

const ACTIVE_KEY = 'frontend-active-tenant'

export const useTenancyStore = defineStore('tenancy', {
  state: () => ({
    ready: false,
    context: null as EnterpriseContext | null,
    members: [] as EnterpriseMember[],
    activeTenant: null as ActiveTenant | null,
  }),
  getters: {
    tenants(state): EnterpriseTenant[] {
      return state.context?.tenants ?? []
    },
    role(state): EnterpriseRole | 'unknown' {
      return state.context?.role ?? 'unknown'
    },
    permissions(state): string[] {
      return state.context?.permissions ?? []
    },
    displayActiveName(state): string {
      return state.activeTenant?.name ?? '默认租户'
    },
    roleLabel(state): string {
      return state.activeTenant?.role_label ?? state.context?.role_label ?? ''
    },
    canManageMembers(): boolean {
      // 仅供参考（前端展示开关）；最终授权由后端完成
      return this.permissions.includes('member:manage')
    },
  },
  actions: {
    hasPermission(permission: string) {
      return this.permissions.includes(permission)
    },
    async loadContext(): Promise<void> {
      try {
        const context = await apiRequest<EnterpriseContext>('/api/enterprise/tenant-context')
        this.context = context
        this.activeTenant = context.active_tenant ?? null
        if (this.activeTenant) {
          localStorage.setItem(ACTIVE_KEY, JSON.stringify(this.activeTenant))
        }
        this.ready = true
      } catch {
        this.context = null
        this.ready = false
      }
    },
    async switchTenant(tenantId: string): Promise<boolean> {
      const context = await apiRequest<EnterpriseContext>('/api/enterprise/tenant-context/switch', {
        method: 'POST',
        body: JSON.stringify({ tenant_id: tenantId }),
      })
      this.context = context
      this.activeTenant = context.active_tenant ?? null
      if (this.activeTenant) {
        localStorage.setItem(ACTIVE_KEY, JSON.stringify(this.activeTenant))
      }
      return true
    },
    async loadMembers(): Promise<EnterpriseMember[]> {
      const members = await apiRequest<EnterpriseMember[]>('/api/enterprise/members')
      this.members = members ?? []
      return this.members
    },
    async updateMember(
      membershipId: string,
      updates: { enabled?: boolean; role?: EnterpriseRole },
    ): Promise<EnterpriseMember> {
      const updated = await apiRequest<EnterpriseMember>(`/api/enterprise/members/${membershipId}`, {
        method: 'PATCH',
        body: JSON.stringify(updates),
      })
      const index = this.members.findIndex((member) => member.id === membershipId)
      if (index >= 0) this.members[index] = updated
      return updated
    },
  },
})