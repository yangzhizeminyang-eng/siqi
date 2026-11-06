// AgentTask → Task 映射与运行期产物注册：真实任务与 mock 演示任务共用同一渲染 schema。
// 产物查找顺序：运行期注册表（API 拉取/变更响应）→ mock 演示产物 → 空占位产物。
import type { AgentTask, ExecutionStep, Task, TimelineEvent } from '../types/domain'
import { demoArtifacts, type TaskArtifact } from './mockTaskSource'

const runtimeArtifacts: Record<string, TaskArtifact> = {}

export function formatTimestamp(date = new Date()) {
  const pad = (value: number) => String(value).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

export function createEmptyArtifact(task?: Task): TaskArtifact {
  const label = task ? `${task.id} · ${task.title}` : '任务已创建'
  return {
    isPlaceholder: true,
    issueSummary: `${label} 已进入分析队列，正在拉取 Issue 与仓库上下文。`,
    issueBody: '当前任务还没有完成首轮分析，系统会在上下文加载完成后自动生成审阅内容。',
    analysisTitle: 'AI 正在拉取 Issue 与仓库上下文',
    analysisBody: '任务刚创建，暂时没有可审阅的分析结果。上下文就绪后，这里会展示影响范围、风险点和修改建议。',
    analysisPoints: [
      { label: '当前状态', value: task?.status === 'CREATED' ? '任务已创建' : '等待上下文' },
      { label: '下一步', value: '拉取 Issue / 代码 / 测试上下文' },
      { label: '预计输出', value: '首轮计划与风险摘要' },
    ],
    questionTitle: '等待首轮分析完成',
    questionBody: '系统完成 Issue 拉取与代码检索后，会自动给出确认问题或跳过建议。',
    planTitle: '计划待生成',
    planItems: [],
    planFooter: '当前阶段暂无可执行计划',
    planVersion: 'v0',
    timeline: [
      { id: '1', title: '任务已创建', time: task?.updatedAt ?? '刚刚', status: 'done', duration: '+1s', summary: '任务已进入分析队列。', detail: '任务已进入分析队列。' },
      { id: '2', title: '等待上下文拉取', time: '--', status: 'active', duration: '进行中', summary: '正在拉取 Issue、仓库和检索上下文。', detail: '正在拉取 Issue、仓库和检索上下文。' },
      { id: '3', title: '生成修改计划', time: '--', status: 'waiting', detail: '上下文完成后自动生成。' },
    ],
    diffFiles: [],
    relatedCode: [],
    auditNode: 'analysis_queue',
    replayNote: '任务正在等待上下文拉取，当前没有可回放步骤。',
    replayResult: '上下文就绪后会自动生成回放节点。',
  }
}

export function getTaskArtifact(taskId: string, task?: Task): TaskArtifact {
  return runtimeArtifacts[taskId] ?? demoArtifacts[taskId] ?? createEmptyArtifact(task)
}

function nextActionText(action?: string) {
  const map: Record<string, string> = {
    answer_analysis_question: '补充分析问题回答',
    auto_generate_plan: '自动生成修改计划',
    auto_generate_diff: '等待 Diff 生成模块',
    auto_run_test: '等待测试执行',
    retry_test: '重试测试',
    watch_test_result: '查看测试结果',
    review_plan: '等待审批',
    retry_from_failure: '人工重试',
    retry_or_cancel: '重试或取消任务',
    wait_analysis: '等待分析完成',
    wait_index: '等待仓库索引完成',
    check_index: '检查索引复用',
    view_replay: '查看任务回放',
  }
  return action ? map[action] ?? action : '等待自动流转'
}

function riskLabel(risk: string) {
  const map: Record<string, string> = { high: '高风险', medium: '中风险', low: '低风险', pending: '待分析' }
  return map[risk] ?? risk
}

function manualReasonText(reason: string) {
  const map: Record<string, string> = {
    none: '当前无需人工处理。',
    high_risk: '任务风险较高，需要审批人复核修改计划后再继续。',
    approval_required: '任务命中仓库审批策略，需要人工审批后继续。',
    context_missing: 'Issue 或仓库上下文不足，需要补充信息或重试上下文拉取。',
    auth_expired: '仓库授权已失效，需要更新 Gitee Token 后重试。',
    test_failed: '测试节点失败，需要查看失败原因后重试。',
    command_blocked: '测试命令被安全策略拦截或执行环境不可用，需要调整仓库测试命令或检查执行环境后重试。',
    gitee_fetch_failed: 'Gitee Issue 拉取失败，需要检查仓库地址、Issue ID 或 Token。',
    answer_required: 'AI 分析发现阻塞问题，需要补充回答后才会继续自动流转。',
    plan_rejected: '修改计划被审批驳回，可重试重建任务版本。',
    diff_failed: 'Diff 生成失败，需要查看失败原因后处理。',
  }
  return map[reason] ?? '任务需要人工处理后继续。'
}

export function mapAgentTask(task: AgentTask): Task {
  return {
    id: task.id,
    title: task.title,
    issue: `#${task.issue_id}`,
    repository: task.repository_name,
    repositoryId: task.repository_id,
    projectId: task.project_id,
    projectName: task.project_name,
    status: task.status,
    risk: task.risk_level ?? 'pending',
    approver: task.approval_owner_name ?? '未指定',
    updatedAt: formatTimestamp(new Date(task.updated_at)),
    createdAt: task.created_at,
    branch: task.work_branch,
    owner: task.created_by_name,
    automationState: task.automation_state,
    manualRequired: task.manual_required,
    manualReason: task.manual_reason ?? undefined,
    nextAction: task.next_action,
    failureNode: task.failure_node,
    failureReason: task.failure_reason,
    retryCount: task.retry_count,
    baseCommitSha: task.base_commit_sha ?? null,
    teamRequestId: task.team_request_id ?? null,
    teamRequestNo: task.team_request_no ?? null,
    giteeIssueUrl: task.gitee_issue_url ?? null,
    relatedIndexId: task.related_index_id ?? null,
    codeContexts: task.code_contexts ?? [],
    similarTaskRefs: task.similar_task_refs ?? [],
  }
}

function toTimelineItems(task: AgentTask, isFailed: boolean): TimelineEvent[] {
  return task.events.map((event, index) => ({
    id: event.id,
    title: event.summary,
    time: formatTimestamp(new Date(event.created_at)),
    status: index === task.events.length - 1 && !['COMPLETED', 'CANCELLED'].includes(task.status) ? (isFailed ? 'failed' : 'active') : 'done',
    summary: event.diff_summary[0] ?? event.event_type,
    detail: event.diff_summary.join('；'),
    eventType: event.event_type,
    demoFallback: event.event_type === 'DIFF_GENERATED' && event.diff_summary.includes('演示兜底：是'),
  }))
}

function groupExecutionSteps(task: AgentTask): Record<string, ExecutionStep[]> {
  const grouped: Record<string, ExecutionStep[]> = {}
  for (const step of task.execution_steps ?? []) {
    if (!grouped[step.event_id]) grouped[step.event_id] = []
    grouped[step.event_id].push(step)
  }
  return grouped
}

export function registerTaskArtifact(task: AgentTask) {
  const isFailed = task.status === 'FAILED'
  const analysis = task.analysis
  if (analysis) {
    const blockingQuestions = analysis.questions.filter((question) => question.blocking)
    const pendingBlocking = blockingQuestions.filter((question) => !question.answered)
    const analysisTitle = pendingBlocking.length ? 'AI 分析完成，存在待确认问题' : 'AI 分析完成'
    const analysisBody = analysis.overview
    runtimeArtifacts[task.id] = {
      isPlaceholder: false,
      issueSummary: task.issue_snapshot.title,
      issueBody: task.issue_snapshot.body || '该 Issue 没有正文内容，系统会结合评论和标签继续分析。',
      analysisTitle,
      analysisBody,
      analysisPoints: [
        { label: '风险等级', value: riskLabel(analysis.suggested_risk_level) },
        { label: '风险原因', value: analysis.risk_reason },
        { label: '分析版本', value: `第 ${analysis.revision} 版 · ${analysis.provider}` },
        { label: '下一步', value: nextActionText(task.next_action) },
      ],
      decisionSummary: analysis.decision_summary,
      findings: analysis.findings,
      uncertainties: analysis.uncertainties,
      riskReason: analysis.risk_reason,
      affectedAreas: analysis.affected_areas,
      promptInjectionFlags: analysis.prompt_injection_flags,
      analysisRevision: analysis.revision,
      analysisSources: analysis.sources,
      questions: analysis.questions,
      questionTitle: pendingBlocking.length ? pendingBlocking[0].question : analysis.questions.length ? analysis.questions[0].question : '暂无待确认问题',
      questionBody: pendingBlocking.length ? pendingBlocking[0].reason : analysis.questions.length ? analysis.questions[0].reason : '所有问题已确认，任务已恢复自动流转。',
      planTitle: '修改计划待生成',
      planItems: [],
      planFooter: '分析已完成，修改计划由后续模块生成。',
      planVersion: `v${task.version}`,
      timeline: toTimelineItems(task, isFailed),
      diffFiles: [],
      relatedCode: [],
      auditNode: 'analysis_completed',
      replayNote: task.manual_required ? 'AI 分析已完成，等待确认问题后继续流转。' : 'AI 分析已完成，等待后续计划生成模块推进。',
      replayResult: task.manual_required ? `待确认问题：${pendingBlocking.length} 个阻塞问题。` : '无阻塞问题，任务已恢复自动流转。',
      executionSteps: groupExecutionSteps(task),
    }
    return
  }
  runtimeArtifacts[task.id] = {
    isPlaceholder: true,
    issueSummary: task.issue_snapshot.title,
    issueBody: task.issue_snapshot.body || '该 Issue 没有正文内容，系统会结合评论和标签继续分析。',
    analysisTitle: isFailed ? `任务在「${task.failure_node ?? 'Agent 执行'}」节点失败` : 'Issue 已拉取，等待 AI 分析',
    analysisBody: isFailed ? task.failure_reason ?? '任务执行失败，请查看审计事件后重试。' : '系统已经保存 Issue 标题、正文、评论、标签和原始响应，正在检查索引复用并进入分析队列。',
    analysisPoints: [
      { label: '评论数', value: `${task.issue_snapshot.comments.length} 条` },
      { label: '标签', value: task.issue_snapshot.labels.length ? task.issue_snapshot.labels.join(' / ') : '无标签' },
      { label: '风险', value: task.risk_level ? `${task.risk_level}` : '等待 AI 分析和安全策略计算' },
      { label: '下一步', value: nextActionText(task.next_action) },
    ],
    questionTitle: task.manual_required ? '需要人工处理' : '等待首轮分析完成',
    questionBody: task.manual_required ? manualReasonText(task.manual_reason ?? '') : 'AI 分析完成后，如果需要人工补充背景，会在这里生成确认问题。',
    planTitle: isFailed ? '失败恢复方案' : '修改计划待生成',
    planItems: [],
    planFooter: task.manual_required ? '当前任务已暂停自动流转，需要人工处理后继续。' : '已进入分析队列，暂时没有可审批计划',
    planVersion: `v${task.version}`,
    timeline: toTimelineItems(task, isFailed),
    diffFiles: [],
    relatedCode: [],
    auditNode: 'analysis_queue',
    replayNote: 'Issue 快照已保存，分析队列等待处理。',
    replayResult: '后续 AI 分析模块会生成计划、风险等级和审批建议。',
    executionSteps: groupExecutionSteps(task),
  }
}
