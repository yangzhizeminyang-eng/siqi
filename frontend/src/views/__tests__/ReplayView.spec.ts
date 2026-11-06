import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import type { AgentTask, ExecutionStep } from '../../types/domain'
import { makeAgentTask } from '../../test/fixtures'
import { setDataSourceMode } from '../../services/dataSourceMode'
import { useWorkspaceStore } from '../../stores/workspace'
import { ApiError } from '../../services/api'
import ReplayView from '../ReplayView.vue'

const { apiRequestMock, messageMocks } = vi.hoisted(() => ({
  apiRequestMock: vi.fn(),
  messageMocks: { error: vi.fn() },
}))

vi.mock('element-plus', () => ({
  ElMessage: { error: messageMocks.error },
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
let activeWrapper: ReturnType<typeof mount> | undefined
type WorkspaceStore = ReturnType<typeof useWorkspaceStore>
// 组件经注入获取的 store 就是注册在 activePinia 上的实例。直接读注册表拿同一实例，
// 绕过 pinia 模块级 activePinia 在用例间残留导致的“返回旧实例”问题
function registeredStore(): WorkspaceStore {
  useWorkspaceStore(activePinia)
  const registry = (activePinia as unknown as { _s: Map<string, WorkspaceStore> })._s
  return registry.get('workspace')!
}

function eventRecord(id: string, eventType: string, summary: string, diffSummary: string[], createdAt = '2026-09-04T09:05:00Z') {
  return {
    id,
    event_type: eventType,
    resource_type: 'task',
    resource_id: 'TASK-9001',
    actor_id: 'system',
    actor_name: 'Agent',
    summary,
    diff_summary: diffSummary,
    created_at: createdAt,
  }
}

// 模拟后端回答阻塞问题后的任务：GENERATED → ANSWERED → REGENERATED 三个审计事件
function answeredReplayTask(): AgentTask {
  return makeAgentTask({
    manual_required: false,
    manual_reason: 'none',
    next_action: 'auto_generate_plan',
    automation_state: 'running',
    events: [
      eventRecord('evt-1', 'ANALYSIS_GENERATED', 'AI 分析完成，存在待确认问题', ['风险等级：high', '存在阻塞问题'], '2026-09-04T09:00:00Z'),
      eventRecord('evt-2', 'ANALYSIS_REGENERATED', '已根据问题回答重新生成分析', ['分析版本：第 2 版'], '2026-09-04T09:06:00Z'),
      eventRecord('evt-3', 'ANALYSIS_ANSWERED', '已回答问题：该改动是否需要保持对旧版本的兼容？', [
        '问题 ID：q_blocking_1',
        '问题类型：阻塞问题',
        '处理方式：提交回答',
        '回答：仅兼容当前主版本。',
        '剩余阻塞问题：0 个',
      ]),
    ],
  })
}

async function mountReplay(path = '/replay/TASK-9001') {
  await router.push(path)
  activeWrapper = mount(ReplayView, {
    global: {
      plugins: [activePinia, router],
      stubs: { StatusTag: true },
    },
  })
  return activeWrapper
}

async function openAudit(wrapper: ReturnType<typeof mount>) {
  const button = wrapper.findAll('button').find((entry) => entry.text().includes('查看全部事件'))
  if (!button) return
  await button.trigger('click')
  await flushPromises()
}

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

describe('ReplayView：直达/刷新从后端读取真实事件回放', () => {
  beforeEach(async () => {
    setDataSourceMode('real')
    activeWrapper?.unmount()
    localStorage.clear()
    activePinia = createPinia()
    setActivePinia(activePinia)
    router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/', component: { template: '<div />' } },
        { path: '/tasks', component: { template: '<div />' } },
        { path: '/replay/:id', component: ReplayView },
      ],
    })
    await router.push('/')
    apiRequestMock.mockReset()
    apiRequestMock.mockResolvedValue(makeAgentTask())
    messageMocks.error.mockClear()
  })

  it('直达访问且 store 为空时拉取任务，渲染 ANALYSIS_ANSWERED 事件完整字段', async () => {
    apiRequestMock.mockResolvedValueOnce(answeredReplayTask())
    const wrapper = await mountReplay()
    await flushPromises()
    await openAudit(wrapper)

    expect(apiRequestMock).toHaveBeenCalledWith('/api/tasks/TASK-9001/replay')
    expect(apiRequestMock).toHaveBeenCalledWith('/api/tasks/TASK-9001')
    expect(wrapper.find('.replay-real__timeline').exists()).toBe(true)

    const answeredNode = wrapper.findAll('.replay-real__audit-summary').find((item) => item.text().includes('已回答问题'))
    expect(answeredNode).toBeDefined()

    await answeredNode!.trigger('click')
    const detailText = wrapper.find('.replay-detail').text()
    expect(wrapper.find('.replay-detail h2').text()).toBe('已回答问题：该改动是否需要保持对旧版本的兼容？')
    for (const expected of [
      '问题 ID：q_blocking_1',
      '问题类型：阻塞问题',
      '处理方式：提交回答',
      '回答：仅兼容当前主版本。',
      '剩余阻塞问题：0 个',
    ]) {
      expect(detailText).toContain(expected)
    }
  })

  it('刷新时 store 只有陈旧任务，仍重新拉取并用真实事件替换', async () => {
    // 陈旧 store：只有旧的 ANALYSIS_GENERATED 事件，没有回答/重生成事件
    const staleTask = makeAgentTask({
      events: [eventRecord('evt-stale', 'ANALYSIS_GENERATED', '陈旧的执行事件：仅有一次分析记录', ['风险等级：high'])],
    })
    // 组件经注入得到的 store 就是注册在 activePinia 上的实例，直接操作同一实例完成“陈旧数据”预置
    registeredStore().createTaskFromApi(staleTask)

    const pending = deferred<AgentTask>()
    apiRequestMock.mockReset()
    apiRequestMock.mockReturnValue(pending.promise)

    const wrapper = await mountReplay()
    await flushPromises()
    await openAudit(wrapper)
    expect(wrapper.text()).toContain('陈旧的执行事件：仅有一次分析记录')

    pending.resolve(answeredReplayTask())
    await flushPromises()

    expect(apiRequestMock).toHaveBeenCalledWith('/api/tasks/TASK-9001/replay')
    expect(apiRequestMock).toHaveBeenCalledWith('/api/tasks/TASK-9001')
    // 真实事件替换陈旧事件
    expect(wrapper.find('.replay-real__timeline').exists()).toBe(true)
    expect(wrapper.text()).not.toContain('陈旧的执行事件：仅有一次分析记录')
    expect(wrapper.text()).toContain('已回答问题：该改动是否需要保持对旧版本的兼容？')
    expect(wrapper.text()).toContain('已根据问题回答重新生成分析')
  })

  it('404 时显示任务不存在，不回退到本地 mock 任务', async () => {
    apiRequestMock.mockRejectedValueOnce(new ApiError('任务不存在', 404))
    const wrapper = await mountReplay()
    await flushPromises()

    expect(wrapper.text()).toContain('任务不存在或无权查看')
    expect(wrapper.text()).not.toContain('修复支付回调重复记账')
    expect(wrapper.find('.replay-timeline').exists()).toBe(false)
    expect(messageMocks.error).not.toHaveBeenCalled()

    const backButton = wrapper.findAll('button').find((button) => button.text().includes('返回任务列表'))
    await backButton!.trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/tasks')
  })

  it('401 时显示登录状态失效，不被误判为任务不存在', async () => {
    apiRequestMock.mockRejectedValueOnce(new ApiError('未认证', 401))
    const wrapper = await mountReplay()
    await flushPromises()

    expect(wrapper.text()).toContain('登录状态已失效')
    expect(wrapper.text()).not.toContain('任务不存在或无权查看')
    expect(messageMocks.error).not.toHaveBeenCalled()
  })

  it('路由切换竞态：A 尚未返回时切到 B，A 返回后不能覆盖 B', async () => {
    const taskA = makeAgentTask({
      id: 'TASK-9001',
      issue_id: 'IDEMOA001',
      title: '任务A标题（旧请求）',
      events: [eventRecord('evt-a', 'ANALYSIS_GENERATED', '任务A的分析完成', ['风险等级：low'])],
    })
    const taskB = makeAgentTask({
      id: 'TASK-9002',
      issue_id: 'IDEMOB002',
      title: '任务B标题（新请求）',
      events: [eventRecord('evt-b', 'ANALYSIS_GENERATED', '任务B的分析完成', ['风险等级：high'], '2026-09-04T10:00:00Z')],
    })
    const a = deferred<AgentTask>()
    const b = deferred<AgentTask>()
    apiRequestMock.mockReset()
    apiRequestMock.mockImplementation((path: string) => (path.includes('TASK-9001') ? a.promise : b.promise))

    const wrapper = await mountReplay()
    await flushPromises()

    // A 尚未返回时切到 B
    await router.push('/replay/TASK-9002')
    await flushPromises()

    b.resolve(taskB)
    await flushPromises()
    await openAudit(wrapper)
    expect(wrapper.text()).toContain('任务B标题（新请求）')

    a.resolve(taskA)
    await flushPromises()

    // 直连两个任务各触发 任务详情 + 回放 两个端点（A 已切走，回放响应被丢弃）
    const paths = apiRequestMock.mock.calls.map((call) => String(call[0]))
    expect(paths).toContain('/api/tasks/TASK-9001')
    expect(paths).toContain('/api/tasks/TASK-9002')
    // A 的响应被丢弃：页面仍显示 B，A 的任务内容不出现
    expect(wrapper.text()).toContain('TASK-9002')
    expect(wrapper.text()).toContain('任务B的分析完成')
    expect(wrapper.text()).not.toContain('任务A标题（旧请求）')
    expect(wrapper.text()).not.toContain('任务A的分析完成')
  })
})

