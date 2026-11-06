import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { RouterLinkStub, flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import type { AgentTask } from '../../types/domain'
import { makeAgentTask, makeAnalysis, makeDiffArtifact, makeModificationPlan, makePrDescription, makeTestRun } from '../../test/fixtures'
import { setDataSourceMode } from '../../services/dataSourceMode'
import { useWorkspaceStore } from '../../stores/workspace'
import { ApiError } from '../../services/api'
import TaskDetailView from '../TaskDetailView.vue'

const { apiRequestMock, messageMocks, routeState, routerPushMock } = vi.hoisted(() => ({
  apiRequestMock: vi.fn(),
  messageMocks: {
    success: vi.fn(),
    error: vi.fn(),
    closeAll: vi.fn(),
    confirm: vi.fn(),
  },
  routeState: { params: { id: 'TASK-9001' } as Record<string, string | string[]> },
  routerPushMock: vi.fn(),
}))

vi.mock('vue-router', () => ({
  useRoute: () => routeState,
  useRouter: () => ({ push: routerPushMock }),
}))

vi.mock('element-plus', () => ({
  ElMessage: {
    success: messageMocks.success,
    error: messageMocks.error,
    closeAll: messageMocks.closeAll,
  },
  ElMessageBox: { confirm: messageMocks.confirm },
}))

vi.mock('../../stores/auth', () => ({
  useAuthStore: () => ({ hasPermission: () => true }),
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

function answeredTask(): AgentTask {
  return makeAgentTask({
    manual_required: false,
    manual_reason: 'none',
    next_action: 'auto_generate_plan',
    automation_state: 'running',
    analysis: makeAnalysis({
      revision: 2,
      questions: [
        {
          id: 'q_blocking_1',
          question: '该改动是否需要保持对旧版本的兼容？',
          reason: 'Issue 文本包含「兼容」表述。',
          blocking: true,
          default_answer: null,
          answer: '仅兼容当前主版本。',
          answered: true,
        },
      ],
    }),
  })
}

function mountDetail() {
  return mount(TaskDetailView, {
    global: {
      plugins: [activePinia],
      stubs: {
        RouterLink: RouterLinkStub,
        StatusTag: true,
        EmptyPanel: { props: ['title', 'description', 'action'], template: '<div class="empty-panel-stub"><h3>{{ title }}</h3><p>{{ description }}</p><button v-if="action">{{ action }}</button></div>' },
      },
    },
  })
}

describe('TaskDetailView：AI 分析详情与问题确认交互', () => {
  beforeEach(() => {
    // 本组用例以本地任务为准，mock 模式避免真实请求（与 .env 默认无关，显式声明）
    setDataSourceMode('mock')
    localStorage.clear()
    activePinia = createPinia()
    setActivePinia(activePinia)
    apiRequestMock.mockReset()
    apiRequestMock.mockResolvedValue(makeAgentTask())
    messageMocks.success.mockClear()
    messageMocks.error.mockClear()
    messageMocks.closeAll.mockClear()
    routeState.params.id = 'TASK-9001'
  })

  it('渲染完整分析详情、来源引用与注入警示，阻塞与非阻塞问题均提供三种操作', async () => {
    const store = useWorkspaceStore()
    store.createTaskFromApi(makeAgentTask())
    const wrapper = mountDetail()
    await flushPromises()

    expect(wrapper.text()).toContain('收起完整分析')
    expect(wrapper.text()).toContain('关键发现')
    expect(wrapper.text()).toContain('影响范围')
    expect(wrapper.text()).toContain('不确定点')
    expect(wrapper.text()).toContain('Issue 定位为缺陷修复类任务。')
    expect(wrapper.text()).toContain('回调链路')

    expect(wrapper.text()).toContain('分析来源')
    expect(wrapper.text()).toContain('引用 2 项')
    expect(wrapper.text()).toContain('Issue #IDEMO001 标题')
    expect(wrapper.text()).toContain('仓库 payment-service')

    expect(wrapper.text()).toContain('Issue 内容包含疑似指令注入片段')
    expect(wrapper.text()).toContain('忽略之前的指令')

    expect(wrapper.text()).toContain('存在待确认问题')
    expect(wrapper.text()).toContain('该改动是否需要保持对旧版本的兼容？')

    const items = wrapper.findAll('.question-item')
    expect(items).toHaveLength(2)

    const blockingItem = wrapper.find('.question-item--blocking')
    for (const text of ['使用建议回答', '跳过', '提交回答']) {
      expect(blockingItem.text()).toContain(text)
    }
    expect(blockingItem.find('textarea').exists()).toBe(true)
    expect(blockingItem.text()).toContain('建议回答：仅兼容当前主版本。')

    const optionalItem = items.find((item) => item.text().includes('可选问题'))!
    for (const text of ['使用建议回答', '跳过', '提交回答']) {
      expect(optionalItem.text()).toContain(text)
    }
    expect(optionalItem.find('textarea').exists()).toBe(true)
    expect(optionalItem.text()).toContain('建议回答：无需补充，按现有信息继续分析。')

    const blockingSubmit = blockingItem.findAll('button').find((button) => button.text().includes('提交回答'))
    expect(blockingSubmit?.attributes('disabled')).toBeDefined()
  })

  it('收起/展开切换完整分析区块可见性', async () => {
    const store = useWorkspaceStore()
    store.createTaskFromApi(makeAgentTask())
    const wrapper = mountDetail()
    await flushPromises()

    const toggle = wrapper.find('.analysis-expand-toggle')
    const content = wrapper.find('.analysis-detail-content')
    expect(toggle.text()).toContain('收起完整分析')
    expect(content.attributes('style') ?? '').not.toContain('display: none')

    await toggle.trigger('click')
    expect(toggle.text()).toContain('展开完整分析')
    expect(content.attributes('style') ?? '').toContain('display: none')

    await toggle.trigger('click')
    expect(toggle.text()).toContain('收起完整分析')
    expect(content.attributes('style') ?? '').not.toContain('display: none')
  })

  it('折叠的补充来源在展开后同步切换为收起文案', async () => {
    const store = useWorkspaceStore()
    store.createTaskFromApi(makeAgentTask({
      analysis: makeAnalysis({
        sources: Array.from({ length: 4 }, (_, index) => ({
          source_type: 'issue_comment',
          source_ref: `Issue 评论 ${index + 1}`,
          content: `补充分析来源 ${index + 1}`,
          relevance: 'medium',
        })),
      }),
    }))
    const wrapper = mountDetail()
    await flushPromises()

    const more = wrapper.find('details.source-evidence-more')
    expect(more.exists()).toBe(true)
    expect(more.text()).toContain('展开其余 1 项来源')
    expect((more.element as HTMLDetailsElement).open).toBe(false)

    ;(more.element as HTMLDetailsElement).open = true
    await more.trigger('toggle')

    expect(more.text()).toContain('收起其余 1 项来源')
    expect((more.element as HTMLDetailsElement).open).toBe(true)
  })

  it('使用建议回答填充阻塞问题输入框并启用提交按钮', async () => {
    const store = useWorkspaceStore()
    store.createTaskFromApi(makeAgentTask())
    const wrapper = mountDetail()
    await flushPromises()

    const blockingItem = wrapper.find('.question-item--blocking')
    const textarea = blockingItem.find('textarea')
    expect((textarea.element as HTMLTextAreaElement).value).toBe('')

    const useDefaultButton = blockingItem.findAll('button').find((button) => button.text().includes('使用建议回答'))
    await useDefaultButton!.trigger('click')

    expect((textarea.element as HTMLTextAreaElement).value).toBe('仅兼容当前主版本。')
    const submitButton = blockingItem.findAll('button').find((button) => button.text().includes('提交回答'))
    expect(submitButton?.attributes('disabled')).toBeUndefined()
  })

  it('提交阻塞问题回答调用回答接口并恢复自动流转', async () => {
    const store = useWorkspaceStore()
    store.createTaskFromApi(makeAgentTask())
    const wrapper = mountDetail()
    await flushPromises()

    apiRequestMock.mockClear()
    apiRequestMock.mockResolvedValueOnce(answeredTask())

    await wrapper.find('.question-item--blocking textarea').setValue('仅兼容当前主版本。')
    const submitButton = wrapper
      .find('.question-item--blocking')
      .findAll('button')
      .find((button) => button.text().includes('提交回答'))
    await submitButton!.trigger('click')
    await flushPromises()

    expect(apiRequestMock).toHaveBeenCalledTimes(1)
    expect(apiRequestMock).toHaveBeenCalledWith('/api/tasks/TASK-9001/analysis/questions/q_blocking_1/answer', {
      method: 'POST',
      body: JSON.stringify({ answer: '仅兼容当前主版本。' }),
    })
    expect(messageMocks.success).toHaveBeenCalled()

    expect(store.taskById('TASK-9001')?.manualRequired).toBe(false)
    expect(store.taskById('TASK-9001')?.nextAction).toBe('auto_generate_plan')
    expect(wrapper.find('.question-panel__saved-state').exists()).toBe(true)
    expect(wrapper.text()).toContain('已确认')
  })

  it('提交非阻塞问题回答调用回答接口并保持自动流转', async () => {
    const store = useWorkspaceStore()
    store.createTaskFromApi(makeAgentTask())
    const wrapper = mountDetail()
    await flushPromises()

    apiRequestMock.mockClear()
    apiRequestMock.mockResolvedValueOnce(
      makeAgentTask({
        manual_required: false,
        manual_reason: 'none',
        next_action: 'auto_generate_plan',
        automation_state: 'running',
        analysis: makeAnalysis({
          revision: 2,
          questions: [
            {
              id: 'q_optional_1',
              question: 'Issue 正文信息较少，是否还有其他补充材料？',
              reason: '正文过短。',
              blocking: false,
              default_answer: '无需补充，按现有信息继续分析。',
              answer: '补充了截图与复现步骤。',
              answered: true,
            },
          ],
        }),
      }),
    )

    const optionalItem = wrapper.findAll('.question-item').find((item) => item.text().includes('可选问题'))!
    await optionalItem.find('textarea').setValue('补充了截图与复现步骤。')
    const submitButton = optionalItem.findAll('button').find((button) => button.text().includes('提交回答'))
    await submitButton!.trigger('click')
    await flushPromises()

    expect(apiRequestMock).toHaveBeenCalledTimes(1)
    expect(apiRequestMock).toHaveBeenCalledWith('/api/tasks/TASK-9001/analysis/questions/q_optional_1/answer', {
      method: 'POST',
      body: JSON.stringify({ answer: '补充了截图与复现步骤。' }),
    })
    expect(messageMocks.success).toHaveBeenCalled()

    expect(store.taskById('TASK-9001')?.manualRequired).toBe(false)
    expect(store.taskById('TASK-9001')?.nextAction).toBe('auto_generate_plan')
    expect(wrapper.text()).toContain('补充了截图与复现步骤。')
    expect(wrapper.text()).toContain('已确认')
  })

  it('跳过阻塞问题调用 skip=true，仍有阻塞问题待确认时保持阻塞状态', async () => {
    const twoBlockingTask = makeAgentTask({
      analysis: makeAnalysis({
        questions: [
          {
            id: 'q_blocking_1',
            question: '该改动是否需要保持对旧版本的兼容？',
            reason: 'Issue 文本包含「兼容」表述。',
            blocking: true,
            default_answer: '仅兼容当前主版本。',
            answer: null,
            answered: false,
          },
          {
            id: 'q_blocking_2',
            question: '是否需要保留历史数据与历史行为？',
            reason: 'Issue 文本包含「历史」表述。',
            blocking: true,
            default_answer: '保留历史数据。',
            answer: null,
            answered: false,
          },
        ],
      }),
    })
    apiRequestMock.mockReset()
    apiRequestMock.mockResolvedValue(twoBlockingTask)

    const store = useWorkspaceStore()
    store.createTaskFromApi(twoBlockingTask)
    const wrapper = mountDetail()
    await flushPromises()
    apiRequestMock.mockClear()
    apiRequestMock.mockResolvedValueOnce(
      makeAgentTask({
        analysis: makeAnalysis({
          questions: [
            {
              id: 'q_blocking_1',
              question: '该改动是否需要保持对旧版本的兼容？',
              reason: 'Issue 文本包含「兼容」表述。',
              blocking: true,
              default_answer: '仅兼容当前主版本。',
              answer: '跳过',
              answered: true,
            },
            {
              id: 'q_blocking_2',
              question: '是否需要保留历史数据与历史行为？',
              reason: 'Issue 文本包含「历史」表述。',
              blocking: true,
              default_answer: '保留历史数据。',
              answer: null,
              answered: false,
            },
          ],
        }),
      }),
    )

    const firstItem = wrapper.findAll('.question-item--blocking')[0]
    const skipButton = firstItem.findAll('button').find((button) => button.text().includes('跳过'))
    await skipButton!.trigger('click')
    await flushPromises()

    expect(apiRequestMock).toHaveBeenCalledTimes(1)
    expect(apiRequestMock).toHaveBeenCalledWith('/api/tasks/TASK-9001/analysis/questions/q_blocking_1/answer', {
      method: 'POST',
      body: JSON.stringify({ skip: true }),
    })
    expect(store.taskById('TASK-9001')?.manualRequired).toBe(true)
    expect(store.taskById('TASK-9001')?.nextAction).toBe('answer_analysis_question')
    expect(wrapper.text()).toContain('已跳过该问题。')
    expect(wrapper.text()).toContain('存在待确认问题')
  })

  it('失败任务展示失败提示与重试入口，且不渲染问题确认面板', async () => {
    routeState.params.id = 'TASK-9002'
    const store = useWorkspaceStore()
    store.createTaskFromApi(
      makeAgentTask({
        id: 'TASK-9002',
        issue_id: 'IDEMO004',
        status: 'FAILED',
        risk_level: null,
        risk_source: 'pending_ai_analysis',
        analysis: null,
        automation_state: 'blocked',
        manual_required: true,
        manual_reason: 'context_missing',
        next_action: 'retry_or_cancel',
        failure_node: '上下文聚合',
        failure_reason: 'Issue 信息过少。',
      }),
    )
    const wrapper = mountDetail()
    await flushPromises()

    expect(wrapper.text()).toContain('任务已暂停：上下文聚合')
    expect(wrapper.text()).toContain('Issue 信息过少。')
    expect(wrapper.find('.detail-section--failure').exists()).toBe(true)
    expect(wrapper.find('.analysis-detail-block').exists()).toBe(false)
    expect(wrapper.find('.question-panel').exists()).toBe(false)

    const buttonTexts = wrapper.findAll('button').map((button) => button.text())
    expect(buttonTexts).toContain('一键重试')
    expect(buttonTexts).toContain('重试任务')
  })
})

describe('TaskDetailView：数据源模式与直达错误态（DEV-009）', () => {
  beforeEach(() => {
    setDataSourceMode('real')
    localStorage.clear()
    activePinia = createPinia()
    setActivePinia(activePinia)
    apiRequestMock.mockReset()
    apiRequestMock.mockResolvedValue(makeAgentTask())
    messageMocks.success.mockClear()
    messageMocks.error.mockClear()
    messageMocks.closeAll.mockClear()
    routeState.params.id = 'TASK-9001'
  })

  afterEach(() => {
    setDataSourceMode('real')
  })

  it('real 模式直达未知任务：404 渲染「任务不存在」错误态，不回退到其它任务', async () => {
    routeState.params.id = 'TASK-9999'
    apiRequestMock.mockReset()
    apiRequestMock.mockRejectedValueOnce(new ApiError('任务不存在', 404))

    const wrapper = mountDetail()
    await flushPromises()

    expect(apiRequestMock).toHaveBeenCalledWith('/api/tasks/TASK-9999')
    const empty = wrapper.find('.empty-panel-stub')
    expect(empty.exists()).toBe(true)
    expect(empty.text()).toContain('任务不存在或无权查看')
    expect(wrapper.find('.phase-rail').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('修改计划')
  })

  it('mock 模式直达 demo 任务：不请求后端并渲染完整详情', async () => {
    setDataSourceMode('mock')
    routeState.params.id = 'TASK-8824'

    const wrapper = mountDetail()
    await flushPromises()

    expect(apiRequestMock).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('优化仓库索引超时重试')
    expect(wrapper.text()).toContain('按分片保存进度并优化退避重试')
    expect(wrapper.text()).not.toContain('任务不存在或无权查看')
  })

  it('real 模式任务携带计划：详情页计划面板渲染目标/风险与审批操作', async () => {
    const plan = makeModificationPlan()
    apiRequestMock.mockReset()
    const payload = makeAgentTask({
      status: 'WAITING_APPROVAL',
      automation_state: 'waiting_approval',
      manual_required: true,
      manual_reason: 'approval_required',
      next_action: 'review_plan',
      risk_level: 'high',
      risk_source: 'plan',
      plans: [plan],
      analysis: makeAnalysis({
        revision: 2,
        questions: [
          {
            id: 'q_blocking_1',
            question: '该改动是否需要保持对旧版本的兼容？',
            reason: 'Issue 文本包含「兼容」表述。',
            blocking: true,
            default_answer: null,
            answer: '仅兼容当前主版本。',
            answered: true,
          },
        ],
      }),
    })
    apiRequestMock.mockImplementation((url: string) => {
      if (url === '/api/tasks/TASK-9001') return Promise.resolve(payload)
      if (url === '/api/tasks/TASK-9001/plans') return Promise.resolve([plan])
      return Promise.reject(new ApiError(`未预期请求 ${url}`, 500))
    })

    const wrapper = mountDetail()
    await flushPromises()

    expect(apiRequestMock).toHaveBeenCalledWith('/api/tasks/TASK-9001/plans')
    expect(wrapper.text()).toContain('增加幂等键')
    expect(wrapper.text()).toContain('待审批')
    expect(wrapper.text()).toContain('文件范围')
    expect(wrapper.text()).toContain('回调入口增加幂等校验。')
    const buttons = wrapper.findAll('button').map((button) => button.text())
    expect(buttons).toContain('通过')
    expect(buttons).toContain('要求重写')
    expect(buttons).toContain('拒绝')
  })

  it('TESTED 任务携带测试记录：详情页渲染测试摘要与通过注记', async () => {
    const passed = makeTestRun()
    const payload = makeAgentTask({
      status: 'TESTED',
      automation_state: 'blocked',
      manual_required: false,
      manual_reason: 'none',
      next_action: 'watch_test_result',
      diffs: [makeDiffArtifact()],
      test_runs: [passed],
    })
    apiRequestMock.mockReset()
    apiRequestMock.mockImplementation((url: string) => {
      if (url === '/api/tasks/TASK-9001') return Promise.resolve(payload)
      if (url.endsWith('/tests')) return Promise.resolve([passed])
      if (url.endsWith('/plans')) return Promise.resolve([])
      return Promise.reject(new ApiError(`未预期请求 ${url}`, 500))
    })

    const wrapper = mountDetail()
    await flushPromises()

    const section = wrapper.find('.detail-section--tests')
    expect(section.exists()).toBe(true)
    expect(section.text()).toContain('测试执行记录')
    expect(section.text()).toContain('最近一次：第 1 次 · 通过')
    expect(section.text()).toContain('第 1 次 · 通过')
    expect(section.text()).toContain('exit 0')
    // 通过注记明确 Diff 产物未受影响（测试通过不等于 Diff 出问题）
    expect(section.find('.detail-test-note--passed').text()).toContain('测试通过（耗时 1.02s，模式：mocked_runner），Diff 产物未受影响。')
    expect(section.text()).toContain('在 Diff 页查看输出')
  })

  it('TEST_FAILED 任务展示失败注记与去重试入口，不出现失败任务横幅', async () => {
    const failed = makeTestRun({
      status: 'failed',
      exit_code: 1,
      stdout: '',
      stderr: 'FAILED tests/test_callback_idempotent.py',
      failure_reason: 'AssertionError: 重复入账',
      duration: 3.14,
    })
    const payload = makeAgentTask({
      status: 'TEST_FAILED',
      automation_state: 'blocked',
      manual_required: true,
      manual_reason: 'test_failed',
      next_action: 'retry_test',
      failure_node: null,
      failure_reason: null,
      diffs: [makeDiffArtifact()],
      test_runs: [failed],
    })
    apiRequestMock.mockReset()
    apiRequestMock.mockImplementation((url: string) => {
      if (url === '/api/tasks/TASK-9001') return Promise.resolve(payload)
      if (url.endsWith('/tests')) return Promise.resolve([failed])
      if (url.endsWith('/plans')) return Promise.resolve([])
      return Promise.reject(new ApiError(`未预期请求 ${url}`, 500))
    })

    const wrapper = mountDetail()
    await flushPromises()

    // 测试失败不是任务级失败：不展示「任务已暂停」失败横幅
    expect(wrapper.find('.detail-section--failure').exists()).toBe(false)
    expect(wrapper.text()).toContain('测试未通过（第 1 次尝试）：AssertionError: 重复入账')

    const section = wrapper.find('.detail-section--tests')
    expect(section.text()).toContain('最近一次：第 1 次 · 失败')
    expect(section.find('.detail-test-note--failed').text()).toContain('失败原因：AssertionError: 重复入账')

    const retryLink = wrapper.findAllComponents(RouterLinkStub).find((link) => link.text().includes('去重试测试'))
    expect(retryLink).toBeDefined()
    expect(retryLink!.props('to')).toBe('/diff/TASK-9001')
  })
})

describe('TaskDetailView：PR 描述摘要（DEV-013）', () => {
  beforeEach(() => {
    setDataSourceMode('real')
    localStorage.clear()
    activePinia = createPinia()
    setActivePinia(activePinia)
    apiRequestMock.mockReset()
    messageMocks.success.mockClear()
    messageMocks.error.mockClear()
    messageMocks.closeAll.mockClear()
    routeState.params.id = 'TASK-9001'
  })

  function prPayload(status: AgentTask['status'], testRuns: ReturnType<typeof makeTestRun>[], records: ReturnType<typeof makePrDescription>[]) {
    return makeAgentTask({
      status,
      automation_state: 'blocked',
      manual_required: status === 'TEST_FAILED',
      manual_reason: status === 'TEST_FAILED' ? 'test_failed' : 'none',
      next_action: status === 'TEST_FAILED' ? 'retry_test' : 'watch_test_result',
      diffs: [makeDiffArtifact()],
      test_runs: testRuns,
      pr_descriptions: records,
    })
  }

  function installDetail(urlHandler: (url: string) => unknown) {
    apiRequestMock.mockImplementation((url: string) => {
      if (url === '/api/tasks/TASK-9001') return Promise.resolve(urlHandler(url))
      if (url.endsWith('/tests')) return Promise.resolve(urlHandler(url))
      if (url.endsWith('/pr-description')) return Promise.resolve(urlHandler(url))
      if (url.endsWith('/plans')) return Promise.resolve([])
      return Promise.reject(new ApiError(`未预期请求 ${url}`, 500))
    })
  }

  it('TESTED 携带已生成记录：详情展示 PR 摘要、版本信息与去 Diff 页入口', async () => {
    const passed = makeTestRun()
    const v1 = makePrDescription()
    const v2 = makePrDescription({
      id: 'PRD-9001-2',
      version: 2,
      plan_version: 2,
      generated_at: '2026-09-04T10:30:00Z',
    })
    installDetail((url: string) => {
      if (url.endsWith('/tests')) return [passed]
      if (url.endsWith('/pr-description')) return { task_id: 'TASK-9001', status: 'GENERATED', stale: false, stale_reason: null, current: v2, versions: [v2, v1] }
      return prPayload('TESTED', [passed], [v2, v1])
    })

    const wrapper = mountDetail()
    await flushPromises()

    const section = wrapper.find('.detail-section--pr')
    expect(section.exists()).toBe(true)
    expect(section.text()).toContain('PR 描述')
    expect(section.text()).toContain('已生成 · 第 2 版')
    expect(section.text()).toContain('计划 v2 · 生成于')
    expect(section.text()).toContain('不自动创建 Gitee PR')
    expect(section.find('.status-tag--warning').exists()).toBe(false)
    // 区块与产物入口都指向 Diff 页
    const prLinks = wrapper.findAllComponents(RouterLinkStub).filter((link) => link.text().includes('在 Diff 页查看全文与生成'))
    expect(prLinks.length).toBeGreaterThanOrEqual(2)
    expect(prLinks[0].props('to')).toBe('/diff/TASK-9001')
  })

  it('测试结果变化导致过期：摘要标记「需重新生成」并给出原因', async () => {
    const oldTest = makeTestRun()
    const newerTest = makeTestRun({
      id: 'TEST-9001-2',
      attempt: 2,
      started_at: '2026-09-04T10:20:00Z',
      finished_at: '2026-09-04T10:20:02Z',
    })
    const v1 = makePrDescription()
    installDetail((url: string) => {
      if (url.endsWith('/tests')) return [newerTest, oldTest]
      if (url.endsWith('/pr-description')) return { task_id: 'TASK-9001', status: 'GENERATED', stale: true, stale_reason: '测试结果已变化', current: v1, versions: [v1] }
      return prPayload('TESTED', [newerTest, oldTest], [v1])
    })

    const wrapper = mountDetail()
    await flushPromises()

    const chip = wrapper.find('.detail-section--pr .status-tag--warning')
    expect(chip.text()).toContain('需重新生成')
    expect(chip.attributes('title')).toContain('测试结果已变化')
  })

  it('最近一次生成失败：摘要展示失败原因，不出现成功文案', async () => {
    const passed = makeTestRun()
    const failed = makePrDescription({
      generation_status: 'FAILED',
      failure_reason: '测试结果为空：未找到已完成的测试记录',
    })
    installDetail((url: string) => {
      if (url.endsWith('/tests')) return [passed]
      if (url.endsWith('/pr-description')) return { task_id: 'TASK-9001', status: 'FAILED', stale: false, stale_reason: null, current: failed, versions: [failed] }
      return prPayload('TESTED', [passed], [failed])
    })

    const wrapper = mountDetail()
    await flushPromises()

    const section = wrapper.find('.detail-section--pr')
    expect(section.exists()).toBe(true)
    expect(section.text()).toContain('生成失败')
    expect(section.text()).toContain('生成失败：测试结果为空：未找到已完成的测试记录')
    expect(section.text()).not.toContain('已生成 ·')
    expect(section.find('.detail-test-note--failed').exists()).toBe(true)
  })

  it('TESTED 尚无记录且无生成权限口径：不展示 PR 摘要（生成入口在 Diff 页）', async () => {
    // approver/只读与生成入口无关：摘要仅在存在记录或 TESTED/TEST_FAILED 任务上出现
    installDetail((url: string) => {
      if (url.endsWith('/tests')) return []
      if (url.endsWith('/pr-description')) return { task_id: 'TASK-9001', status: 'PENDING', stale: false, stale_reason: null, current: null, versions: [] }
      return prPayload('DIFF_GENERATED', [], [])
    })

    const wrapper = mountDetail()
    await flushPromises()

    // DIFF_GENERATED 无测试结论：PR 摘要区不出现，产物入口保持禁用说明
    expect(wrapper.find('.detail-section--pr').exists()).toBe(false)
    const entryText = wrapper.find('.artifact-links').text()
    expect(entryText).toContain('PR 描述')
    expect(entryText).toContain('等待测试')
  })
})

describe('TaskDetailView：最近事件摘要行与失败红点（DEV-014）', () => {
  beforeEach(() => {
    setDataSourceMode('real')
    localStorage.clear()
    activePinia = createPinia()
    setActivePinia(activePinia)
    apiRequestMock.mockReset()
    messageMocks.success.mockClear()
    messageMocks.error.mockClear()
    messageMocks.closeAll.mockClear()
    routeState.params.id = 'TASK-9001'
  })

  function eventRecord(id: string, eventType: string, summary: string, diffSummary: string[], createdAt: string) {
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

  it('失败任务最近事件：末条失败事件带红点与摘要行，普通事件只补摘要行', async () => {
    const failedTask = makeAgentTask({
      status: 'FAILED',
      failure_node: '测试执行',
      failure_reason: '重复入账断言不通过',
      manual_required: true,
      manual_reason: 'test_failed',
      next_action: 'retry_or_cancel',
      events: [
        eventRecord('evt-1', 'ANALYSIS_GENERATED', 'AI 分析完成', ['风险等级：high'], '2026-09-04T09:00:00Z'),
        eventRecord('evt-2', 'PLAN_GENERATED', '修改计划已生成', ['计划版本：v1'], '2026-09-04T09:10:00Z'),
        eventRecord('evt-3', 'PLAN_APPROVED', '计划审批通过', ['审批人：陈晓'], '2026-09-04T09:20:00Z'),
        eventRecord('evt-4', 'DIFF_GENERATED', '已生成 Diff v1', ['变更文件：3 个'], '2026-09-04T09:30:00Z'),
        eventRecord('evt-5', 'TASK_FAILED', '任务失败：测试执行', ['失败原因：重复入账断言不通过'], '2026-09-04T10:00:00Z'),
      ],
    })
    apiRequestMock.mockImplementation((url: string) => {
      if (url === '/api/tasks/TASK-9001') return Promise.resolve(failedTask)
      return Promise.reject(new ApiError(`未预期请求 ${url}`, 500))
    })

    const wrapper = mountDetail()
    await flushPromises()

    const items = wrapper.findAll('.mini-timeline li')
    // 最近 4 条：evt-2 ~ evt-5
    expect(items).toHaveLength(4)
    const failedItem = items.find((item) => item.text().includes('任务失败：测试执行'))
    expect(failedItem).toBeDefined()
    expect(failedItem!.classes()).toContain('is-failed')
    // 失败事件带摘要行（diff_summary[0]）
    expect(failedItem!.find('.mini-timeline__summary').text()).toContain('失败原因：重复入账断言不通过')
    // 普通已完成事件无失败红点，但补上了摘要行
    const passedItem = items.find((item) => item.text().includes('已生成 Diff v1'))
    expect(passedItem).toBeDefined()
    expect(passedItem!.classes()).not.toContain('is-failed')
    expect(passedItem!.find('.mini-timeline__summary').text()).toContain('变更文件：3 个')
  })
})

describe('TaskDetailView：平台需求来源闭环展示（DEV-016-C）', () => {
  beforeEach(() => {
    setDataSourceMode('mock')
    localStorage.clear()
    activePinia = createPinia()
    setActivePinia(activePinia)
    apiRequestMock.mockReset()
    apiRequestMock.mockResolvedValue(makeAgentTask())
    routeState.params.id = 'TASK-9001'
  })

  it('从平台需求派生的任务展示平台需求链接、Gitee Issue 外链与当前 Commit', async () => {
    const store = useWorkspaceStore()
    store.createTaskFromApi(
      makeAgentTask({
        team_request_id: 'team_req_001',
        team_request_no: 'REQ-1001',
        gitee_issue_url: 'https://gitee.com/example/payment-service/issues/IKC1601',
        base_commit_sha: 'c0ffee11deadbeef22c0ffee33deadbeef44556677',
      }),
    )
    const wrapper = mountDetail()
    await flushPromises()

    const sources = wrapper.find('.task-detail__sources')
    expect(sources.exists()).toBe(true)
    const requestLinks = sources.findAllComponents(RouterLinkStub)
    const requestLink = requestLinks.find((link: (typeof requestLinks)[number]) => link.text().includes('REQ-1001'))
    expect(requestLink).toBeTruthy()
    expect(requestLink!.props('to')).toBe('/requests/team_req_001')
    const giteeLink = wrapper.find('a[href="https://gitee.com/example/payment-service/issues/IKC1601"]')
    expect(giteeLink.exists()).toBe(true)
    expect(wrapper.text()).toContain('c0ffee11dead')
  })

  it('旧 /api/tasks 手动任务无平台需求来源时不渲染来源行', async () => {
    const store = useWorkspaceStore()
    store.createTaskFromApi(makeAgentTask())
    const wrapper = mountDetail()
    await flushPromises()
    expect(wrapper.find('.task-detail__sources').exists()).toBe(false)
  })
})

describe('TaskDetailView：上下文不足人工补充入口（DEV-017）', () => {
  beforeEach(() => {
    setDataSourceMode('real')
    localStorage.clear()
    activePinia = createPinia()
    setActivePinia(activePinia)
    apiRequestMock.mockReset()
    routeState.params.id = 'TASK-9001'
  })

  it('context_missing 任务横幅展示「去补充上下文」，answer_required 仍展示「去回答」', async () => {
    const scrollIntoViewMock = vi.fn()
    const querySpy = vi.spyOn(document, 'querySelector').mockImplementation((selector: string) => {
      if (selector === '.code-context-section') return { scrollIntoView: scrollIntoViewMock } as unknown as Element
      return null
    })
    const store = useWorkspaceStore()
    store.createTaskFromApi(
      makeAgentTask({
        status: 'ISSUE_FETCHED',
        manual_required: true,
        manual_reason: 'context_missing',
        next_action: 'supplement_context',
        automation_state: 'blocked',
        related_index_id: 'idx_1',
      }),
    )
    const wrapper = mountDetail()
    await flushPromises()

    const notice = wrapper.find('.page-notice')
    expect(notice.exists()).toBe(true)
    expect(notice.text()).toContain('补充上下文')
    const supplementButton = notice.findAll('button').find((button) => button.text().includes('去补充上下文'))
    expect(supplementButton).toBeTruthy()
    expect(notice.text()).not.toContain('去回答')

    await supplementButton!.trigger('click')
    expect(scrollIntoViewMock).toHaveBeenCalledWith({ behavior: 'smooth', block: 'center' })
    querySpy.mockRestore()
  })
})
