import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import { makeAgentTask, makeDiffArtifact, makeTestRun } from '../../test/fixtures'
import type { TestRun } from '../../types/domain'
import { setDataSourceMode } from '../../services/dataSourceMode'
import DiffView from '../DiffView.vue'
import { ApiError } from '../../services/api'

const { apiRequestMock, messageMocks } = vi.hoisted(() => ({
  apiRequestMock: vi.fn(),
  messageMocks: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
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

let activePinia: Pinia
let router: Router

function mountDiff() {
  return mount(DiffView, {
    global: {
      plugins: [activePinia, router],
      stubs: {
        StatusTag: true,
        EmptyPanel: { props: ['title', 'description', 'action'], template: '<div class="empty-panel-stub"><h3>{{ title }}</h3><p>{{ description }}</p><button v-if="action">{{ action }}</button></div>' },
      },
    },
  })
}

function createTestRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: { template: '<div />' } },
      { path: '/tasks', component: { template: '<div />' } },
      { path: '/tasks/:id', component: { template: '<div />' } },
      { path: '/diff/:id', component: DiffView },
    ],
  })
}

async function go(path: string) {
  await router.push(path)
  const wrapper = mountDiff()
  await flushPromises()
  return wrapper
}

async function openReviewTab(wrapper: ReturnType<typeof mountDiff>, label: string) {
  const tab = wrapper.findAll('[role="tab"]').find((item) => item.text() === label)
  expect(tab).toBeDefined()
  await tab!.trigger('click')
  await flushPromises()
  return wrapper
}

/** 写入本地登录快照，让 auth store 的 hasPermission 按角色生效（ready=false 时回退 cachedUser） */
function seedAuthUser(role: 'developer' | 'approver' = 'developer') {
  localStorage.setItem(
    'frontend-last-auth-user',
    JSON.stringify({
      id: role === 'developer' ? 'user_developer' : 'user_approver',
      account: role === 'developer' ? 'developer@company.com' : 'approver@company.com',
      name: role === 'developer' ? '周航' : '陈晓',
      role,
    }),
  )
}

function approvedTask() {
  return makeAgentTask({
    status: 'APPROVED',
    manual_required: false,
    manual_reason: 'none',
    next_action: 'auto_generate_diff',
    events: [
      { id: 'evt-approve', event_type: 'PLAN_APPROVED', resource_type: 'task', resource_id: 'TASK-9001', actor_id: 'user_approver', actor_name: '陈晓', summary: '计划审批通过', diff_summary: ['风险等级：high'], created_at: '2026-09-04T09:20:00Z' },
    ],
  })
}

function diffGeneratedTask() {
  return makeAgentTask({
    status: 'DIFF_GENERATED',
    automation_state: 'blocked',
    manual_required: false,
    manual_reason: 'none',
    next_action: 'auto_run_test',
    diffs: [makeDiffArtifact()],
    events: [
      { id: 'evt-approve', event_type: 'PLAN_APPROVED', resource_type: 'task', resource_id: 'TASK-9001', actor_id: 'user_approver', actor_name: '陈晓', summary: '计划审批通过', diff_summary: ['风险等级：high'], created_at: '2026-09-04T09:20:00Z' },
      { id: 'evt-diff', event_type: 'DIFF_GENERATED', resource_type: 'task', resource_id: 'TASK-9001', actor_id: 'user_developer', actor_name: '周航', summary: '已生成 Diff v1', diff_summary: ['任务 ID：TASK-9001', '计划版本：v1', '演示兜底：是'], created_at: '2026-09-04T09:30:00Z' },
    ],
  })
}

