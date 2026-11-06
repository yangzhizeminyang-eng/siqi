import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import RequestDetailView from '../RequestDetailView.vue'
import type { TeamRequest, TeamRequestTaskResponse, AgentTask } from '../../types/domain'

const { apiRequestMock, hasPermissionMock, hasRoleMock, authUser, messageMocks } = vi.hoisted(() => ({
  apiRequestMock: vi.fn(),
  hasPermissionMock: vi.fn(),
  hasRoleMock: vi.fn(),
  authUser: { id: 'user_admin' },
  messageMocks: { success: vi.fn(), warning: vi.fn(), error: vi.fn(), info: vi.fn() },
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
  useAuthStore: () => ({
    hasPermission: hasPermissionMock,
    hasRole: hasRoleMock,
    user: authUser,
    currentUser: authUser,
  }),
}))

function makeRequest(overrides: Partial<TeamRequest> = {}): TeamRequest {
  return {
    id: 'team_req_001',
    request_no: 'REQ-1001',
    project_id: 'proj_payment',
    project_name: '支付中台',
    repository_id: 'repo_payment_service',
    repository_name: 'payment-service',
    type: 'bug',
    title: '支付回调重复入账',
    body: '同一 out_trade_no 收到两次回调时会重复入账，期望幂等。',
    reproduction_steps: '',
    acceptance_criteria: ['同一单号只入账一次'],
    priority: 'high',
    labels: ['bug'],
    target_branch: 'main',
    test_command: 'pytest tests/ -q',
    sync_status: 'SYNCED',
    content_sufficient: true,
    validation_warnings: [],
    failure_reason: null,
    manual_reason: null,
    gitee_issue_id: '2002',
    gitee_issue_number: 'IKC1601',
    gitee_issue_url: 'https://gitee.com/example/payment-service/issues/IKC1601',
    task_id: null,
    created_by_id: 'user_developer',
    created_by_name: '周航',
    created_at: '2026-09-06T00:00:00Z',
    updated_at: '2026-09-06T00:05:00Z',
    ...overrides,
  }
}

function makeTask(overrides: Partial<AgentTask> = {}): AgentTask {
  return {
    id: 'TASK-8801',
    version: 1,
    project_id: 'proj_payment',
    project_name: '支付中台',
    repository_id: 'repo_payment_service',
    repository_name: 'payment-service',
    repository_url: 'https://gitee.com/example/payment-service.git',
    issue_id: 'IKC1601',
    title: '支付回调重复入账',
    status: 'ISSUE_FETCHED',
    risk_level: null,
    risk_source: 'pending_ai_analysis',
    base_branch: 'main',
    test_command: 'pytest tests/ -q',
    approval_owner_id: null,
    approval_owner_name: null,
    work_branch: 'agent/main/issue-IKC1601-v1',
    issue_snapshot: { title: '支付回调重复入账', body: '正文', comments: [], labels: ['bug'], raw_response: {}, fetched_at: '2026-09-06T00:06:00Z' },
    automation_state: 'running',
    manual_required: false,
    manual_reason: 'none',
    next_action: 'wait_index',
    retry_count: 0,
    base_commit_sha: 'c0ffee11deadbeef22c0ffee33deadbeef44556677',
    team_request_id: 'team_req_001',
    team_request_no: 'REQ-1001',
    gitee_issue_url: 'https://gitee.com/example/payment-service/issues/IKC1601',
    events: [],
    created_by: 'user_admin',
    created_by_name: '管理员',
    created_at: '2026-09-06T00:06:00Z',
    updated_at: '2026-09-06T00:06:00Z',
    ...overrides,
  }
}

function makeCreateResponse(record: TeamRequest, overrides: Partial<TeamRequestTaskResponse> = {}): TeamRequestTaskResponse {
  return {
    task: makeTask(),
    request: record,
    jump_url: '/tasks/TASK-8801',
    created: true,
    reused: false,
    ...overrides,
  }
}

