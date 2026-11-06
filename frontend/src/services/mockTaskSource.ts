// mock 任务数据源：仅在数据源模式为 mock 时对外提供演示任务与产物。
// 内容为前端演示种子（TASK-8842/8837/8824/8819），与后端真实任务相互隔离。
import type { AnalysisQuestion, AnalysisSource, DiffFile, ExecutionStep, Task, TimelineEvent } from '../types/domain'

export interface TaskArtifact {
  isPlaceholder?: boolean
  issueSummary: string
  issueBody: string
  analysisTitle: string
  analysisBody: string
  analysisPoints: Array<{ label: string; value: string }>
  decisionSummary?: string[]
  findings?: string[]
  uncertainties?: string[]
  riskReason?: string
  affectedAreas?: string[]
  promptInjectionFlags?: string[]
  analysisRevision?: number
  analysisSources?: AnalysisSource[]
  questions?: AnalysisQuestion[]
  questionTitle: string
  questionBody: string
  planTitle: string
  planItems: Array<{ title: string; detail: string }>
  planFooter: string
  planVersion: string
  timeline: TimelineEvent[]
  executionSteps?: Record<string, ExecutionStep[]>
  diffFiles: DiffFile[]
  relatedCode: Array<{ path: string; range: string; revision: string; score: string }>
  auditNode: string
  replayNote: string
  replayResult: string
}

export const demoTasks: Task[] = [
  {
    id: 'TASK-8842',
    title: '修复支付回调重复消费问题',
    issue: '#1842',
    repository: 'payment-service',
    status: 'WAITING_APPROVAL',
    risk: 'high',
    approver: '陈晓',
    updatedAt: '2026-08-31 10:42',
    branch: 'agent/issue-1842-v1',
    owner: '王琳',
  },
  {
    id: 'TASK-8837',
    title: '补充租户隔离回归测试',
    issue: '#1837',
    repository: 'tenant-api',
    status: 'DIFF_GENERATED',
    risk: 'medium',
    approver: '陈晓',
    updatedAt: '2026-08-31 09:18',
    branch: 'agent/issue-1837-v2',
    owner: '赵文',
  },
  {
    id: 'TASK-8824',
    title: '优化仓库索引超时重试',
    issue: '#1824',
    repository: 'agent-runtime',
    status: 'TESTED',
    risk: 'medium',
    approver: '刘雪',
    updatedAt: '2026-08-30 18:32',
    branch: 'agent/issue-1824-v1',
    owner: '王琳',
    isDemo: true,
  },
  {
    id: 'TASK-8819',
    title: '更新 Gitee Token 失效提示',
    issue: '#1819',
    repository: 'console-web',
    status: 'FAILED',
    risk: 'low',
    approver: '未分配',
    updatedAt: '2026-08-30 16:05',
    branch: 'agent/issue-1819-v1',
    owner: '李航',
  },
]

export function isDemoTaskId(taskId: string): boolean {
  return demoTasks.some((task) => task.id === taskId)
}

const sharedTimeline: TimelineEvent[] = [
  { id: '1', title: '任务已创建', time: '08-31 09:28', status: 'done', duration: '+1s', summary: '由 Issue #1842 创建，基线 main。', detail: '由 Issue #1842 创建，基线分支为 main。' },
  { id: '2', title: 'Issue 已拉取', time: '08-31 09:29', status: 'done', duration: '+2s', summary: '读取 3 条评论与 2 个关联标签。', detail: '读取 3 条评论与 2 个关联标签。' },
  { id: '3', title: '代码上下文已索引', time: '08-31 09:31', status: 'done', duration: '+3s', summary: '召回 6 个高相关代码片段。', detail: '召回 6 个高相关代码片段。', input: '支付回调重复消费 幂等校验', output: 'payment/callback.py: 88-146' },
  { id: '4', title: 'AI 分析完成', time: '08-31 09:36', status: 'done', duration: '+4m', summary: '识别到回调去重窗口未覆盖重试场景。', detail: '识别到回调去重窗口未覆盖重试场景。' },
  { id: '5', title: '修改计划已生成', time: '08-31 09:39', status: 'done', duration: '+1m', summary: '计划 v1 · 涉及 3 个文件。', detail: '计划版本 v1，涉及 3 个文件。' },
  { id: '6', title: '等待人工审批', time: '08-31 09:42', status: 'active', duration: '已 1h24m', summary: '风险等级高，审批意见必填。', detail: '风险等级高，审批意见必填。' },
  { id: '7', title: '生成 Diff', time: '--', status: 'waiting', detail: '审批通过后可执行。' },
  { id: '8', title: '执行测试', time: '--', status: 'waiting', detail: '等待 Diff 生成。' },
]