describe('ReplayView：mock 模式直达 demo 任务不走后端', () => {
  beforeEach(async () => {
    setDataSourceMode('mock')
    activeWrapper?.unmount()
    localStorage.clear()
    activePinia = createPinia()
    setActivePinia(activePinia)
    router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/', component: { template: '<div />' } },
        { path: '/tasks', component: { template: '<div />' } },
        { path: '/replay/:id', component: ReplayView },
      ],
    })
    await router.push('/')
    apiRequestMock.mockReset()
    messageMocks.error.mockClear()
  })

  afterEach(() => {
    setDataSourceMode('real')
  })

  it('直达 /replay/TASK-8824 渲染 demo 时间线且不发请求', async () => {
    const wrapper = await mountReplay('/replay/TASK-8824')
    await flushPromises()

    expect(apiRequestMock).not.toHaveBeenCalled()
    expect(wrapper.find('.replay-timeline').exists()).toBe(true)
    expect(wrapper.text()).toContain('优化仓库索引超时重试')
    expect(wrapper.text()).toContain('AI 分析完成')
    expect(wrapper.text()).toContain('生成 Diff')
    expect(wrapper.text()).not.toContain('任务不存在或无权查看')
  })

  it('mock 模式未知任务显示任务不存在，仍不发请求', async () => {
    const wrapper = await mountReplay('/replay/TASK-9001')
    await flushPromises()

    expect(apiRequestMock).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('任务不存在或无权查看')
    expect(messageMocks.error).not.toHaveBeenCalled()
  })
})

