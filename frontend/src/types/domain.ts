export type RiskLevel = 'pending' | 'low' | 'medium' | 'high'

export type AnalysisSourceType =
  | 'issue_title'
  | 'issue_body'
  | 'issue_comment'
  | 'issue_label'
  | 'repository'
  | 'code_context'
  | 'history'
  | 'project_default'
  | 'answered_question'

export type AnalysisRelevance = 'low' | 'medium' | 'high'

export interface AnalysisSource {
  source_type: AnalysisSourceType
  source_ref: string
  content: string
  relevance: AnalysisRelevance
}

export interface AnalysisQuestion {
  id: string
  question: string
  reason: string
  blocking: boolean
  default_answer?: string | null
  answer?: string | null
  answered: boolean
}

export interface AnalysisResult {
  task_id: string
  task_version: number
  decision_summary: string[]
  overview: string
  findings: string[]
  affected_areas: string[]
  suggested_risk_level: RiskThreshold
  risk_reason: string
  uncertainties: string[]
  questions: AnalysisQuestion[]
  sources: AnalysisSource[]
  prompt_injection_flags: string[]
  generated_at: string
  provider: string
  revision: number
}

export type TaskStatus =
  | 'CREATED'
  | 'ISSUE_FETCHED'
  | 'CONTEXT_INDEXED'
  | 'ANALYSIS_QUEUED'
  | 'ANALYZED'
  | 'PLAN_GENERATED'
  | 'WAITING_APPROVAL'
  | 'APPROVED'
  | 'DIFF_GENERATED'
  | 'TEST_RUNNING'
  | 'TEST_FAILED'
  | 'TESTED'
  | 'COMPLETED'
  | 'FAILED'
  | 'CANCELLED'

export interface Task {
  id: string
  title: string
  issue: string
  repository: string
  repositoryId?: string
  projectId?: string
  projectName?: string
  status: TaskStatus
  risk: RiskLevel
  approver: string
  updatedAt: string
  createdAt?: string
  branch: string
  owner: string
  automationState?: 'running' | 'blocked' | 'waiting_approval' | 'completed'
  manualRequired?: boolean
  manualReason?: string
  nextAction?: string
  failureNode?: string | null
  failureReason?: string | null
  retryCount?: number
  baseCommitSha?: string | null
  teamRequestId?: string | null
  teamRequestNo?: string | null
  giteeIssueUrl?: string | null
  relatedIndexId?: string | null
  codeContexts?: CodeContext[]
  // DEV-017 自动上下文补齐：历史相似任务引用（仅参考，不自动套用旧 Diff）
  similarTaskRefs?: SimilarTaskRef[]
  isDemo?: boolean
}

// DEV-017 历史相似任务轻量引用，字段与后端 SimilarTaskRef 对齐（snake_case）
export interface SimilarTaskRef {
  task_id: string
  version: number
  title: string
  issue_id: string
  status: TaskStatus
  risk_level: RiskLevel | null
  score: number
  summary: string
}

export interface IssueCommentSnapshot {
  id: string
  user_name: string
  body: string
  created_at?: string | null
  raw_response: Record<string, unknown>
}

export interface IssueSnapshot {
  title: string
  body: string
  comments: IssueCommentSnapshot[]
  labels: string[]
  raw_response: Record<string, unknown>
  fetched_at: string
}

export interface AgentTask {
  id: string
  version: number
  project_id: string
  project_name: string
  repository_id: string
  repository_name: string
  repository_url: string
  issue_id: string
  title: string
  status: TaskStatus
  risk_level?: Exclude<RiskLevel, 'pending'> | null
  risk_source: 'pending_ai_analysis' | 'ai_analysis' | 'plan' | 'plan_approval'
  base_branch: string
  test_command: string
  approval_owner_id: string | null
  approval_owner_name: string | null
  work_branch: string
  issue_snapshot: IssueSnapshot
  analysis?: AnalysisResult | null
  plans?: ModificationPlan[] | null
  diffs?: DiffArtifact[] | null
  test_runs?: TestRun[] | null
  pr_descriptions?: PrDescriptionRecord[] | null
  automation_state: 'running' | 'blocked' | 'waiting_approval' | 'completed'
  manual_required: boolean
  manual_reason: string | null
  next_action: string
  failure_node?: string | null
  failure_reason?: string | null
  failed_at?: string | null
  retry_count: number
  base_commit_sha?: string | null
  // DEV-016-C：从平台需求派生任务时的来源闭环信息
  team_request_id?: string | null
  team_request_no?: string | null
  gitee_issue_url?: string | null
  related_index_id?: string | null
  code_contexts?: CodeContext[]
  // DEV-017：自动上下文补齐检索到的历史相似任务引用
  similar_task_refs?: SimilarTaskRef[]
  cancelled_at?: string | null
  events: AuditEvent[]
  execution_steps?: ExecutionStep[] | null
  created_by: string
  created_by_name: string
  created_at: string
  updated_at: string
}

