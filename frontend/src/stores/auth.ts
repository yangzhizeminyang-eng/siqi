// 文件级注释：前端鉴权 Pinia Store。
// 负责登录、登出、bootstrap（拉取当前用户）、权限/角色判定。
// 修复点：bootstrap 增加请求级 Promise 缓存与对网络错误的 1 次自动重试，
// 避免刷新页面时 /api/auth/me 瞬时失败被守卫判为未登录而跳到 /login。
import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { ApiError, apiRequest } from '../services/api'
import { useWorkspaceStore } from './workspace'
import type { AuthUser, LoginResponse, Permission, UserRole } from '../types/auth'

const LAST_USER_KEY = 'frontend-last-auth-user'

// 模块级 Promise 缓存：避免多个路由守卫并发触发 /api/auth/me 造成竞态。
let bootstrapInFlight: Promise<void> | null = null
// 标记本次启动是否已经做过网络重试，避免无限重试。
let hasRetriedThisBoot = false

function readCachedUser(): AuthUser | null {
  if (typeof window === 'undefined') return null
  try {
    const raw = localStorage.getItem(LAST_USER_KEY)
    return raw ? normalizeUser(JSON.parse(raw) as AuthUser) : null
  } catch {
    return null
  }
}

function writeCachedUser(user: AuthUser | null) {
  if (typeof window === 'undefined') return
  if (!user) {
    localStorage.removeItem(LAST_USER_KEY)
    return
  }
  localStorage.setItem(LAST_USER_KEY, JSON.stringify(user))
}

