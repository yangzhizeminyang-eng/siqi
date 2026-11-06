"""DEV-021 历史任务复用索引服务。

- 完成任务沉淀知识资产：真实数据构建复用摘要（缺失标记「暂无记录」，不凭空造）。
- 幂等：同 taskId 已生成/同 summary_id 已索引不重复。
- 异步索引：索引失败写 failure_reason/failure_context，绝不阻断任务主链。
- 向量后端复用现有 Milvus 缝（`llamaindex_backend`）：生产写 Milvus；不可用降级 memory（显式标 provider）。
- 检索：默认项目隔离 + 权限过滤 + 排除自身 + 阈值 + 排序（相似度 × 完成时间）；结果标注「历史任务参考」。
"""

from __future__ import annotations

import traceback
from datetime import datetime, timezone
from typing import Any

from app.services import embedding_service

HISTORY_COLLECTION = "historical_task_summaries"
SUMMARY_VERSION = 1
DEFAULT_LIMIT = 5
DEFAULT_MIN_SIMILARITY = 0.15

# 检索 candidates 的来源说明（诚实标注，不冒充当前任务自动方案）
SOURCE_LABEL = "历史任务参考"


def _main():
    from app import main as m

    return m


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _clip(text: str, limit: int = 400) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit] + "…"


def _none() -> str:
    return "暂无记录"


def history_collection_name() -> str:
    """按 embedding 维度隔离历史集合，避免旧维度集合被复用后写入失败。"""
    from app.retrieval.llamaindex_backend import embedding_dim

    return f"{HISTORY_COLLECTION}_v{embedding_dim()}"


def _fallback_allowed() -> bool:
    from app.core.runtime_config import load_runtime_settings

    return load_runtime_settings().rag_mock_fallback


# ---------------------------------------------------------------------------
# 摘要生成
# ---------------------------------------------------------------------------

def _build_summary_fields(task) -> dict[str, str]:
    fields: dict[str, str] = {}

    snapshot = getattr(task, "issue_snapshot", None)
    if snapshot is not None:
        title = getattr(snapshot, "title", "") or ""
        body = getattr(snapshot, "body", "") or ""
        fields["issue_summary"] = _clip(f"{title}\n{body}")
    else:
        fields["issue_summary"] = _none()
    fields["issue_summary"] = fields["issue_summary"] or _none()

    contexts = list(getattr(task, "code_contexts", []) or [])
    if contexts:
        paths = [getattr(c, "file_path", "") for c in contexts[:6] if getattr(c, "file_path", "")]
        fields["context_summary"] = f"注入代码上下文 {len(contexts)} 条；相关文件：" + "、".join(p for p in paths if p) or _none()
    else:
        fields["context_summary"] = _none()

    analysis = getattr(task, "analysis", None)
    if analysis is not None:
        fields["analysis_summary"] = _clip(
            f"provider={getattr(analysis, 'provider', '') or '暂无记录'}；"
            f"总览：{getattr(analysis, 'overview', '') or '暂无记录'}；"
            f"结论：{'；'.join(getattr(analysis, 'decision_summary', []) or []) or '暂无记录'}；"
            f"建议风险：{getattr(analysis, 'suggested_risk_level', '') or '暂无记录'}"
        )
    else:
        fields["analysis_summary"] = _none()

    plans = list(getattr(task, "plans", []) or [])
    if plans:
        latest = max(plans, key=lambda p: p.version)
        fields["plan_summary"] = _clip(
            f"最新计划 v{latest.version}（{latest.status}）：目标={_clip(getattr(latest, 'goal', '') or '暂无记录', 200)}；"
            f"风险={latest.risk_level or '暂无记录'}；文件范围={len(latest.file_scope or [])}个"
        )
    else:
        fields["plan_summary"] = _none()

    diffs = list(getattr(task, "diffs", []) or [])
    if diffs:
        flags = []
        for d in diffs:
            mode = getattr(d, "execution_mode", "")
            if getattr(d, "is_demo_fallback", False) or mode == "DEMO_FALLBACK":
                flags.append("演示兜底/降级")
            elif getattr(d, "generation_status", "") == "FAILED":
                flags.append("Diff 生成失败")
        fields["diff_summary"] = _clip(
            f"Diff 产物 {len(diffs)} 个；模式={','.join(str(getattr(d, 'execution_mode', '') or '暂无记录') for d in diffs[:3]) or '暂无记录'}"
            + (f"；标记：{'、'.join(sorted(set(flags)))}" if flags else "")
        )
    else:
        fields["diff_summary"] = _none()

    runs = list(getattr(task, "test_runs", []) or [])
    if runs:
        lines = []
        for r in sorted(runs, key=lambda r: r.attempt):
            env = ""
            if getattr(r, "runner_mode", "") in {"mocked_runner", "unavailable"}:
                env = f"（{r.runner_mode}，未伪装成功）"
            lines.append(
                f"`{getattr(r, 'command', '') or '暂无记录'}` → {r.status}，退出码={r.exit_code if r.exit_code is not None else '暂无记录'}，"
                f"耗时={r.duration or 0}s，尝试={r.attempt}{env}"
            )
        fields["test_summary"] = _clip("；".join(lines))
    else:
        fields["test_summary"] = _none()

    pr_descs = list(getattr(task, "pr_descriptions", []) or [])
    pr_lines = [f"v{p.version} {p.generation_status}" for p in pr_descs] if pr_descs else ["暂无记录"]
    if getattr(task, "pr_number", None):
        pr_lines.append(f"PR 创建：{task.pr_number}（{task.pr_status or '暂无记录'}）")
    fields["pr_summary"] = "；".join(pr_lines)

    ev_count = len(getattr(task, "events", []) or [])
    step_count = len(getattr(task, "execution_steps", []) or [])
    replay_bits = [f"回放事件 {ev_count} 条", f"执行步骤 {step_count} 条"]
    if getattr(task, "failure_node", None):
        replay_bits.append(f"失败节点：{task.failure_node}（{getattr(task, 'failure_reason', None) or '暂无记录'}）")
    fields["replay_summary"] = "；".join(replay_bits)
    return fields