export type RepositoryIndexStatus = 'PENDING' | 'SYNCING' | 'PARSING' | 'EMBEDDING' | 'STORING' | 'COMPLETED' | 'FAILED'

export interface RepositoryIndex {
  id: string
  repository_id: string
  project_id: string
  base_branch: string
  commit_sha: string
  status: RepositoryIndexStatus
  mode: 'full' | 'incremental'
  total_files: number
  processed_files: number
  chunk_count: number
  milvus_collection: string | null
  error_message: string | null
  error_stage: string | null
  requested_by: string
  requested_by_name: string
  retry_count: number
  requested_at: string
  created_at: string
  updated_at: string
  started_at: string | null
  finished_at: string | null
}

export type ContextSource = 'milvus' | 'manual' | 'fallback'

export interface CodeContext {
  chunk_id: string
  file_path: string
  start_line: number
  end_line: number
  language: string
  symbol_name: string | null
  content: string
  score: number | null
  commit_sha: string
  index_id: string
  fresh: boolean
  source: ContextSource
}

export type PlanStatus = 'DRAFT' | 'WAITING_APPROVAL' | 'APPROVED' | 'REJECTED' | 'SUPERSEDED'
export type PlanChangeType = 'modify' | 'add' | 'delete' | 'unknown'
export type PlanProvider = 'deepseek' | 'mvp-rule-based'

export type DiffGenerationStatus = 'SUCCEEDED' | 'FALLBACK' | 'FAILED'
export type DiffExecutionMode = 'REAL' | 'DEMO_FALLBACK'
export type DiffFileChangeType = 'add' | 'modify' | 'delete'

export interface DiffFileRecord {
  id: string
  file_path: string
  change_type: DiffFileChangeType
  patch: string
  added_lines: number
  deleted_lines: number
  before: string[]
  after: string[]
}

export interface DiffArtifact {
  id: string
  task_id: string
  plan_id: string
  plan_version: number
  execution_step: string
  generation_status: DiffGenerationStatus
  execution_mode: DiffExecutionMode
  is_demo_fallback: boolean
  fallback_reason: string | null
  real_failure_reason: string | null
  generated_at: string
  files: DiffFileRecord[]
  test_summary: string
  pr_description_draft: string
}

export type TestRunStatus = 'running' | 'passed' | 'failed' | 'skipped'
export type TestRunnerMode = 'docker' | 'mocked_runner' | 'unavailable'

export interface TestRun {
  id: string
  task_id: string
  diff_artifact_id: string
  plan_id: string
  plan_version: number
  command: string
  exit_code: number | null
  stdout: string
  stderr: string
  status: TestRunStatus
  duration: number
  attempt: number
  started_at: string
  finished_at: string | null
  failure_reason: string | null
  blocked_reason: string | null
  runner_mode: TestRunnerMode
}

// PR 描述模块状态：PENDING（任务无记录）/GENERATING（生成中）为任务级展示状态；
// GENERATED/FAILED 为记录级状态（PrDescriptionRecord.generation_status）。
export type PrDescriptionStatus = 'PENDING' | 'GENERATING' | 'GENERATED' | 'FAILED'
export type PrDescriptionRecordStatus = Exclude<PrDescriptionStatus, 'PENDING' | 'GENERATING'>

export interface PrDescriptionRecord {
  id: string
  task_id: string
  plan_id: string
  plan_version: number
  diff_artifact_id: string
  test_run_id: string
  version: number
  background: string
  change_summary: string
  impact_scope: string
  test_result: string
  risk_notes: string
  rollback_notes: string
  generation_status: PrDescriptionRecordStatus
  failure_reason: string | null
  generated_at: string
  generated_by: string
  generated_by_name: string
}

// GET /pr-description 复合接口：任务级状态 + 最新记录 + 版本历史（后端按版本倒序）
export interface PrDescriptionOverview {
  task_id: string
  status: 'PENDING' | 'GENERATED' | 'FAILED'
  stale: boolean
  stale_reason: string | null
  current: PrDescriptionRecord | null
  versions: PrDescriptionRecord[]
}

export interface PlanFileScope {
  file_path: string
  change_type: PlanChangeType
  reason: string
  risk_hint?: string | null
}

export interface PlanStep {
  order: number
  title: string
  description: string
  target_files: string[]
  risk_hint?: string | null
}

