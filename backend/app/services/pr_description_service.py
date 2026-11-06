"""PR 描述生成服务：基于真实落库产物（Issue/分析/计划/Diff/测试记录）组装六段式描述。

MVP 采用规则组装，不调用真实模型、不自动创建 Gitee PR：

- ``compose_pr_description`` 是生成入口：只读取任务/计划/Diff/测试记录的快照字段，
  输出六段结构文本（background / change_summary / impact_scope / test_result /
  risk_notes / rollback_notes）；内部异常返回 None，由调用方落 FAILED 记录并留痕。
- 每段文本都标注来源（如「来源：审批通过的修改计划 v1」），不编造产物中不存在的结论；
  空数据以「暂无记录」说明，不虚构演示内容。

只依赖任务/计划对象的属性（duck typing），不 import main，避免循环依赖。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.main import AnalysisResult, DiffArtifact, ModificationPlan, TaskRecord, TestRunRecord

RISK_LABELS = {"low": "低", "medium": "中", "high": "高"}
CHANGE_LABELS = {"modify": "修改", "add": "新增", "delete": "删除", "unknown": "未知"}
STATUS_LABELS = {"passed": "通过", "failed": "失败"}


def compose_pr_description(
    task: TaskRecord,
    plan: ModificationPlan,
    artifact: DiffArtifact,
    test_run: TestRunRecord,
) -> dict[str, str] | None:
    """组装 PR 描述六段文本；字段缺失或组装异常时返回 None（不抛异常）。"""
    try:
        analysis = getattr(task, "analysis", None)
        return {
            "background": _background(task, analysis),
            "change_summary": _change_summary(task, plan, artifact),
            "impact_scope": _impact_scope(plan, artifact, analysis),
            "test_result": _test_result(plan, artifact, test_run),
            "risk_notes": _risk_notes(plan, analysis),
            "rollback_notes": _rollback_notes(task, plan),
        }
    except Exception:  # noqa: BLE001 - 组装失败走 FAILED 记录，不向调用方抛异常
        return None


def _background(task: TaskRecord, analysis: AnalysisResult | None) -> str:
    lines = [f"背景说明（来源：Issue #{task.issue_id}）", f"- 任务标题：{_clip(task.title, 120)}"]
    body = _clip((task.issue_snapshot.body or "").replace("\r\n", "\n").strip(), 400)
    if body:
        lines.append(f"- 原始诉求：{body}")
    if analysis:
        overview = _clip((analysis.overview or "").strip(), 400)
        if overview:
            lines.append(f"- AI 分析概述（来源：分析 v{analysis.revision}）：{overview}")
    if len(lines) == 2 and not body:
        lines.append("- 原始诉求：暂无记录")
    return "\n".join(lines)


def _change_summary(task: TaskRecord, plan: ModificationPlan, artifact: DiffArtifact) -> str:
    lines = ["改动说明（来源：审批通过的修改计划 v%d）" % plan.version, f"- 计划目标：{_clip(plan.goal, 200)}"]
    if plan.steps:
        steps = "；".join(f"步骤 {step.order}：{_clip(step.title, 80)}" for step in plan.steps)
        lines.append(f"- 实施步骤：{_clip(steps, 400)}")
    files = artifact.files or []
    if files:
        files_text = "、".join(f"「{_clip(f.file_path, 80)}」（{CHANGE_LABELS.get(f.change_type, f.change_type)} +{f.added_lines}/-{f.deleted_lines}）" for f in files)
        lines.append(f"- 变更产物（来源：Diff {artifact.id}）：{_clip(files_text, 500)}")
    if getattr(artifact, "is_demo_fallback", False):
        lines.append("- 提示：当前 Diff 为演示兜底产物，未真实执行代码改动，合入前需真实生成")
    return "\n".join(lines)


def _impact_scope(plan: ModificationPlan, artifact: DiffArtifact, analysis: AnalysisResult | None) -> str:
    lines = ["影响范围"]
    scope_lines = []
    for item in plan.file_scope:
        if item.file_path and item.file_path != "unknown":
            scope_lines.append(f"- {item.file_path}（{CHANGE_LABELS.get(item.change_type, item.change_type)}，理由：{_clip(item.reason, 100)}）")
    if scope_lines:
        lines.extend(scope_lines)
    elif artifact.files:
        lines.append(f"- 按 Diff 产物文件：{_clip('、'.join(f.file_path for f in artifact.files), 300)}")
    else:
        lines.append("- 文件影响：暂无记录")
    if analysis and analysis.affected_areas:
        lines.append(f"- 影响模块（来源：AI 分析 v{analysis.revision}）：{_clip('、'.join(analysis.affected_areas), 300)}")
    return "\n".join(lines)


def _test_result(plan: ModificationPlan, artifact: DiffArtifact, test_run: TestRunRecord) -> str:
    label = STATUS_LABELS.get(test_run.status, test_run.status)
    lines = ["测试情况"]
    if test_run.status == "passed":
        lines.append(f"- 结果：通过（exit 0，第 {test_run.attempt} 次尝试，耗时 {test_run.duration}s）")
        tail = _tail_text(test_run.stdout)
        if tail:
            lines.append(f"- 执行输出（尾行）：{tail}")
    else:
        lines.append(f"- 结果：失败（exit {test_run.exit_code}，第 {test_run.attempt} 次尝试）")
        reason = (test_run.failure_reason or "").strip()
        if reason:
            lines.append(f"- 失败原因：{_clip(reason, 300)}")
    lines.append(f"- 执行命令：{_clip(test_run.command, 150)}")
    lines.append(f"- 运行方式：{test_run.runner_mode}（记录 {test_run.id}，关联计划 v{test_run.plan_version}）")
    suggestions = plan.test_suggestions or []
    if suggestions:
        lines.append(f"- 计划建议补充：{_clip('；'.join(suggestions), 200)}")
    if test_run.status == "failed":
        lines.append("- 注意：测试未通过，合入前需修复并重新生成 PR 描述")
    return "\n".join(lines)


def _risk_notes(plan: ModificationPlan, analysis: AnalysisResult | None) -> str:
    lines = [
        "风险提示",
        f"- 计划风险等级：{RISK_LABELS.get(plan.risk_level, plan.risk_level)}",
    ]
    if plan.risk_adjust_from and plan.risk_adjust_reason:
        lines.append(
            f"- 审批调整：由 {RISK_LABELS.get(plan.risk_adjust_from, plan.risk_adjust_from)} 调整为 "
            f"{RISK_LABELS.get(plan.risk_level, plan.risk_level)}（理由：{_clip(plan.risk_adjust_reason, 150)}）"
        )
    reason = (plan.risk_reason or "").strip()
    if reason:
        lines.append(f"- 计划风险依据（来源：计划 v{plan.version}）：{_clip(reason, 200)}")
    if analysis:
        risk_reason = (analysis.risk_reason or "").strip()
        if risk_reason:
            lines.append(f"- 分析风险依据（来源：分析 v{analysis.revision}）：{_clip(risk_reason, 200)}")
        if analysis.prompt_injection_flags:
            lines.append(f"- 注入识别提示（来源：AI 分析 v{analysis.revision}）：{'；'.join(analysis.prompt_injection_flags)}")
    return "\n".join(lines)


def _rollback_notes(task: TaskRecord, plan: ModificationPlan) -> str:
    lines = ["回滚方案"]
    if plan.rollback_plan:
        lines.extend(f"- {_clip(item, 150)}" for item in plan.rollback_plan)
    else:
        lines.append("- 计划未提供回滚步骤：请人工评估回滚策略后再合入")
    lines.append(f"- 工作分支：{_clip(task.work_branch or '未创建', 100)}（基线分支 {_clip(task.base_branch or '未知', 60)}）")
    lines.append("- 回滚由人工执行（合并回退或关闭工作分支），当前 MVP 不自动创建/回滚 PR")
    return "\n".join(lines)


def _tail_text(value: str) -> str:
    lines = [line.strip() for line in (value or "").splitlines() if line.strip()]
    if not lines:
        return ""
    return _clip(lines[-1], 200)


def _clip(value: str, limit: int) -> str:
    value = (value or "").strip()
    return value if len(value) <= limit else f"{value[: limit - 1]}…"