/** real 模式按路径分发：任务详情 / 生成动作 / Diff 列表 / 测试记录（内部维护列表，生成/执行成功自动吸收产物） */
function installPathMock(overrides: { task?: () => unknown; generate?: () => unknown; initialDiffs?: unknown[]; tests?: () => unknown[]; runTest?: () => unknown } = {}) {
  let diffList: unknown[] = overrides.initialDiffs ?? []
  let testList: unknown[] = overrides.tests?.() ?? []
  apiRequestMock.mockImplementation((path: string) => {
    if (path.endsWith('/diffs/generate')) {
      // generate 可能返回 Promise（防连点用例用 deferred），必须 resolve 后再吸收产物
      return Promise.resolve(overrides.generate?.() ?? diffGeneratedTask()).then((next) => {
        const artifacts = (next as { diffs?: unknown[] })?.diffs
        if (Array.isArray(artifacts)) diffList = artifacts
        return next
      })
    }
    if (path.endsWith('/tests/run')) {
      return Promise.resolve(overrides.runTest?.() ?? diffGeneratedTask()).then((next) => {
        const records = (next as { test_runs?: unknown[] })?.test_runs
        if (Array.isArray(records)) testList = records
        return next
      })
    }
    if (path.endsWith('/diffs')) return Promise.resolve([...diffList])
    if (path.endsWith('/tests')) return Promise.resolve([...testList])
    return Promise.resolve(overrides.task?.() ?? approvedTask())
  })
}

describe('DiffView：mock 模式渲染 demo 变更，不发请求', () => {
  beforeEach(async () => {
    setDataSourceMode('mock')
    localStorage.clear()
    activePinia = createPinia()
    setActivePinia(activePinia)
    router = createTestRouter()
    await router.push('/')
    apiRequestMock.mockReset()
    messageMocks.success.mockClear()
    messageMocks.error.mockClear()
  })

  afterEach(() => {
    setDataSourceMode('real')
  })

  it('直达 /diff/TASK-8824 渲染 2 个 demo 变更文件与统计', async () => {
    const wrapper = await go('/diff/TASK-8824')

    expect(apiRequestMock).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('优化仓库索引超时重试')
    const fileItems = wrapper.findAll('.diff-file-item')
    expect(fileItems).toHaveLength(2)
    expect(wrapper.text()).toContain('workers/index_worker.ts')
    expect(wrapper.text()).toContain('workers/retry_policy.ts')
    expect(wrapper.text()).toContain('+24')
    expect(wrapper.text()).toContain('-9')
    expect(wrapper.find('.empty-panel-stub').exists()).toBe(false)
    expect(wrapper.find('.diff-generate-gate').exists()).toBe(false)
  })

  it('mock 模式未知任务显示任务不存在错误态，不渲染其它任务内容', async () => {
    const wrapper = await go('/diff/TASK-9999')

    expect(apiRequestMock).not.toHaveBeenCalled()
    expect(wrapper.find('.empty-panel-stub').text()).toContain('任务不存在或无权查看')
    expect(wrapper.findAll('.diff-file-item')).toHaveLength(0)
  })
})