export interface ModificationPlan {
  id: string
  task_id: string
  version: number
  goal: string
  file_scope: PlanFileScope[]
  steps: PlanStep[]
  risk_level: RiskThreshold
  risk_reason: string
  test_suggestions: string[]
  assumptions: string[]
  rollback_plan: string[]
  generated_from_analysis_revision: number
  generated_from_context_ids: string[]
  // 团队版 7.5：分析初判风险快照与历史相似任务参考（仅参考，不套用旧结论）
  analysis_risk_level?: RiskThreshold | null
  referenced_historical_tasks?: SimilarTaskRef[]
  provider: PlanProvider
  status: PlanStatus
  created_by: string
  created_by_name: string
  created_at: string
  risk_adjust_reason?: string | null
  risk_adjust_from?: RiskThreshold | null
  approved_by_id?: string | null
  approved_by_name?: string | null
  approved_at?: string | null
  approval_comment?: string | null
}

export interface IndexTriggerResult {
  index_id: string
  repository_id: string
  repository_name: string
  status: RepositoryIndexStatus
  commit_sha: string
  mode?: 'full' | 'incremental'
  rebuild_mode?: 'full' | 'incremental'
}

export interface TaskContextsResult {
  task_id: string
  query_text: string
  top_k: number
  threshold: number
  index_id: string | null
  index_status: RepositoryIndexStatus | null
  commit_sha: string | null
  fresh: boolean | null
  mode: 'auto' | 'manual'
  empty_reason: string | null
  contexts: CodeContext[]
  retrieval_id?: string
}

export interface ManualContextResult {
  task_id: string
  index_id: string
  commit_sha: string
  fresh: boolean
  contexts: CodeContext[]
  // DEV-017：任务处于 context_missing 时，手动补充后是否已恢复自动流转
  resumed?: boolean
}

// DEV-017 上下文不足后重新检索并恢复的结果
export interface ContextResumeResult {
  resumed: boolean
  contexts: CodeContext[]
  empty_reason: string | null
  index_status?: RepositoryIndexStatus
  task?: AgentTask
}

// DEV-017 历史相似任务查询结果
export interface TaskSimilarResult {
  task_id: string
  threshold: number
  similar_tasks: SimilarTaskRef[]
}

export interface TimelineEvent {
  id: string
  title: string
  time: string
  status: 'done' | 'active' | 'waiting' | 'failed'
  detail: string
  input?: string
  output?: string
  duration?: string
  summary?: string
  eventType?: string
  demoFallback?: boolean
}

export interface DiffFile {
  path: string
  change: '新增' | '修改' | '删除'
  additions: number
  deletions: number
  before: string[]
  after: string[]
}

export type ApprovalMode = 'single' | 'all'
export type RiskThreshold = 'low' | 'medium' | 'high'

export interface Project {
  id: string
  name: string
  description: string
  /** DEV-018b 项目级默认测试命令：测试命令三级来源（任务 > 仓库 > 项目）的最末级 */
  default_test_command?: string | null
  created_at: string
  updated_at: string
}

export interface RepositoryConfig {
  id: string
  project_id: string
  name: string
  url: string
  default_branch: string
  token_alias?: string | null
  token_configured: boolean
  token_updated_at?: string | null
  test_command: string
  command_whitelist: string[]
  approval_owner_id: string
  approval_owner_name: string
  approval_mode: ApprovalMode
  risk_threshold: RiskThreshold
  max_files: number
  max_retries: number
  blocked_paths: string[]
  /** DEV-016-C：Gitee Issue 同步成功后自动创建 Agent 任务 */
  auto_create_task_after_sync?: boolean
  /** 地址切换后旧仓库配置会保留为归档记录，但不再作为当前仓库展示。 */
  is_active?: boolean
  archived_at?: string | null
  superseded_by_id?: string | null
  created_at: string
  updated_at: string
}

export interface RepositoryConfigPayload {
  project_id: string
  name: string
  url: string
  default_branch: string
  gitee_token?: string | null
  test_command: string
  command_whitelist: string[]
  approval_owner_id: string
  approval_mode: ApprovalMode
  risk_threshold: RiskThreshold
  max_files: number
  max_retries: number
  blocked_paths: string[]
  auto_create_task_after_sync?: boolean
}

export interface RepositoryInspectResult {
  suggested_payload: Partial<RepositoryConfigPayload>
  sources: Record<string, string>
  confidence: Record<string, 'high' | 'medium' | 'low'>
  detected_stack: string[]
  warnings: string[]
}