describe('ReplayView：Diff 生成/失败事件在回放时间线显著标记', () => {
  beforeEach(async () => {
    setDataSourceMode('real')
    activeWrapper?.unmount()
    localStorage.clear()
    activePinia = createPinia()
    setActivePinia(activePinia)
    router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/', component: { template: '<div />' } },
        { path: '/tasks', component: { template: '<div />' } },
        { path: '/replay/:id', component: ReplayView },
      ],
    })
    await router.push('/')
    apiRequestMock.mockReset()
    messageMocks.error.mockClear()
  })

  it('DIFF_GENERATED 事件带「Diff 生成 + 演示兜底」标记，审计信息保留版本与兜底原因', async () => {
    const diffTask = makeAgentTask({
      status: 'DIFF_GENERATED',
      automation_state: 'blocked',
      manual_required: false,
      manual_reason: 'none',
      next_action: 'auto_run_test',
      events: [
        eventRecord('evt-approve', 'PLAN_APPROVED', '计划审批通过', ['风险等级：high'], '2026-09-04T09:20:00Z'),
        eventRecord('evt-diff', 'DIFF_GENERATED', '已生成 Diff v1', [
          '任务 ID：TASK-9001',
          '计划 ID：PLAN-9001-1',
          '计划版本：v1',
          '执行步骤：步骤 2：补充重复推送回归测试',
          '生成模式：DEMO_FALLBACK',
          '演示兜底：是',
          '变更文件：3 个',
          '兜底原因：MVP 未接入真实代码执行环境（Docker Runner）',
        ], '2026-09-04T09:30:00Z'),
      ],
    })
    apiRequestMock.mockResolvedValueOnce(diffTask)
    const wrapper = await mountReplay()
    await flushPromises()
    await openAudit(wrapper)

    expect(wrapper.find('.replay-chip--diff').text()).toContain('Diff 生成')
    expect(wrapper.find('.replay-chip--demo').text()).toContain('演示兜底')
    // 节点输出区能看到版本、模式与兜底原因
    expect(wrapper.text()).toContain('计划版本：v1')
    expect(wrapper.text()).toContain('演示兜底：是')
    expect(wrapper.text()).toContain('兜底原因：MVP 未接入真实代码执行环境（Docker Runner）')
    // 生成成功事件渲染「Diff 生成 + 演示兜底」两个标记
    expect(wrapper.findAll('.replay-chip--diff')).toHaveLength(1)
    expect(wrapper.findAll('.replay-chip--demo')).toHaveLength(1)
  })

  it('DIFF_GENERATION_FAILED 事件标记失败，任务 FAILED 不被误标为演示兜底', async () => {
    const failedTask = makeAgentTask({
      status: 'FAILED',
      failure_node: 'Diff 生成',
      failure_reason: '真实执行失败：clone 超时（模拟）',
      manual_required: true,
      manual_reason: 'diff_failed',
      next_action: 'retry_or_cancel',
      events: [
        eventRecord('evt-approve', 'PLAN_APPROVED', '计划审批通过', ['风险等级：high'], '2026-09-04T09:20:00Z'),
        eventRecord('evt-diff-fail', 'DIFF_GENERATION_FAILED', 'Diff 生成失败', [
          '任务 ID：TASK-9001',
          '计划版本：v1',
          '生成模式：DEMO_FALLBACK',
          '演示兜底：是',
          '兜底原因：演示兜底产物构造失败',
          '真实执行失败原因：真实执行失败：clone 超时（模拟）',
        ], '2026-09-04T09:30:00Z'),
        eventRecord('evt-failed', 'TASK_FAILED', '任务失败：Diff 生成节点', ['失败节点：Diff 生成'], '2026-09-04T09:30:05Z'),
      ],
    })
    apiRequestMock.mockResolvedValueOnce(failedTask)
    const wrapper = await mountReplay()
    await flushPromises()
    await openAudit(wrapper)

    const failedChip = wrapper.find('.replay-chip--fail')
    expect(failedChip.exists()).toBe(true)
    expect(failedChip.text()).toContain('Diff 生成失败')
    // 失败事件只标记失败，不标记成功生成的「演示兜底」状态
    expect(wrapper.find('.replay-chip--diff').exists()).toBe(false)
    expect(wrapper.find('.replay-chip--demo').exists()).toBe(false)

    // 点击失败节点后，详情区完整展示审计字段（含真实执行失败原因）
    const failedItem = wrapper.findAll('.replay-real__audit-summary').find((item) => item.text().includes('Diff 生成失败'))
    await failedItem!.trigger('click')
    await flushPromises()
    expect(wrapper.find('.replay-detail').text()).toContain('真实执行失败原因：真实执行失败：clone 超时（模拟）')
  })
})

