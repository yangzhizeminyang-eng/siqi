export type UserRole = 'admin' | 'approver' | 'developer' | 'lead' | 'observer'

export type Permission =
  | 'dashboard:view'
  | 'task:view'
  | 'task:create'
  | 'task:operate'
  | 'approval:view'
  | 'approval:manage'
  | 'approval:assign'
  | 'diff:view'
  | 'replay:view'
  | 'index:view'
  | 'index:manage'
  | 'project:manage'
  | 'user:view'
  | 'request:view'
  | 'request:create'

export interface AuthUser {
  id: string
  name: string
  email: string
  role: UserRole
  role_label: string
  permissions: Permission[]
}

export interface LoginResponse {
  user: AuthUser
  expires_at: string
}

export interface ApiResponse<T> {
  code: number
  message: string
  data: T
}

export interface PasswordResetRequest {
  id: string
  email: string
  user_name: string
  role_label: string
  status: 'pending' | 'completed'
  requested_at: string
  handled_by?: string | null
  handled_at?: string | null
}
