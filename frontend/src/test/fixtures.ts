import type { AgentTask, AnalysisResult, DiffArtifact, ModificationPlan, PrDescriptionRecord, TestRun } from '../types/domain'

function unifiedPatch(filePath: string, before: string[], after: string[]) {
  const lines = [
    `--- a/${filePath}`,
    `+++ b/${filePath}`,
    `@@ -1,${before.length || 1} +1,${after.length || 1} @@`,
    ...before.map((line) => `-${line}`),
    ...after.map((line) => `+${line}`),
  ]
  return lines.join('\n')
}

export function makeDiffArtifact(overrides: Partial<DiffArtifact> = {}): DiffArtifact {
  const callbackBefore = ['def process_callback(payload):', '    order = find_order(payload.order_no)', '    update_order(order, payload.status)', '    publish_event(order.id, payload.status)']
  const callbackAfter = ['def process_callback(payload):', '    event_key = build_event_key(payload)', '    if event_store.exists(event_key):', '        return CallbackResult.ignored("重复回调")', '    order = find_order(payload.order_no)', '    update_order(order, payload.status)', '    event_store.save(event_key, ttl=86400)', '    publish_event(order.id, payload.status)']
  const testBefore: string[] = []
  const testAfter = ['def test_duplicate_callback_is_ignored(client):', '    payload = callback_fixture("paid")', '    client.post("/callbacks/gitee", json=payload)', '    response = client.post("/callbacks/gitee", json=payload)', '    assert response.json()["ignored"] is True']
  const legacyBefore = ['def retry_workaround(event):', '    # 旧版兜底：仅重试一次，已废弃']
  const legacyAfter: string[] = []
  return {
    id: 'DIFF-9001-v1',
    task_id: 'TASK-9001',
    plan_id: 'PLAN-9001-1',
    plan_version: 1,
    execution_step: '步骤 2：补充重复推送回归测试',
    generation_status: 'FALLBACK',
    execution_mode: 'DEMO_FALLBACK',
    is_demo_fallback: true,
    fallback_reason: 'MVP 未接入真实代码执行环境（Docker Runner），无法在工作区执行改动并产出真实补丁',
    real_failure_reason: null,
    generated_at: '2026-09-04T09:30:00Z',
    files: [
      {
        id: 'DIFF-9001-v1-1',
        file_path: 'services/payment/callback.py',
        change_type: 'modify',
        patch: unifiedPatch('services/payment/callback.py', callbackBefore, callbackAfter),
        added_lines: callbackAfter.length,
        deleted_lines: callbackBefore.length,
        before: callbackBefore,
        after: callbackAfter,
      },
      {
        id: 'DIFF-9001-v1-2',
        file_path: 'tests/test_callback_idempotent.py',
        change_type: 'add',
        patch: unifiedPatch('tests/test_callback_idempotent.py', testBefore, testAfter),
        added_lines: testAfter.length,
        deleted_lines: 0,
        before: testBefore,
        after: testAfter,
      },
      {
        id: 'DIFF-9001-v1-3',
        file_path: 'legacy/retry_workaround.py',
        change_type: 'delete',
        patch: unifiedPatch('legacy/retry_workaround.py', legacyBefore, legacyAfter),
        added_lines: 0,
        deleted_lines: legacyBefore.length,
        before: legacyBefore,
        after: legacyAfter,
      },
    ],
    test_summary: '演示兜底：未接入真实执行环境，未运行测试命令。\n测试命令：pytest tests/',
    pr_description_draft: '演示兜底：PR 描述草稿由演示流程构造，未连接真实 Gitee 仓库。\n- 目标：修复支付回调重复记账',
    ...overrides,
  }
}

export function makeTestRun(overrides: Partial<TestRun> = {}): TestRun {
  return {
    id: 'TEST-9001-1',
    task_id: 'TASK-9001',
    diff_artifact_id: 'DIFF-9001-v1',
    plan_id: 'PLAN-9001-1',
    plan_version: 1,
    command: 'pytest tests/',
    exit_code: 0,
    stdout: '3 passed in 1.02s',
    stderr: '',
    status: 'passed',
    duration: 1.02,
    attempt: 1,
    started_at: '2026-09-04T10:00:00Z',
    finished_at: '2026-09-04T10:00:02Z',
    failure_reason: null,
    blocked_reason: null,
    runner_mode: 'mocked_runner',
    ...overrides,
  }
}

export function makePrDescription(overrides: Partial<PrDescriptionRecord> = {}): PrDescriptionRecord {
  return {
    id: 'PRD-9001-1',
    task_id: 'TASK-9001',
    plan_id: 'PLAN-9001-1',
    plan_version: 1,
    diff_artifact_id: 'DIFF-9001-v1',
    test_run_id: 'TEST-9001-1',
    version: 1,
    background: '背景说明（来源：Issue #IDEMO001）\n- 任务标题：修复支付回调重复记账\n- 原始诉求：支付渠道网络重试时，同一回调会被重复提交，导致订单重复入账。',
    change_summary: '改动说明（来源：审批通过的修改计划 v1）\n- 计划目标：为支付回调增加幂等键，重复回调直接返回可审计的忽略结果。',
    impact_scope: '影响范围\n- services/payment/callback.py（修改，理由：回调入口增加幂等校验。）',
    test_result: '测试情况\n- 结果：通过（exit 0，第 1 次尝试，耗时 1.02s）\n- 执行输出（尾行）：3 passed in 1.02s',
    risk_notes: '风险提示\n- 计划风险等级：高\n- 计划风险依据（来源：计划 v1）：变更涉及支付回调核心链路，存在资金一致性风险。',
    rollback_notes: '回滚方案\n- 回滚 callback 入口改动并保留幂等表结构。\n- 工作分支：agent/main/issue-IDEMO001-v1（基线分支 main）',
    generation_status: 'GENERATED',
    failure_reason: null,
    generated_at: '2026-09-04T10:05:00Z',
    generated_by: 'user_developer',
    generated_by_name: '周航',
    ...overrides,
  }
}

