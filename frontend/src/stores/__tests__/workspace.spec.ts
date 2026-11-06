import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { makeAgentTask, makeAnalysis, makeDiffArtifact } from '../../test/fixtures'
import { setDataSourceMode } from '../../services/dataSourceMode'
import { useWorkspaceStore } from '../workspace'
import type { CodeContext, RepositoryIndex } from '../../types/domain'

const { apiRequestMock } = vi.hoisted(() => ({ apiRequestMock: vi.fn() }))

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

describe('workspace store：AgentTask → Task/Artifact 映射', () => {
  beforeEach(() => {
    setDataSourceMode('real')
    localStorage.clear()
    setActivePinia(createPinia())
  })

  afterEach(() => {
    setDataSourceMode('real')
  })

  it('把带阻塞问题的分析任务映射为需人工处理的 Task 与完整产物', () => {
    const store = useWorkspaceStore()
    const created = store.createTaskFromApi(makeAgentTask())

    expect(created.status).toBe('ANALYZED')
    expect(created.risk).toBe('high')
    expect(created.manualRequired).toBe(true)
    expect(created.manualReason).toBe('answer_required')
    expect(created.nextAction).toBe('answer_analysis_question')
    expect(created.branch).toBe('agent/main/issue-IDEMO001-v1')
    expect(created.issue).toBe('#IDEMO001')
    expect(created.automationState).toBe('blocked')

    const artifact = store.artifactFor('TASK-9001')
    expect(artifact.isPlaceholder).toBeFalsy()
    expect(artifact.analysisTitle).toBe('AI 分析完成，存在待确认问题')
    expect(artifact.decisionSummary).toHaveLength(3)
    expect(artifact.findings).toEqual(['Issue 定位为缺陷修复类任务。'])
    expect(artifact.affectedAreas).toEqual(['回调链路', '支付与资金链路'])
    expect(artifact.uncertainties).toEqual(['复现步骤不完整。'])
    expect(artifact.promptInjectionFlags).toEqual(['忽略之前的指令'])
    expect(artifact.analysisRevision).toBe(1)
    expect(artifact.analysisSources).toHaveLength(2)
    expect(artifact.questions).toHaveLength(2)
    expect(artifact.analysisPoints.find((point) => point.label === '风险等级')?.value).toBe('高风险')
    expect(artifact.analysisPoints.find((point) => point.label === '下一步')?.value).toBe('补充分析问题回答')
    expect(artifact.replayResult).toContain('1 个阻塞问题')
  })

  it('全部问题确认后产物恢复自动流转文案', () => {
    const store = useWorkspaceStore()
    store.createTaskFromApi(
      makeAgentTask({
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
          sources: [
            { source_type: 'issue_title', source_ref: 'Issue #IDEMO001 标题', content: '修复支付回调重复记账', relevance: 'high' },
            { source_type: 'answered_question', source_ref: '问题确认 q_blocking_1', content: 'Q: 兼容\nA: 仅兼容当前主版本。', relevance: 'high' },
          ],
        }),
      }),
    )

    const artifact = store.artifactFor('TASK-9001')
    expect(artifact.analysisTitle).toBe('AI 分析完成')
    expect(artifact.analysisRevision).toBe(2)
    expect(artifact.analysisPoints.find((point) => point.label === '下一步')?.value).toBe('自动生成修改计划')
    expect(artifact.analysisSources?.some((source) => source.source_type === 'answered_question')).toBe(true)
    expect(artifact.replayNote).toBe('AI 分析已完成，等待后续计划生成模块推进。')
  })

  it('失败任务无分析时回落到失败占位文案', () => {
    const store = useWorkspaceStore()
    store.createTaskFromApi(
      makeAgentTask({
        id: 'TASK-9002',
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

    const task = store.taskById('TASK-9002')
    expect(task?.status).toBe('FAILED')
    expect(task?.failureNode).toBe('上下文聚合')
    expect(task?.risk).toBe('pending')

    const artifact = store.artifactFor('TASK-9002')
    expect(artifact.isPlaceholder).toBe(true)
    expect(artifact.analysisTitle).toBe('任务在「上下文聚合」节点失败')
    expect(artifact.analysisBody).toBe('Issue 信息过少。')
    expect(artifact.questionBody).toContain('Issue 或仓库上下文不足')
    expect(artifact.analysisPoints.find((point) => point.label === '下一步')?.value).toBe('重试或取消任务')
  })

  it('createTaskFromApi 覆盖同 id 任务并注册产物', () => {
    const store = useWorkspaceStore()
    store.createTaskFromApi(makeAgentTask())
    const updated = makeAgentTask({
      manual_required: false,
      manual_reason: 'none',
      next_action: 'auto_generate_plan',
      automation_state: 'running',
      analysis: makeAnalysis({ revision: 2, questions: [] }),
    })
    store.createTaskFromApi(updated)

    expect(store.tasks.filter((task) => task.id === 'TASK-9001')).toHaveLength(1)
    expect(store.taskById('TASK-9001')?.manualReason).toBe('none')
    expect(store.artifactFor('TASK-9001').analysisRevision).toBe(2)
  })

  it('replaceTaskList 整体替换并修正选中（不保留本地残留）', () => {
    const store = useWorkspaceStore()
    store.createTaskFromApi(makeAgentTask())
    store.createTaskFromApi(
      makeAgentTask({
        id: 'TASK-9002',
        manual_required: false,
        manual_reason: 'none',
        next_action: 'auto_generate_plan',
        automation_state: 'running',
        analysis: makeAnalysis({ questions: [] }),
      }),
    )
    expect(store.tasks.map((task) => task.id)).toEqual(['TASK-9002', 'TASK-9001'])
    expect(store.selectedTaskId).toBe('TASK-9002')

    // replace 语义：只保留传入列表，残留任务（TASK-9002 被剔除）一并清掉
    store.replaceTaskList(store.tasks.filter((task) => task.id === 'TASK-9001'))

    expect(store.tasks.map((task) => task.id)).toEqual(['TASK-9001'])
    expect(store.selectedTaskId).toBe('TASK-9001')
    expect(store.taskById('TASK-9002')).toBeUndefined()
    expect(store.artifactFor('TASK-9001').analysisRevision).toBe(1)
  })

  it('未知 next_action 显示原值，空值时回落「等待自动流转」', () => {
    const store = useWorkspaceStore()
    store.createTaskFromApi(
      makeAgentTask({
        manual_required: false,
        manual_reason: 'none',
        next_action: 'future_action_xyz',
        automation_state: 'running',
        analysis: makeAnalysis({ questions: [] }),
      }),
    )
    const artifact = store.artifactFor('TASK-9001')
    expect(artifact.analysisPoints.find((point) => point.label === '下一步')?.value).toBe('future_action_xyz')

    store.createTaskFromApi(
      makeAgentTask({
        id: 'TASK-9003',
        manual_required: false,
        manual_reason: 'none',
        next_action: '',
        automation_state: 'running',
        analysis: makeAnalysis({ questions: [] }),
      }),
    )
    const emptyArtifact = store.artifactFor('TASK-9003')
    expect(emptyArtifact.analysisPoints.find((point) => point.label === '下一步')?.value).toBe('等待自动流转')
  })
})

describe('workspace store：DEV-008 索引字段透传与索引记录存取', () => {
  const SHA = 'a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2'

  function makeContext(overrides: Partial<CodeContext> = {}): CodeContext {
    return {
      chunk_id: 'chunk_1',
      file_path: 'services/payment/callback.py',
      start_line: 12,
      end_line: 34,
      language: 'python',
      symbol_name: 'handle_payment_callback',
      content: 'def handle_payment_callback(payload):',
      score: 0.82,
      commit_sha: SHA,
      index_id: 'idx_100',
      fresh: true,
      source: 'milvus',
      ...overrides,
    }
  }

  function makeIndex(overrides: Partial<RepositoryIndex> = {}): RepositoryIndex {
    return {
      id: 'idx_100',
      repository_id: 'repo_payment_service',
      project_id: 'proj_payment',
      base_branch: 'main',
      commit_sha: SHA,
      status: 'PENDING',
      mode: 'full',
      total_files: 0,
      processed_files: 0,
      chunk_count: 0,
      milvus_collection: null,
      error_message: null,
      error_stage: null,
      requested_by: 'user_admin',
      requested_by_name: '小泽',
      retry_count: 0,
      requested_at: '2026-09-04T09:00:00Z',
      created_at: '2026-09-04T09:00:00Z',
      updated_at: '2026-09-04T09:00:00Z',
      started_at: null,
      finished_at: null,
      ...overrides,
    }
  }

  beforeEach(() => {
    setDataSourceMode('real')
    localStorage.clear()
    setActivePinia(createPinia())
  })

  it('等待索引任务映射索引字段，索引记录按 id 更新存取', () => {
    const store = useWorkspaceStore()
    const task = store.createTaskFromApi(
      makeAgentTask({
        id: 'TASK-9100',
        issue_id: 'IDEMO100',
        status: 'ISSUE_FETCHED',
        next_action: 'wait_index',
        automation_state: 'blocked',
        manual_required: true,
        manual_reason: 'context_missing',
        risk_level: null,
        risk_source: 'pending_ai_analysis',
        analysis: null,
        base_commit_sha: SHA,
        related_index_id: 'idx_100',
        code_contexts: [makeContext()],
      }),
    )

    expect(task.repositoryId).toBe('repo_payment_service')
    expect(task.baseCommitSha).toBe(SHA)
    expect(task.relatedIndexId).toBe('idx_100')
    expect(task.codeContexts).toHaveLength(1)
    expect(task.codeContexts?.[0].file_path).toBe('services/payment/callback.py')

    const artifact = store.artifactFor('TASK-9100')
    expect(artifact.analysisPoints.find((point) => point.label === '下一步')?.value).toBe('等待仓库索引完成')

    expect(store.indexById('idx_100')).toBeNull()
    store.mergeRepositoryIndex(makeIndex())
    expect(store.indexById('idx_100')?.status).toBe('PENDING')
    store.mergeRepositoryIndex(makeIndex({ status: 'COMPLETED', total_files: 3, chunk_count: 12 }))
    expect(store.indexById('idx_100')?.status).toBe('COMPLETED')
    expect(store.indexById('idx_100')?.chunk_count).toBe(12)
    expect(store.repoIndexes).toHaveLength(1)
  })
})

describe('workspace store：DEV-009 数据源模式与快照隔离', () => {
  beforeEach(() => {
    setDataSourceMode('mock')
    localStorage.clear()
    setActivePinia(createPinia())
  })

  afterEach(() => {
    setDataSourceMode('real')
  })

  it('mock 模式无快照时播种 4 条 demo 任务并默认选中 TASK-8842', () => {
    const store = useWorkspaceStore()
    expect(store.tasks.map((task) => task.id)).toEqual(['TASK-8842', 'TASK-8837', 'TASK-8824', 'TASK-8819'])
    expect(store.selectedTaskId).toBe('TASK-8842')
    expect(store.taskById('TASK-8824')?.isDemo).toBe(true)
  })

  it('real 模式无快照时列表为空且不选中任何任务', () => {
    setDataSourceMode('real')
    const store = useWorkspaceStore()
    expect(store.tasks).toEqual([])
    expect(store.selectedTaskId).toBe('')
  })

  it('快照 key 带模式后缀：mock 快照不污染 real 新实例', () => {
    const mockStore = useWorkspaceStore()
    mockStore.selectTask('TASK-8824')
    expect(localStorage.getItem('frontend-workspace-snapshot-v1-mock')).not.toBeNull()
    expect(localStorage.getItem('frontend-workspace-snapshot-v1-real')).toBeNull()

    setDataSourceMode('real')
    setActivePinia(createPinia())
    const realStore = useWorkspaceStore()
    expect(realStore.tasks).toEqual([])
    expect(realStore.selectedTaskId).toBe('')
    expect(localStorage.getItem('frontend-workspace-snapshot-v1-mock')).not.toBeNull()
  })

  it('旧版无模式后缀快照 key 在 store 初始化时被一次性清理', () => {
    localStorage.setItem('frontend-workspace-snapshot-v1', JSON.stringify({ stale: true }))
    useWorkspaceStore()
    expect(localStorage.getItem('frontend-workspace-snapshot-v1')).toBeNull()
  })

  it('refreshTaskList 在 mock 模式空转：不发请求且不改变本地任务', async () => {
    const store = useWorkspaceStore()
    await store.refreshTaskList({ status: 'FAILED' })
    expect(store.tasks).toHaveLength(4)
    expect(store.taskById('TASK-8819')).toBeDefined()
  })

  it('real 模式刷新默认携带当前项目 ID，避免请求全量任务', async () => {
    setDataSourceMode('real')
    localStorage.clear()
    setActivePinia(createPinia())
    apiRequestMock.mockResolvedValueOnce([])

    const store = useWorkspaceStore()
    await store.refreshTaskList()

    expect(apiRequestMock).toHaveBeenCalledWith('/api/tasks?project_id=proj_payment')
    expect(store.tasks).toEqual([])
  })

  it('refreshDashboardSummary 真实模式会请求带时间范围的摘要接口并写入 store', async () => {
    setDataSourceMode('real')
    localStorage.clear()
    setActivePinia(createPinia())
    apiRequestMock.mockResolvedValueOnce({
      runningTasks: 2,
      pendingApprovals: 3,
      completedThisWeek: 5,
      savedHours: 18,
      automationRateLabel: '80%',
      overdueApprovals: 1,
      weeklyGrowthLabel: '+25%',
      trend: [
        { day: '周一', created: 1, done: 0 },
        { day: '周二', created: 0, done: 1 },
        { day: '周三', created: 1, done: 1 },
        { day: '周四', created: 0, done: 0 },
        { day: '周五', created: 2, done: 1 },
        { day: '周六', created: 0, done: 1 },
        { day: '今天', created: 1, done: 1 },
      ],
      events: [{ level: 'success', title: '测试通过', detail: 'TASK-9001 · 10:00' }],
    })
    const store = useWorkspaceStore()
    await store.refreshDashboardSummary(30)

    expect(apiRequestMock).toHaveBeenCalledWith('/api/dashboard/automation-summary?project_name=%E6%94%AF%E4%BB%98%E4%B8%AD%E5%8F%B0&days=30')
    expect(store.dashboardSummary.pendingApprovals).toBe(3)
    expect(store.dashboardSummary.events[0].title).toBe('测试通过')
  })

  it('ensureTaskLoaded mock：已播种任务直接返回，未知任务抛 404', async () => {
    const store = useWorkspaceStore()
    await expect(store.ensureTaskLoaded('TASK-8824')).resolves.toBeUndefined()
    await expect(store.ensureTaskLoaded('TASK-9999')).rejects.toMatchObject({ status: 404 })
  })

  it('retryRemote 在 mock 模式本地重置失败任务状态', async () => {
    const store = useWorkspaceStore()
    expect(store.taskById('TASK-8819')?.status).toBe('FAILED')
    await store.retryRemote('TASK-8819')
    expect(store.taskById('TASK-8819')?.status).toBe('CREATED')
  })
})

describe('workspace store：DEV-011 Diff 产物缓存与生成动作', () => {
  beforeEach(() => {
    setDataSourceMode('real')
    localStorage.clear()
    setActivePinia(createPinia())
    apiRequestMock.mockReset()
  })

  afterEach(() => {
    setDataSourceMode('real')
  })

  it('createTaskFromApi 同步 diffsByTask；mock 模式 loadDiffs 只清空缓存', async () => {
    const artifact = makeDiffArtifact()
    const store = useWorkspaceStore()
    store.createTaskFromApi(
      makeAgentTask({
        status: 'DIFF_GENERATED',
        next_action: 'auto_run_test',
        diffs: [artifact],
      }),
    )
    expect(store.diffsByTask['TASK-9001']).toEqual([artifact])

    setDataSourceMode('mock')
    setActivePinia(createPinia())
    const mockStore = useWorkspaceStore()
    mockStore.diffsByTask['TASK-9001'] = [artifact]
    await mockStore.loadDiffs('TASK-9001')
    expect(apiRequestMock).not.toHaveBeenCalled()
    expect(mockStore.diffsByTask['TASK-9001']).toEqual([])
  })

  it('real loadDiffs 拉取 GET /diffs 并写入缓存', async () => {
    apiRequestMock.mockResolvedValueOnce([makeDiffArtifact()])
    const store = useWorkspaceStore()
    await store.loadDiffs('TASK-9001')

    expect(apiRequestMock).toHaveBeenCalledWith('/api/tasks/TASK-9001/diffs')
    expect(store.diffsByTask['TASK-9001']).toHaveLength(1)
    expect(store.diffsByTask['TASK-9001'][0].plan_version).toBe(1)
  })

  it('real generateDiff POST 后任务落库为 DIFF_GENERATED 并吸收产物；mock 返回 null', async () => {
    const artifact = makeDiffArtifact()
    apiRequestMock.mockResolvedValueOnce(
      makeAgentTask({
        status: 'DIFF_GENERATED',
        next_action: 'auto_run_test',
        diffs: [artifact],
      }),
    )
    const store = useWorkspaceStore()
    const next = await store.generateDiff('TASK-9001')

    expect(apiRequestMock).toHaveBeenCalledWith('/api/tasks/TASK-9001/diffs/generate', { method: 'POST' })
    expect(next?.status).toBe('DIFF_GENERATED')
    expect(store.taskById('TASK-9001')?.status).toBe('DIFF_GENERATED')
    expect(store.taskById('TASK-9001')?.nextAction).toBe('auto_run_test')
    expect(store.diffsByTask['TASK-9001']).toEqual([artifact])

    setDataSourceMode('mock')
    apiRequestMock.mockReset()
    expect(await store.generateDiff('TASK-9001')).toBeNull()
    expect(apiRequestMock).not.toHaveBeenCalled()
  })
})