export interface AuditEvent {
  id: string
  event_type: string
  resource_type: string
  resource_id: string
  actor_id: string
  actor_name: string
  summary: string
  diff_summary: string[]
  created_at: string
  execution_step_ids?: string[]
}

export type ExecutionStepStatus = 'running' | 'completed' | 'failed' | 'blocked'

export interface ExecutionStep {
  id: string
  task_id: string
  event_id: string
  node: string
  title: string
  status: ExecutionStepStatus
  input: string | null
  output: string | null
  duration_ms: number | null
  failure_reason: string | null
  blocked_reason: string | null
  created_at: string
}

// ---------------------------------------------------------------------------
// DEV-019 评论、通知与催办
// ---------------------------------------------------------------------------

export interface TaskComment {
  id: string
  task_id: string
  project_id: string
  repository_id: string
  author_id: string
  author_name: string
  body: string
  mentioned_user_ids: string[]
  reference_type?: string | null
  reference_id?: string | null
  created_at: string
  /** 软删除：置位后正文对外隐藏为「该评论已删除」，历史回放保留记录 */
  deleted_at?: string | null
}

export interface TaskCommentCreatePayload {
  body: string
  reference_type?: string | null
  reference_id?: string | null
}

export type NotificationType = 'MENTION' | 'APPROVAL_REQUIRED' | 'RISK_HIGH' | 'APPROVAL_RESULT' | 'NUDGE'

export interface TaskNotification {
  id: string
  recipient_id: string
  task_id: string
  type: NotificationType
  title: string
  body: string
  actor_id: string
  actor_name: string
  priority: 'normal' | 'high'
  read_at?: string | null
  created_at: string
}

export const notificationTypeText: Record<NotificationType, string> = {
  MENTION: '提及',
  APPROVAL_REQUIRED: '待审批',
  RISK_HIGH: '高风险',
  APPROVAL_RESULT: '审批结果',
  NUDGE: '催办',
}

export interface NudgeResult {
  nudged: boolean
  plan_id: string
}

// ---------------------------------------------------------------------------
// DEV-020 回放摘要与审计（DB 真源只读聚合）
// ---------------------------------------------------------------------------

export type ReplayActorType = 'user' | 'system' | 'worker'

export interface ReplayTimelineEvent {
  id: string
  event_type: string
  occurred_at: string
  actor_id: string
  actor_name: string
  actor_type: ReplayActorType
  status: 'completed' | 'failed' | 'skipped' | 'running'
  node: string
  message: string
  failure_reason?: string | null
  retry_count: number
  metadata: Record<string, unknown>
  relations: {
    task_id?: string | null
    plan_id?: string | null
    plan_version?: string | null
    diff_id?: string | null
    test_run_id?: string | null
    pr_description_id?: string | null
    comment_id?: string | null
  }
}

export interface ReplaySummary {
  text: string
  source: 'deterministic' | 'llm' | 'fallback' | string
  input_fingerprint: string
  generated_at: string
}

export interface TaskReplay {
  task_id: string
  generated_at: string
  timeline: ReplayTimelineEvent[]
  artifacts: {
    plans: unknown[]
    diffs: unknown[]
    test_runs: unknown[]
    pr_descriptions: unknown[]
    comments: unknown[]
    retrievals?: unknown[]
  }
  summary: ReplaySummary
}

// ---------------------------------------------------------------------------
// DEV-021 历史任务复用索引
// ---------------------------------------------------------------------------

export interface HistoricalTaskReference {
  taskId: string
  summaryId: string
  title: string
  project: string
  similarity: number | null
  completedAt: string | null
  matchedSections: string[]
  sourceLabel: string
  summaryPreview: string
  canOpenDetail: boolean
}

export interface HistoricalTaskSearchInput {
  projectId?: string
  query: string
  taskId?: string
  limit?: number
  minSimilarity?: number
}

export interface HistoricalTaskSummaryDetail {
  id: string
  task_id: string
  project_id: string
  source_task_title: string
  issue_summary: string
  context_summary: string
  analysis_summary: string
  plan_summary: string
  diff_summary: string
  test_summary: string
  pr_summary: string
  replay_summary: string
  searchable_text: string
  summary_version: number
  completed_at: string
}

export type DashboardEventLevel = 'success' | 'warning' | 'danger' | 'info'
export type DashboardTrendDays = 7 | 30

export interface DashboardTrendPoint {
  day: string
  date: string
  created: number
  done: number
  failed: number
  successRate: number | null
}

export interface DashboardEvent {
  level: DashboardEventLevel
  title: string
  detail: string
}

