"""企业版 E3-01 企业 RAG 查询服务：边界强制 + 租户隔离 + 审计 + Hybrid + 新鲜度。"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException, status

from app.core.clock import utcnow as audit_now

from app.enterprise_rag.filters import build_filters
from app.enterprise_rag.hybrid import enterprise_retrieve_by_mode
from app.enterprise_rag.models import EnterpriseRagSearchPayload
from app.enterprise_rag.terms import build_query_variants
from app.enterprise_rag.reranker import enterprise_rerank

logger = logging.getLogger("backend.enterprise_rag.service")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _err(code: int, message: str, data: dict | None = None) -> HTTPException:
    return HTTPException(status_code=code, detail={"code": code, "message": message, "data": data})


def enterprise_rag_search(ctx, payload: EnterpriseRagSearchPayload) -> dict:
    """企业 RAG 查询入口（OPT-07 包装层）：记录 rag.search span，业务语义不变。

    - 成功：status=ok + 模式/命中数/stale；异常：status=error + error_kind（http_404/http_403 等）。
    - 在 Agent 工具上下文内调用时自动继承同一 trace_id（全链路串联）。
    """
    from app.enterprise_runtime import observability as obs

    with obs.span("rag", "rag.search", tenant_id=str(getattr(ctx, "tenant_id", "") or ""),
                  attributes={"repository_id": payload.repository_id,
                              "project_id": payload.project_id}) as span_obj:
        result = _search_impl(ctx, payload)
        span_obj.attributes["retrieval_mode"] = result.get("retrieval_mode", "")
        span_obj.attributes["hit_count"] = len(result.get("results") or [])
        span_obj.attributes["stale"] = bool(result.get("stale"))
        return result


def _search_impl(ctx, payload: EnterpriseRagSearchPayload) -> dict:
    """企业 RAG 查询：tenant 只来自 ctx，绝不信任请求体/URL/Header 里传的 tenant_id。"""
    from app import main
    from app.tenancy.context import deny_and_audit, forbidden

    # ---- 1. 边界强制：project/repository 必填，commit_sha 或 ref 至少一个（不允许全库/未知版本检索）----
    target_commit = (payload.commit_sha or "").strip()
    ref = (payload.ref or "").strip()
    if not target_commit and not ref:
        raise _err(422, "必须提供 commit_sha 或 ref 作为版本边界，不允许无界检索")

    # ---- 2. 仓库/项目归属校验（服务端解析，跨租户 403 + 审计，不暴露敏感信息）----
    repository = main.REPOSITORIES.get(payload.repository_id)
    if repository is None:
        raise _err(404, "仓库不存在")
    if str(getattr(repository, "tenant_id", "") or "tenant_default") != str(ctx.tenant_id):
        deny_and_audit(ctx, "rag.search.repo_cross_tenant",
                       "企业 RAG 跨租户仓库访问被拒绝", resource_type="repository",
                       resource_id=payload.repository_id)
        raise forbidden("仓库不属于当前租户")
    project = main.PROJECTS.get(payload.project_id)
    if project is None:
        raise _err(404, "项目不存在")
    if str(getattr(project, "tenant_id", "") or "tenant_default") != str(ctx.tenant_id):
        deny_and_audit(ctx, "rag.search.project_cross_tenant",
                       "企业 RAG 跨租户项目访问被拒绝", resource_type="project",
                       resource_id=payload.project_id)
        raise forbidden("项目不属于当前租户")
    if getattr(repository, "project_id", None) not in (None, payload.project_id):
        raise _err(403, "仓库不属于该项目")

    # ---- 3. 索引与新鲜度 ----
    from app.db import reads

    index = None
    if target_commit:
        index = next((r for r in reads.list_repository_indexes(payload.repository_id)
                      if r.status == "COMPLETED" and r.commit_sha == target_commit), None)
    if index is None:
        index = reads.latest_completed_index(payload.repository_id)
    if index is None:
        raise _err(409, "该仓库尚无可用索引，请先为对应 Commit/分支建立索引")
    stale = bool(target_commit) and str(index.commit_sha) != target_commit
    if stale:
        # 企业模式默认不静默使用明显过期索引：显式标记 stale + 提示重建，交由展示/上层决策
        stale_notice = (f"目标 Commit {target_commit[:12]} 与已完成索引 "
                        f"{index.commit_sha[:12]} 不一致，结果标记为过期，请重建索引")
    else:
        stale_notice = None
    effective_commit = target_commit or str(index.commit_sha)

    # ---- 3b. FIX-02 Embedding 兼容性：记录指纹 vs 当前配置（不触发网络探测）----
    # 向量模式只在 compatible 时放行；记录缺失/维度未知/指纹缺失/状态不可信一律 fail-closed。
    from app.enterprise_rag import index_lifecycle

    lifecycle_entry = index_lifecycle.get(index.id)
    current_fingerprint = index_lifecycle.current_embedding_fingerprint()
    embedding_compat, compat_reasons = index_lifecycle.assess_compatibility(
        lifecycle_entry, current_fingerprint)

    # ---- 4. Hybrid 检索（候选生成阶段即受限过滤）----
    from app.core import runtime_config

    settings = runtime_config.load_runtime_settings()
    filters = build_filters(
        tenant_id=ctx.tenant_id,
        project_id=payload.project_id,
        repository_id=payload.repository_id,
        commit_sha=effective_commit,
        ref=ref or None,
        source_types=payload.source_types,
        index_id=index.id,
    )
    collection_name = getattr(main, "index_collection_name", lambda _id: f"index_{_id}")(index.id)
    chunks = reads.code_chunks_of_index(index.id)
    # E3-02：Query Rewrite / 多路 Expansion（原 query 恒在首位；LLM 可选、不可用确定性回退；
    # 每个变体共用同一份 filters，不绕过 rag:search / tenant / project / repo / version / source_type）
    query_variants = build_query_variants(
        payload.query,
        max_queries=settings.rag_query_max_variants,
        allow_llm=settings.rag_query_llm,
        enabled=settings.rag_query_rewrite_enabled,
    )
    trace_out: dict = {}
    # 企业链默认 hybrid；仅当请求显式指定模式才切换（bm25/vector/hybrid/rerank 共享同一过滤链）
    mode = (payload.retrieval_mode or "hybrid").lower()
    # FIX-02：向量模式只允许 compatible；unknown/untrusted/incompatible 全部 fail-closed。
    # bm25 不消费向量 → 放行但如实标注（响应字段保留 null/false 与 reasons）。
    if not index_lifecycle.guard_allows_mode(embedding_compat, mode):
        # 记录缺失时构造一个只用于审计的最小上下文，禁止把 unknown 当放行。
        audit_entry = lifecycle_entry or index_lifecycle.IndexLifecycleRecord(
            index_id=index.id,
            tenant_id=str(getattr(ctx, "tenant_id", "") or ""),
            repository_id=payload.repository_id,
            project_id=payload.project_id,
            base_branch="",
            commit_sha=str(index.commit_sha or ""),
            collection_name=collection_name,
            embedding=index_lifecycle.EmbeddingFingerprint(
                provider="", model="", dimension=None, source="", fingerprint=""),
            status="missing",
            created_at=_now(),
            updated_at=_now(),
        )
        index_lifecycle.audit_incompatible_rejection(
            ctx, entry=audit_entry, current=current_fingerprint,
            reasons=compat_reasons, compatibility=embedding_compat)
        state = "缺失/不可验证" if embedding_compat in {"unknown", "untrusted"} else "不兼容"
        raise _err(
            409,
            f"索引 Embedding {state}（{'; '.join(compat_reasons)}）："
            f"禁止以 {mode} 模式检索，请重建索引或恢复原 Embedding 配置",
            data={
                "compatibility": embedding_compat,
                "reasons": compat_reasons,
                "index_id": index.id,
                "lifecycle_status": lifecycle_entry.status if lifecycle_entry is not None else None,
            },
        )
    results = enterprise_retrieve_by_mode(
        chunks,
        query_text=payload.query,
        mode=mode,
        query_variants=query_variants,
        variant_decay=settings.rag_query_variant_decay,
        symbol_boost=settings.rag_symbol_boost,
        vector_weight=settings.rag_hybrid_vector_weight,
        bm25_weight=settings.rag_hybrid_bm25_weight,
        rerank_enabled=settings.rag_rerank_enabled,
        rerank_top_n=settings.rag_rerank_max_candidates,
        trace_out=trace_out,
        filters=filters,
        collection_name=collection_name,
        vector_top_k=settings.rag_vector_top_k,
        bm25_top_k=settings.rag_bm25_top_k,
        final_top_k=settings.rag_final_top_k,
    )
    # E3-03：Rerank 精排（只对 hybrid/rerank 候选执行，bm25/vector 单模式不精排；
    # 默认 heuristic，只重排已有候选 top_n，不删除；可解释字段写回）
    if mode in ("hybrid", "rerank"):
        results, rerank_trace = enterprise_rerank(
            results,
            query_text=payload.query,
            enabled=settings.rag_rerank_enabled,
            provider=settings.rag_rerank_provider,
            top_n=settings.rag_rerank_top_n,
            timeout_seconds=settings.rag_rerank_timeout_seconds,
            cross_encoder_enabled=settings.rag_cross_encoder_enabled,
            cross_encoder_model=settings.rag_cross_encoder_model,
            cross_encoder_device=settings.rag_cross_encoder_device,
            cross_encoder_batch_size=settings.rag_cross_encoder_batch_size,
            cross_encoder_max_candidates=settings.rag_cross_encoder_max_candidates,
        )
    else:
        rerank_trace = None

    # ---- 5. 租户隔离审计（不含敏感信息）----
    _audit_rag_query(ctx, payload, results, stale=stale)

    return {
        "results": [r.model_dump() for r in results],
        "filters": filters.model_dump(),
        "target_commit_sha": target_commit or None,
        "index_commit_sha": index.commit_sha,
        "fresh": not stale,
        "stale": stale,
        "notice": stale_notice,
        # OPT-09：Embedding 兼容性如实标注（True/False/None=unknown，不冒称兼容）
        "index_embedding_compatible": (True if embedding_compat == "compatible"
                                       else False if embedding_compat == "incompatible" else None),
        "index_embedding_compat_reasons": compat_reasons,
        "index_lifecycle_status": lifecycle_entry.status if lifecycle_entry is not None else None,
        "index_embedding_compat_state": embedding_compat,
        "index_embedding_guard_enforced": mode in index_lifecycle.VECTOR_CONSUMING_MODES,
        "index_embedding_fingerprint": (lifecycle_entry.embedding.model_dump()
                                        if lifecycle_entry is not None else None),
        "retrieval_mode": mode,
        "query_variants": query_variants,
        "variant_count": len(query_variants),
        "retrieval_trace": trace_out.get("per_variant", []),
        "rerank_enabled": settings.rag_rerank_enabled,
        "rerank_provider": (rerank_trace.get("provider") if isinstance(rerank_trace, dict) else settings.rag_rerank_provider)
            or settings.rag_rerank_provider,
        "rerank_trace": rerank_trace,
        "rerank_model_name": rerank_trace.get("model_name", "") if isinstance(rerank_trace, dict) else "",
        "rerank_model_version": rerank_trace.get("model_version", "") if isinstance(rerank_trace, dict) else "",
        "rerank_candidate_count": rerank_trace.get("candidate_count", 0) if isinstance(rerank_trace, dict) else 0,
        "rerank_scored_count": rerank_trace.get("scored_count", 0) if isinstance(rerank_trace, dict) else 0,
        "rerank_fallback_used": bool(rerank_trace.get("fallback_used", False)) if isinstance(rerank_trace, dict) else False,
        "rerank_fallback_reason": rerank_trace.get("fallback_reason", "") if isinstance(rerank_trace, dict) else "",
        "rerank_latency_ms": rerank_trace.get("latency_ms", 0) if isinstance(rerank_trace, dict) else 0,
        "counts": {
            "vector": sum(1 for r in results if r.contributions.get("vector")),
            "bm25": sum(1 for r in results if r.contributions.get("bm25")),
            "hybrid": sum(1 for r in results if r.retrieval_source == "hybrid"),
        },
    }


def _audit_rag_query(ctx, payload: EnterpriseRagSearchPayload, results, *, stale: bool) -> None:
    from app import main

    try:
        event = main.AuditEventRecord(
            id=f"audit_{uuid.uuid4().hex[:16]}",
            event_type="rag.query",
            tenant_id=ctx.tenant_id,
            resource_type="repository",
            resource_id=payload.repository_id,
            actor_id=ctx.user_id,
            actor_name="",
            summary=(
                f"企业 RAG 查询 project={payload.project_id} repo={payload.repository_id} "
                f"results={len(results)} fresh={not stale}"
            ),
            diff_summary=[],
            created_at=audit_now(),
            execution_step_ids=[],
        )
        main.AUDIT_EVENTS[event.id] = event
        from app.db.repositories import shared_persistence_enabled, upsert_audit_event

        if shared_persistence_enabled():
            upsert_audit_event(event)
    except Exception:  # noqa: BLE001 审计失败不阻断主流程，但必须可观测
        logger.warning("[rag] 查询审计写入失败 repo=%s", payload.repository_id, exc_info=True)