function makeRouter(path = '/') {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: { template: '<div />' } },
      { path: '/requests', component: { template: '<div />' } },
      { path: '/requests/:id', component: RequestDetailView },
      { path: '/tasks/:id', component: { template: '<div />' } },
      { path: '/settings', component: { template: '<div />' } },
    ],
  })
  return router.push(path).then(() => router)
}

async function mountDetail(record: TeamRequest, router: Router) {
  apiRequestMock.mockImplementation((path: string) => {
    if (path === '/api/team/requests/team_req_001') return Promise.resolve(record)
    return Promise.resolve(record)
  })
  const wrapper = mount(RequestDetailView, { global: { plugins: [router] } })
  await flushPromises()
  return wrapper
}

describe('DEV-016-C RequestDetail 创建 Agent 任务面板', () => {
  beforeEach(() => {
    apiRequestMock.mockReset()
    hasPermissionMock.mockReset()
    hasPermissionMock.mockReturnValue(true)
    hasRoleMock.mockReset()
    hasRoleMock.mockReturnValue(false)
    authUser.id = 'user_admin'
    Object.values(messageMocks).forEach((mock) => (mock as ReturnType<typeof vi.fn>).mockClear())
  })

  it('SYNCED 且无关联任务时显示创建 Agent 任务按钮', async () => {
    const router = await makeRouter('/requests/team_req_001')
    const wrapper = await mountDetail(makeRequest(), router)
    const button = wrapper.findAll('button').find((item) => item.text().includes('创建 Agent 任务'))
    expect(button).toBeTruthy()
    expect((button!.element as HTMLButtonElement).disabled).toBe(false)
  })

  it('DRAFT 不显示任务面板与创建按钮', async () => {
    const router = await makeRouter('/requests/team_req_001')
    const wrapper = await mountDetail(makeRequest({ sync_status: 'DRAFT', gitee_issue_id: null, gitee_issue_number: null, gitee_issue_url: null }), router)
    expect(wrapper.find('.request-task').exists()).toBe(false)
    expect(wrapper.findAll('button').some((item) => item.text().includes('创建 Agent 任务'))).toBe(false)
  })

  it('无 request:create 权限不显示创建按钮', async () => {
    hasPermissionMock.mockReturnValue(false)
    const router = await makeRouter('/requests/team_req_001')
    const wrapper = await mountDetail(makeRequest(), router)
    expect(wrapper.findAll('button').some((item) => item.text().includes('创建 Agent 任务'))).toBe(false)
  })

  it('开发者对他人需求不显示创建按钮，本人需求显示', async () => {
    hasRoleMock.mockImplementation((role: string) => role === 'developer')
    authUser.id = 'user_other'
    const router = await makeRouter('/requests/team_req_001')
    const otherWrapper = await mountDetail(makeRequest(), router)
    expect(otherWrapper.findAll('button').some((item) => item.text().includes('创建 Agent 任务'))).toBe(false)

    authUser.id = 'user_developer'
    const ownerWrapper = await mountDetail(makeRequest(), router)
    expect(ownerWrapper.findAll('button').some((item) => item.text().includes('创建 Agent 任务'))).toBe(true)
  })

  it('点击创建调用 create-task 并刷新为 TASK_CREATED，展示任务跳转链接', async () => {
    const taskCreated = makeRequest({ sync_status: 'TASK_CREATED', task_id: 'TASK-8801' })
    apiRequestMock.mockImplementation((path: string, options?: { method?: string }) => {
      if (path === '/api/team/requests/team_req_001/create-task' && options?.method === 'POST') {
        return Promise.resolve(makeCreateResponse(taskCreated))
      }
      return Promise.resolve(makeRequest())
    })
    const router = await makeRouter('/requests/team_req_001')
    const wrapper = mount(RequestDetailView, { global: { plugins: [router] } })
    await flushPromises()

    const button = wrapper.findAll('button').find((item) => item.text().includes('创建 Agent 任务'))
    expect(button).toBeTruthy()
    await button!.trigger('click')
    await flushPromises()

    expect(
      apiRequestMock.mock.calls.some(
        (call) => call[0] === '/api/team/requests/team_req_001/create-task' && call[1]?.method === 'POST',
      ),
    ).toBe(true)
    const taskLink = wrapper.find('a[href="/tasks/TASK-8801"], .request-task a')
    // RouterLink 渲染为 a，to 经 stub/真实路由均可在文本中找到任务号
    expect(wrapper.text()).toContain('TASK-8801')
    expect(taskLink.exists()).toBe(true)
    expect(messageMocks.success).toHaveBeenCalledWith('Agent 任务已创建')
  })

  it('后端返回 reused 时提示已存在未结束任务，不重复创建', async () => {
    const linked = makeRequest({ sync_status: 'TASK_CREATED', task_id: 'TASK-8801' })
    apiRequestMock.mockImplementation((path: string, options?: { method?: string }) => {
      if (path === '/api/team/requests/team_req_001/create-task' && options?.method === 'POST') {
        return Promise.resolve(makeCreateResponse(linked, { created: false, reused: true }))
      }
      return Promise.resolve(makeRequest())
    })
    const router = await makeRouter('/requests/team_req_001')
    const wrapper = mount(RequestDetailView, { global: { plugins: [router] } })
    await flushPromises()
    await wrapper.findAll('button').find((item) => item.text().includes('创建 Agent 任务'))!.trigger('click')
    await flushPromises()
    expect(messageMocks.info).toHaveBeenCalledWith('该 Issue 已存在未结束任务，已直接关联，无需重复创建')
  })

  it('上下文不足进入人工补充时弹 warning 提示', async () => {
    const linked = makeRequest({ sync_status: 'TASK_CREATED', task_id: 'TASK-8801' })
    apiRequestMock.mockImplementation((path: string, options?: { method?: string }) => {
      if (path === '/api/team/requests/team_req_001/create-task' && options?.method === 'POST') {
        return Promise.resolve(
          makeCreateResponse(linked, { task: makeTask({ manual_required: true, manual_reason: 'context_missing' }) }),
        )
      }
      return Promise.resolve(makeRequest())
    })
    const router = await makeRouter('/requests/team_req_001')
    const wrapper = mount(RequestDetailView, { global: { plugins: [router] } })
    await flushPromises()
    await wrapper.findAll('button').find((item) => item.text().includes('创建 Agent 任务'))!.trigger('click')
    await flushPromises()
    expect(messageMocks.warning).toHaveBeenCalled()
  })

  it('创建请求进行中按钮禁用，防止重复点击', async () => {
    let resolveCreate: (value: TeamRequestTaskResponse) => void = () => {}
    apiRequestMock.mockImplementation((path: string) => {
      if (path === '/api/team/requests/team_req_001/create-task') {
        return new Promise((resolve) => {
          resolveCreate = resolve
        })
      }
      return Promise.resolve(makeRequest())
    })
    const router = await makeRouter('/requests/team_req_001')
    const wrapper = mount(RequestDetailView, { global: { plugins: [router] } })
    await flushPromises()
    const button = wrapper.findAll('button').find((item) => item.text().includes('创建 Agent 任务'))!
    await button.trigger('click')
    await flushPromises()
    const pending = wrapper.findAll('button').find((item) => item.text().includes('创建中'))
    expect(pending).toBeTruthy()
    expect((pending!.element as HTMLButtonElement).disabled).toBe(true)
    resolveCreate(makeCreateResponse(makeRequest({ sync_status: 'TASK_CREATED', task_id: 'TASK-8801' })))
    await flushPromises()
  })

  it('TASK_CREATED 已有关联任务时展示任务链接与新建版本入口', async () => {
    const router = await makeRouter('/requests/team_req_001')
    const wrapper = await mountDetail(makeRequest({ sync_status: 'TASK_CREATED', task_id: 'TASK-8801' }), router)
    expect(wrapper.text()).toContain('查看任务 TASK-8801')
    expect(wrapper.findAll('button').some((item) => item.text().includes('新建任务版本'))).toBe(true)
  })
})
