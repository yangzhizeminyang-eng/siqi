import type { ApiResponse } from '../types/auth'

type ErrorDetail = string | { message?: string; data?: unknown } | Array<{ msg?: string }>
type ErrorPayload = {
  detail?: ErrorDetail
  message?: string
  error?: string
  statusCode?: number
  code?: number
  data?: unknown
}

export class ApiError extends Error {
  status: number
  data: unknown

  constructor(message: string, status: number, data?: unknown) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.data = data
  }
}

function parsePayload(text: string): ApiResponse<unknown> | ErrorPayload | null {
  if (!text) return null
  try {
    return JSON.parse(text) as ApiResponse<unknown> | ErrorPayload
  } catch {
    return null
  }
}

function getErrorMessage(payload: ApiResponse<unknown> | ErrorPayload | null, status: number): string {
  if (payload && typeof payload === 'object') {
    if ('detail' in payload) {
      const detail = payload.detail
      if (typeof detail === 'string') return detail
      if (Array.isArray(detail)) return detail[0]?.msg || '请求参数不正确'
      if (detail && typeof detail === 'object' && detail.message) return detail.message
    }
    if ('message' in payload && typeof payload.message === 'string' && payload.message) return payload.message
    if ('error' in payload && typeof payload.error === 'string' && payload.error) return payload.error
  }

  if (status === 401) return '账号或密码错误'
  if (status === 502 || status === 503 || status === 504) return '研发服务暂不可用，请确认团队后端已启动（端口 8002）'
  return '请求失败'
}

export async function apiRequest<T>(path: string, options: RequestInit = {}): Promise<T> {
  let response: Response
  try {
    response = await fetch(path, {
      credentials: 'include',
      headers: {
        'Content-Type': 'application/json',
        ...(options.headers ?? {}),
      },
      ...options,
    })
  } catch {
    throw new ApiError('无法连接研发服务，请确认团队后端已启动（端口 8002）', 0)
  }

  const text = await response.text()
  const payload = parsePayload(text)

  if (!response.ok) {
    const message = getErrorMessage(payload, response.status)
    const data = payload && typeof payload === 'object' && 'detail' in payload && payload.detail && typeof payload.detail === 'object' && !Array.isArray(payload.detail)
      ? payload.detail.data
      : payload && typeof payload === 'object' && 'data' in payload
        ? payload.data
        : undefined
    throw new ApiError(message, response.status, data)
  }

  if (payload && typeof payload === 'object' && 'code' in payload) {
    const apiPayload = payload as ApiResponse<T>
    if (apiPayload.code !== 0) {
      throw new ApiError(apiPayload.message || '请求失败', response.status, apiPayload.data)
    }
    return apiPayload.data
  }

  return payload as T
}
