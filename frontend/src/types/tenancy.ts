// 企业版 E1-01 租户/成员类型。最终授权由后端完成，前端仅展示由 /api/enterprise 返回的字段。

export type EnterpriseRole =
  | 'tenant_owner'
  | 'tenant_admin'
  | 'security_admin'
  | 'project_owner'
  | 'developer'
  | 'reviewer'
  | 'auditor'

export type EnterprisePermission =
  | 'tenant:context'
  | 'tenant:switch'
  | 'member:view'
  | 'member:manage'
  | 'org:view'
  | 'org:manage'
  | 'tenant:create'
  | 'audit:view'
  | 'rag:search'

export const ENTERPRISE_ROLE_LABELS: Record<EnterpriseRole, string> = {
  tenant_owner: '租户所有者',
  tenant_admin: '企业管理员',
  security_admin: '安全管理员',
  project_owner: '项目负责人',
  developer: '开发人员',
  reviewer: '审查人',
  auditor: '审计人员',
}

export interface EnterpriseTenant {
  id: string
  name: string
  role: EnterpriseRole
  status: string
}

export interface ActiveTenant {
  id: string
  name: string
  role: EnterpriseRole
  role_label: string
}

export interface EnterpriseContext {
  active_tenant: ActiveTenant
  tenants: EnterpriseTenant[]
  role: EnterpriseRole
  role_label: string
  permissions: string[]
}

export interface EnterpriseMember {
  id: string
  tenant_id: string
  user_id: string
  user_name: string
  organization_id: string | null
  role: EnterpriseRole
  role_label: string
  status: string
  is_active: boolean
}

export interface EnterpriseOrganization {
  id: string
  tenant_id: string
  name: string
  description: string
}