describe('ReplayView：测试执行事件在回放时间线标记（DEV-012）', () => {
  beforeEach(async () => {
    setDataSourceMode('real')
    activeWrapper?.unmount()
    localStorage.clear()
    activePinia = createPinia()
    setActivePinia(activePinia)
    router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/', component: { template: '<div />' } },
        { path: '/tasks', component: { template: '<div />' } },
        { path: '/replay/:id', component: ReplayView },
      ],
    })
    await router.push('/')
    apiRequestMock.mockReset()
    messageMocks.error.mockClear()
  })

  it('TEST_RUN_STARTED / TEST_RUN_PASSED 事件标记「测试执行/测试通过」，点击可见完整审计信息', async () => {
    const testedTask = makeAgentTask({
      status: 'TESTED',
      automation_state: 'blocked',
      manual_required: false,
      manual_reason: 'none',
      next_action: 'watch_test_result',
      events: [
        eventRecord('evt-approve', 'PLAN_APPROVED', '计划审批通过', ['风险等级：high'], '2026-09-04T09:20:00Z'),
        eventRecord('evt-diff', 'DIFF_GENERATED', '已生成 Diff v1', ['任务 ID：TASK-9001', '计划版本：v1'], '2026-09-04T09:30:00Z'),
        eventRecord('evt-test-start', 'TEST_RUN_STARTED', '开始执行测试（第 1 次尝试）', [
          '任务 ID：TASK-9001',
          'Diff 产物：DIFF-9001-v1（计划 v1）',
          '执行模式：mocked_runner',
          '尝试次数：第 1 次',
          '执行命令：pytest tests/',
        ], '2026-09-04T10:00:00Z'),
        eventRecord('evt-test-pass', 'TEST_RUN_PASSED', '测试通过（第 1 次尝试）', [
          '任务 ID：TASK-9001',
          'Diff 产物：DIFF-9001-v1（计划 v1）',
          '执行模式：mocked_runner',
          '尝试次数：第 1 次',
          '执行命令：pytest tests/',
          '退出码：0',
          '耗时：1.02s',
        ], '2026-09-04T10:00:02Z'),
      ],
    })
    apiRequestMock.mockResolvedValueOnce(testedTask)
    const wrapper = await mountReplay()
    await flushPromises()
    await openAudit(wrapper)

    expect(wrapper.find('.replay-chip--running').text()).toBe('测试执行')
    expect(wrapper.find('.replay-chip--pass').text()).toBe('测试通过')

    const passItem = wrapper.findAll('.replay-real__audit-summary').find((item) => item.text().includes('测试通过（第 1 次尝试）'))
    await passItem!.trigger('click')
    await flushPromises()
    const detailText = wrapper.find('.replay-detail').text()
    expect(detailText).toContain('测试通过（第 1 次尝试）')
    expect(detailText).toContain('Diff 产物：DIFF-9001-v1（计划 v1）')
    expect(detailText).toContain('执行命令：pytest tests/')
    expect(detailText).toContain('退出码：0')
    expect(detailText).toContain('耗时：1.02s')
  })

  it('TEST_RUN_FAILED / TEST_BLOCKED 事件标记「测试失败/测试拦截」，被拦截审计信息可查', async () => {
    const failedTask = makeAgentTask({
      status: 'TEST_FAILED',
      automation_state: 'blocked',
      manual_required: true,
      manual_reason: 'test_failed',
      next_action: 'retry_test',
      events: [
        eventRecord('evt-approve', 'PLAN_APPROVED', '计划审批通过', ['风险等级：high'], '2026-09-04T09:20:00Z'),
        eventRecord('evt-diff', 'DIFF_GENERATED', '已生成 Diff v1', ['任务 ID：TASK-9001', '计划版本：v1'], '2026-09-04T09:30:00Z'),
        eventRecord('evt-block', 'TEST_BLOCKED', '测试命令存在安全风险，已被拦截', [
          '任务 ID：TASK-9001',
          'Diff 产物：DIFF-9001-v1（计划 v1）',
          '执行模式：mocked_runner',
          '尝试次数：第 1 次',
          '执行命令：rm -rf /',
          '拦截原因：命令包含危险规则 rm -rf /',
          '可重试提示：请调整仓库测试命令后重新触发',
        ], '2026-09-04T10:01:00Z'),
        eventRecord('evt-test-fail', 'TEST_RUN_FAILED', '测试失败（第 1 次尝试）', [
          '任务 ID：TASK-9001',
          'Diff 产物：DIFF-9001-v1（计划 v1）',
          '执行模式：mocked_runner',
          '尝试次数：第 1 次',
          '执行命令：pytest tests/',
          '退出码：1',
          '耗时：3.14s',
          '失败原因：AssertionError: 重复入账',
          '剩余重试次数：1 次',
        ], '2026-09-04T10:05:00Z'),
      ],
    })
    apiRequestMock.mockResolvedValueOnce(failedTask)
    const wrapper = await mountReplay()
    await flushPromises()
    await openAudit(wrapper)

    const failChip = wrapper.findAll('.replay-chip--fail').find((chip) => chip.text() === '测试失败')
    expect(failChip).toBeDefined()
    expect(wrapper.find('.replay-chip--blocked').text()).toBe('测试拦截')

    const blockedItem = wrapper.findAll('.replay-real__audit-summary').find((item) => item.text().includes('测试命令存在安全风险'))
    await blockedItem!.trigger('click')
    await flushPromises()
    expect(wrapper.find('.replay-detail').text()).toContain('拦截原因：命令包含危险规则 rm -rf /')
  })
})

