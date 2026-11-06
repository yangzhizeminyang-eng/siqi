import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, apiRequest } from '../api'

describe('apiRequest：错误响应解析', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('保留后端返回的 FastAPI detail.message', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: { code: 401, message: '账号或密码错误', data: null } }), {
          status: 401,
          headers: { 'Content-Type': 'application/json' },
        }),
      ),
    )

    await expect(apiRequest('/api/auth/login')).rejects.toMatchObject({
      name: 'ApiError',
      message: '账号或密码错误',
      status: 401,
    })
  })

  it('非 JSON 网关响应显示可执行的服务提示', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('Bad Gateway', { status: 502 })))

    await expect(apiRequest('/api/auth/login')).rejects.toMatchObject({
      name: 'ApiError',
      message: '研发服务暂不可用，请确认团队后端已启动（端口 8002）',
      status: 502,
    })
  })

  it('网络连接失败显示后端连接提示', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('fetch failed')))

    await expect(apiRequest('/api/auth/login')).rejects.toEqual(
      expect.objectContaining({
        name: 'ApiError',
        message: '无法连接研发服务，请确认团队后端已启动（端口 8002）',
        status: 0,
      } satisfies Partial<ApiError>),
    )
  })
})