def _build_searchable_text(task, fields: dict[str, str]) -> str:
    parts = [
        getattr(task, "title", "") or "",
        getattr(task, "repository_name", "") or "",
        fields["issue_summary"],
        fields["context_summary"],
        fields["analysis_summary"],
        fields["plan_summary"],
        fields["diff_summary"],
        fields["test_summary"],
        fields["pr_summary"],
        getattr(task, "test_command", "") or "",
    ]
    return "\n".join(p for p in parts if p and p != _none())


def generate_for_completed_task(task_id: str, *, force: bool = False, user: Any | None = None) -> Any | None:
    """为完成任务生成（幂等）复用摘要；非完成态返回 None；生成纯本地不抛错给主链。

    返回 HistoricalTaskSummary 记录；重复触发（同 taskId + summaryVersion）返回既有记录。
    """
    m = _main()
    task = m.task_for_mutation(task_id)
    if task is None or task.status != "COMPLETED":
        return None
    from app.db import reads

    existing = reads.get_historical_summary_by_task(task_id)
    if existing is not None and not force:
        return existing

    now = _now()
    fields = _build_summary_fields(task)
    record = m.HistoricalTaskSummary(
        id=f"hist_sum_{task_id.upper()}",
        task_id=task.id,
        project_id=task.project_id,
        repository_id=task.repository_id or None,
        source_task_title=task.title,
        **fields,
        searchable_text=_build_searchable_text(task, fields),
        summary_version=SUMMARY_VERSION,
        completed_at=getattr(task, "updated_at", now),
        created_at=existing.created_at if existing else now,
        updated_at=now,
    )
    m.HISTORICAL_SUMMARIES[record.id] = record
    m._persist_historical_task_summary(record)
    m.create_audit_event(
        actor=user or m.USERS.get(task.created_by) or m.UserRecord(id="system:history", name="历史任务索引", email="", role="admin", password_hash=""),
        event_type="HISTORICAL_SUMMARY_GENERATED",
        resource_type="task",
        resource_id=task.id,
        summary=f"为完成任务 {task.id} 生成历史复用摘要（v{record.summary_version}）",
        diff_summary=["来源：真实任务数据", "SearchableText 稳定拼接", "不自动套用旧 Diff"],
    )
    return record


def get_by_task_id(task_id: str) -> Any | None:
    from app.db import reads

    return reads.get_historical_summary_by_task(task_id)


# ---------------------------------------------------------------------------
# 异步索引（复用 Milvus 缝；失败记录不阻断主链）
# ---------------------------------------------------------------------------

def create_index_record(summary) -> Any:
    m = _main()
    now = _now()
    rec = m.HistoricalTaskIndexRecord(
        id=f"hist_idx_{summary.id}",
        summary_id=summary.id,
        task_id=summary.task_id,
        project_id=summary.project_id,
        embedding_provider="",
        embedding_model="",
        index_status="PENDING",
        retry_count=0,
        created_at=now,
        updated_at=now,
    )
    m.HISTORICAL_INDEXES[rec.id] = rec
    m._persist_historical_task_index(rec)
    return rec