export const demoArtifacts: Record<string, TaskArtifact> = {
  'TASK-8842': {
    issueSummary: '支付回调的重复推送会重复更新订单状态，并触发下游事件。',
    issueBody:
      '当 Gitee 或第三方支付渠道发生网络重试时，同一回调可能被多次提交。目前回调处理逻辑没有足够稳定的事件幂等控制。',
    analysisTitle: '重复事件未被稳定识别',
    analysisBody:
      '当前实现直接根据订单号更新状态，没有为回调事件建立唯一键。建议以渠道、交易号和事件类型生成幂等键，并在写入订单状态前完成去重。',
    analysisPoints: [
      { label: '影响模块', value: 'payment callback / event store' },
      { label: '建议范围', value: '3 个文件' },
      { label: '预计风险', value: '下游事件兼容性' },
    ],
    questionTitle: '是否需要兼容历史无交易号的回调？',
    questionBody: '该结论会影响幂等键的回退策略，以及是否需要增加订单号与时间窗口的兼容逻辑。',
    planTitle: '增加回调事件去重与回归覆盖',
    planItems: [
      { title: '定义事件幂等键', detail: '在 callback service 中按渠道、交易号、事件类型构造唯一键。' },
      { title: '在更新订单前检查并写入事件记录', detail: '命中重复回调时返回可审计的忽略结果，不触发下游事件。' },
      { title: '补充重复推送回归测试', detail: '覆盖首次处理与重复处理的状态、事件数量和返回结果。' },
    ],
    planFooter: '高风险计划：审批意见必须填写',
    planVersion: 'v1',
    timeline: sharedTimeline,
    diffFiles: [
      {
        path: 'services/payment/callback.py',
        change: '修改',
        additions: 18,
        deletions: 5,
        before: ['def process_callback(payload):', '    order = find_order(payload.order_no)', '    update_order(order, payload.status)', '    publish_event(order.id, payload.status)'],
        after: ['def process_callback(payload):', '    event_key = build_event_key(payload)', '    if event_store.exists(event_key):', '        return CallbackResult.ignored("重复回调")', '    order = find_order(payload.order_no)', '    update_order(order, payload.status)', '    event_store.save(event_key, ttl=86400)', '    publish_event(order.id, payload.status)'],
      },
      {
        path: 'tests/test_callback.py',
        change: '新增',
        additions: 42,
        deletions: 0,
        before: [],
        after: ['def test_duplicate_callback_is_ignored(client):', '    payload = callback_fixture("paid")', '    client.post("/callbacks/gitee", json=payload)', '    response = client.post("/callbacks/gitee", json=payload)', '    assert response.json()["ignored"] is True'],
      },
      {
        path: 'config/settings.py',
        change: '修改',
        additions: 2,
        deletions: 1,
        before: ['CALLBACK_TTL = 3600'],
        after: ['CALLBACK_TTL = 86400'],
      },
    ],
    relatedCode: [
      { path: 'services/payment/callback.py', range: '88-146', revision: '4e91bd2', score: '0.94' },
      { path: 'services/events/store.py', range: '20-68', revision: '4e91bd2', score: '0.88' },
      { path: 'tests/test_callback.py', range: '1-94', revision: '4e91bd2', score: '0.81' },
    ],
    auditNode: 'plan_approval_gate',
    replayNote: '计划已提交给审批人，等待审批动作。\n高风险规则：审批意见必填。',
    replayResult: '审批通过后将生成 Diff 并触发测试节点。',
  },
  'TASK-8824': {
    issueSummary: '仓库索引在网络抖动时会重复等待，导致上下文准备时间过长。',
    issueBody:
      '当前索引任务对瞬时网络错误和服务端限流使用同一套重试策略，失败后会重复扫描已完成分片。需要区分可恢复错误，并保留已完成的索引进度。',
    analysisTitle: '重试策略没有复用已完成进度',
    analysisBody:
      '索引服务在超时后会重新创建完整任务，既增加仓库压力，也让用户难以判断当前进度。建议按分片保存检查点，仅对失败分片执行退避重试。',
    analysisPoints: [
      { label: '影响模块', value: 'index worker / scheduler' },
      { label: '建议范围', value: '2 个文件' },
      { label: '测试结果', value: '12 项通过' },
    ],
    questionTitle: '是否需要保留失败分片的历史快照？',
    questionBody: '保留快照可以支持失败诊断和增量恢复，但会增加短期存储成本，默认建议保留最近一次失败记录。',
    planTitle: '按分片保存进度并优化退避重试',
    planItems: [
      { title: '记录分片检查点', detail: '索引完成一个分片后立即写入进度，重试时跳过已完成内容。' },
      { title: '区分错误类型', detail: '对网络超时、限流和不可恢复错误分别设置退避与终止策略。' },
      { title: '补充重试回归测试', detail: '覆盖断点恢复、重复执行和失败快照保留场景。' },
    ],
    planFooter: '测试已完成：12 项通过，0 项失败',
    planVersion: 'v1',
    timeline: [
      { id: '1', title: '任务已创建', time: '08-30 15:12', status: 'done', duration: '+1s', summary: '由 Issue #1824 创建，基线 main。', detail: '由 Issue #1824 创建，基线分支为 main。' },
      { id: '2', title: 'Issue 已拉取', time: '08-30 15:13', status: 'done', duration: '+2s', summary: '读取 4 条评论与 2 个关联标签。', detail: '读取 4 条评论与 2 个关联标签。' },
      { id: '3', title: '代码上下文已索引', time: '08-30 15:17', status: 'done', duration: '+4m', summary: '召回 5 个高相关代码片段。', detail: '召回 5 个高相关代码片段。', input: 'index timeout retry checkpoint', output: 'workers/index_worker.ts: 44-132' },
      { id: '4', title: 'AI 分析完成', time: '08-30 15:24', status: 'done', duration: '+7m', summary: '识别到重试会重复扫描已完成分片。', detail: '识别到重试会重复扫描已完成分片。' },
      { id: '5', title: '修改计划已生成', time: '08-30 15:28', status: 'done', duration: '+4m', summary: '计划 v1 · 涉及 2 个文件。', detail: '计划版本 v1，涉及 2 个文件。' },
      { id: '6', title: '等待人工审批', time: '08-30 15:32', status: 'done', duration: '+4m', summary: '风险等级中，审批通过后自动生成 Diff。', detail: '风险等级中，审批通过后自动生成 Diff。' },
      { id: '7', title: '生成 Diff', time: '08-30 15:38', status: 'done', duration: '+6m', summary: '已生成 2 个变更文件。', detail: 'Diff 已生成，进入测试阶段。', output: '已生成 2 个变更文件。' },
      { id: '8', title: '执行测试', time: '08-30 15:46', status: 'active', duration: '已完成', summary: '12 项测试通过，0 项失败。', detail: '测试已完成，等待任务归档。' },
    ],
    diffFiles: [
      {
        path: 'workers/index_worker.ts',
        change: '修改',
        additions: 24,
        deletions: 9,
        before: ['async function rebuild(repo) {', '  return scanAll(repo)', '}'],
        after: ['async function rebuild(repo) {', '  const checkpoint = await loadCheckpoint(repo)', '  return scanRemaining(repo, checkpoint)', '}'],
      },
      {
        path: 'workers/retry_policy.ts',
        change: '修改',
        additions: 18,
        deletions: 6,
        before: ['export const retry = (error) => delay(1000)', ''],
        after: ['export const retry = (error) => {', '  if (isRateLimit(error)) return backoff(3000)', '  if (isTransient(error)) return backoff(1000)', '  return stop()'],
      },
    ],
    relatedCode: [
      { path: 'workers/index_worker.ts', range: '44-132', revision: '8ac41de', score: '0.93' },
      { path: 'workers/retry_policy.ts', range: '1-76', revision: '8ac41de', score: '0.87' },
      { path: 'tests/index_retry.spec.ts', range: '1-108', revision: '8ac41de', score: '0.82' },
    ],
    auditNode: 'test_execution',
    replayNote: '测试节点已完成，当前任务可以归档。',
    replayResult: '所有自动化测试通过，未发现阻断问题。',
  },
  'TASK-8819': {
    issueSummary: 'Gitee Token 在拉取仓库上下文时失效，任务无法继续执行。',
    issueBody:
      '任务创建后，系统在访问 console-web 仓库时收到授权失效响应。为避免重复请求，当前流程已暂停，等待项目授权恢复后再重新拉取上下文。',
    analysisTitle: '仓库授权已失效，流程在检索阶段中断',
    analysisBody:
      '失败原因已定位到 Gitee Token 过期或被撤销，当前没有进入 AI 分析、计划生成和 Diff 阶段。恢复项目授权后，可以复用任务信息并从检索阶段重新开始。',
    analysisPoints: [
      { label: '失败节点', value: '仓库上下文检索' },
      { label: '失败原因', value: 'Gitee Token 失效' },
      { label: '恢复方式', value: '更新授权后重试' },
    ],
    questionTitle: '恢复授权后自动继续该任务？',
    questionBody: '系统默认复用当前 Issue、分支和任务配置，从失败节点重新拉取上下文，不会重复创建任务。',
    planTitle: '恢复授权并重新拉取上下文',
    planItems: [
      { title: '更新项目 Gitee Token', detail: '在项目配置中重新验证 console-web 仓库授权。' },
      { title: '从检索节点恢复任务', detail: '复用当前任务上下文，跳过已完成的 Issue 拉取步骤。' },
      { title: '重新生成分析与修改计划', detail: '授权恢复后由 Agent 自动继续，异常会再次进入待处理列表。' },
    ],
    planFooter: '授权恢复后可重新进入自动化队列',
    planVersion: '恢复方案',
    timeline: [
      { id: '1', title: '任务已创建', time: '08-30 15:58', status: 'done', duration: '+1s', summary: '由 Issue #1819 创建，基线 main。', detail: '由 Issue #1819 创建，基线分支为 main。' },
      { id: '2', title: 'Issue 已拉取', time: '08-30 15:59', status: 'done', duration: '+2s', summary: '读取 Issue 描述与关联标签。', detail: '读取 Issue 描述与关联标签。' },
      { id: '3', title: '代码上下文检索失败', time: '08-30 16:05', status: 'failed', duration: '已停止', summary: 'Gitee 返回 Token 失效，流程已暂停。', detail: 'Gitee 返回 Token 失效，流程已暂停。' },
      { id: '4', title: '等待授权恢复', time: '--', status: 'active', duration: '待处理', summary: '更新授权后可从检索节点重试。', detail: '更新授权后可从检索节点重试。' },
    ],
    diffFiles: [],
    relatedCode: [
      { path: 'config/gitee.ts', range: '1-58', revision: 'b2a7fd1', score: '0.91' },
      { path: 'services/repository_auth.ts', range: '22-96', revision: 'b2a7fd1', score: '0.86' },
      { path: 'tests/gitee_auth.spec.ts', range: '1-72', revision: 'b2a7fd1', score: '0.78' },
    ],
    auditNode: 'repository_context_fetch',
    replayNote: '任务在仓库上下文检索节点失败，等待授权恢复。',
    replayResult: '更新授权后将从检索节点继续执行。',
  },
  'TASK-8837': {
    issueSummary: '租户隔离在回归测试中出现覆盖不全，某些共享查询仍可能串租户上下文。',
    issueBody:
      '在多租户请求链路里，部分仓库查询函数没有统一挂载租户过滤器。新增回归测试后，需要补齐 tenant scope 的显式校验。',
    analysisTitle: '租户上下文过滤缺失',
    analysisBody:
      '当前仓库在查询与写入层之间缺少稳定的 tenant boundary。建议将 tenant_id 透传到 repository 层，并补齐隔离回归测试以覆盖共享表查询。',
    analysisPoints: [
      { label: '影响模块', value: 'tenant api / repository / tests' },
      { label: '建议范围', value: '3 个文件' },
      { label: '预计风险', value: '历史数据兼容性' },
    ],
    questionTitle: '是否需要强制所有查询入口都透传 tenant_id？',
    questionBody: '该决定会影响 repository 的函数签名，以及共享查询是否允许兼容旧调用方。',
    planTitle: '补齐租户过滤与隔离回归',
    planItems: [
      { title: '显式传递 tenant_id', detail: '在 service 层将租户上下文传入 repository 查询入口。' },
      { title: '收紧 repository 查询范围', detail: '所有读写都必须带 tenant 过滤条件，避免跨租户读取。' },
      { title: '新增隔离回归测试', detail: '覆盖共享表查询、跨租户读取与 404 兜底场景。' },
    ],
    planFooter: '可直接进入 Diff 生成，审批意见为可选',
    planVersion: 'v2',
    timeline: [
      { id: '1', title: '任务已创建', time: '08-31 08:57', status: 'done', duration: '+1s', summary: '由 Issue #1837 创建，基线 main。', detail: '由 Issue #1837 创建，基线分支为 main。' },
      { id: '2', title: 'Issue 已拉取', time: '08-31 08:58', status: 'done', duration: '+2s', summary: '读取 2 条评论与 1 个关联标签。', detail: '读取 2 条评论与 1 个关联标签。' },
      { id: '3', title: '代码上下文已索引', time: '08-31 09:02', status: 'done', duration: '+3s', summary: '召回 5 个高相关代码片段。', detail: '召回 5 个高相关代码片段。', input: 'tenant isolation 回归测试 共享查询', output: 'tenant/service.py: 32-104' },
      { id: '4', title: 'AI 分析完成', time: '08-31 09:08', status: 'done', duration: '+5m', summary: '识别到 repository 层缺少显式 tenant filter。', detail: '识别到 repository 层缺少显式 tenant filter。' },
      { id: '5', title: '修改计划已生成', time: '08-31 09:12', status: 'done', duration: '+1m', summary: '计划 v2 · 涉及 3 个文件。', detail: '计划版本 v2，涉及 3 个文件。' },
      { id: '6', title: '等待人工审批', time: '08-31 09:16', status: 'done', duration: '+4m', summary: '风险已降级，允许直接进入 Diff。', detail: '风险已降级，允许直接进入 Diff 生成。' },
      { id: '7', title: '生成 Diff', time: '08-31 09:18', status: 'active', duration: '已 20m', summary: '已生成 3 个变更文件。', detail: 'Diff 已生成，等待测试阶段。', output: '已生成 3 个变更文件。' },
      { id: '8', title: '执行测试', time: '--', status: 'waiting', detail: '等待 Diff 审核完成后自动执行。' },
    ],
    diffFiles: [
      {
        path: 'services/tenant/service.py',
        change: '修改',
        additions: 15,
        deletions: 4,
        before: ['def list_records(repo):', '    return repo.list_all()'],
        after: ['def list_records(repo, tenant_id):', '    return repo.list_by_tenant(tenant_id)', '', 'def get_record(repo, record_id, tenant_id):', '    return repo.get_by_tenant(record_id, tenant_id)'],
      },
      {
        path: 'repositories/tenant_repository.py',
        change: '修改',
        additions: 21,
        deletions: 6,
        before: ['def list_all(self):', '    return self.session.query(TenantRecord).all()'],
        after: ['def list_by_tenant(self, tenant_id):', '    return self.session.query(TenantRecord).filter(TenantRecord.tenant_id == tenant_id).all()', '', 'def get_by_tenant(self, record_id, tenant_id):', '    return self.session.query(TenantRecord).filter(TenantRecord.id == record_id, TenantRecord.tenant_id == tenant_id).one_or_none()'],
      },
      {
        path: 'tests/test_tenant_isolation.py',
        change: '新增',
        additions: 38,
        deletions: 0,
        before: [],
        after: ['def test_shared_query_respects_tenant_scope(client):', '    tenant_a = seed_tenant("tenant-a")', '    tenant_b = seed_tenant("tenant-b")', '    response = client.get(f"/tenants/{tenant_a}/records")', '    assert all(item["tenant_id"] == tenant_a for item in response.json()["items"])', '', 'def test_cross_tenant_record_is_hidden(client):', '    assert client.get(f"/tenants/{tenant_a}/records/{tenant_b_record_id}").status_code == 404'],
      },
    ],
    relatedCode: [
      { path: 'tenant/service.py', range: '32-104', revision: 'd2f07b4c', score: '0.92' },
      { path: 'repositories/tenant_repository.py', range: '12-81', revision: 'd2f07b4c', score: '0.86' },
      { path: 'tests/test_tenant_isolation.py', range: '1-116', revision: 'd2f07b4c', score: '0.79' },
    ],
    auditNode: 'diff_generation_gate',
    replayNote: 'Diff 已生成，等待人工审阅。',
    replayResult: '当前版本已进入测试前检查阶段。',
  },
}