describe('DiffView：real 模式生成 Diff 与产物展示', () => {
  beforeEach(async () => {
    setDataSourceMode('real')
    localStorage.clear()
    seedAuthUser('developer')
    activePinia = createPinia()
    setActivePinia(activePinia)
    router = createTestRouter()
    await router.push('/')
    apiRequestMock.mockReset()
    messageMocks.success.mockClear()
    messageMocks.error.mockClear()
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText: vi.fn().mockResolvedValue(undefined) },
    })
  })

  it('APPROVED 无产物时展示生成入口，点击后 POST 生成并渲染演示兜底产物', async () => {
    installPathMock()
    const wrapper = await go('/diff/TASK-9001')

    // 取数：任务详情 + Diff 列表
    expect(apiRequestMock).toHaveBeenCalledWith('/api/tasks/TASK-9001')
    expect(apiRequestMock).toHaveBeenCalledWith('/api/tasks/TASK-9001/diffs')
    expect(wrapper.find('.empty-panel-stub').exists()).toBe(false)
    const gate = wrapper.find('.diff-generate-gate')
    expect(gate.exists()).toBe(true)
    expect(gate.text()).toContain('尚未生成 Diff')

    await gate.find('button').trigger('click')
    await flushPromises()

    expect(apiRequestMock).toHaveBeenCalledWith('/api/tasks/TASK-9001/diffs/generate', { method: 'POST' })
    expect(messageMocks.success).toHaveBeenCalledWith(expect.stringContaining('演示兜底'))

    // 任务流转 DIFF_GENERATED + 演示兜底横幅
    expect(wrapper.text()).toContain('演示兜底产物')
    expect(wrapper.text()).toContain('计划 v1')
    // 修改/新增/删除三类文件
    const items = wrapper.findAll('.diff-file-item')
    expect(items).toHaveLength(3)
    expect(wrapper.text()).toContain('services/payment/callback.py')
    expect(wrapper.text()).toContain('tests/test_callback_idempotent.py')
    expect(wrapper.text()).toContain('legacy/retry_workaround.py')
    expect(wrapper.text()).toContain('+13')
    expect(wrapper.text()).toContain('-6')
    // 测试摘要与 PR 草稿都明确标记演示兜底
    await openReviewTab(wrapper, '审计记录')
    expect(wrapper.text()).toContain('产物测试摘要')
    expect(wrapper.text()).toContain('PR 描述草稿')
    expect(wrapper.find('.diff-artifact-note.is-warning').exists()).toBe(true)
  })

  it('生成中按钮禁用防重复提交；生成完成渲染产物', async () => {
    let resolveGenerate!: (value: unknown) => void
    installPathMock({
      generate: () => new Promise((resolve) => { resolveGenerate = resolve }),
    })
    const wrapper = await go('/diff/TASK-9001')

    const button = wrapper.find('.diff-generate-gate button')
    await button.trigger('click')
    await flushPromises()
    // 未完成前按钮禁用，连点不重复发请求
    expect(wrapper.find('.diff-generate-gate button').attributes('disabled')).toBeDefined()
    await wrapper.find('.diff-generate-gate button').trigger('click')
    await flushPromises()
    const generateCalls = apiRequestMock.mock.calls.filter(([path]) => String(path).endsWith('/diffs/generate'))
    expect(generateCalls).toHaveLength(1)

    resolveGenerate(diffGeneratedTask())
    await flushPromises()
    expect(messageMocks.error).not.toHaveBeenCalled()
    expect(wrapper.findAll('.diff-file-item')).toHaveLength(3)
    expect(wrapper.find('.diff-generate-gate').exists()).toBe(false)
  })

  it('生成接口 409（非审批通过）时错误提示，入口仍在', async () => {
    installPathMock({
      generate: () => Promise.reject(new ApiError('仅审批通过的任务可生成 Diff，请先完成计划审批', 409)),
    })
    const wrapper = await go('/diff/TASK-9001')

    await wrapper.find('.diff-generate-gate button').trigger('click')
    await flushPromises()

    expect(messageMocks.error).toHaveBeenCalledWith('仅审批通过的任务可生成 Diff，请先完成计划审批')
    expect(wrapper.find('.diff-generate-gate').exists()).toBe(true)
  })

  it('真实执行失败保留原因：演示兜底横幅同时展示兜底原因与真实失败原因', async () => {
    const artifact = makeDiffArtifact({
      generation_status: 'FALLBACK',
      is_demo_fallback: true,
      real_failure_reason: '真实执行失败：runner exit=1（模拟命令执行异常）',
    })
    installPathMock({ task: () => diffGeneratedTask(), initialDiffs: [artifact] })
    const wrapper = await go('/diff/TASK-9001')

    await openReviewTab(wrapper, '审计记录')
    expect(wrapper.find('.diff-artifact-note.is-warning').exists()).toBe(true)
    expect(wrapper.text()).toContain('演示兜底产物')
    expect(wrapper.text()).toContain('真实执行失败原因：真实执行失败：runner exit=1')
    expect(wrapper.find('.diff-failure-notice').exists()).toBe(false)
  })

  it('真实与兜底都失败：任务 FAILED 展示失败原因与返回详情入口', async () => {
    const failed = makeAgentTask({
      status: 'FAILED',
      failure_node: 'Diff 生成',
      failure_reason: '真实执行失败：clone 超时（模拟）；演示兜底产物构造失败',
      manual_required: true,
      manual_reason: 'diff_failed',
      next_action: 'retry_or_cancel',
      diffs: [
        makeDiffArtifact({
          generation_status: 'FAILED',
          execution_mode: 'DEMO_FALLBACK',
          is_demo_fallback: true,
          fallback_reason: '演示兜底产物构造失败',
          real_failure_reason: '真实执行失败：clone 超时（模拟）',
          files: [],
          test_summary: '',
          pr_description_draft: '',
        }),
      ],
    })
    installPathMock({ task: () => failed, initialDiffs: failed.diffs ?? [] })
    const wrapper = await go('/diff/TASK-9001')

    expect(wrapper.find('.diff-failure-notice').exists()).toBe(true)
    expect(wrapper.text()).toContain('Diff 生成失败')
    expect(wrapper.text()).toContain('真实执行失败原因：真实执行失败：clone 超时（模拟）')
    expect(wrapper.text()).toContain('演示兜底产物也未构造成功')
    expect(wrapper.findAll('.diff-file-item')).toHaveLength(0)
    // 无审批通过任务时不再展示生成入口
    expect(wrapper.find('.diff-generate-gate').exists()).toBe(false)

    const backButton = wrapper.findAll('button').find((button) => button.text().includes('返回任务详情处理'))
    await backButton!.trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/tasks/TASK-9001')
  })

  it('approver（无 task:operate）在 APPROVED 空产物时只见空态，无生成入口', async () => {
    localStorage.clear()
    seedAuthUser('approver')
    installPathMock()
    const wrapper = await go('/diff/TASK-9001')

    expect(wrapper.find('.diff-generate-gate').exists()).toBe(false)
    expect(wrapper.find('.empty-panel-stub').text()).toContain('当前任务没有可展示的变更')
    expect(wrapper.findAll('.diff-file-item')).toHaveLength(0)
  })

  it('复制 Patch 使用后端完整 patch（含文件头与行号行）', async () => {
    installPathMock()
    const wrapper = await go('/diff/TASK-9001')
    await wrapper.find('.diff-generate-gate button').trigger('click')
    await flushPromises()

    await wrapper.find('.diff-viewer__header .button').trigger('click')
    const writeText = navigator.clipboard.writeText as ReturnType<typeof vi.fn>
    const copied = writeText.mock.calls[0][0] as string
    expect(copied).toContain('--- a/services/payment/callback.py')
    expect(copied).toContain('+++ b/services/payment/callback.py')
    expect(copied).toContain('@@ -1,4 +1,8 @@')
    expect(messageMocks.success).toHaveBeenCalledWith('当前文件 Patch 已复制')
  })

  it('直达未审批任务 Diff 时显示门禁提示，不渲染 mock Diff 也不显示生成入口', async () => {
    localStorage.clear()
    seedAuthUser('developer')
    installPathMock({
      task: () =>
        makeAgentTask({
          status: 'WAITING_APPROVAL',
          manual_required: true,
          manual_reason: 'approval_required',
          next_action: 'review_plan',
        }),
    })

    const wrapper = await go('/diff/TASK-9001')

    expect(apiRequestMock).toHaveBeenCalledWith('/api/tasks/TASK-9001')
    expect(wrapper.find('.empty-panel-stub').text()).toContain('Diff 尚未开放')
    expect(wrapper.find('.empty-panel-stub').text()).toContain('尚未通过审批')
    expect(wrapper.findAll('.diff-file-item')).toHaveLength(0)
    expect(wrapper.find('.diff-generate-gate').exists()).toBe(false)
  })

  it('后端 404 显示任务不存在错误态', async () => {
    apiRequestMock.mockReset()
    apiRequestMock.mockRejectedValue(new ApiError('任务不存在', 404))
    const wrapper = await go('/diff/TASK-4040')

    expect(apiRequestMock).toHaveBeenCalledWith('/api/tasks/TASK-4040')
    expect(wrapper.find('.empty-panel-stub').text()).toContain('任务不存在或无权查看')
  })

  it('后端 401 显示登录状态失效', async () => {
    apiRequestMock.mockReset()
    apiRequestMock.mockRejectedValue(new ApiError('未认证', 401))
    const wrapper = await go('/diff/TASK-9001')

    expect(wrapper.find('.empty-panel-stub').text()).toContain('登录状态已失效')
  })
})