def get_index_record(summary_id: str) -> Any | None:
    m = _main()
    from app.db import reads

    recs = reads.list_historical_indexes(summary_id)
    if not recs:
        return None
    latest = max(recs, key=lambda r: (r.updated_at, r.created_at))
    # Keep the process-local mirror aligned with the DB result written by a Worker.
    m.HISTORICAL_INDEXES[latest.id] = latest
    return latest


def enqueue(summary_id: str, *, force: bool = False) -> Any:
    """异步触达索引（幂等）；force=True 用于显式重建已完成索引。"""
    m = _main()
    summary = m.HISTORICAL_SUMMARIES.get(summary_id)
    if summary is None:
        raise ValueError(f"摘要不存在：{summary_id}")
    existing = get_index_record(summary_id)
    if existing is not None and existing.index_status in {"PENDING", "EMBEDDING"}:
        return existing
    if existing is not None and existing.index_status == "INDEXED" and not force:
        return existing
    if existing is not None and force:
        rec = existing
        rec.index_status = "PENDING"
        rec.failure_reason = None
        rec.failure_context = None
        rec.retry_count = int(rec.retry_count or 0) + 1
        rec.updated_at = _now()
        m._persist_historical_task_index(rec)
    else:
        rec = create_index_record(summary)
    from app.workers.dispatch import enqueue_or_run
    from app.workers.tasks import index_historical_summary as _celery_task

    enqueue_or_run(_celery_task, kwargs={"summary_id": summary_id}, run_inline=lambda: index(summary_id), label=f"history_index:{summary_id}")
    return rec


def index(summary_id: str) -> Any:
    """向量索引：embedding → 历史向量内存镜像 + （可用时）Milvus 缝；失败记录，不抛出。"""
    m = _main()
    summary = m.HISTORICAL_SUMMARIES.get(summary_id)
    if summary is None:
        from app.db import reads

        summary = reads.get_historical_summary_by_task("")
        sum_list = [s for s in reads.list_historical_summaries() if s.id == summary_id]
        summary = sum_list[0] if sum_list else None
    if summary is None:
        raise ValueError(f"摘要不存在：{summary_id}")
    rec = get_index_record(summary_id) or create_index_record(summary)
    rec.index_status = "EMBEDDING"
    rec.updated_at = _now()
    m._persist_historical_task_index(rec)
    try:
        vector = embedding_service.embed_texts([summary.searchable_text or summary.id])[0]
        provider = "milvus"
        # Milvus 缝：复用 llamaindex_backend（生产写 Milvus；不可用走 memory 显式标注）
        try:
            from app.retrieval import llamaindex_backend as rag_backend

            if rag_backend.is_available():
                store = rag_backend.build_vector_store(history_collection_name(), overwrite=False)
                from llama_index.core.schema import TextNode

                node = TextNode(id_=summary.id, text=summary.searchable_text or summary.id,
                                metadata={"project_id": summary.project_id, "task_id": summary.task_id})
                node.embedding = vector
                store.add([node], force_flush=True)
                provider = "milvus"
            elif _fallback_allowed():
                provider = "memory(local)"
            else:
                raise RuntimeError("LlamaIndex/Milvus 不可用，且 RAG_MOCK_FALLBACK=false，拒绝静默降级")
        except Exception:
            if not _fallback_allowed():
                raise
            provider = "memory(local)"
        m.HISTORY_VECTOR_STORE[summary.id] = vector
        rec.embedding_provider = provider
        rec.embedding_model = "project-embedding-bridge"
        rec.vector_document_id = summary.id
        rec.index_status = "INDEXED"
        rec.failure_reason = None
        rec.failure_context = None
        rec.indexed_at = _now()
        rec.retry_count = 0
        rec.updated_at = _now()
        m._persist_historical_task_index(rec)
        return rec
    except Exception as error:  # noqa: BLE001 - 索引失败记录，绝不阻断主链
        rec.index_status = "FAILED"
        rec.failure_reason = f"{type(error).__name__}: {error}"
        rec.failure_context = traceback.format_exc(limit=3)
        rec.retry_count = max(rec.retry_count or 0, 1)
        rec.updated_at = _now()
        m._persist_historical_task_index(rec)
        return rec


def retry(summary_id: str) -> Any:
    return enqueue(summary_id, force=True)


def get_status(summary_id: str) -> Any | None:
    return get_index_record(summary_id)


