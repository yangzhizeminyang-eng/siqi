import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import { makeAgentTask, makeDiffArtifact, makePrDescription, makeTestRun } from '../../test/fixtures'
import type { AgentTask, PrDescriptionOverview, PrDescriptionRecord, TestRun } from '../../types/domain'
import { setDataSourceMode } from '../../services/dataSourceMode'
import DiffView from '../DiffView.vue'

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

function testedTask(records: TestRun[], prs: PrDescriptionRecord[] = [], extra: Partial<AgentTask> = {}): AgentTask {
  return makeAgentTask({
    status: 'TESTED',
    automation_state: 'blocked',
    manual_required: false,
    manual_reason: 'none',
    next_action: 'watch_test_result',
    diffs: [makeDiffArtifact()],
    test_runs: records,
    pr_descriptions: prs,
    events: [
      { id: 'evt-approve', event_type: 'PLAN_APPROVED', resource_type: 'task', resource_id: 'TASK-9001', actor_id: 'user_approver', actor_name: '陈晓', summary: '计划审批通过', diff_summary: ['风险等级：high'], created_at: '2026-09-04T09:20:00Z' },
      { id: 'evt-diff', event_type: 'DIFF_GENERATED', resource_type: 'task', resource_id: 'TASK-9001', actor_id: 'user_developer', actor_name: '周航', summary: '已生成 Diff v1', diff_summary: ['任务 ID：TASK-9001', '计划版本：v1', '演示兜底：是'], created_at: '2026-09-04T09:30:00Z' },
      { id: 'evt-test', event_type: 'TEST_RUN_PASSED', resource_type: 'task', resource_id: 'TASK-9001', actor_id: 'system', actor_name: 'Agent', summary: '测试通过（第 1 次）', diff_summary: ['任务 ID：TASK-9001', '测试记录：TEST-9001-1', 'exit 0'], created_at: '2026-09-04T10:02:00Z' },
    ],
    ...extra,
  })
}

const passedTest = makeTestRun()

/** GET /pr-description 复合接口的返回体（视图经 store.loadPrDescription 消费 versions） */
function makeOverview(versions: PrDescriptionRecord[]): PrDescriptionOverview {
  const sorted = [...versions].sort((left, right) => right.version - left.version)
  const current = sorted[0] ?? null
  return {
    task_id: 'TASK-9001',
    status: !current ? 'PENDING' : current.generation_status === 'FAILED' ? 'FAILED' : 'GENERATED',
    stale: false,
    stale_reason: null,
    current,
    versions,
  }
}

/**
 * real 模式按路径分发：任务详情 / Diff 列表 / 测试记录 / PR 描述复合接口 /
 * PR 描述生成（响应任务经 createTaskFromApi 吸收新版本）。
 */
function installRealMock(overrides: { task?: () => AgentTask; diffs?: unknown[]; tests?: () => TestRun[]; prVersions?: () => PrDescriptionRecord[]; generatePr?: () => AgentTask } = {}) {
  apiRequestMock.mockImplementation((path: string) => {
    if (path.endsWith('/pr-description/generate')) {
      return Promise.resolve(overrides.generatePr?.() ?? testedTask([passedTest], [makePrDescription()]))
    }
    if (path.endsWith('/pr-description')) {
      return Promise.resolve(makeOverview(overrides.prVersions?.() ?? []))
    }
    if (path.endsWith('/diffs')) return Promise.resolve(overrides.diffs ?? [makeDiffArtifact()])
    if (path.endsWith('/tests')) return Promise.resolve(overrides.tests?.() ?? [passedTest])
    return Promise.resolve(overrides.task?.() ?? testedTask([passedTest]))
  })
}

