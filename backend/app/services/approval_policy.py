"""DEV-018 审批分级与自动推进：单一策略模块（HTTP 与 Worker/LangGraph 共用）。

计划生成后做二次风险判断：
- 满足「低风险安全条件」→ 自动审批（AUTO_APPROVED），任务直接进入 Diff 阶段；
- 否则 → 保持 WAITING_APPROVAL，进人工审批中心。

边界：
- 只改任务/计划 pydantic 字段 + 写事件/步骤/审计 + persist；不新增状态机（复用现有 APPROVED→auto_generate_diff→Diff 链路）。
- 不 import `app.main` 于模块顶层（避免循环）；函数内惰性导入。
- 幂等：仅当计划仍为 WAITING_APPROVAL 才应用，避免图重复进入时二次写事件。
"""
from __future__ import annotations

from datetime import datetime, timezone

_RISK_RANK = {"low": 0, "medium": 1, "high": 2}

_SENSITIVE_SUBSTRINGS = (
    ".env", "secret", "credential", "id_rsa", "password", "token",
    ".pem", ".key", ".p12", ".pfx", "private", "credentials", "vault",
)


def _main():
    from app import main as m

    return m


def _plan_paths(plan) -> list[str]:
    """计划涉及文件路径：file_scope[].file_path（兼容 changed_files/files/diff_targets where present）。"""
    paths = []
    for f in getattr(plan, "file_scope", None) or []:
        p = getattr(f, "file_path", None) or (f.get("file_path") if isinstance(f, dict) else None)
        if p:
            paths.append(p)
    for attr in ("changed_files", "files", "diff_targets"):
        for f in getattr(plan, attr, None) or []:
            p = getattr(f, "file_path", None) or (f.get("file_path") if isinstance(f, dict) else f if isinstance(f, str) else None)
            if p:
                paths.append(str(p))
    return paths


def _is_sensitive(path: str, blocked_paths: list[str]) -> bool:
    low = path.lower()
    for pat in blocked_paths or []:
        pat = pat.strip().rstrip("/")
        if pat and (low == pat or low.startswith(pat + "/")):
            return True
    for sub in _SENSITIVE_SUBSTRINGS:
        if sub in low:
            return True
    return False


def _sensitive_file_hits(plan, blocked_paths: list[str]) -> list[str]:
    return [p for p in _plan_paths(plan) if _is_sensitive(p, blocked_paths)]


def _plan_commands(task, plan) -> list[str]:
    cmds = [getattr(task, "test_command", None) or ""]
    for s in getattr(plan, "test_suggestions", None) or []:
        cmds.append(str(s))
    return [c for c in cmds if c and c.strip()]


def _dangerous_command_reason(command: str) -> str | None:
    from app.services.test_runner import check_dangerous_command

    return check_dangerous_command(command)


def _dangerous_command_hits(task, plan) -> list[str]:
    hits = []
    for c in _plan_commands(task, plan):
        if _dangerous_command_reason(c):
            hits.append(c[:40])
    return hits


def evaluate_plan_gate(task, plan) -> tuple[str, str]:
    """二次风险判断。返回 (decision, reason)，decision ∈ {auto_approve, require_manual}。"""
    m = _main()
    reasons: list[str] = []

    repo = m.REPOSITORIES.get(task.repository_id)
    threshold = (repo.risk_threshold if repo and repo.risk_threshold else "medium")
    risk = plan.risk_level or task.risk_level or "medium"
    if _RISK_RANK.get(risk, 1) > _RISK_RANK.get(threshold, 1):
        reasons.append(f"风险等级 {risk} 高于自动推进阈值 {threshold}")

    struct_missing = m.validate_plan_structure(plan)
    if struct_missing:
        reasons.append(f"计划结构不完整（缺：{'、'.join(struct_missing)}）")

    fresh_ok, fresh_reason = m.plan_context_gate(task)
    if not fresh_ok:
        reasons.append(fresh_reason or "检索上下文过期")

    analysis = getattr(task, "analysis", None) or {}
    questions = list(getattr(analysis, "questions", None) or [])
    if any(getattr(q, "blocking", False) and not getattr(q, "answered", False) for q in questions):
        reasons.append("存在未回答的阻塞问题")

    if not m.command_allowed(getattr(task, "test_command", "") or "", (repo.command_whitelist if repo else None) or []):
        reasons.append("测试命令未命中仓库白名单")

    # 敏感文件独立复核（不依赖索引过滤 / 执行期拦截）
    for hit in _sensitive_file_hits(plan, (repo.blocked_paths if repo else None) or []):
        reasons.append(f"sensitive_file_matched:{hit}")

    # 危险命令独立复核（不依赖测试执行阶段拦截）
    for cmd in _dangerous_command_hits(task, plan):
        reasons.append(f"dangerous_command_matched:{cmd}")

    if reasons:
        return "require_manual", "；".join(reasons)
    return "auto_approve", f"低风险安全（风险 {risk}≤阈值 {threshold}、结构完整、上下文 fresh、无未答阻塞问题、测试命令白名单、无敏感文件/危险命令）"


def apply_auto_approval(task, plan, reason: str) -> None:
    """把计划与任务置为 APPROVED（与人工 approve 同一套状态），写 AUTO_APPROVED 事件+步骤+审计并落库。"""
    m = _main()
    now = datetime.now(timezone.utc)

    plan.status = "APPROVED"
    plan.approved_by_id = "agent-system"
    plan.approved_by_name = "Agent 自动化"
    plan.approved_at = now
    plan.approval_comment = f"AUTO_APPROVED：{reason}"

    task.status = "APPROVED"
    task.automation_state = "blocked"
    task.manual_required = False
    task.manual_reason = "none"
    task.next_action = "auto_generate_diff"
    task.risk_level = plan.risk_level
    task.risk_source = "plan_approval"
    task.updated_at = now

    event = m.task_event(
        task_id=task.id,
        event_type="PLAN_AUTO_APPROVED",
        summary=f"自动审批通过修改计划 v{plan.version}（低风险安全策略）",
        diff_summary=[
            f"task_id={task.id}",
            f"plan_id={plan.id}",
            f"plan_version={plan.version}",
            f"risk_level={plan.risk_level or task.risk_level or 'low'}",
            f"reasons={reason}",
            f"next_action={task.next_action}",
        ],
        actor_id="system:auto_approval",
        actor_name="系统自动审批",
    )
    task.events.append(event)  # task_event 亦落 audit_events（全局审计）+ task_events（时间线）双表
    m.record_execution_step(
        task, event=event, node="plan_approval", title="自动审批（低风险安全策略）",
        status="completed", input=f"修改计划 v{plan.version}", output=reason,
    )
    # DEV-019：通知任务发起人「自动审批通过」
    m.emit_approval_result_notification(task, approver_name="系统自动审批", approved=True, reason=f"AUTO_APPROVED：{reason}")
    m._persist_task(task)


def evaluate_and_apply(task, plan) -> str:
    """计划生成后的统一入口：idempotent 二次风险判断 + 自动推进。返回决策字符串。"""
    if plan is None:
        return "no_plan"
    if plan.status == "APPROVED":
        return "already_approved"
    if plan.status != "WAITING_APPROVAL" or task.status != "WAITING_APPROVAL":
        return "not_waiting"
    decision, reason = evaluate_plan_gate(task, plan)
    if decision == "auto_approve":
        apply_auto_approval(task, plan, reason)
        return "auto_approved"
    # DEV-019：仍保留在人工审批（WAITING_APPROVAL）时通知审批人；高风险标强提醒
    m = _main()
    m.emit_approval_required_notification(task)
    return "require_manual"