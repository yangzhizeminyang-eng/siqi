import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import SettingsView from '../SettingsView.vue'
import type { RepositoryConfig } from '../../types/domain'

const { apiRequestMock, hasPermissionMock } = vi.hoisted(() => ({
  apiRequestMock: vi.fn(),
  hasPermissionMock: vi.fn(),
}))

vi.mock('element-plus', () => ({
  ElMessage: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
}))

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

vi.mock('../../stores/auth', () => ({
  useAuthStore: () => ({ hasPermission: hasPermissionMock }),
}))

function makeRepository(overrides: Partial<RepositoryConfig> = {}): RepositoryConfig {
  return {
    id: 'repo_payment_service',
    project_id: 'proj_payment',
    name: 'payment-service',
    url: 'https://gitee.com/example/payment-service.git',
    default_branch: 'main',
    token_alias: null,
    token_configured: false,
    token_updated_at: null,
    test_command: 'pytest tests/ -q',
    command_whitelist: ['pytest', 'npm test', 'pnpm test'],
    approval_owner_id: '',
    approval_owner_name: '',
    approval_mode: 'single',
    risk_threshold: 'medium',
    max_files: 12,
    max_retries: 2,
    blocked_paths: [],
    auto_create_task_after_sync: false,
    created_at: '2026-09-01T00:00:00Z',
    updated_at: '2026-09-01T00:00:00Z',
    ...overrides,
  } as RepositoryConfig
}

async function mountSettings() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/', component: SettingsView }],
  })
  await router.push('/')
  const wrapper = mount(SettingsView, { global: { plugins: [router] } })
  await flushPromises()
  return wrapper
}

describe('DEV-016-C SettingsView 仓库自动创建开关', () => {
  beforeEach(() => {
    apiRequestMock.mockReset()
    hasPermissionMock.mockReset()
    hasPermissionMock.mockReturnValue(true)
    apiRequestMock.mockImplementation((path: string) => {
      if (path === '/api/projects') return Promise.resolve([{ id: 'proj_payment', name: '支付中台', description: '' }])
      if (path === '/api/users') return Promise.resolve([])
      if (path.startsWith('/api/repositories')) return Promise.resolve([])
      if (path.startsWith('/api/audit-events')) return Promise.resolve([])
      return Promise.resolve({})
    })
  })

  it('新增仓库默认不勾选自动创建，保存 payload 携带 false', async () => {
    const wrapper = await mountSettings()
    const checkbox = wrapper.find('input[type="checkbox"]')
    expect(checkbox.exists()).toBe(true)
    expect((checkbox.element as HTMLInputElement).checked).toBe(false)

    await wrapper.find('input[placeholder="payment-service"]').setValue('payment-service')
    await wrapper.find('input[placeholder*="gitee.com/company"]').setValue('https://gitee.com/example/payment-service.git')
    await wrapper.find('footer.form-footer button').trigger('click')
    await flushPromises()

    const createCall = apiRequestMock.mock.calls.find(
      (call) => call[0] === '/api/repositories' && call[1]?.method === 'POST',
    )
    expect(createCall).toBeTruthy()
    const payload = JSON.parse(createCall![1].body)
    expect(payload.auto_create_task_after_sync).toBe(false)
  })

  it('勾选开关后保存 payload 携带 true', async () => {
    const wrapper = await mountSettings()
    await wrapper.find('input[placeholder="payment-service"]').setValue('payment-service')
    await wrapper.find('input[placeholder*="gitee.com/company"]').setValue('https://gitee.com/example/payment-service.git')
    const checkbox = wrapper.find('.settings-checkbox input[type="checkbox"]')
    await checkbox.setValue(true)
    expect((checkbox.element as HTMLInputElement).checked).toBe(true)

    await wrapper.find('footer.form-footer button').trigger('click')
    await flushPromises()

    const createCall = apiRequestMock.mock.calls.find(
      (call) => call[0] === '/api/repositories' && call[1]?.method === 'POST',
    )
    expect(createCall).toBeTruthy()
    const payload = JSON.parse(createCall![1].body)
    expect(payload.auto_create_task_after_sync).toBe(true)
  })

  it('编辑已有仓库时回填开关状态', async () => {
    apiRequestMock.mockImplementation((path: string) => {
      if (path === '/api/projects') return Promise.resolve([{ id: 'proj_payment', name: '支付中台', description: '' }])
      if (path === '/api/users') return Promise.resolve([])
      if (path.startsWith('/api/repositories')) return Promise.resolve([makeRepository({ auto_create_task_after_sync: true })])
      if (path.startsWith('/api/audit-events')) return Promise.resolve([])
      return Promise.resolve({})
    })
    const wrapper = await mountSettings()
    await flushPromises()
    const checkbox = wrapper.find('.settings-checkbox input[type="checkbox"]')
    expect((checkbox.element as HTMLInputElement).checked).toBe(true)
  })
})