describe('ReplayView：PR 描述生成事件在回放时间线标记（DEV-013）', () => {
  beforeEach(async () => {
    setDataSourceMode('real')
    activeWrapper?.unmount()
    localStorage.clear()
    activePinia = createPinia()
    setActivePinia(activePinia)
    router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/', component: { template: '<div />' } },
        { path: '/tasks', component: { template: '<div />' } },
        { path: '/replay/:id', component: ReplayView },
      ],
    })
    await router.push('/')
    apiRequestMock.mockReset()
    messageMocks.error.mockClear()
  })

  it('PR_DESC_FAILED / PR_DESC_GENERATED 事件分别标记失败与成功，审计信息含版本与关联产物', async () => {
    const prTask = makeAgentTask({
      status: 'TESTED',
      automation_state: 'blocked',
      manual_required: false,
      manual_reason: 'none',
      next_action: 'watch_test_result',
      events: [
        eventRecord('evt-approve', 'PLAN_APPROVED', '计划审批通过', ['风险等级：high'], '2026-09-04T09:20:00Z'),
        eventRecord('evt-diff', 'DIFF_GENERATED', '已生成 Diff v1', ['任务 ID：TASK-9001', '计划版本：v1'], '2026-09-04T09:30:00Z'),
        eventRecord('evt-test-pass', 'TEST_RUN_PASSED', '测试通过（第 1 次尝试）', ['任务 ID：TASK-9001', 'Diff 产物：DIFF-9001-v1（计划 v1）', '退出码：0'], '2026-09-04T10:00:02Z'),
        eventRecord('evt-pr-fail', 'PR_DESC_FAILED', 'PR 描述生成失败（第 1 版）', [
          '任务 ID：TASK-9001',
          '生成人：周航（user_developer）',
          '版本：第 1 版',
          '关联计划：PLAN-9001-1（v1）',
          'Diff 产物：DIFF-9001-v1',
          '测试记录：TEST-9001-1',
          '失败原因：模拟生成器异常',
        ], '2026-09-04T10:05:00Z'),
        eventRecord('evt-pr-ok', 'PR_DESC_GENERATED', 'PR 描述已生成（第 2 版），可复制全文', [
          '任务 ID：TASK-9001',
          '生成人：周航（user_developer）',
          '版本：第 2 版',
          '关联计划：PLAN-9001-1（v1）',
          'Diff 产物：DIFF-9001-v1',
          '测试记录：TEST-9001-1',
        ], '2026-09-04T10:06:00Z'),
      ],
    })
    apiRequestMock.mockResolvedValueOnce(prTask)
    const wrapper = await mountReplay()
    await flushPromises()
    await openAudit(wrapper)

    expect(wrapper.find('.replay-chip--pr').text()).toBe('PR 描述生成')
    const failChip = wrapper.findAll('.replay-chip--fail').find((chip) => chip.text() === 'PR 描述失败')
    expect(failChip).toBeDefined()

    const okItem = wrapper.findAll('.replay-real__audit-summary').find((item) => item.text().includes('PR 描述已生成（第 2 版）'))
    await okItem!.trigger('click')
    await flushPromises()
    const detailText = wrapper.find('.replay-detail').text()
    expect(detailText).toContain('版本：第 2 版')
    expect(detailText).toContain('关联计划：PLAN-9001-1（v1）')
    expect(detailText).toContain('Diff 产物：DIFF-9001-v1')
    expect(detailText).toContain('测试记录：TEST-9001-1')
  })
})

