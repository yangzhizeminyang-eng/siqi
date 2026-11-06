import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import { ApiError } from '../../services/api'
import RequestCreateView from '../RequestCreateView.vue'
import RequestDetailView from '../RequestDetailView.vue'
import RequestListView from '../RequestListView.vue'
import type { TeamRequest } from '../../types/domain'

const { apiRequestMock, hasPermissionMock, hasRoleMock, authUser, messageMocks } = vi.hoisted(() => ({
  apiRequestMock: vi.fn(),
  hasPermissionMock: vi.fn(),
  hasRoleMock: vi.fn(),
  authUser: { id: 'user_developer' },
  messageMocks: { success: vi.fn(), warning: vi.fn(), error: vi.fn() },
}))

vi.mock('element-plus', () => ({
  ElMessage: messageMocks,
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
  useAuthStore: () => ({ hasPermission: hasPermissionMock, hasRole: hasRoleMock, user: authUser, currentUser: authUser }),
}))

const project = { id: 'proj_payment', name: '支付中台', description: '', created_at: '2026-09-06T00:00:00Z', updated_at: '2026-09-06T00:00:00Z' }
const repository = {
  id: 'repo_payment_service',
  project_id: 'proj_payment',
  name: 'payment-service',
  url: 'https://gitee.com/company/payment-service.git',
  default_branch: 'main',
  token_configured: true,
  test_command: 'pytest tests/ -q',
  command_whitelist: ['pytest tests/ -q', 'pytest -q'],
  approval_owner_id: 'user_approver',
  approval_owner_name: '陈晓',
  approval_mode: 'single',
  risk_threshold: 'medium',
  max_files: 20,
  max_retries: 2,
  blocked_paths: [],
  created_at: '2026-09-06T00:00:00Z',
  updated_at: '2026-09-06T00:00:00Z',
}

function makeRequest(overrides: Partial<TeamRequest> = {}): TeamRequest {
  return {
    id: 'team_req_001',
    request_no: 'REQ-1001',
    project_id: project.id,
    project_name: project.name,
    repository_id: repository.id,
    repository_name: repository.name,
    type: 'bug',
    title: '登录页刷新后误跳转',
    body: '登录页在刷新后会误跳转到登录页，期望保持当前页面，仅登录态失效时跳转。',
    reproduction_steps: '',
    acceptance_criteria: ['刷新后保持当前页面'],
    priority: 'medium',
    labels: ['bug', 'frontend'],
    target_branch: 'main',
    test_command: 'pytest tests/ -q',
    sync_status: 'DRAFT',
    content_sufficient: true,
    validation_warnings: [],
    failure_reason: null,
    manual_reason: null,
    gitee_issue_id: null,
    gitee_issue_number: null,
    gitee_issue_url: null,
    task_id: null,
    created_by_id: 'user_developer',
    created_by_name: '周航',
    created_at: '2026-09-06T00:00:00Z',
    updated_at: '2026-09-06T00:05:00Z',
    ...overrides,
  }
}

function makeRouter(path = '/') {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: { template: '<div />' } },
      { path: '/requests', component: RequestListView },
      { path: '/requests/new', component: RequestCreateView },
      { path: '/requests/:id', component: RequestDetailView },
    ],
  })
  return router.push(path).then(() => router)
}

function mockBaseApi(created = makeRequest()) {
  apiRequestMock.mockImplementation((path: string, options?: { method?: string; body?: string }) => {
    if (path === '/api/projects') return Promise.resolve([project])
    if (path.startsWith('/api/repositories')) return Promise.resolve([repository])
    if (path === '/api/team/requests' && options?.method === 'POST') return Promise.resolve(created)
    if (path === '/api/team/requests') return Promise.resolve([created])
    if (path === '/api/team/requests/team_req_001') return Promise.resolve(created)
    if (path === '/api/team/requests/team_req_001' && options?.method === 'PATCH') return Promise.resolve(created)
    return Promise.resolve(created)
  })
}

async function mountWithRouter(component: object, router: Router) {
  return mount(component, {
    global: {
      plugins: [router],
    },
  })
}

