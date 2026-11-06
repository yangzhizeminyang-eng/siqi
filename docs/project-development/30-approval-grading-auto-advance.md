# DEV-018 审批分级与自动推进

## 目标
计划生成后做二次风险判断：满足「低风险安全条件」的任务自动审批并直接进入 Diff 阶段；不满足的进入人工审批中心。审批动作全程可审计、可追溯、并发安全。

## 策略单一模块
`backend/app/services/approval_policy.py`（HTTP 与 Worker/LangGraph 共用同一份策略）：
- `evaluate_plan_gate(task, plan) -> (decision, reason)`：二次风险判断。
- `apply_auto_approval(task, plan, reason)`：置计划/任务 APPROVED、写 `PLAN_AUTO_APPROVED` 事件 + `node="plan_approval"` 步骤 + 审计、`_persist_task`。
- `evaluate_and_apply(task, plan)`：统一入口，幂等（仅当计划仍 `WAITING_APPROVAL` 才应用，避免图重复进入二次写事件）。

## 自动推进条件（全部满足才 AUTO_APPROVE；任一不满足 → WAITING_APPROVAL 人工中心）
1. 计划风险等级 ≤ 仓库 `risk_threshold`（默认 medium ⇒ 默认仅 `low` 自动推进）；
2. 计划结构完整（`validate_plan_structure` 五要素齐全）；
3. 检索上下文 fresh（`plan_context_gate`，无过期上下文）；
4. 无未回答的阻塞问题（`analysis.questions` 中 `blocking and not answered`）；
5. 测试命令命中仓库 `command_whitelist`。

## 接入点
- `backend/app/agent/workflow.py::node_generate_plan`：`attach_plan` 后调用 `evaluate_and_apply`（派生的自动链路主入口：`plan_generation→AUTO_APPROVED→generate_diff→…` 单次 invoke 内由 router 继续）。
- HTTP 手动 `POST /api/tasks/{id}/plans` 仍为**人工提交审批**入口（先决条件须已指派默认审批人）；同一份策略模块共享，运维如需手动路径也自动推进，可在该 handler 同样调 `evaluate_and_apply`。

## 并发/可审计
- 复用现有 `_APPROVAL_LOCK` 的人工审批路径（通过/拒绝/重写/改派/风险调整均写 `task_event`/`audit_event`）。
- 自动审批写 `PLAN_AUTO_APPROVED` 审计事件 + `node="plan_approval"` 执行步骤 + `approved_by="agent-system"`；
  不新造状态机，直接复用 `APPROVED → next_action=auto_generate_diff → DIFF_GENERATED` 既有链路。

## 测试
`backend/tests/test_approval_policy.py`：低风险自动审批、高危留人工、阻塞问题拦截、结构不完整拦截、幂等不重复。
`tests/test_workflow_graph.py`：低风险任务经图自动越过 `WAITING_APPROVAL` 并产生 `PLAN_AUTO_APPROVED` 事件。