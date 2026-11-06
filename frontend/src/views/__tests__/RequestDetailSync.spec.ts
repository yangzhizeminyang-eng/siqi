import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import RequestDetailView from '../RequestDetailView.vue'
import type { TeamRequest } from '../../types/domain'

const { apiRequestMock, hasPermissionMock, hasRoleMock, authUser, messageMocks } = vi.hoisted(() => ({
  apiRequestMock: vi.fn(),
  hasPermissionMock: vi.fn(),
  hasRoleMock: vi.fn(),
  authUser: { id: 'user_admin' },
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
      { path: '/requests', component: { template: '<div />' } },
      { path: '/settings', component: { template: '<div />' } },
      { path: '/requests/:id', component: RequestDetailView },
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

describe('DEV-016-B RequestDetail sync panel', () => {
  beforeEach(() => {
    apiRequestMock.mockReset()
    hasPermissionMock.mockReset()
    hasPermissionMock.mockReturnValue(true)
    hasRoleMock.mockReset()
    hasRoleMock.mockReturnValue(false)
    authUser.id = 'user_admin'
    messageMocks.success.mockClear()
    messageMocks.warning.mockClear()
    messageMocks.error.mockClear()
  })

  it('DRAFT 显示同步按钮', async () => {
    const router = await makeRouter('/requests/team_req_001')
    const wrapper = await mountDetail(makeRequest({ sync_status: 'DRAFT' }), router)
    const buttons = wrapper.findAll('button')
    expect(buttons.some((button) => button.text().includes('同步到 Gitee'))).toBe(true)
  })

  it('SYNCING 状态禁止重复点击且不显示再次创建按钮', async () => {
    const router = await makeRouter('/requests/team_req_001')
    const wrapper = await mountDetail(makeRequest({ sync_status: 'SYNCING' }), router)
    expect(wrapper.text()).toContain('正在同步到 Gitee')
    const syncButtons = wrapper.findAll('button').filter((button) => button.text().includes('同步'))
    expect(syncButtons.length).toBe(1)
    expect((syncButtons[0].element as HTMLButtonElement).disabled).toBe(true)
  })

  it('SYNC_FAILED 显示失败原因和重试按钮', async () => {
    const router = await makeRouter('/requests/team_req_001')
    const wrapper = await mountDetail(
      makeRequest({ sync_status: 'SYNC_FAILED', failure_reason: 'Gitee Issue 创建失败：标题不能为空', manual_reason: 'gitee_sync_failed' }),
      router,
    )
    expect(wrapper.text()).toContain('Gitee Issue 创建失败：标题不能为空')
    expect(wrapper.findAll('button').some((button) => button.text().includes('重试同步'))).toBe(true)
  })

  it('auth_expired 显示重新授权入口，有权限时跳项目配置', async () => {
    const router = await makeRouter('/requests/team_req_001')
    const wrapper = await mountDetail(
      makeRequest({ sync_status: 'SYNC_FAILED', failure_reason: 'Gitee Token 已过期或无权限，请重新授权后重试', manual_reason: 'auth_expired' }),
      router,
    )
    expect(wrapper.text()).toContain('需要重新授权')
    const reauthButton = wrapper.findAll('button').find((button) => button.text().includes('前往项目配置重新授权'))
    expect(reauthButton).toBeTruthy()

    await reauthButton!.trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/settings')
  })

  it('auth_expired 无项目配置权限时提示联系管理员', async () => {
    hasPermissionMock.mockImplementation((permission: string) => permission !== 'project:manage')
    const router = await makeRouter('/requests/team_req_001')
    const wrapper = await mountDetail(
      makeRequest({ sync_status: 'SYNC_FAILED', failure_reason: 'Gitee Token 已过期或无权限，请重新授权后重试', manual_reason: 'auth_expired' }),
      router,
    )
    expect(wrapper.text()).toContain('需要重新授权')
    expect(wrapper.text()).toContain('请联系项目管理员更新 Gitee Token 后重试')
    expect(wrapper.findAll('button').some((button) => button.text().includes('前往项目配置'))).toBe(false)
  })

  it('SYNCED 显示 Gitee Issue 外链', async () => {
    const router = await makeRouter('/requests/team_req_001')
    const wrapper = await mountDetail(
      makeRequest({
        sync_status: 'SYNCED',
        gitee_issue_id: '1001',
        gitee_issue_number: 'IKD5H1',
        gitee_issue_url: 'https://gitee.com/example/payment-service/issues/IKD5H1',
      }),
      router,
    )
    expect(wrapper.text()).toContain('已同步 Gitee Issue')
    const link = wrapper.find('a[href="https://gitee.com/example/payment-service/issues/IKD5H1"]')
    expect(link.exists()).toBe(true)
    expect(link.text()).toContain('IKD5H1')
  })

  it('SYNCED 状态不显示再次同步按钮（DEV-016-C 起改显创建 Agent 任务入口）', async () => {
    const router = await makeRouter('/requests/team_req_001')
    const wrapper = await mountDetail(
      makeRequest({
        sync_status: 'SYNCED',
        gitee_issue_id: '1001',
        gitee_issue_number: 'IKD5H1',
        gitee_issue_url: 'https://gitee.com/example/payment-service/issues/IKD5H1',
      }),
      router,
    )
    const syncButtons = wrapper.findAll('button').filter((button) => button.text().includes('同步'))
    expect(syncButtons.length).toBe(0)
    expect(wrapper.findAll('button').some((button) => button.text().includes('创建 Agent 任务'))).toBe(true)
  })

  it('开发者只能同步本人草稿，他人草稿不显示同步按钮', async () => {
    hasRoleMock.mockImplementation((role: string) => role === 'developer')
    authUser.id = 'user_other'
    const router = await makeRouter('/requests/team_req_001')
    const wrapper = await mountDetail(makeRequest({ sync_status: 'DRAFT' }), router)
    expect(wrapper.findAll('button').some((button) => button.text().includes('同步到 Gitee'))).toBe(false)

    authUser.id = 'user_developer'
    const wrapperOwner = await mountDetail(makeRequest({ sync_status: 'DRAFT' }), router)
    expect(wrapperOwner.findAll('button').some((button) => button.text().includes('同步到 Gitee'))).toBe(true)
  })

  it('点击同步按钮调用 sync-gitee 并刷新为 SYNCED', async () => {
    const synced = makeRequest({
      sync_status: 'SYNCED',
      gitee_issue_id: '1001',
      gitee_issue_number: 'IKD5H1',
      gitee_issue_url: 'https://gitee.com/example/payment-service/issues/IKD5H1',
    })
    apiRequestMock.mockImplementation((path: string, options?: { method?: string }) => {
      if (path === '/api/team/requests/team_req_001/sync-gitee' && options?.method === 'POST') return Promise.resolve(synced)
      if (path === '/api/team/requests/team_req_001') return Promise.resolve(makeRequest())
      return Promise.resolve(makeRequest())
    })
    const router = await makeRouter('/requests/team_req_001')
    const wrapper = mount(RequestDetailView, { global: { plugins: [router] } })
    await flushPromises()

    const syncButton = wrapper.findAll('button').find((button) => button.text().includes('同步到 Gitee'))
    expect(syncButton).toBeTruthy()
    await syncButton!.trigger('click')
    await flushPromises()

    expect(apiRequestMock.mock.calls.some((call) => call[0] === '/api/team/requests/team_req_001/sync-gitee' && call[1]?.method === 'POST')).toBe(true)
    expect(wrapper.text()).toContain('已同步 Gitee Issue')
    expect(messageMocks.success).toHaveBeenCalledWith('已同步 Gitee Issue')
  })
})