# ---------------------------------------------------------------------------
# 相似检索（项目隔离 + 权限过滤 + 阈值 + 排序）
# ---------------------------------------------------------------------------

_SECTION_LABELS = ["issue", "context", "analysis", "plan", "diff", "test", "pr", "replay"]


def _vector_for(summary) -> list[float]:
    m = _main()
    cached = m.HISTORY_VECTOR_STORE.get(summary.id)
    if cached is not None:
        return cached
    vec = embedding_service.embed_texts([summary.searchable_text or summary.id])[0]
    m.HISTORY_VECTOR_STORE[summary.id] = vec
    return vec


def _can_see_summary(summary, user) -> bool:
    m = _main()
    task = m.task_for_mutation(summary.task_id)
    if task is None:
        return False
    return m._can_view_replay(task, user)


def search_similar(input_payload: dict[str, Any], user: Any) -> list[dict[str, Any]]:
    from app.db import reads

    project_id = (input_payload.get("projectId") or "").strip()
    query = (input_payload.get("query") or "").strip()
    exclude_task = (input_payload.get("taskId") or "").strip()
    limit = max(1, min(int(input_payload.get("limit") or DEFAULT_LIMIT), 20))
    min_sim = float(input_payload.get("minSimilarity") if input_payload.get("minSimilarity") is not None else DEFAULT_MIN_SIMILARITY)

    # 未显式给项目时，由当前任务所属项目推导（默认只检索当前项目）
    if not project_id and exclude_task:
        m = _main()
        current = m.task_for_mutation(exclude_task)
        if current is None:
            return []
        project_id = current.project_id
    if not project_id:
        return []
    candidates = [s for s in reads.list_historical_summaries(project_id) if s.task_id != exclude_task]
    # 权限过滤（复用回放可见性）
    candidates = [s for s in candidates if _can_see_summary(s, user)]
    if not candidates:
        return []

    # 无查询词（任务标题为空等）→ 回退展示项目内最近完成的任务，保证相似卡有内容而非空
    if not query:
        recent = sorted(candidates, key=lambda s: s.completed_at.timestamp() if s.completed_at else 0.0, reverse=True)[:limit]
        return [
            {
                "taskId": s.task_id, "summaryId": s.id, "title": s.source_task_title or s.task_id,
                "project": s.project_id, "similarity": None, "completedAt": s.completed_at.isoformat() if s.completed_at else None,
                "matchedSections": [], "sourceLabel": SOURCE_LABEL,
                "summaryPreview": _clip(s.issue_summary or s.plan_summary or s.diff_summary, 160), "canOpenDetail": True,
            }
            for s in recent
        ]

    query_vec = embedding_service.embed_query(query)
    scored: list[tuple[float, Any]] = []
    for s in candidates:
        cosine = embedding_service.cosine_similarity(query_vec, _vector_for(s)) or 0.0
        keyword = embedding_service.keyword_overlap_score(query, s.searchable_text)
        score = 0.7 * cosine + 0.3 * keyword
        if score < min_sim:
            continue
        scored.append((score, s))
    scored.sort(key=lambda pair: (-pair[0], -(pair[1].completed_at.timestamp() if pair[1].completed_at else 0.0)))
    results = []
    for score, s in scored[:limit]:
        matched = _matched_sections(s, query)
        results.append({
            "taskId": s.task_id,
            "summaryId": s.id,
            "title": s.source_task_title or s.task_id,
            "project": s.project_id,
            "similarity": round(score, 4),
            "completedAt": s.completed_at.isoformat() if s.completed_at else None,
            "matchedSections": matched,
            "sourceLabel": SOURCE_LABEL,
            "summaryPreview": _clip(s.issue_summary or s.plan_summary or s.analysis_summary or s.diff_summary, 160),
            "canOpenDetail": True,
        })
    return results


def _matched_sections(summary, query: str) -> list[str]:
    low = query.lower()
    matched = []
    mappings = {
        "issue": getattr(summary, "issue_summary", ""),
        "context": getattr(summary, "context_summary", ""),
        "analysis": getattr(summary, "analysis_summary", ""),
        "plan": getattr(summary, "plan_summary", ""),
        "diff": getattr(summary, "diff_summary", ""),
        "test": getattr(summary, "test_summary", ""),
        "pr": getattr(summary, "pr_summary", ""),
        "replay": getattr(summary, "replay_summary", ""),
    }
    for label, content in mappings.items():
        if content and any(tok in content.lower() for tok in [low] if tok):
            matched.append(label)
    return matched or []