describe('仓库安全策略数值步进器', () => {
  beforeEach(() => {
    apiRequestMock.mockReset()
    hasPermissionMock.mockReset()
    hasPermissionMock.mockReturnValue(true)
    apiRequestMock.mockImplementation((path: string) => {
      if (path === '/api/projects') return Promise.resolve([{ id: 'proj_payment', name: '支付中台', description: '' }])
      if (path === '/api/users') return Promise.resolve([])
      if (path.startsWith('/api/repositories')) return Promise.resolve([makeRepository()])
      if (path.startsWith('/api/audit-events')) return Promise.resolve([])
      return Promise.resolve({})
    })
  })

  it('只调整被操作的数值，并正确处理上下界', async () => {
    const wrapper = await mountSettings()
    await wrapper.findAll('.settings-nav button').find((node) => node.text() === '安全策略')!.trigger('click')
    const maxFilesInput = wrapper.find('input[type="number"][max="200"]')
    const decrease = wrapper.find('button[aria-label="减少最大可修改文件数"]')
    const increase = wrapper.find('button[aria-label="增加最大可修改文件数"]')

    expect(decrease.element.closest('label')).toBeNull()
    expect(increase.element.closest('label')).toBeNull()
    expect(decrease.classes()).toContain('settings-number-field__control--decrease')
    expect(increase.classes()).toContain('settings-number-field__control--increase')
    expect((maxFilesInput.element as HTMLInputElement).value).toBe('12')

    increase.element.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true }))
    increase.element.dispatchEvent(new MouseEvent('click', { bubbles: true, detail: 1 }))
    await flushPromises()
    expect((maxFilesInput.element as HTMLInputElement).value).toBe('13')

    await maxFilesInput.setValue(200)
    expect((increase.element as HTMLButtonElement).disabled).toBe(true)

    await maxFilesInput.setValue(1)
    expect((decrease.element as HTMLButtonElement).disabled).toBe(true)
  })
})