describe('ReplayView：执行步骤展开与关联跳转（DEV-014）', () => {
  beforeEach(async () => {
    setDataSourceMode('real')
    activeWrapper?.unmount()
    localStorage.clear()
    activePinia = createPinia()
    setActivePinia(activePinia)
    router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/', component: { template: '<div />' } },
        { path: '/tasks', component: { template: '<div />' } },
        { path: '/replay/:id', component: ReplayView },
      ],
    })
    await router.push('/')
    apiRequestMock.mockReset()
    messageMocks.error.mockClear()
  })

  function stepRecord(
    id: string,
    eventId: string,
    node: string,
    status: ExecutionStep['status'],
    overrides: Partial<ExecutionStep> = {},
  ) {
    return {
      id,
      task_id: 'TASK-9001',
      event_id: eventId,
      node,
      title: `${node} 节点执行`,
      status,
      input: null,
      output: null,
      duration_ms: null,
      failure_reason: null,
      blocked_reason: null,
      created_at: '2026-09-04T10:00:02Z',
      ...overrides,
    }
  }

  async function clickTimelineItem(wrapper: ReturnType<typeof mount>, titlePart: string) {
    await openAudit(wrapper)
    const item = wrapper.findAll('.replay-real__audit-summary').find((entry) => entry.text().includes(titlePart))
      ?? wrapper.findAll('.replay-steps__item').find((entry) => entry.text().includes(titlePart))
    await item!.trigger('click')
    await flushPromises()
  }

  it('TEST_RUN_PASSED 事件带执行步骤，展开后可见输入/输出/耗时/完成标签', async () => {
    const passedTask = makeAgentTask({
      status: 'TESTED',
      automation_state: 'blocked',
      manual_required: false,
      manual_reason: 'none',
      next_action: 'watch_test_result',
      events: [
        eventRecord('evt-approve', 'PLAN_APPROVED', '计划审批通过', ['风险等级：high'], '2026-09-04T09:20:00Z'),
        eventRecord('evt-diff', 'DIFF_GENERATED', '已生成 Diff v1', ['任务 ID：TASK-9001', '计划版本：v1'], '2026-09-04T09:30:00Z'),
        eventRecord('evt-test', 'TEST_RUN_PASSED', '测试通过（第 1 次尝试）', [
          '任务 ID：TASK-9001',
          'Diff 产物：DIFF-9001-v1（计划 v1）',
          '退出码：0',
          '耗时：1.02s',
        ], '2026-09-04T10:00:02Z'),
      ],
      execution_steps: [
        stepRecord('step-test-1', 'evt-test', 'test_execution', 'completed', {
          title: '执行仓库测试（第 1 次尝试）',
          input: 'Diff 产物：DIFF-9001-v1（计划 v1）\n执行命令：pytest tests/',
          output: '退出码：0，测试通过',
          duration_ms: 1020,
          created_at: '2026-09-04T10:00:02Z',
        }),
      ],
    })
    apiRequestMock.mockResolvedValueOnce(passedTask)
    const wrapper = await mountReplay()
    await flushPromises()

    await clickTimelineItem(wrapper, '测试通过（第 1 次尝试）')
    const detail = wrapper.find('.replay-detail')
    expect(detail.text()).toContain('执行步骤')
    const stepCard = wrapper.find('.step-card')
    expect(stepCard.exists()).toBe(true)
    expect(stepCard.text()).toContain('test_execution')
    expect(stepCard.text()).toContain('执行仓库测试（第 1 次尝试）')
    expect(stepCard.find('.step-status.is-completed').text()).toBe('完成')
    expect(stepCard.text()).toContain('耗时 1.0s')
    expect(stepCard.text()).toContain('Diff 产物：DIFF-9001-v1（计划 v1）')
    expect(stepCard.text()).toContain('退出码：0，测试通过')
    // 输入/输出以独立小节出现
    expect(stepCard.findAll('.step-card__pre')).toHaveLength(2)
  })

  it('TEST_RUN_FAILED 失败节点展开显示红色失败原因', async () => {
    const failedTask = makeAgentTask({
      status: 'TEST_FAILED',
      automation_state: 'blocked',
      manual_required: true,
      manual_reason: 'test_failed',
      next_action: 'retry_test',
      events: [
        eventRecord('evt-approve', 'PLAN_APPROVED', '计划审批通过', ['风险等级：high'], '2026-09-04T09:20:00Z'),
        eventRecord('evt-diff', 'DIFF_GENERATED', '已生成 Diff v1', ['任务 ID：TASK-9001', '计划版本：v1'], '2026-09-04T09:30:00Z'),
        eventRecord('evt-test-fail', 'TEST_RUN_FAILED', '测试失败（第 1 次尝试）', [
          '任务 ID：TASK-9001',
          '退出码：1',
          '失败原因：AssertionError: 重复入账',
        ], '2026-09-04T10:05:00Z'),
      ],
      execution_steps: [
        stepRecord('step-test-fail', 'evt-test-fail', 'test_execution', 'failed', {
          title: '执行仓库测试（第 1 次尝试）',
          input: '执行命令：pytest tests/',
          output: '退出码：1',
          duration_ms: 3140,
          failure_reason: 'AssertionError: 重复入账',
          created_at: '2026-09-04T10:05:00Z',
        }),
      ],
    })
    apiRequestMock.mockResolvedValueOnce(failedTask)
    const wrapper = await mountReplay()
    await flushPromises()

    await clickTimelineItem(wrapper, '测试失败（第 1 次尝试）')
    const stepCard = wrapper.find('.step-card.step-card--failed')
    expect(stepCard.exists()).toBe(true)
    expect(stepCard.find('.step-status.is-failed').text()).toBe('失败')
    const failedNote = stepCard.find('.detail-test-note--failed')
    expect(failedNote.exists()).toBe(true)
    expect(failedNote.text()).toContain('失败原因：AssertionError: 重复入账')
    expect(stepCard.text()).toContain('耗时 3.1s')
  })

  it('TEST_BLOCKED 拦截节点展开显示拦截原因与拦截标签', async () => {
    const blockedTask = makeAgentTask({
      status: 'DIFF_GENERATED',
      automation_state: 'blocked',
      manual_required: true,
      manual_reason: 'command_blocked',
      next_action: 'retry_test',
      events: [
        eventRecord('evt-approve', 'PLAN_APPROVED', '计划审批通过', ['风险等级：high'], '2026-09-04T09:20:00Z'),
        eventRecord('evt-diff', 'DIFF_GENERATED', '已生成 Diff v1', ['任务 ID：TASK-9001', '计划版本：v1'], '2026-09-04T09:30:00Z'),
        eventRecord('evt-block', 'TEST_BLOCKED', '测试命令存在安全风险，已被拦截', [
          '任务 ID：TASK-9001',
          '执行命令：rm -rf /',
          '拦截原因：命令包含危险规则 rm -rf /',
        ], '2026-09-04T10:01:00Z'),
      ],
      execution_steps: [
        stepRecord('step-block', 'evt-block', 'test_execution', 'blocked', {
          title: '执行仓库测试（第 1 次尝试）',
          input: '执行命令：rm -rf /',
          blocked_reason: '命令包含危险规则 rm -rf /',
          created_at: '2026-09-04T10:01:00Z',
        }),
      ],
    })
    apiRequestMock.mockResolvedValueOnce(blockedTask)
    const wrapper = await mountReplay()
    await flushPromises()

    await clickTimelineItem(wrapper, '测试命令存在安全风险')
    const stepCard = wrapper.find('.step-card.step-card--blocked')
    expect(stepCard.exists()).toBe(true)
    expect(stepCard.find('.step-status.is-blocked').text()).toBe('拦截')
    expect(stepCard.find('.detail-test-note--blocked').text()).toContain('拦截原因：命令包含危险规则 rm -rf /')
    // blocked 节点不显示耗时（无实测值，UI 不显示空值）
    expect(stepCard.text()).not.toContain('耗时')
  })

  it('审批/计划事件可跳任务详情，Diff/测试/PR 事件可跳 Diff 页', async () => {
    const fullTask = makeAgentTask({
      status: 'TESTED',
      automation_state: 'blocked',
      manual_required: false,
      manual_reason: 'none',
      next_action: 'watch_test_result',
      events: [
        eventRecord('evt-approve', 'PLAN_APPROVED', '计划审批通过', ['风险等级：high'], '2026-09-04T09:20:00Z'),
        eventRecord('evt-diff', 'DIFF_GENERATED', '已生成 Diff v1', ['任务 ID：TASK-9001', '计划版本：v1'], '2026-09-04T09:30:00Z'),
        eventRecord('evt-test', 'TEST_RUN_PASSED', '测试通过（第 1 次尝试）', ['退出码：0'], '2026-09-04T10:00:02Z'),
        eventRecord('evt-pr', 'PR_DESC_GENERATED', 'PR 描述已生成（第 1 版），可复制全文', ['版本：第 1 版'], '2026-09-04T10:06:00Z'),
      ],
      execution_steps: [
        stepRecord('step-test-1', 'evt-test', 'test_execution', 'completed', {
          input: '执行命令：pytest tests/',
          output: '退出码：0，测试通过',
          duration_ms: 1020,
        }),
        stepRecord('step-pr-1', 'evt-pr', 'pr_description', 'completed', {
          input: '关联计划：PLAN-9001-1（v1）',
          output: '六段式描述已生成（6/6 段有内容），可复制全文',
        }),
      ],
    })
    apiRequestMock.mockResolvedValueOnce(fullTask)
    const wrapper = await mountReplay()
    await flushPromises()

    await clickTimelineItem(wrapper, '计划审批通过')
    expect(wrapper.find('.replay-jump').attributes('href')).toBe('/tasks/TASK-9001')
    expect(wrapper.find('.replay-jump').text()).toContain('查看任务详情（计划/审批）')

    await clickTimelineItem(wrapper, '已生成 Diff v1')
    expect(wrapper.find('.replay-jump').attributes('href')).toBe('/diff/TASK-9001')
    expect(wrapper.find('.replay-jump').text()).toContain('查看 Diff')

    await clickTimelineItem(wrapper, '测试通过（第 1 次尝试）')
    expect(wrapper.find('.replay-jump').attributes('href')).toBe('/diff/TASK-9001')
    expect(wrapper.find('.replay-jump').text()).toContain('查看测试记录')

    await clickTimelineItem(wrapper, 'PR 描述已生成（第 1 版）')
    expect(wrapper.find('.replay-jump').attributes('href')).toBe('/diff/TASK-9001')
    expect(wrapper.find('.replay-jump').text()).toContain('查看 PR 描述')
  })

  it('无执行步骤/旧数据事件不渲染步骤区与跳转链接，时间线不受破坏', async () => {
    const legacyTask = makeAgentTask({
      status: 'FAILED',
      failure_node: '上下文拉取',
      failure_reason: '上下文聚合失败',
      manual_required: true,
      manual_reason: 'context_missing',
      next_action: 'retry_or_cancel',
      events: [
        eventRecord('evt-created', 'TASK_CREATED', '任务已创建', ['创建人：周航'], '2026-09-04T08:58:00Z'),
        eventRecord('evt-fetch', 'ISSUE_FETCHED', 'Issue 已拉取', ['标题：修复支付回调重复记账'], '2026-09-04T08:59:00Z'),
        eventRecord('evt-failed', 'TASK_FAILED', '任务失败：上下文拉取', ['失败节点：上下文拉取'], '2026-09-04T09:00:00Z'),
      ],
      execution_steps: [],
    })
    apiRequestMock.mockResolvedValueOnce(legacyTask)
    const wrapper = await mountReplay()
    await flushPromises()
    await openAudit(wrapper)

    // 旧数据：事件时间线正常渲染
    expect(wrapper.findAll('.replay-real__audit-summary')).toHaveLength(3)

    // 点击 TASK_CREATED（人工/进度事件）：无执行步骤区、无跳转链接
    await clickTimelineItem(wrapper, '任务已创建')
    expect(wrapper.find('.replay-block--steps').exists()).toBe(false)
    expect(wrapper.find('.step-card').exists()).toBe(false)
    expect(wrapper.find('.replay-jump').exists()).toBe(false)

    // 点击 ISSUE_FETCHED：事件类型不匹配任何跳转规则，仍不渲染链接
    await clickTimelineItem(wrapper, 'Issue 已拉取')
    expect(wrapper.find('.replay-block--steps').exists()).toBe(false)
    expect(wrapper.find('.replay-jump').exists()).toBe(false)
    // 审计信息等既有区块保留
    expect(wrapper.find('.replay-detail').text()).toContain('审计信息')
  })

  it('mock 演示任务（无事件类型/无步骤）不渲染执行步骤与跳转，回放照常', async () => {
    setDataSourceMode('mock')
    apiRequestMock.mockReset()
    const wrapper = await mountReplay('/replay/TASK-8824')
    await flushPromises()

    expect(wrapper.find('.replay-timeline').exists()).toBe(true)
    expect(wrapper.find('.replay-block--steps').exists()).toBe(false)
    expect(wrapper.find('.replay-jump').exists()).toBe(false)
    expect(apiRequestMock).not.toHaveBeenCalled()
  })
})
