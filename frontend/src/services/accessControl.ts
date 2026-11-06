import type { Permission } from '../types/auth'
import type { EnterprisePermission } from '../types/tenancy'

export type AccessPermission = Permission | EnterprisePermission
export type AccessRequirement = AccessPermission | AccessPermission[]

type TeamPermissionSource = {
  hasPermission(permission: Permission): boolean
}

type TenantPermissionSource = {
  hasPermission(permission: string): boolean
}

export function hasAccessRequirement(
  auth: TeamPermissionSource,
  tenancy: TenantPermissionSource,
  requirement?: AccessRequirement,
): boolean {
  if (!requirement) return true
  const permissions = Array.isArray(requirement) ? requirement : [requirement]
  return permissions.some((permission) => auth.hasPermission(permission as Permission) || tenancy.hasPermission(permission))
}
