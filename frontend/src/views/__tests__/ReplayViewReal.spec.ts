import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import type { TaskReplay } from '../../types/domain'
import { makeAgentTask } from '../../test/fixtures'
import { setDataSourceMode } from '../../services/dataSourceMode'
import { ApiError } from '../../services/api'
import ReplayView from '../ReplayView.vue'

const { apiRequestMock, messageMocks } = vi.hoisted(() => ({
  apiRequestMock: vi.fn(),
  messageMocks: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}))

vi.mock('element-plus', () => ({
  ElMessage: { success: messageMocks.success, error: messageMocks.error, info: messageMocks.info },
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
let wrapper: ReturnType<typeof mount> | undefined

const replayPayload: TaskReplay = {
  task_id: 'TASK-9001',
  generated_at: '2026-09-09T00:00:00Z',
  timeline: [
    {
      id: 'evt-ok',
      event_type: 'DIFF_GENERATED',
      occurred_at: '2026-09-09T00:00:01Z',
      actor_id: 'agent-system',
      actor_name: 'Agent 自动化',
      actor_type: 'system',
      status: 'completed',
      node: 'generate_diff',
      message: '生成 Diff',
      failure_reason: null,
      retry_count: 0,
      metadata: {},
      relations: { task_id: 'TASK-9001', plan_id: 'P-1', plan_version: '1' },
    },
    {
      id: 'evt-fail',
      event_type: 'TEST_RUN_FAILED',
      occurred_at: '2026-09-09T00:00:02Z',
      actor_id: 'agent-system',
      actor_name: 'Agent 自动化',
      actor_type: 'worker',
      status: 'failed',
      node: 'run_test',
      message: '测试失败',
      failure_reason: '断言未通过',
      retry_count: 1,
      metadata: { execution_env: '正式流程' },
      relations: { task_id: 'TASK-9001', test_run_id: 'T-1' },
    },
  ],
  artifacts: { plans: [], diffs: [], test_runs: [], pr_descriptions: [], comments: [] },
  summary: { text: '# 任务回放摘要 TASK-9001\n测试失败', source: 'deterministic', input_fingerprint: 'fp-1', generated_at: '2026-09-09T00:00:00Z' },
}

function makeStatusTagStub() {
  return { template: '<span class="status-tag"><slot /></span>' }
}

async function mountReplay() {
  const statusTag = makeStatusTagStub()
  const component = mount(ReplayView, {
    global: {
      plugins: [router, activePinia],
      stubs: { StatusTag: statusTag, EmptyPanel: { template: '<div class="empty-panel-stub"><slot /></div>' } },
    },
  })
  wrapper = component
  await flushPromises()
  return component
}

beforeEach(async () => {
  setDataSourceMode('real')
  wrapper?.unmount()
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
  messageMocks.success.mockClear(); messageMocks.error.mockClear(); messageMocks.info.mockClear()
})

afterEach(() => {
  wrapper?.unmount()
})

describe('DEV-020 ReplayView 真实 DB 回放', () => {
  it('real 模式默认渲染回放概览，完整事件按需查看', async () => {
    const task = makeAgentTask({ id: 'TASK-9001' })
    apiRequestMock.mockImplementation((path: string) => {
      if (path.includes('/replay')) return Promise.resolve(replayPayload)
      return Promise.resolve(task)
    })
    await router.push('/replay/TASK-9001')
    const el = await mountReplay()

    const replayCall = apiRequestMock.mock.calls.find((c) => String(c[0]).includes('/replay'))
    expect(replayCall).toBeTruthy()
    expect(el.text()).toContain('存在阻塞项')
    expect(el.text()).toContain('失败节点')
    expect(el.text()).toContain('审计事件')
    expect(el.text()).toContain('阶段')
    expect(el.text()).toContain('最终测试结论')
    expect(el.text()).toContain('测试失败')
    expect(el.find('.replay-real__timeline').exists()).toBe(false)

    const viewAll = el.findAll('button').find((button) => button.text().includes('查看全部事件'))
    expect(viewAll).toBeDefined()
    await viewAll!.trigger('click')
    await flushPromises()

    expect(el.find('.replay-real__timeline').exists()).toBe(true)
    expect(el.text()).toContain('生成 Diff')
    expect(el.text()).toContain('TEST_RUN_FAILED')
  })

  it('失败节点在完整审计中默认展开显示失败原因与重试', async () => {
    const task = makeAgentTask({ id: 'TASK-9001' })
    apiRequestMock.mockImplementation((path: string) => {
      if (path.includes('/replay')) return Promise.resolve(replayPayload)
      return Promise.resolve(task)
    })
    await router.push('/replay/TASK-9001')
    const el = await mountReplay()

    const viewAll = el.findAll('button').find((button) => button.text().includes('查看全部事件'))
    await viewAll!.trigger('click')
    await flushPromises()

    const failedEvent = el.find('.replay-real__audit-event.is-failed')
    expect(failedEvent.attributes('open')).toBeDefined()
    expect(el.text()).toContain('失败原因：断言未通过')
    expect(el.text()).toContain('重试次数：1')
  })

  it('复制摘要调用 clipboard 并提示成功', async () => {
    const task = makeAgentTask({ id: 'TASK-9001' })
    apiRequestMock.mockImplementation((path: string) => {
      if (path.includes('/replay')) return Promise.resolve(replayPayload)
      return Promise.resolve(task)
    })
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    })
    await router.push('/replay/TASK-9001')
    const el = await mountReplay()

    const copyBtn = el.findAll('button').find((b) => b.text().includes('复制摘要'))
    expect(copyBtn).toBeDefined()
    await copyBtn!.trigger('click')
    expect(writeText).toHaveBeenCalledWith('# 任务回放摘要 TASK-9001\n测试失败')
    expect(messageMocks.success).toHaveBeenCalled()
  })

  it('/replay 失败时展示明确的错误提示', async () => {
    const task = makeAgentTask({ id: 'TASK-9001' })
    apiRequestMock.mockImplementation((path: string) => {
      if (path.includes('/replay')) return Promise.reject(new ApiError('回放失败', 500))
      return Promise.resolve(task)
    })
    await router.push('/replay/TASK-9001')
    const el = await mountReplay()
    expect(el.text()).toContain('回放失败')
  })

  it('不使用静态 dashboard 事件作为数据源', async () => {
    // real 面板的事件来自 /replay 的 timeline，而非 dashboardSummary.events
    const task = makeAgentTask({ id: 'TASK-9001' })
    apiRequestMock.mockImplementation((path: string) => {
      if (path.includes('/replay')) return Promise.resolve(replayPayload)
      return Promise.resolve(task)
    })
    await router.push('/replay/TASK-9001')
    const el = await mountReplay()
    const viewAll = el.findAll('button').find((button) => button.text().includes('查看全部事件'))
    await viewAll!.trigger('click')
    expect(el.text()).toContain('生成 Diff')
  })

  it('完整审计支持只看异常和只看人工操作筛选', async () => {
    const task = makeAgentTask({ id: 'TASK-9001' })
    const payload: TaskReplay = {
      ...replayPayload,
      timeline: [
        ...replayPayload.timeline,
        {
          id: 'evt-manual',
          event_type: 'PLAN_APPROVED',
          occurred_at: '2026-09-09T00:00:03Z',
          actor_id: 'user-1',
          actor_name: '周航',
          actor_type: 'user',
          status: 'completed',
          node: 'plan_approval',
          message: '人工审批通过计划',
          failure_reason: null,
          retry_count: 0,
          metadata: {},
          relations: { task_id: 'TASK-9001' },
        },
      ],
    }
    apiRequestMock.mockImplementation((path: string) => {
      if (path.includes('/replay')) return Promise.resolve(payload)
      return Promise.resolve(task)
    })
    await router.push('/replay/TASK-9001')
    const el = await mountReplay()
    const viewAll = el.findAll('button').find((button) => button.text().includes('查看全部事件'))
    await viewAll!.trigger('click')
    await flushPromises()

    const anomalyToggle = el.findAll('button').find((button) => button.text().includes('只看异常'))
    await anomalyToggle!.trigger('click')
    expect(el.text()).toContain('测试失败')
    expect(el.text()).not.toContain('人工审批通过计划')

    await anomalyToggle!.trigger('click')
    const manualToggle = el.findAll('button').find((button) => button.text().includes('只看人工操作'))
    await manualToggle!.trigger('click')
    expect(el.text()).toContain('人工审批通过计划')
    expect(el.text()).not.toContain('生成 Diff')
  })
})