function isNetworkError(err: unknown): boolean {
  // fetch 直接抛错（DNS/连接拒绝/CORS 拦截等）时，ApiError 不会被构造，
  // 这里通过 ApiError 缺失或 status === undefined 来识别。
  return !(err instanceof ApiError) || typeof err.status !== 'number'
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

const ROLE_PERMISSION_MAP: Record<UserRole, Permission[]> = {
  admin: [
    'dashboard:view',
    'task:view',
    'task:create',
    'task:operate',
    'approval:view',
    'approval:manage',
    'approval:assign',
    'diff:view',
    'replay:view',
    'index:view',
    'index:manage',
    'project:manage',
    'user:view',
  ],
  approver: ['dashboard:view', 'task:view', 'approval:view', 'approval:manage', 'diff:view', 'replay:view', 'index:view', 'request:view'],
  developer: ['dashboard:view', 'task:view', 'task:create', 'task:operate', 'diff:view', 'replay:view', 'index:view', 'index:manage', 'request:view', 'request:create'],
  lead: ['dashboard:view', 'task:view', 'approval:view', 'approval:assign', 'diff:view', 'replay:view', 'index:view', 'request:view', 'request:create'],
  observer: ['dashboard:view', 'task:view', 'diff:view', 'replay:view', 'index:view', 'request:view'],
}

function normalizeUser(user: AuthUser): AuthUser {
  const permissions = Array.from(new Set([...(user.permissions ?? []), ...(ROLE_PERMISSION_MAP[user.role] ?? [])])) as Permission[]
  return { ...user, permissions }
}

export const useAuthStore = defineStore('auth', () => {
  const currentUser = ref<AuthUser | null>(null)
  const cachedUser = ref<AuthUser | null>(readCachedUser())
  const ready = ref(false)
  const loading = ref(false)
  const error = ref('')
  const isAuthenticated = computed(() => currentUser.value !== null)
  const displayUser = computed(() => currentUser.value ?? cachedUser.value)

  /**
   * 函数级注释：判定当前用户是否拥有指定权限。
   * 入参：permission 权限标识。
   * 返回值：boolean。后端用户未确认前只使用上次确认过的本地展示快照，
   *         避免刷新瞬间菜单抖动；鉴权拦截仍由 router 在 bootstrap 后判断。
   *         真正的授权拦截由 router.beforeEach 兜底，未授权用户会被重定向。
   * 异常：无。
   */
  function hasPermission(permission: Permission) {
    const user = currentUser.value ?? (!ready.value ? cachedUser.value : null)
    return Boolean(user?.permissions.includes(permission))
  }

  function hasRole(...roles: UserRole[]) {
    const user = currentUser.value ?? (!ready.value ? cachedUser.value : null)
    return Boolean(user && roles.includes(user.role))
  }

  /**
   * 函数级注释：拉取当前登录用户。
   * 入参：无。
   * 返回值：Promise<void>，副作用为写入 currentUser / ready。
   * 异常：内部吞掉 ApiError（401 视为未登录，其它写入 error）；
   *       网络错误做 1 次自动重试，仍失败则视为未登录。
   */
  async function bootstrap(): Promise<void> {
    // 已就绪：直接返回，避免重复请求。
    if (ready.value) return
    // 正在 in-flight：复用同一个 Promise，避免并发。
    if (bootstrapInFlight) return bootstrapInFlight

    bootstrapInFlight = (async () => {
      loading.value = true
      error.value = ''
      try {
        const user = await apiRequest<AuthUser>('/api/auth/me')
        currentUser.value = normalizeUser(user)
        cachedUser.value = currentUser.value
        writeCachedUser(currentUser.value)
        hasRetriedThisBoot = false
      } catch (err) {
        // 网络层瞬时失败：重试 1 次。
        if (isNetworkError(err) && !hasRetriedThisBoot) {
          hasRetriedThisBoot = true
          try {
            await delay(200)
            const user = await apiRequest<AuthUser>('/api/auth/me')
            currentUser.value = normalizeUser(user)
            cachedUser.value = currentUser.value
            writeCachedUser(currentUser.value)
          } catch (retryErr) {
            currentUser.value = null
            cachedUser.value = null
            writeCachedUser(null)
            // 重试仍失败：仅记录非 401 错误。
            if (retryErr instanceof ApiError && retryErr.status !== 401) {
              error.value = retryErr.message
            }
          }
        } else {
          currentUser.value = null
          cachedUser.value = null
          writeCachedUser(null)
          if (err instanceof ApiError && err.status !== 401) {
            error.value = err.message
          }
        }
      } finally {
        ready.value = true
        loading.value = false
        bootstrapInFlight = null
      }
    })()

    return bootstrapInFlight
  }

  /**
   * 函数级注释：账号密码登录。
   * 入参：account(账号)、password(密码)、rememberMe(保持登录)。
   * 返回值：Promise<LoginResponse>，含后端返回的 user 信息。
   * 异常：失败时抛 ApiError，error 中写入人类可读消息。
   */
  async function login(account: string, password: string, rememberMe: boolean) {
    loading.value = true
    error.value = ''
    try {
      const data = await apiRequest<LoginResponse>('/api/auth/login', {
        method: 'POST',
        body: JSON.stringify({ account, password, remember_me: rememberMe }),
      })
      currentUser.value = normalizeUser(data.user)
      cachedUser.value = currentUser.value
      writeCachedUser(currentUser.value)
      ready.value = true
      return data
    } catch (err) {
      currentUser.value = null
      error.value = err instanceof ApiError
        ? err.message
        : '无法连接研发服务，请确认团队后端已启动（端口 8002）'
      throw err
    } finally {
      loading.value = false
    }
  }

  /**
   * 函数级注释：登出。始终清空本地 currentUser，登出接口失败也走 finally。
   * 工作区任务状态是全站共享演示态，切换角色时保留快照，
   * 用于体现审批、执行、复盘之间的接力更新。
   * 入参：无。
   * 返回值：Promise<void>。
   * 异常：吞掉登出接口错误，由 finally 保证本地状态被清空。
   */
  async function logout() {
    try {
      await apiRequest('/api/auth/logout', { method: 'POST' })
    } finally {
      currentUser.value = null
      cachedUser.value = null
      writeCachedUser(null)
      ready.value = true
      useWorkspaceStore().persistSnapshot()
    }
  }

  return {
    currentUser,
    cachedUser,
    ready,
    loading,
    error,
    isAuthenticated,
    displayUser,
    bootstrap,
    login,
    logout,
    hasPermission,
    hasRole,
  }
})