export function makeModificationPlan(overrides: Partial<ModificationPlan> = {}): ModificationPlan {
  return {
    id: 'PLAN-9001-1',
    task_id: 'TASK-9001',
    version: 1,
    goal: '为支付回调增加幂等键，重复回调直接返回可审计的忽略结果。',
    file_scope: [
      { file_path: 'services/payment/callback.py', change_type: 'modify', reason: '回调入口增加幂等校验。', risk_hint: '支付' },
      { file_path: 'tests/test_callback_idempotent.py', change_type: 'add', reason: '补充重复回调回归测试。', risk_hint: null },
    ],
    steps: [
      { order: 1, title: '定义事件幂等键', description: '按渠道、交易号、事件类型构造唯一键。', target_files: ['services/payment/callback.py'], risk_hint: null },
      { order: 2, title: '补充重复推送回归测试', description: '覆盖首次处理与重复处理的状态与返回结果。', target_files: ['tests/test_callback_idempotent.py'], risk_hint: null },
    ],
    risk_level: 'high',
    risk_reason: '变更涉及支付回调核心链路，存在资金一致性风险。',
    test_suggestions: ['补充重复回调幂等回归测试。'],
    assumptions: ['支付渠道的重试间隔不会短于 1 秒。'],
    rollback_plan: ['回滚 callback 入口改动并保留幂等表结构。'],
    generated_from_analysis_revision: 1,
    generated_from_context_ids: ['services/payment/callback.py:88-146'],
    provider: 'mvp-rule-based',
    status: 'WAITING_APPROVAL',
    created_by: 'user_admin',
    created_by_name: '小泽',
    created_at: '2026-09-04T09:10:00Z',
    risk_adjust_reason: null,
    ...overrides,
  }
}

export function makeAnalysis(overrides: Partial<AnalysisResult> = {}): AnalysisResult {
  return {
    task_id: 'TASK-9001',
    task_version: 1,
    decision_summary: ['改什么：修复回调重复记账', '影响哪里：回调链路', '风险点：高风险，命中高风险关键词：支付'],
    overview: '基于 Issue 快照完成分析。',
    findings: ['Issue 定位为缺陷修复类任务。'],
    affected_areas: ['回调链路', '支付与资金链路'],
    suggested_risk_level: 'high',
    risk_reason: '命中高风险关键词：支付',
    uncertainties: ['复现步骤不完整。'],
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
        id: 'q_optional_1',
        question: 'Issue 正文信息较少，是否还有其他补充材料？',
        reason: '正文过短。',
        blocking: false,
        default_answer: '无需补充，按现有信息继续分析。',
        answer: null,
        answered: false,
      },
    ],
    sources: [
      { source_type: 'issue_title', source_ref: 'Issue #IDEMO001 标题', content: '修复支付回调重复记账', relevance: 'high' },
      { source_type: 'repository', source_ref: '仓库 payment-service', content: 'https://gitee.com/company/payment-service.git', relevance: 'low' },
    ],
    prompt_injection_flags: ['忽略之前的指令'],
    generated_at: '2026-09-04T09:00:00Z',
    provider: 'mvp-rule-based',
    revision: 1,
    ...overrides,
  }
}

export function makeAgentTask(overrides: Partial<AgentTask> = {}): AgentTask {
  return {
    id: 'TASK-9001',
    version: 1,
    project_id: 'proj_payment',
    project_name: '支付中台',
    repository_id: 'repo_payment_service',
    repository_name: 'payment-service',
    repository_url: 'https://gitee.com/company/payment-service.git',
    issue_id: 'IDEMO001',
    title: '修复支付回调重复记账',
    status: 'ANALYZED',
    risk_level: 'high',
    risk_source: 'ai_analysis',
    base_branch: 'main',
    test_command: 'pytest tests/',
    approval_owner_id: 'user_approver',
    approval_owner_name: '陈晓',
    work_branch: 'agent/main/issue-IDEMO001-v1',
    issue_snapshot: {
      title: '修复支付回调重复记账',
      body: '支付渠道网络重试时，同一回调会被重复提交，导致订单重复入账。',
      comments: [],
      labels: ['bug', '支付'],
      raw_response: {},
      fetched_at: '2026-09-04T08:59:00Z',
    },
    analysis: makeAnalysis(),
    automation_state: 'blocked',
    manual_required: true,
    manual_reason: 'answer_required',
    next_action: 'answer_analysis_question',
    failure_node: null,
    failure_reason: null,
    retry_count: 0,
    events: [
      {
        id: 'evt-1',
        event_type: 'ANALYSIS_GENERATED',
        resource_type: 'task',
        resource_id: 'TASK-9001',
        actor_id: 'system',
        actor_name: 'Agent',
        summary: 'AI 分析完成，存在待确认问题',
        diff_summary: ['风险等级：high', '存在阻塞问题'],
        created_at: '2026-09-04T09:00:00Z',
      },
    ],
    created_by: 'user_developer',
    created_by_name: '周航',
    created_at: '2026-09-04T08:58:00Z',
    updated_at: '2026-09-04T09:00:00Z',
    ...overrides,
  }
}
