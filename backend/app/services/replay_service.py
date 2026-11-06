"""DEV-020 回放摘要与审计：DB 真源的只读聚合服务。

- 聚合现有真实数据（task_events / execution_steps / 计划 / Diff / 测试 / PR 描述 / 评论 /
  检索记录 / 通知），绝不凭空创建事件；虚构演示数据不进入本服务。
- 只读：不推进会务状态、不改审计日志。
- 持久化模式下以 DB 为真源（调用方用 task_for_mutation + reads 读层，见 endpoints）。
- 稳定排序：occurred_at 升序，相同时按 (occurred_at, 原事件序号, id) 稳定；不依赖 dict 顺序。
- 摘要为确定性服务端模板（离线可用，不依赖 LLM），标记 source 与输入指纹。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any


# 事件类型 → 展示节点（仅用于无 step 时的兜底标注；缺失为 "暂无记录"）
_EVENT_NODE = {
    "TASK_CREATED": "task_create",
    "ISSUE_FETCHED": "issue_fetch",
    "COMMIT_SELECTED": "commit_select",
    "INDEX_REUSE_CHECKED": "index_action",
    "INDEX_CREATED": "index_build",
    "INDEX_SCHEDULED": "index_build",
    "CONTEXT_INDEXED": "retrieval",
    "CONTEXT_RETRIEVED": "retrieval",
    "ANALYSIS_GENERATED": "ai_analyze",
    "PLAN_GENERATED": "plan_generation",
    "PLAN_REWRITTEN": "plan_generation",
    "PLAN_APPROVED": "plan_approval",
    "PLAN_AUTO_APPROVED": "plan_approval",
    "PLAN_REJECTED": "plan_approval",
    "PLAN_REWRITE_REQUESTED": "plan_approval",
    "DIFF_GENERATED": "generate_diff",
    "DIFF_GENERATION_FAILED": "generate_diff",
    "TEST_RUN_STARTED": "run_test",
    "TEST_RUN_PASSED": "run_test",
    "TEST_RUN_FAILED": "run_test",
    "TEST_BLOCKED": "run_test",
    "TEST_RUN_UNAVAILABLE": "run_test",
    "PR_DESC_GENERATED": "pr_description",
    "PR_DESC_FAILED": "pr_description",
    "PR_CREATED": "create_pr",
    "COMMENT_ADDED": "comment",
    "COMMENT_EDITED": "comment",
    "COMMENT_DELETED": "comment",
    "NUDGE_SENT": "nudge",
    "INDEX_FAILED": "index_build",
    "GITEE_ISSUE_SYNC_FAILED": "gitee_sync",
}

_FAILED_TYPES = {
    "DIFF_GENERATION_FAILED", "TEST_RUN_FAILED", "TEST_BLOCKED", "PR_DESC_FAILED",
    "PLAN_REJECTED", "INDEX_FAILED", "GITEE_ISSUE_SYNC_FAILED",
}
_SKIPPED_TYPES = {"TEST_RUN_UNAVAILABLE"}

_RUNNER_SKIP_PATTERNS = ("mocked_runner", "unavailable", "DEMO_FALLBACK", "eager(test)", "fallback")
_MSG_KEYWORD = ("风险等级", "fallback", "mocked_runner", "unavailable", "演示兜底", "回退", "降级", "Docker 不可用")


def _utc(dt: datetime | None) -> datetime:
    return dt if dt is not None else datetime.fromtimestamp(0, tz=timezone.utc)


def _parse_diff_summary_meta(lines: list[str]) -> dict[str, str]:
    """从事件 diff_summary 行解析 `key=value` / `key：value` 结构化字段（真实事件自带）。"""
    meta: dict[str, str] = {}
    for line in lines or []:
        text = str(line).strip()
        for sep in ("=", "："):
            if sep in text:
                key, _, value = text.partition(sep)
                key = key.strip()
                if key:
                    meta[key] = value.strip()
                break
    return meta


def _actor_type(actor_id: str) -> str:
    aid = (actor_id or "").strip()
    if aid in {"", "system", "agent-system"} or aid.startswith("system:") or aid.startswith("agent"):
        return "system"
    return "user"


def _status_for(event_type: str, step_statuses: list[str], meta: dict[str, str]) -> str:
    if event_type in _FAILED_TYPES or "failed" in step_statuses:
        return "failed"
    if event_type in _SKIPPED_TYPES or "blocked" in step_statuses:
        return "skipped"
    return "completed"


def _fingerprint(parts: list[Any]) -> str:
    raw = json.dumps(parts, default=str, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _step_records(task, event_id: str) -> list[Any]:
    return [s for s in getattr(task, "execution_steps", []) or [] if getattr(s, "event_id", None) == event_id]


def build_task_replay(task, current_user: Any | None) -> dict[str, Any]:
    """组装 DB 真源回放 DTO：timeline + artifacts + summary。

    - task 必须来自 task_for_mutation（持久化下 DB 真源，前端不可伪造）。
    - 评论/检索记录/通知走 reads 读层（DB 真源）。
    """
    from app import main
    from app.db import reads

    now = datetime.now(timezone.utc)
    events = list(getattr(task, "events", []) or [])
    # 稳定源码顺序：保持预置顺序，再按 (created_at, 原下标) 稳定排序
    indexed = sorted(enumerate(events), key=lambda pair: (_utc(pair[1].created_at), pair[0]))

    timeline: list[dict[str, Any]] = []
    for original_index, event in indexed:
        meta = _parse_diff_summary_meta(getattr(event, "diff_summary", None) or [])
        steps = _step_records(task, event.id)
        step_statuses = [getattr(s, "status", "") for s in steps]
        node = (steps[0].node if steps else _EVENT_NODE.get(event.event_type, ""))
        failure_reason = (
            getattr(task, "failure_reason", None)
            or next((getattr(s, "failure_reason", None) for s in steps if getattr(s, "failure_reason", None)), None)
            or meta.get("失败原因")
        )
        retry = meta.get("剩余重试次数") or (task.retry_count if event.event_type in _FAILED_TYPES else None)
        relations = {
            "task_id": task.id,
            "plan_id": meta.get("plan_id") or meta.get("plan id") or None,
            "plan_version": meta.get("plan_version") or (meta.get("计划版本") or "").lstrip("v") or None,
            "diff_id": meta.get("diff_id") or meta.get("Diff产物ID") or None,
            "test_run_id": meta.get("test_run_id") or meta.get("测试记录ID") or None,
            "pr_description_id": meta.get("pr_description_id") or meta.get("PrDescriptionID") or None,
            "comment_id": meta.get("评论ID") or meta.get("comment_id") or None,
        }
        execution_env = "正式流程"
        if event.event_type == "TEST_RUN_UNAVAILABLE":
            execution_env = "不可用（skipped/unavailable，未伪装成功）"
        elif event.event_type == "TEST_BLOCKED":
            execution_env = "拦截（blocked）"
        meta["execution_env"] = execution_env
        meta["original_index"] = original_index
        if steps:
            meta["execution_steps"] = [
                {
                    "node": s.node,
                    "title": s.title,
                    "status": s.status,
                    "duration_ms": s.duration_ms,
                    "failure_reason": s.failure_reason,
                    "blocked_reason": s.blocked_reason,
                }
                for s in steps
            ]
        timeline.append(
            {
                "id": event.id,
                "event_type": event.event_type,
                "occurred_at": _utc(event.created_at).isoformat(),
                "actor_id": getattr(event, "actor_id", "") or "",
                "actor_name": getattr(event, "actor_name", "") or "暂无记录",
                "actor_type": _actor_type(getattr(event, "actor_id", "") or ""),
                "status": _status_for(event.event_type, step_statuses, meta),
                "node": node or "暂无记录",
                "message": getattr(event, "summary", "") or "暂无记录",
                "failure_reason": failure_reason,
                "retry_count": int(retry) if str(retry).isdigit() else 0,
                "metadata": meta,
                "relations": {k: (v if v else None) for k, v in relations.items()} if isinstance(relations, dict) else relations,
            }
        )

    # artifacts：产物原始数据（DB 真源），仅展示不造假
    comments = list(reads.list_task_comments(task.id))
    retrieval_records = list(reads.list_retrieval_records(task.id))

    replay = {
        "task_id": task.id,
        "generated_at": now.isoformat(),
        "timeline": timeline,
        "artifacts": {
            "plans": [_dump(p) for p in getattr(task, "plans", []) or []],
            "diffs": [_dump(d) for d in getattr(task, "diffs", []) or []],
            "test_runs": [_dump(t) for t in getattr(task, "test_runs", []) or []],
            "pr_descriptions": [_dump(p) for p in getattr(task, "pr_descriptions", []) or []],
            "comments": [_dump(c) for c in comments],
            "retrievals": [_dump(r) for r in retrieval_records],
        },
    }
    replay["summary"] = build_replay_summary(task, replay, current_user, now=now)
    return replay


def _dump(record: Any) -> dict[str, Any]:
    if hasattr(record, "model_dump"):
        return record.model_dump(mode="json")
    return dict(record)


def _display(text: str | None) -> str:
    return (text or "").strip() or "暂无记录"


def build_replay_summary(task, replay: dict[str, Any], current_user: Any | None = None, *, now: datetime | None = None) -> dict[str, Any]:
    """确定性服务端摘要（离线可用，不依赖 LLM）。返回 {text, source, input_fingerprint, generated_at}。"""
    from app import main as _m

    now = now or datetime.now(timezone.utc)

    art = replay.get("artifacts", {})
    plans = art.get("plans", [])
    diffs = art.get("diffs", [])
    test_runs = art.get("test_runs", [])
    pr_descs = art.get("pr_descriptions", [])
    retrievals = art.get("retrievals", [])

    status = getattr(task, "status", "") or ""
    risk = getattr(task, "risk_level", None) or "暂无记录"
    risk_source = getattr(task, "risk_source", "") or ""
    manual = getattr(task, "manual_required", False)
    manual_reason = getattr(task, "manual_reason", "") or "none"
    next_action = getattr(task, "next_action", "") or "暂无记录"
    failure_node = _display(getattr(task, "failure_node", None))
    failure_reason = getattr(task, "failure_reason", None)
    retry_count = getattr(task, "retry_count", 0)

    source_line = []
    team_req_no = getattr(task, "team_request_no", None) or "暂无记录"
    source_line.append(f"需求来源：{team_req_no}" if team_req_no != "暂无记录" else "需求来源：暂无记录")

    analysis_lines = []
    analysis = getattr(task, "analysis", None)
    if analysis:
        analysis_lines.append(f"- 分析 provider：{_display(getattr(analysis, 'provider', None))}")
        analysis_lines.append(f"- 结论：{'；'.join(getattr(analysis, 'decision_summary', []) or []) or '暂无记录'}")
        analysis_lines.append(f"- 建议风险：{getattr(analysis, 'suggested_risk_level', '') or '暂无记录'}")
        questions = list(getattr(analysis, "questions", []) or [])
        blocking = [q for q in questions if getattr(q, "blocking", False) and not getattr(q, "answered", False)]
        if blocking:
            analysis_lines.append(f"- 未回答阻塞问题：{len(blocking)} 个")
    else:
        analysis_lines.append("- 暂无记录")
    analysis_lines.insert(0, f"上下文检索记录：{len(retrievals)} 条")

    plan_lines = []
    if plans:
        for p in sorted(plans, key=lambda x: (x.get("version") or 0)):
            plan_lines.append(
                f"- v{p.get('version')}：{p.get('status')}，风险={p.get('risk_level') or '暂无记录'}"
                + (f"，审批人={p.get('approved_by_name') or '暂无记录'}" if p.get("status") == "APPROVED" else "")
            )
            if p.get("risk_adjust_from") and p.get("risk_level") != p.get("risk_adjust_from"):
                plan_lines.append(f"  · 风险调整：{p.get('risk_adjust_from')} → {p.get('risk_level')}（{_display(p.get('risk_adjust_reason'))}）")
    else:
        plan_lines.append("- 暂无记录")

    diff_lines = []
    if diffs:
        for d in diffs:
            mode = d.get("execution_mode", "")
            marker = "（演示兜底/降级）" if (d.get("is_demo_fallback") or mode == "DEMO_FALLBACK") else ""
            diff_lines.append(f"- {d.get('id')}：{d.get('generation_status')}，模式={mode}{marker}")
            if d.get("fallback_reason"):
                diff_lines.append(f"  · 降级原因：{d.get('fallback_reason')}")
    else:
        diff_lines.append("- 暂无记录")

    test_lines = []
    if test_runs:
        for t in sorted(test_runs, key=lambda x: x.get("attempt") or 0):
            env_marker = ""
            if t.get("runner_mode") in {"mocked_runner", "unavailable"}:
                env_marker = f"（{t.get('runner_mode')}，未伪装成功）"
            test_lines.append(
                f"- 命令 `{_display(t.get('command'))}`：{t.get('status')}，退出码={t.get('exit_code') if t.get('exit_code') is not None else '暂无记录'}，"
                f"耗时={t.get('duration', 0)}s，尝试={t.get('attempt')}{env_marker}"
            )
            if t.get("failure_reason"):
                test_lines.append(f"  · 失败原因：{t.get('failure_reason')}")
            if t.get("blocked_reason"):
                test_lines.append(f"  · 拦截原因：{t.get('blocked_reason')}")
    else:
        test_lines.append("- 暂无记录")

    pr_lines = []
    for p in sorted(pr_descs, key=lambda x: x.get("version") or 0):
        pr_lines.append(f"- v{p.get('version')}：{p.get('generation_status')}" + (f"，失败原因={p.get('failure_reason')}" if p.get("generation_status") == "FAILED" else ""))
    if not pr_lines:
        pr_lines.append("- 暂无记录")
    pr_created = getattr(task, "pr_number", None)
    if pr_created:
        pr_lines.append(f"- PR 创建：号码={task.pr_number}，状态={task.pr_status or '暂无记录'}，{task.pr_url or '暂无记录'}")
    else:
        pr_lines.append("- PR 创建：暂无记录")

    # 失败/重试/降级/人工介入
    intervention_lines = []
    degradation = []
    for ev in replay.get("timeline", []):
        msg = f"{ev.get('event_type')} {ev.get('message') or ''} {json.dumps(ev.get('metadata', {}), ensure_ascii=False)}"
        if any(p in msg for p in _RUNNER_SKIP_PATTERNS):
            degradation.append(ev.get("event_type", "") + "（降级/回退）")
    if degradation:
        intervention_lines.append("- 降级/回退事件：" + "、".join(sorted(set(degradation))))
    if getattr(task, "failure_node", None):
        intervention_lines.append(f"- 失败节点：{failure_node}")
    if failure_reason:
        intervention_lines.append(f"- 失败原因：{_display(failure_reason)}")
    if retry_count:
        intervention_lines.append(f"- 重试次数：{retry_count}")
    if manual:
        intervention_lines.append(f"- 人工介入：{manual_reason}，当前后续动作={next_action}")
    if not intervention_lines:
        intervention_lines.append("- 无失败/降级/人工介入记录")

    lines = [
        f"# 任务回放摘要 {task.id}",
        f"- 生成时间：{now.isoformat()}",
        f"- 任务：{task.id} · {_display(getattr(task, 'title', None))}",
        f"- 状态：{status}；风险：{risk}（{risk_source}）",
        f"- 项目：{_display(getattr(task, 'project_name', None))} / 仓库：{_display(getattr(task, 'repository_name', None))} / 分支：{_display(getattr(task, 'base_branch', None))}",
        f"- 需求来源：{team_req_no if team_req_no != '暂无记录' else _display(getattr(task, 'issue_id', None))}",
        "",
        "## AI 分析与上下文检索",
        *analysis_lines,
        "",
        "## 修改计划与审批",
        *plan_lines,
        "",
        "## 风险",
        f"- 当前风险等级：{risk}"
        + (f"；风险调整历史见计划版本" if any(p.get('risk_adjust_from') for p in plans if p.get('risk_adjust_from')) else ""),
        "",
        "## Diff 结果",
        *diff_lines,
        "",
        "## 测试",
        *test_lines,
        "",
        "## PR 描述 / PR 创建",
        *pr_lines,
        "",
        "## 失败、重试、降级与人工介入",
        *intervention_lines,
        "",
        "## 当前结论与后续",
        f"- 当前状态：{status}；后续动作：{next_action}",
        "- 摘要为确定性模板生成，不替代原始审计记录与产物。",
    ]

    text = "\n".join(lines)
    fingerprint = _fingerprint([
        task.id,
        getattr(task, "status", ""),
        getattr(task, "risk_level", None),
        getattr(task, "failure_node", None),
        getattr(task, "failure_reason", None),
        getattr(task, "retry_count", 0),
        [[e.get("id"), e.get("event_type"), e.get("occurred_at")] for e in replay.get("timeline", [])],
        plans, diffs, test_runs, pr_descs,
        [c.get("id") for c in art.get("comments", [])],
    ])
    return {
        "text": text,
        "source": "deterministic",
        "input_fingerprint": fingerprint,
        "generated_at": now.isoformat(),
    }