describe('DiffView：PR 描述面板（DEV-013）', () => {
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

  afterEach(() => {
    setDataSourceMode('real')
  })

  it('默认停在合入概览，PR 长文不进入默认视图', async () => {
    const record = makePrDescription()
    installRealMock({
      task: () => testedTask([passedTest], [record]),
      prVersions: () => [record],
    })
    const wrapper = await go('/diff/TASK-9001')

    const overviewTab = wrapper.find('#diff-tab-overview')
    expect(overviewTab.attributes('aria-selected')).toBe('true')
    expect(wrapper.find('#diff-panel-overview').exists()).toBe(true)
    expect(wrapper.find('.pr-panel').exists()).toBe(false)
    expect(wrapper.findAll('.pr-field')).toHaveLength(0)
    expect(wrapper.text()).not.toContain('背景说明（来源：Issue #IDEMO001）')
  })

  it('TESTED 已有记录：渲染六段式全文、版本历史与整页复制', async () => {
    const v1 = makePrDescription()
    const v2 = makePrDescription({
      id: 'PRD-9001-2',
      version: 2,
      generated_at: '2026-09-04T10:30:00Z',
      change_summary: '改动说明（来源：审批通过的修改计划 v2）\n- 计划目标：v2 追加重复回调审计日志。',
    })
    installRealMock({ task: () => testedTask([passedTest], [v1, v2]), prVersions: () => [v1, v2] })
    const wrapper = await go('/diff/TASK-9001')
    await openReviewTab(wrapper, 'PR 描述')

    const panel = wrapper.find('.pr-panel')
    expect(panel.exists()).toBe(true)
    expect(panel.text()).toContain('PR 描述')
    expect(panel.text()).toContain('已生成')
    // 六段式字段齐全
    expect(wrapper.findAll('.pr-field')).toHaveLength(6)
    expect(panel.text()).toContain('背景说明（来源：Issue #IDEMO001）')
    expect(panel.text()).toContain('改动说明（来源：审批通过的修改计划 v2）')
    expect(panel.text()).toContain('测试情况')
    expect(panel.text()).toContain('回滚方案')
    // 历史版本 chips：默认选中最新 v2
    const chips = wrapper.findAll('.pr-version-chip')
    expect(chips.map((chip) => chip.text())).toEqual(['v2', 'v1'])
    expect(chips[0].classes()).toContain('is-active')
    expect(chips[0].text()).toBe('v2')
    // 旧版本可切换查看且内容对应
    await chips[1].trigger('click')
    await flushPromises()
    expect(chips[1].classes()).toContain('is-active')
    expect(panel.text()).toContain('改动说明（来源：审批通过的修改计划 v1）')

    // 整页复制：六段正文一次复制
    await wrapper.find('.pr-panel .button--secondary').trigger('click')
    const writeText = navigator.clipboard.writeText as ReturnType<typeof vi.fn>
    const copied = writeText.mock.calls[writeText.mock.calls.length - 1][0] as string
    expect(copied).toContain('背景说明（来源：Issue #IDEMO001）')
    expect(copied).toContain('回滚方案')
    expect(messageMocks.success).toHaveBeenCalledWith(expect.stringContaining('已复制'))
  })

  it('TESTED 无记录：空态说明 + 生成按钮，生成成功后展示新版本与复制入口', async () => {
    installRealMock({
      prVersions: () => [],
      generatePr: () => testedTask([passedTest], [makePrDescription()]),
    })
    const wrapper = await go('/diff/TASK-9001')
    await openReviewTab(wrapper, 'PR 描述')

    const panel = wrapper.find('.pr-panel')
    expect(panel.text()).toContain('尚未生成 PR 描述')
    expect(panel.find('.button--primary').text()).toContain('生成 PR 描述')

    await panel.find('.button--primary').trigger('click')
    await flushPromises()

    expect(apiRequestMock).toHaveBeenCalledWith('/api/tasks/TASK-9001/pr-description/generate', { method: 'POST' })
    expect(messageMocks.success).toHaveBeenCalledWith(expect.stringContaining('PR 描述已生成（第 1 版）'))
    expect(panel.text()).not.toContain('尚未生成 PR 描述')
    expect(panel.text()).toContain('v1')
    expect(wrapper.find('.pr-panel .button--secondary').exists()).toBe(true)
    // 产物未变化：无「需重新生成」与重新生成按钮
    expect(panel.find('.status-tag--warning').exists()).toBe(false)
    expect(panel.find('.button--primary').exists()).toBe(false)
  })

  it('Diff/测试结果变化：标记需重新生成，一键重生成并保留旧版本', async () => {
    const v1 = makePrDescription() // 绑定 DIFF-9001-v1 / TEST-9001-1
    const test1 = passedTest
    const test2 = makeTestRun({
      id: 'TEST-9001-2',
      attempt: 2,
      started_at: '2026-09-04T10:20:00Z',
      finished_at: '2026-09-04T10:20:02Z',
      duration: 1.6,
    })
    const diff2 = makeDiffArtifact({
      id: 'DIFF-9001-v2',
      plan_version: 2,
      generated_at: '2026-09-04T10:10:00Z',
    })
    installRealMock({
      task: () => testedTask([test2, test1], [v1]),
      diffs: [diff2],
      tests: () => [test2, test1],
      prVersions: () => [v1],
      generatePr: () =>
        testedTask([test2, test1], [
          makePrDescription({
            id: 'PRD-9001-2',
            version: 2,
            plan_id: 'PLAN-9001-2',
            plan_version: 2,
            diff_artifact_id: 'DIFF-9001-v2',
            test_run_id: 'TEST-9001-2',
            generated_at: '2026-09-04T10:40:00Z',
          }),
          v1,
        ], { diffs: [diff2] }),
    })
    const wrapper = await go('/diff/TASK-9001')
    await openReviewTab(wrapper, 'PR 描述')

    const panel = wrapper.find('.pr-panel')
    // 输入基线已前进：展示「需重新生成」提示与重新生成按钮
    expect(panel.find('.status-tag--warning').text()).toContain('需重新生成')
    expect(panel.text()).toContain('该版本基于旧产物')
    expect(panel.text()).toContain('Diff 产物/计划版本已更新')
    expect(panel.find('.button--primary').text()).toContain('重新生成 PR 描述')

    await panel.find('.button--primary').trigger('click')
    await flushPromises()

    expect(apiRequestMock).toHaveBeenCalledWith('/api/tasks/TASK-9001/pr-description/generate', { method: 'POST' })
    expect(messageMocks.success).toHaveBeenCalledWith(expect.stringContaining('PR 描述已生成（第 2 版）'))
    // 新版本基于最新输入：过期标记消除
    expect(panel.find('.status-tag--warning').exists()).toBe(false)
    expect(panel.find('.button--primary').exists()).toBe(false)
    // 旧版本仍在历史中可核对
    const chips = wrapper.findAll('.pr-version-chip')
    expect(chips.map((chip) => chip.text())).toEqual(['v2', 'v1'])
    await openReviewTab(wrapper, '审计记录')
    expect(wrapper.text()).toContain('DIFF-9001-v2')
    expect(wrapper.text()).toContain('TEST-9001-2')
  })

  it('最近一次生成失败：展示失败原因并可重试（不阻塞页面）', async () => {
    const failed = makePrDescription({
      generation_status: 'FAILED',
      failure_reason: '测试结果为空：未找到已完成的测试记录',
      generated_at: '2026-09-04T10:40:00Z',
    })
    installRealMock({
      task: () => testedTask([passedTest], [failed]),
      prVersions: () => [failed],
      generatePr: () =>
        testedTask([passedTest], [
          makePrDescription({ id: 'PRD-9001-2', version: 2, generated_at: '2026-09-04T10:45:00Z' }),
          failed,
        ]),
    })
    const wrapper = await go('/diff/TASK-9001')
    await openReviewTab(wrapper, 'PR 描述')

    const panel = wrapper.find('.pr-panel')
    expect(panel.text()).toContain('PR 描述生成失败（第 1 版）')
    expect(panel.text()).toContain('测试结果为空：未找到已完成的测试记录')
    // 失败态仍可重试生成
    expect(panel.find('.button--primary').text()).toContain('重新生成 PR 描述')

    await panel.find('.button--primary').trigger('click')
    await flushPromises()

    expect(messageMocks.error).not.toHaveBeenCalled()
    expect(messageMocks.success).toHaveBeenCalledWith(expect.stringContaining('PR 描述已生成（第 2 版）'))
    expect(panel.text()).toContain('v2')
    expect(panel.find('.pr-panel__failed').exists()).toBe(false)
  })

  it('TEST_FAILED 时以失败测试为输入：可生成并绑定失败记录', async () => {
    const failedRun = makeTestRun({
      status: 'failed',
      exit_code: 1,
      stderr: 'FAILED tests/test_callback_idempotent.py\nAssertionError: 重复入账',
      failure_reason: 'AssertionError: 重复入账',
      finished_at: '2026-09-04T10:03:00Z',
    })
    const prRecord = makePrDescription({
      test_run_id: 'TEST-9001-1',
      test_result: '测试情况\n- 结果：失败（exit 1，第 1 次尝试，耗时 3.14s）\n- 失败原因：AssertionError: 重复入账\n- 合入前需修复测试并重新生成 PR 描述。',
    })
    installRealMock({
      task: () => testedTask([failedRun], [], { status: 'TEST_FAILED', manual_required: true, manual_reason: 'test_failed', next_action: 'retry_test' }),
      tests: () => [failedRun],
      prVersions: () => [],
      generatePr: () => testedTask([failedRun], [prRecord], { status: 'TEST_FAILED', manual_required: true, manual_reason: 'test_failed', next_action: 'retry_test' }),
    })
    const wrapper = await go('/diff/TASK-9001')
    await openReviewTab(wrapper, 'PR 描述')

    const panel = wrapper.find('.pr-panel')
    expect(panel.text()).toContain('尚未生成 PR 描述')

    await panel.find('.button--primary').trigger('click')
    await flushPromises()

    expect(apiRequestMock).toHaveBeenCalledWith('/api/tasks/TASK-9001/pr-description/generate', { method: 'POST' })
    await openReviewTab(wrapper, '审计记录')
    expect(wrapper.text()).toContain('TEST-9001-1')
    expect(panel.text()).toContain('失败原因：AssertionError: 重复入账')
  })

  it('DIFF_GENERATED 尚无测试结论：面板说明生成条件未满足，无生成按钮', async () => {
    installRealMock({
      task: () => testedTask([], [], { status: 'DIFF_GENERATED', manual_required: false, manual_reason: 'none', next_action: 'auto_run_test' }),
      tests: () => [],
      prVersions: () => [],
    })
    const wrapper = await go('/diff/TASK-9001')
    await openReviewTab(wrapper, 'PR 描述')

    const panel = wrapper.find('.pr-panel')
    expect(panel.exists()).toBe(true)
    expect(panel.text()).toContain('需先产生测试结论后才可生成 PR 描述')
    expect(panel.text()).toContain('DIFF_GENERATED')
    expect(panel.findAll('button')).toHaveLength(0)
  })

  it('approver 只读：TESTED 可查看面板但不提供生成按钮', async () => {
    localStorage.clear()
    seedAuthUser('approver')
    installRealMock({
      task: () => testedTask([passedTest], [makePrDescription()]),
      prVersions: () => [makePrDescription()],
    })
    const wrapper = await go('/diff/TASK-9001')
    await openReviewTab(wrapper, 'PR 描述')

    const panel = wrapper.find('.pr-panel')
    expect(panel.exists()).toBe(true)
    expect(panel.text()).toContain('已生成')
    expect(panel.find('.pr-panel .button--secondary').exists()).toBe(true)
    expect(panel.find('.button--primary').exists()).toBe(false)
  })
})
