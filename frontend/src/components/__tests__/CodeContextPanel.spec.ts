import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { RouterLinkStub, flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import CodeContextPanel from '../CodeContextPanel.vue'
import { makeAgentTask } from '../../test/fixtures'
import { useWorkspaceStore } from '../../stores/workspace'
import type { AgentTask, CodeContext, RepositoryIndex, SimilarTaskRef, Task } from '../../types/domain'

const { apiRequestMock, hasPermissionMock, messageMocks, routerPushMock } = vi.hoisted(() => ({
  apiRequestMock: vi.fn(),
  hasPermissionMock: vi.fn(),
  messageMocks: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
  routerPushMock: vi.fn(),
}))

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: routerPushMock }),
}))

vi.mock('element-plus', () => ({
  ElMessage: { success: messageMocks.success, error: messageMocks.error, warning: messageMocks.warning, info: messageMocks.info },
}))

vi.mock('../../stores/auth', () => ({
  useAuthStore: () => ({ hasPermission: hasPermissionMock }),
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

const SHA = 'a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2'

function makeContext(overrides: Partial<CodeContext> = {}): CodeContext {
  return {
    chunk_id: 'chunk_cb_1',
    file_path: 'services/payment/callback.py',
    start_line: 12,
    end_line: 34,
    language: 'python',
    symbol_name: 'handle_payment_callback',
    content: 'def handle_payment_callback(payload):\n    # 幂等键检查通过后入账',
    score: 0.82,
    commit_sha: SHA,
    index_id: 'idx_1',
    fresh: true,
    source: 'milvus',
    ...overrides,
  }
}

function makeIndexRecord(overrides: Partial<RepositoryIndex> = {}): RepositoryIndex {
  return {
    id: 'idx_1',
    repository_id: 'repo_payment_service',
    project_id: 'proj_payment',
    base_branch: 'main',
    commit_sha: SHA,
    status: 'COMPLETED',
    mode: 'full',
    total_files: 12,
    processed_files: 12,
    chunk_count: 48,
    milvus_collection: null,
    error_message: null,
    error_stage: null,
    requested_by: 'user_admin',
    requested_by_name: '小泽',
    retry_count: 0,
    requested_at: '2026-09-04T09:00:00Z',
    created_at: '2026-09-04T09:00:00Z',
    updated_at: '2026-09-04T09:01:00Z',
    started_at: '2026-09-04T09:00:05Z',
    finished_at: '2026-09-04T09:00:50Z',
    ...overrides,
  }
}

function baseAgentTask(overrides: Partial<AgentTask> = {}): AgentTask {
  return makeAgentTask({
    code_contexts: [],
    base_commit_sha: SHA,
    ...overrides,
  })
}

let activePinia: Pinia
let wrapper: VueWrapper | null = null

function mountPanel(task: Task) {
  wrapper = mount(CodeContextPanel, {
    props: { task },
    global: {
      plugins: [activePinia],
      stubs: { RouterLink: RouterLinkStub },
    },
  })
  return wrapper
}

function taskFor(agentTask: AgentTask): Task {
  const store = useWorkspaceStore()
  return store.createTaskFromApi(agentTask)
}

describe('CodeContextPanel：任务代码上下文面板', () => {
  beforeEach(() => {
    localStorage.clear()
    activePinia = createPinia()
    setActivePinia(activePinia)
    apiRequestMock.mockReset()
    hasPermissionMock.mockReset()
    hasPermissionMock.mockReturnValue(true)
    messageMocks.success.mockClear()
    messageMocks.error.mockClear()
    messageMocks.warning.mockClear()
    messageMocks.info.mockClear()
    routerPushMock.mockClear()
  })

  afterEach(() => {
    wrapper?.unmount()
    wrapper = null
    vi.useRealTimers()
  })

  it('渲染任务快照中的检索结果：路径/行号/Commit/相似度/来源标签', async () => {
    const task = taskFor(
      baseAgentTask({
        status: 'ANALYZED',
        next_action: 'auto_generate_plan',
        manual_required: false,
        manual_reason: 'none',
        automation_state: 'running',
        code_contexts: [makeContext()],
      }),
    )
    mountPanel(task)
    await flushPromises()

    expect(apiRequestMock).not.toHaveBeenCalled()
    expect(wrapper!.text()).toContain('相关文件')
    expect(wrapper!.text()).toContain('services/payment/callback.py')
    expect(wrapper!.text()).toContain('handle_payment_callback')
    expect(wrapper!.text()).toContain('L12-34')
    expect(wrapper!.text()).toContain('a1b2c3d4e5f6')
    expect(wrapper!.text()).toContain('82%')
    expect(wrapper!.text()).toContain('向量检索')
    expect(wrapper!.find('.context-list li').exists()).toBe(true)
  })

  it('空结果面板提供重新检索入口，接口返回 empty_reason 与 Top-K 元信息', async () => {
    const task = taskFor(
      baseAgentTask({
        status: 'ANALYZED',
        next_action: 'auto_generate_plan',
        manual_required: false,
        manual_reason: 'none',
        automation_state: 'running',
      }),
    )
    apiRequestMock.mockResolvedValue({
      task_id: 'TASK-9001',
      query_text: '修复支付回调重复记账',
      top_k: 5,
      threshold: 0.3,
      index_id: 'idx_1',
      index_status: 'COMPLETED',
      commit_sha: SHA,
      fresh: true,
      mode: 'auto',
      empty_reason: '当前 Commit 的索引没有命中任何相关代码',
      contexts: [],
      retrieval_id: 'ret_1',
    })
    mountPanel(task)
    await flushPromises()

    expect(wrapper!.text()).toContain('当前任务没有可展示的代码上下文')
    expect(wrapper!.text()).toContain('重建索引')

    await wrapper!.findAll('button').find((button) => button.text().includes('重新检索'))!.trigger('click')
    await flushPromises()

    expect(apiRequestMock).toHaveBeenCalledWith('/api/tasks/TASK-9001/contexts?top_k=5')
    expect(wrapper!.text()).toContain('检索无结果')
    expect(wrapper!.text()).toContain('当前 Commit 的索引没有命中任何相关代码')
    expect(wrapper!.text()).toContain('自动检索 Top-K 5 · 相似度阈值 0.3')
  })

  it('手动指定文件生成人工上下文：路径清洗、成功提交与超限校验', async () => {
    const task = taskFor(
      baseAgentTask({
        status: 'ANALYZED',
        next_action: 'auto_generate_plan',
        manual_required: false,
        manual_reason: 'none',
        automation_state: 'running',
      }),
    )
    apiRequestMock.mockResolvedValue({
      task_id: 'TASK-9001',
      index_id: 'idx_1',
      commit_sha: SHA,
      fresh: true,
      contexts: [
        makeContext({ chunk_id: 'chunk_manual_1', source: 'manual', score: null, file_path: 'services/payment/validator.py', symbol_name: 'validate_id_card' }),
      ],
    })
    mountPanel(task)
    await flushPromises()

    await wrapper!.findAll('button').find((button) => button.text().includes('手动指定文件'))!.trigger('click')
    const textarea = wrapper!.find('.code-context-manual textarea')
    expect(textarea.exists()).toBe(true)

    await textarea.setValue(
      ['services/payment/callback.py', './services/payment/validator.py', 'r1.py', 'r2.py', 'r3.py', 'r4.py', 'r5.py', 'r6.py', 'r7.py'].join('\n'),
    )
    await wrapper!.findAll('button').find((button) => button.text().includes('生成人工上下文'))!.trigger('click')
    await flushPromises()

    expect(wrapper!.text()).toContain('单次最多指定 8 个文件路径')
    expect(apiRequestMock).not.toHaveBeenCalled()

    await textarea.setValue('services/payment/callback.py\n./services/payment/validator.py')
    await wrapper!.findAll('button').find((button) => button.text().includes('生成人工上下文'))!.trigger('click')
    await flushPromises()

    expect(apiRequestMock).toHaveBeenCalledTimes(1)
    expect(apiRequestMock).toHaveBeenCalledWith('/api/tasks/TASK-9001/contexts/manual', {
      method: 'POST',
      body: JSON.stringify({ file_paths: ['services/payment/callback.py', 'services/payment/validator.py'] }),
    })
    expect(messageMocks.success).toHaveBeenCalled()
    expect(wrapper!.text()).toContain('人工指定')
    expect(wrapper!.text()).toContain('validate_id_card')
  })

  it('权限门控：无 index:manage 时隐藏重建入口，仅保留检索操作', async () => {
    hasPermissionMock.mockImplementation((permission: string) => permission === 'task:operate')
    const task = taskFor(
      baseAgentTask({
        status: 'ANALYZED',
        next_action: 'auto_generate_plan',
        manual_required: false,
        manual_reason: 'none',
        automation_state: 'running',
      }),
    )
    mountPanel(task)
    await flushPromises()

    const buttonTexts = wrapper!.findAll('.code-context-empty button').map((button) => button.text())
    expect(buttonTexts).toContain('重新检索')
    expect(buttonTexts).toContain('手动指定文件')
    expect(buttonTexts).not.toContain('重建索引')
  })

  it('过期索引展示警示徽标与说明，仅作人工核对', async () => {
    const task = taskFor(
      baseAgentTask({
        status: 'ANALYZED',
        next_action: 'auto_generate_plan',
        manual_required: false,
        manual_reason: 'none',
        automation_state: 'running',
        code_contexts: [makeContext({ fresh: false })],
      }),
    )
    const store = useWorkspaceStore()
    store.mergeRepositoryIndex(makeIndexRecord({ commit_sha: '9999999999999999999999999999999999999999' }))
    mountPanel(task)
    await flushPromises()

    expect(wrapper!.text()).toContain('过期')
    expect(wrapper!.text()).toContain('检索来源索引已过期（Commit 落后于仓库最新）')
    expect(wrapper!.find('.code-context-stale .text-link').text()).toBe('重建索引')
  })

  it('等待索引任务轮询索引进度，终态变化时发出一次 refresh', async () => {
    vi.useFakeTimers()
    const task = taskFor(
      baseAgentTask({
        status: 'ISSUE_FETCHED',
        next_action: 'wait_index',
        manual_required: true,
        manual_reason: 'context_missing',
        automation_state: 'blocked',
        related_index_id: 'idx_1',
        analysis: null,
      }),
    )
    const pending = makeIndexRecord({ status: 'PENDING', total_files: 0, processed_files: 0, chunk_count: 0, started_at: null, finished_at: null })
    const completed = makeIndexRecord({ status: 'COMPLETED' })
    apiRequestMock.mockResolvedValueOnce(pending)
    apiRequestMock.mockResolvedValue(completed)
    mountPanel(task)
    await flushPromises()

    expect(apiRequestMock).toHaveBeenCalledWith('/api/repositories/repo_payment_service/indexes/idx_1')
    expect(wrapper!.text()).toContain('任务等待仓库索引完成')
    expect(wrapper!.text()).toContain('排队中')
    expect(wrapper!.text()).toContain('索引进度中')
    expect(wrapper!.emitted('refresh')).toBeUndefined()

    await vi.advanceTimersByTimeAsync(3000)
    expect(wrapper!.emitted('refresh')).toHaveLength(1)
    expect(wrapper!.text()).toContain('已完成')

    await vi.advanceTimersByTimeAsync(3000)
    expect(wrapper!.emitted('refresh')).toHaveLength(1)
    wrapper!.unmount()
  })

  it('任务在代码索引节点失败时展示失败卡，可重试失败索引或跳转管理页', async () => {
    const store = useWorkspaceStore()
    store.mergeRepositoryIndex(
      makeIndexRecord({ id: 'idx_fail', status: 'FAILED', error_stage: 'EMBEDDING', error_message: '向量化内存不足', total_files: 0, processed_files: 0, chunk_count: 0 }),
    )
    const task = taskFor(
      baseAgentTask({
        status: 'FAILED',
        next_action: 'retry_or_cancel',
        manual_required: true,
        manual_reason: 'context_missing',
        automation_state: 'blocked',
        failure_node: '代码索引',
        failure_reason: '代码索引失败：向量化内存不足',
        related_index_id: 'idx_fail',
        analysis: null,
      }),
    )
    apiRequestMock.mockResolvedValue(makeIndexRecord({ id: 'idx_fail', status: 'PENDING', total_files: 0, processed_files: 0, chunk_count: 0, started_at: null, finished_at: null }))
    mountPanel(task)
    await flushPromises()

    expect(wrapper!.text()).toContain('任务在「代码索引」节点失败')
    expect(wrapper!.text()).toContain('代码索引失败：向量化内存不足')
    expect(wrapper!.text()).toContain('向量化内存不足')

    await wrapper!.findAll('button').find((button) => button.text().includes('查看仓库索引'))!.trigger('click')
    expect(routerPushMock).toHaveBeenCalledWith({ path: '/indexes', query: { repository_id: 'repo_payment_service' } })

    await wrapper!.findAll('button').find((button) => button.text().includes('重试该索引'))!.trigger('click')
    await flushPromises()
    expect(apiRequestMock).toHaveBeenCalledWith('/api/repositories/repo_payment_service/indexes/idx_fail/retry', { method: 'POST' })
    expect(messageMocks.success).toHaveBeenCalledWith('失败索引已重新排队，任务会在索引完成后自动继续')
  })

  // ---------- DEV-017 自动上下文补齐 ----------

  function contextMissingTask(overrides: Partial<AgentTask> = {}): Task {
    return taskFor(
      baseAgentTask({
        status: 'ISSUE_FETCHED',
        next_action: 'supplement_context',
        manual_required: true,
        manual_reason: 'context_missing',
        automation_state: 'blocked',
        related_index_id: 'idx_1',
        ...overrides,
      }),
    )
  }

  it('DEV-017：context_missing 空态提示自动分析暂停，主按钮走 resume 恢复并发出 refresh', async () => {
    const task = contextMissingTask()
    apiRequestMock.mockResolvedValue({
      resumed: true,
      contexts: [makeContext()],
      empty_reason: null,
    })
    mountPanel(task)
    await flushPromises()

    expect(wrapper!.text()).toContain('自动分析已暂停：上下文不足')
    const resumeButton = wrapper!.findAll('button').find((button) => button.text().includes('重新检索并继续'))
    expect(resumeButton).toBeTruthy()
    // 普通「重新检索」只读按钮在 context_missing 下不出现，避免与恢复动作混淆
    expect(wrapper!.findAll('button').some((button) => button.text().trim() === '重新检索')).toBe(false)

    await resumeButton!.trigger('click')
    await flushPromises()

    expect(apiRequestMock).toHaveBeenCalledWith('/api/tasks/TASK-9001/contexts/resume', { method: 'POST', body: '{}' })
    expect(messageMocks.success).toHaveBeenCalledWith('已检索到代码上下文，任务恢复自动流转')
    expect(wrapper!.emitted('refresh')).toHaveLength(1)
  })

  it('DEV-017：resume 仍未命中时保持补充态并给出 warning，不发 refresh', async () => {
    const task = contextMissingTask()
    apiRequestMock.mockResolvedValue({ resumed: false, contexts: [], empty_reason: '未检索到达到相似度阈值（0.3）的代码片段' })
    mountPanel(task)
    await flushPromises()

    await wrapper!.findAll('button').find((button) => button.text().includes('重新检索并继续'))!.trigger('click')
    await flushPromises()

    expect(messageMocks.warning).toHaveBeenCalled()
    expect(wrapper!.emitted('refresh')).toBeUndefined()
    expect(wrapper!.text()).toContain('未检索到达到相似度阈值（0.3）的代码片段')
  })

  it('DEV-017：手动指定文件恢复 context_missing 后发出 refresh 并提示恢复自动流转', async () => {
    const task = contextMissingTask()
    apiRequestMock.mockResolvedValue({
      task_id: 'TASK-9001',
      index_id: 'idx_1',
      commit_sha: SHA,
      fresh: true,
      resumed: true,
      contexts: [makeContext({ chunk_id: 'chunk_manual_resume', source: 'manual', score: null })],
    })
    mountPanel(task)
    await flushPromises()

    await wrapper!.findAll('button').find((button) => button.text().includes('手动指定文件'))!.trigger('click')
    await wrapper!.find('.code-context-manual textarea').setValue('services/payment/callback.py')
    await wrapper!.findAll('button').find((button) => button.text().includes('生成人工上下文'))!.trigger('click')
    await flushPromises()

    expect(messageMocks.success).toHaveBeenCalledWith('已补充代码上下文，任务恢复自动流转')
    expect(wrapper!.emitted('refresh')).toHaveLength(1)
  })

  it('DEV-017：渲染任务携带的历史相似任务引用，标注仅参考且不展示旧 Diff', async () => {
    const similar: SimilarTaskRef = {
      task_id: 'TASK-OLD-1',
      version: 2,
      title: '修复支付回调幂等重复入账',
      issue_id: 'I-OLD-1',
      status: 'COMPLETED',
      risk_level: 'medium',
      score: 0.46,
      summary: '历史任务通过幂等键去重解决重复入账。',
    }
    const task = taskFor(
      baseAgentTask({
        status: 'CONTEXT_INDEXED',
        next_action: 'auto_queue_analysis',
        manual_required: false,
        manual_reason: 'none',
        automation_state: 'running',
        code_contexts: [makeContext()],
        similar_task_refs: [similar],
      }),
    )
    mountPanel(task)
    await flushPromises()

    expect(wrapper!.text()).toContain('历史相似任务参考')
    expect(wrapper!.text()).toContain('修复支付回调幂等重复入账')
    expect(wrapper!.text()).toContain('TASK-OLD-1 · v2 · COMPLETED · 相似度 46%')
    expect(wrapper!.text()).toContain('仅参考，不会自动套用旧 Diff')
    // 挂载时不主动请求 /similar，历史引用直接来自任务详情
    expect(apiRequestMock).not.toHaveBeenCalled()
  })
})