export interface DashboardSummary {
  runningTasks: number
  pendingApprovals: number
  completedThisWeek: number
  savedHours: number
  automationRateLabel: string
  overdueApprovals: number
  weeklyGrowthLabel: string
  trendDays: DashboardTrendDays
  generatedAt: string
  trend: DashboardTrendPoint[]
  events: DashboardEvent[]
}

export const statusText: Record<TaskStatus, string> = {
  CREATED: '已创建',
  ISSUE_FETCHED: 'Issue 已拉取',
  CONTEXT_INDEXED: '代码已索引',
  ANALYSIS_QUEUED: '分析排队中',
  ANALYZED: 'AI 分析完成',
  PLAN_GENERATED: '计划已生成',
  WAITING_APPROVAL: '等待审批',
  APPROVED: '审批通过',
  DIFF_GENERATED: 'Diff 已生成',
  TEST_RUNNING: '测试运行中',
  TEST_FAILED: '测试失败，待重试',
  TESTED: '测试完成',
  COMPLETED: '任务完成',
  FAILED: '任务失败',
  CANCELLED: '已取消',
}

// ---------------------------------------------------------------------------
// DEV-016-A 平台需求/缺陷草稿（docs/team/03-team-api-design.md §2）
// ---------------------------------------------------------------------------

export type RequestType = 'demand' | 'bug' | 'refactor' | 'test' | 'doc'

export type RequestSyncStatus = 'DRAFT' | 'SYNCING' | 'SYNCED' | 'SYNC_FAILED' | 'TASK_CREATED' | 'CLOSED'

export type RequestPriority = 'high' | 'medium' | 'low'

export const requestTypeText: Record<RequestType, string> = {
  demand: '需求',
  bug: 'Bug',
  refactor: '重构',
  test: '测试',
  doc: '文档',
}

export const requestStatusText: Record<RequestSyncStatus, string> = {
  DRAFT: '草稿',
  SYNCING: '同步中',
  SYNCED: '已同步 Gitee',
  SYNC_FAILED: '同步失败',
  TASK_CREATED: '已创建任务',
  CLOSED: '已关闭',
}

export const requestStatusTone: Record<RequestSyncStatus, string> = {
  DRAFT: 'info',
  SYNCING: 'warning',
  SYNCED: 'success',
  SYNC_FAILED: 'danger',
  TASK_CREATED: 'success',
  CLOSED: 'outline',
}

export const requestPriorityText: Record<RequestPriority, string> = {
  high: '高',
  medium: '中',
  low: '低',
}

export interface TeamRequest {
  id: string
  request_no: string
  project_id: string
  project_name: string
  repository_id: string
  repository_name: string
  type: RequestType
  title: string
  body: string
  reproduction_steps: string
  acceptance_criteria: string[]
  priority: RequestPriority | null
  labels: string[]
  target_branch: string | null
  test_command: string | null
  sync_status: RequestSyncStatus
  content_sufficient: boolean
  validation_warnings: string[]
  failure_reason: string | null
  manual_reason: string | null
  gitee_issue_id: string | null
  gitee_issue_number: string | null
  gitee_issue_url: string | null
  task_id: string | null
  created_by_id: string
  created_by_name: string
  created_at: string
  updated_at: string
}

export interface TeamRequestCreatePayload {
  project_id: string
  repository_id: string
  type: RequestType
  title: string
  body: string
  reproduction_steps?: string
  acceptance_criteria?: string[]
  priority?: RequestPriority | null
  labels?: string[]
  target_branch?: string | null
  test_command?: string | null
}

export interface TeamRequestUpdatePayload {
  type?: RequestType
  title?: string
  body?: string
  reproduction_steps?: string
  acceptance_criteria?: string[]
  priority?: RequestPriority | null
  labels?: string[]
  target_branch?: string | null
  test_command?: string | null
}

export interface TeamRequestListQuery {
  project_id?: string
  repository_id?: string
  sync_status?: RequestSyncStatus
  type?: RequestType
  created_by?: string
}

/** DEV-016-B 同步回放事件（GET /api/team/requests/{id}/events）。 */
export interface TeamRequestSyncEvent {
  id: string
  event_type: string
  resource_type: string
  resource_id: string
  actor_id: string
  actor_name: string
  summary: string
  diff_summary: string[]
  created_at: string
  execution_step_ids: string[]
}

export interface TeamRequestEventsResponse {
  request_id: string
  request_no: string
  events: TeamRequestSyncEvent[]
}

/** DEV-016-C POST /api/team/requests/{id}/create-task 响应。 */
export interface TeamRequestTaskResponse {
  task: AgentTask
  request: TeamRequest
  jump_url: string
  created: boolean
  reused: boolean
}