describe('DEV-016-A Request views', () => {
  beforeEach(() => {
    apiRequestMock.mockReset()
    hasPermissionMock.mockReset()
    hasPermissionMock.mockReturnValue(true)
    hasRoleMock.mockReset()
    hasRoleMock.mockReturnValue(false)
    messageMocks.success.mockClear()
    messageMocks.warning.mockClear()
    messageMocks.error.mockClear()
  })

  it('列表展示平台草稿并隐藏观察者创建入口', async () => {
    hasPermissionMock.mockImplementation((permission: string) => permission !== 'request:create')
    mockBaseApi(makeRequest({ content_sufficient: false, validation_warnings: ['同步 Gitee 或创建 Agent 任务前需要补充正文'] }))
    const router = await makeRouter('/requests')
    const wrapper = await mountWithRouter(RequestListView, router)
    await flushPromises()

    expect(wrapper.text()).toContain('REQ-1001')
    expect(wrapper.text()).toContain('登录页刷新后误跳转')
    expect(wrapper.text()).toContain('需要补充正文')
    expect(wrapper.findAll('button').some((button) => button.text().includes('创建需求'))).toBe(false)
  })

  it('列表手动刷新时合并相同成功提示', async () => {
    mockBaseApi()
    const router = await makeRouter('/requests')
    const wrapper = await mountWithRouter(RequestListView, router)
    await flushPromises()

    const refreshButton = wrapper.findAll('button').find((button) => button.text().includes('刷新列表'))!
    await refreshButton.trigger('click')
    await flushPromises()

    expect(messageMocks.success).toHaveBeenCalledWith({
      message: '平台需求列表已刷新',
      grouping: true,
    })
  })

  it('创建页提交 demand 类型草稿并展示短正文 warning', async () => {
    const shortRecord = makeRequest({
      type: 'demand',
      content_sufficient: false,
      validation_warnings: ['同步 Gitee 或创建 Agent 任务前需要补充正文'],
    })
    mockBaseApi(shortRecord)
    const router = await makeRouter('/requests/new')
    const wrapper = await mountWithRouter(RequestCreateView, router)
    await flushPromises()

    await wrapper.find('input[placeholder="例如：登录页刷新后误跳转"]').setValue('优化首页文案')
    await wrapper.find('textarea[placeholder^="写清现象"]').setValue('文案太短')
    await wrapper.find('input[placeholder="pytest tests/ -q"]').setValue('pytest tests/ -q')
    await wrapper.find('form').trigger('submit')
    await flushPromises()

    const createCall = apiRequestMock.mock.calls.find((call) => call[0] === '/api/team/requests' && call[1]?.method === 'POST')
    expect(createCall).toBeTruthy()
    expect(JSON.parse(createCall![1].body).type).toBe('demand')
    expect(messageMocks.warning).toHaveBeenCalledWith('同步 Gitee 或创建 Agent 任务前需要补充正文')
  })

  it('创建页展示 422 中文错误', async () => {
    apiRequestMock.mockImplementation((path: string, options?: { method?: string }) => {
      if (path === '/api/projects') return Promise.resolve([project])
      if (path.startsWith('/api/repositories')) return Promise.resolve([repository])
      if (options?.method === 'POST') {
        return Promise.reject(new ApiError('测试命令不在项目或仓库命令白名单内', 422))
      }
      return Promise.resolve([])
    })
    const router = await makeRouter('/requests/new')
    const wrapper = await mountWithRouter(RequestCreateView, router)
    await flushPromises()

    await wrapper.find('input[placeholder="例如：登录页刷新后误跳转"]').setValue('优化首页文案')
    await wrapper.find('textarea[placeholder^="写清现象"]').setValue('正文足够长，可以进入平台草稿保存。')
    await wrapper.find('input[placeholder="pytest tests/ -q"]').setValue('rm -rf /')
    await wrapper.find('form').trigger('submit')
    await flushPromises()

    expect(wrapper.text()).toContain('测试命令不在项目或仓库命令白名单内')
    expect(messageMocks.error).toHaveBeenCalledWith('测试命令不在项目或仓库命令白名单内')
  })

  it('详情页允许编辑 DRAFT 草稿并提交 PATCH', async () => {
    const updated = makeRequest({ title: '负责人修订标题' })
    apiRequestMock.mockImplementation((path: string, options?: { method?: string }) => {
      if (path === '/api/team/requests/team_req_001' && options?.method === 'PATCH') return Promise.resolve(updated)
      if (path === '/api/team/requests/team_req_001') return Promise.resolve(makeRequest())
      return Promise.resolve([])
    })
    const router = await makeRouter('/requests/team_req_001')
    const wrapper = await mountWithRouter(RequestDetailView, router)
    await flushPromises()

    await wrapper.find('input').setValue('负责人修订标题')
    await wrapper.find('form').trigger('submit')
    await flushPromises()

    expect(apiRequestMock.mock.calls.some((call) => call[0] === '/api/team/requests/team_req_001' && call[1]?.method === 'PATCH')).toBe(true)
    expect(wrapper.text()).toContain('负责人修订标题')
  })
})