describe('DiffView：real 模式测试执行记录与重试（DEV-012）', () => {
  beforeEach(async () => {
    setDataSourceMode('real')
    localStorage.clear()
    seedAuthUser('developer')
    activePinia = createPinia()
    setActivePinia(activePinia)
    router = createTestRouter()
    await router.push('/')
    apiRequestMock.mockReset()
    messageMocks.success.mockClear()
    messageMocks.error.mockClear()
    messageMocks.warning.mockClear()
  })

  function testFailedTask(records: TestRun[]) {
    return makeAgentTask({
      status: 'TEST_FAILED',
      automation_state: 'blocked',
      manual_required: true,
      manual_reason: 'test_failed',
      next_action: 'retry_test',
      diffs: [makeDiffArtifact()],
      test_runs: records,
    })
  }

  function failedRun(attempt: number, failureReason: string): TestRun {
    return makeTestRun({
      id: `TEST-9001-${attempt}`,
      attempt,
      status: 'failed',
      exit_code: 1,
      stdout: '',
      stderr: `FAILED tests/test_callback_idempotent.py\n${failureReason}`,
      failure_reason: failureReason,
      duration: 3.14,
      started_at: `2026-09-04T10:0${attempt}:00Z`,
      finished_at: `2026-09-04T10:0${attempt}:05Z`,
    })
  }

  it('DIFF_GENERATED 展示空态与运行按钮；运行成功渲染通过记录与输出', async () => {
    installPathMock({
      task: () => diffGeneratedTask(),
      initialDiffs: [makeDiffArtifact()],
      runTest: () =>
        makeAgentTask({
          status: 'TESTED',
          automation_state: 'blocked',
          manual_required: false,
          manual_reason: 'none',
          next_action: 'watch_test_result',
          diffs: [makeDiffArtifact()],
          test_runs: [makeTestRun()],
        }),
    })
    const wrapper = await go('/diff/TASK-9001')
    await openReviewTab(wrapper, '测试证据')

    const panel = wrapper.find('.test-run-panel')
    expect(panel.exists()).toBe(true)
    expect(panel.text()).toContain('尚未执行测试')
    expect(panel.find('button').text()).toContain('运行测试')

    await panel.find('button').trigger('click')
    await flushPromises()

    expect(apiRequestMock).toHaveBeenCalledWith('/api/tasks/TASK-9001/tests/run', { method: 'POST' })
    expect(messageMocks.success).toHaveBeenCalledWith(expect.stringContaining('测试通过'))
    expect(wrapper.text()).not.toContain('尚未执行测试')
    // 通过记录：第 1 次 / 状态 / 模式 / exit 0 / stdout
    expect(panel.text()).toContain('第 1 次')
    expect(panel.text()).toContain('通过')
    expect(panel.text()).toContain('演示运行')
    expect(panel.find('.test-run__exit').text()).toContain('exit 0')
    expect(panel.find('.test-run__detail').text()).toContain('3 passed in 1.02s')
    expect(panel.find('.test-run__detail').text()).toContain('pytest tests/')
    // Diff 产物与文件区不受影响
    expect(wrapper.findAll('.diff-file-item')).toHaveLength(3)
    expect(panel.findAll('button')).toHaveLength(0)
  })

  it('TEST_FAILED 展示失败原因与剩余重试；测试失败不被当成 Diff 失败', async () => {
    const failed = failedRun(1, 'AssertionError: 重复入账')
    installPathMock({
      task: () => testFailedTask([failed]),
      initialDiffs: [makeDiffArtifact()],
      tests: () => [failed],
    })
    const wrapper = await go('/diff/TASK-9001')
    await openReviewTab(wrapper, '测试证据')

    // 失败的是测试而不是 Diff：无失败横幅，变更文件照常展示
    expect(wrapper.find('.diff-failure-notice').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('Diff 生成失败')
    expect(wrapper.findAll('.diff-file-item')).toHaveLength(3)

    const panel = wrapper.find('.test-run-panel')
    expect(panel.find('.test-run__reason--failed').text()).toContain('失败原因：AssertionError: 重复入账')
    expect(panel.find('.test-run__exit').text()).toContain('exit 1')

    // 未达上限：按钮变成带次数说明的重试
    const retryButton = panel.find('button')
    expect(retryButton.text()).toContain('重试测试（第 2 次 / 上限 2 次）')
    await retryButton.trigger('click')
    await flushPromises()
    expect(apiRequestMock).toHaveBeenCalledWith('/api/tasks/TASK-9001/tests/run', { method: 'POST' })
  })

  it('危险命令被拦截：409 提示并展示 skipped 记录与拦截原因，任务保持可重试', async () => {
    const blocked = makeTestRun({
      status: 'skipped',
      exit_code: null,
      stdout: '',
      stderr: '',
      failure_reason: null,
      blocked_reason: '匹配危险命令规则：禁止删除根目录文件',
      finished_at: '2026-09-04T10:02:00Z',
    })
    const blockedList = [blocked]
    installPathMock({
      task: () => diffGeneratedTask(),
      initialDiffs: [makeDiffArtifact()],
      tests: () => [...blockedList],
      runTest: () => Promise.reject(new ApiError('测试命令存在安全风险，已被拦截：匹配危险命令规则', 409)),
    })
    const wrapper = await go('/diff/TASK-9001')
    await openReviewTab(wrapper, '测试证据')

    const panel = wrapper.find('.test-run-panel')
    expect(panel.text()).toContain('未执行')
    expect(panel.text()).toContain('跳过/拦截原因：匹配危险命令规则：禁止删除根目录文件')

    await panel.find('button').trigger('click')
    await flushPromises()

    expect(apiRequestMock).toHaveBeenCalledWith('/api/tasks/TASK-9001/tests/run', { method: 'POST' })
    expect(messageMocks.error).toHaveBeenCalledWith(expect.stringContaining('测试命令存在安全风险'))
    // 拦截不流转任务：DIFF_GENERATED 仍可再次运行
    expect(panel.find('button').text()).toContain('运行测试')
    expect(wrapper.findAll('.diff-file-item')).toHaveLength(3)
  })

  it('approver 无 task:operate：测试区只读说明，不渲染运行按钮', async () => {
    localStorage.clear()
    seedAuthUser('approver')
    installPathMock({
      task: () => diffGeneratedTask(),
      initialDiffs: [makeDiffArtifact()],
    })
    const wrapper = await go('/diff/TASK-9001')
    await openReviewTab(wrapper, '测试证据')

    const panel = wrapper.find('.test-run-panel')
    expect(panel.exists()).toBe(true)
    expect(panel.text()).toContain('尚未执行测试')
    expect(panel.findAll('button')).toHaveLength(0)
  })

  it('TESTED：通过记录留档但不再提供运行/重试按钮', async () => {
    const passed = makeTestRun()
    installPathMock({
      task: () =>
        makeAgentTask({
          status: 'TESTED',
          automation_state: 'blocked',
          manual_required: false,
          manual_reason: 'none',
          next_action: 'watch_test_result',
          diffs: [makeDiffArtifact()],
          test_runs: [passed],
        }),
      initialDiffs: [makeDiffArtifact()],
      tests: () => [passed],
    })
    const wrapper = await go('/diff/TASK-9001')
    await openReviewTab(wrapper, '测试证据')

    const panel = wrapper.find('.test-run-panel')
    expect(panel.exists()).toBe(true)
    expect(panel.text()).toContain('第 1 次')
    expect(panel.text()).toContain('通过')
    expect(panel.text()).toContain('exit 0')
    // 无运行/重试按钮（diff-meta 里 test_summary 有「未运行测试命令」字样，不能按全页文本断言）
    expect(panel.find('.test-run-panel__head button').exists()).toBe(false)
  })

  it('两次失败达重试上限（防御后端边界）：不再提供重试按钮', async () => {
    const records = [failedRun(2, 'AssertionError: 重复入账'), failedRun(1, 'AssertionError: 重复入账')]
    installPathMock({
      task: () => testFailedTask(records),
      initialDiffs: [makeDiffArtifact()],
      tests: () => records,
    })
    const wrapper = await go('/diff/TASK-9001')
    await openReviewTab(wrapper, '测试证据')

    const panel = wrapper.find('.test-run-panel')
    expect(panel.findAll('.test-run')).toHaveLength(2)
    // 已达上限：任务应已由后端终止，前端不再给重试入口
    expect(panel.findAll('button')).toHaveLength(0)
  })
})