describe('DEV-018b SettingsView 项目级默认测试命令', () => {
  beforeEach(() => {
    apiRequestMock.mockReset()
    hasPermissionMock.mockReset()
    hasPermissionMock.mockReturnValue(true)
    apiRequestMock.mockImplementation((path: string) => {
      if (path === '/api/projects') {
        return Promise.resolve([{ id: 'proj_payment', name: '支付中台', description: '', default_test_command: 'npm test' }])
      }
      if (path === '/api/users') return Promise.resolve([])
      if (path.startsWith('/api/repositories')) return Promise.resolve([])
      if (path.startsWith('/api/audit-events')) return Promise.resolve([])
      return Promise.resolve({})
    })
  })

  it('加载项目时回显项目级默认测试命令', async () => {
    const wrapper = await mountSettings()
    const input = wrapper.find('input[placeholder="npm test"]')
    expect(input.exists()).toBe(true)
    expect((input.element as HTMLInputElement).value).toBe('npm test')
  })

  it('保存项目信息 PUT 携带项目级默认测试命令', async () => {
    const wrapper = await mountSettings()
    const input = wrapper.find('input[placeholder="npm test"]')
    await input.setValue('pytest tests/ -q')

    await wrapper.findAll('button').filter((node) => node.text().includes('保存项目信息'))[0]!.trigger('click')
    await flushPromises()

    const updateCall = apiRequestMock.mock.calls.find(
      (call) => call[0] === '/api/projects/proj_payment' && call[1]?.method === 'PUT',
    )
    expect(updateCall).toBeTruthy()
    const payload = JSON.parse(updateCall![1].body)
    expect(payload.default_test_command).toBe('pytest tests/ -q')
  })

  it('清空项目级默认测试命令时携带 null', async () => {
    const wrapper = await mountSettings()
    const input = wrapper.find('input[placeholder="npm test"]')
    await input.setValue('')

    await wrapper.findAll('button').filter((node) => node.text().includes('保存项目信息'))[0]!.trigger('click')
    await flushPromises()

    const updateCall = apiRequestMock.mock.calls.find(
      (call) => call[0] === '/api/projects/proj_payment' && call[1]?.method === 'PUT',
    )
    expect(updateCall).toBeTruthy()
    const payload = JSON.parse(updateCall![1].body)
    expect(payload.default_test_command).toBeNull()
  })
})

describe('仓库智能识别', () => {
  beforeEach(() => {
    apiRequestMock.mockReset()
    hasPermissionMock.mockReset()
    hasPermissionMock.mockReturnValue(true)
    apiRequestMock.mockImplementation((path: string) => {
      if (path === '/api/projects') return Promise.resolve([{ id: 'proj_payment', name: '支付中台', description: '' }])
      if (path === '/api/users') return Promise.resolve([])
      if (path.startsWith('/api/repositories')) return Promise.resolve([])
      if (path.startsWith('/api/audit-events')) return Promise.resolve([])
      return Promise.resolve({})
    })
  })

  it('根据真实识别结果回填仓库名、分支、测试命令和白名单', async () => {
    apiRequestMock.mockImplementation((path: string, options?: RequestInit) => {
      if (path === '/api/projects') return Promise.resolve([{ id: 'proj_payment', name: '支付中台', description: '' }])
      if (path === '/api/users') return Promise.resolve([])
      if (path === '/api/repositories/inspect' && options?.method === 'POST') {
        return Promise.resolve({
          suggested_payload: {
            name: 'payment-service',
            url: 'https://gitee.com/example/payment-service.git',
            default_branch: 'develop',
            test_command: 'npm test',
            command_whitelist: ['npm test'],
          },
          sources: { test_command: 'package.json > scripts.test' },
          confidence: { test_command: 'high' },
          detected_stack: ['Node.js'],
          warnings: [],
        })
      }
      if (path.startsWith('/api/repositories')) return Promise.resolve([])
      if (path.startsWith('/api/audit-events')) return Promise.resolve([])
      return Promise.resolve({})
    })
    const wrapper = await mountSettings()
    await wrapper.find('input[placeholder*="gitee.com/company"]').setValue('https://gitee.com/example/payment-service.git')
    await wrapper.find('button.settings-inspect-button').trigger('click')
    await flushPromises()

    expect((wrapper.find('input[placeholder="payment-service"]').element as HTMLInputElement).value).toBe('payment-service')
    expect((wrapper.find('input[placeholder="main"]').element as HTMLInputElement).value).toBe('develop')
    expect(wrapper.text()).toContain('package.json > scripts.test')
    expect(wrapper.text()).toContain('Node.js')

    await wrapper.findAll('button').filter((node) => node.text().includes('测试命令'))[0]!.trigger('click')
    expect((wrapper.find('input.mono').element as HTMLInputElement).value).toBe('npm test')
  })
})
