"""企业版 E3-01 Hybrid 检索：BM25 + 向量 -> RRF 融合 + 去重 + 统一契约。"""

from __future__ import annotations

from typing import Any, Sequence

from app.enterprise_rag.filters import filter_chunks, source_type_of, to_llamaindex_filters
from app.enterprise_rag.models import EnterpriseRetrievedChunk, RAGMetadataFilters

# RRF 平滑常数与融合权重（与 team 链路保持一致口径）
RRF_K = 60


def _bm25_confidence(score: float) -> float:
    return score / (1.0 + score) if score > 0.0 else 0.0


def _to_unified(chunk: Any, *, filters: RAGMetadataFilters, retrieval_source: str,
                score: float, contributions: dict[str, bool], metadata: dict | None = None) -> EnterpriseRetrievedChunk:
    commit_sha = str(getattr(chunk, "commit_sha", "") or "") or filters.commit_sha
    fresh = True
    if filters.commit_sha:
        fresh = bool(commit_sha) and str(commit_sha) == str(filters.commit_sha)
    return EnterpriseRetrievedChunk(
        chunk_id=str(getattr(chunk, "chunk_id", "") or ""),
        source_type=source_type_of(chunk),
        file_path=str(getattr(chunk, "file_path", "") or ""),
        start_line=int(getattr(chunk, "start_line", 0) or 0),
        end_line=int(getattr(chunk, "end_line", 0) or 0),
        language=str(getattr(chunk, "language", "") or ""),
        symbol_name=getattr(chunk, "symbol_name", None),
        repository_id=str(getattr(chunk, "repository_id", "") or "") or filters.repository_id,
        index_id=str(getattr(chunk, "index_id", "") or "") or filters.index_id,
        commit_sha=commit_sha or None,
        retrieval_source=retrieval_source,  # type: ignore[arg-type]
        score=round(float(score), 6),
        fresh=fresh,
        contributions=contributions,
        metadata=metadata or {},
    )


def enterprise_hybrid_retrieve(
    chunks: Sequence[Any],
    *,
    query_text: str,
    query_variants: Sequence[str] | None = None,
    variant_decay: float = 0.85,
    symbol_boost: float = 0.0,
    vector_weight: float = 1.0,
    bm25_weight: float = 1.0,
    trace_out: dict | None = None,
    filters: RAGMetadataFilters,
    collection_name: str,
    vector_top_k: int,
    bm25_top_k: int,
    final_top_k: int,
) -> list[EnterpriseRetrievedChunk]:
    """多路（原 query + 扩展变体）Hybrid 检索：逐变体并行 BM25 + 向量，加权 RRF 融合，按 chunk 去重。

    - 候选生成阶段均基于 filter_chunks 硬过滤的子集（同一份 enterprise 过滤器，扩展 query 不绕过边界）。
    - 原 query(variants[0]) 权重最高，扩展 query 按 variant_decay 衰减。
    - vector_weight / bm25_weight（OPT-10 消融开关）：默认 1.0/1.0 与历史 RRF 公式逐位一致；
      置 0.0 = 禁用该路（不查询、不注册候选）；两路同时为 0 返回空（如实，不伪造候选）。
    - 返回统一候选：retrieval_source = vector / bm25 / hybrid，hybrid 带 contributions 说明各路贡献。
    - trace_out（可空）：填入每变体的 vector/bm25 候选数，供接口可解释。
    """
    variants = [query_text] if not query_variants else list(query_variants)
    unique_variants: list[str] = []
    for v in variants:
        norm = " ".join((v or "").split()).strip() or query_text
        if norm not in unique_variants:
            unique_variants.append(norm)

    subset = filter_chunks(chunks, filters)
    if not subset:
        if trace_out is not None:
            trace_out["per_variant"] = []
        return []

    from app.retrieval.llamaindex_backend import _query_vector_results
    from app.retrieval.bm25_backend import retrieve_bm25

    use_vector = vector_weight > 0.0
    use_bm25 = bm25_weight > 0.0
    per_variant: list[dict] = []
    by_chunk: dict[str, dict[str, Any]] = {}
    for variant_index, variant in enumerate(unique_variants):
        weight = 1.0 if variant_index == 0 else (variant_decay ** variant_index)
        dense = _query_vector_results(
            subset,
            repository_id=filters.repository_id,
            index_id=filters.index_id or (getattr(subset[0], "index_id", None) or ""),
            collection_name=collection_name,
            query_text=variant,
            top_k=vector_top_k,
            threshold=-1.0,
            head_commit=filters.commit_sha,
            max_chunks_per_file=None,
            metadata_filters=to_llamaindex_filters(filters),
            strict_metadata_filters=True,
        ) if use_vector else []
        lexical = retrieve_bm25(subset, variant, top_k=bm25_top_k, symbol_boost=symbol_boost) if use_bm25 else []
        per_variant.append({
            "variant_idx": variant_index,
            "vector_candidates": len(dense),
            "bm25_candidates": len(lexical),
        })
        if use_vector:
            for rank, result in enumerate(dense, start=1):
                state = by_chunk.setdefault(result.chunk_id, {"dense": None, "bm25": 0.0, "rrf": 0.0, "bm25_score": 0.0})
                state["dense"] = result
                state["rrf"] += weight * vector_weight / (RRF_K + rank)
        if use_bm25:
            for result in lexical:
                chunk_id = str(getattr(result.chunk, "chunk_id", "") or "")
                state = by_chunk.setdefault(
                    chunk_id, {"dense": None, "bm25": result.chunk, "rrf": 0.0, "bm25_score": 0.0})
                state["bm25"] = result.chunk
                state["bm25_score"] = max(float(state["bm25_score"]), float(result.score))
                state["rrf"] += weight * bm25_weight / (RRF_K + result.rank)

    if trace_out is not None:
        trace_out["per_variant"] = per_variant

    scored: list[tuple[float, dict[str, Any]]] = []
    for chunk_id, state in by_chunk.items():
        dense_chunk = state["dense"]
        chunk = dense_chunk if dense_chunk is not None else state["bm25"]
        bm25_hit = state["bm25"] is not None and state["bm25_score"] > 0.0
        dense_hit = dense_chunk is not None
        support = max(
            float(dense_chunk.score) if dense_hit and dense_chunk.score is not None else 0.0,
            _bm25_confidence(state["bm25_score"]),
        )
        score = float(state["rrf"]) * (1.0 + support * 0.5)
        if symbol_boost > 0.0:
            symbol_name = str(getattr(chunk, "symbol_name", "") or "").strip().lower()
            if symbol_name and any(symbol_name in v.lower() for v in unique_variants):
                score *= (1.0 + symbol_boost)
        scored.append((score, chunk_id, chunk, dense_hit, bm25_hit, state["bm25_score"]))

    scored.sort(key=lambda item: (-item[0], str(getattr(item[2], "file_path", "") or ""),
                                  int(getattr(item[2], "start_line", 0) or 0)))

    results: list[EnterpriseRetrievedChunk] = []
    for score, chunk_id, chunk, dense_hit, bm25_hit, bm25_score in scored[:final_top_k]:
        if dense_hit and bm25_hit:
            retrieval_source = "hybrid"
        elif dense_hit:
            retrieval_source = "vector"
        else:
            retrieval_source = "bm25"
        metadata = {
            "index_id": filters.index_id or str(getattr(chunk, "index_id", "") or ""),
            "chunk_index": getattr(chunk, "chunk_index", None),
            "checksum": getattr(chunk, "checksum", None),
            "bm25_score": round(float(bm25_score), 6),
        }
        results.append(_to_unified(
            chunk, filters=filters, retrieval_source=retrieval_source,
            score=score,
            contributions={"vector": bool(dense_hit), "bm25": bool(bm25_hit)},
            metadata=metadata,
        ))
    return results


def enterprise_retrieve_by_mode(
    chunks: Sequence[Any],
    *,
    query_text: str,
    mode: str = "hybrid",
    query_variants: Sequence[str] | None = None,
    variant_decay: float = 0.85,
    symbol_boost: float = 0.0,
    vector_weight: float = 1.0,
    bm25_weight: float = 1.0,
    rerank_enabled: bool = False,
    rerank_top_n: int = 8,
    filters: RAGMetadataFilters,
    collection_name: str,
    vector_top_k: int,
    bm25_top_k: int,
    final_top_k: int,
    trace_out: dict | None = None,
) -> list[EnterpriseRetrievedChunk]:
    """按模式路由（bm25 / vector / hybrid / rerank），全部在同一个 filter_chunks 子集内 + 同一过滤器。

    - 不新建绕过权限/过滤的检索；bm25/vector/hybrid 都只对受限子集召回。
    - rerank = hybrid 候选之后执行 enterprise_rerank（只重排、不生成新候选）。
    - vector_weight / bm25_weight 仅作用于 hybrid/rerank 融合路（bm25/vector 单模式天然只走一路）。
    - 返回统一契约；trace_out 可带每路候选数，供报告推导 fallback_used。
    """
    # rerank 模式 = hybrid 候选由 service 在之后统一精排（此处只路由 bm25/vector, 其余走 hybrid）
    selected = (mode or "hybrid").lower()
    if selected == "rerank":
        selected = "hybrid"
    subset = filter_chunks(chunks, filters)

    if selected == "bm25":
        from app.retrieval.bm25_backend import retrieve_bm25

        lexical = retrieve_bm25(subset, query_text, top_k=bm25_top_k, symbol_boost=symbol_boost)
        scored = [(float(r.score), r.chunk) for r in lexical]
        scored.sort(key=lambda item: -item[0])
        if trace_out is not None:
            trace_out["per_variant"] = [{"variant_idx": 0, "bm25_candidates": len(lexical), "vector_candidates": 0}]
        results = [_to_unified(chunk, filters=filters, retrieval_source="bm25",
                               score=_bm25_confidence(score),
                               contributions={"vector": False, "bm25": True})
                   for score, chunk in scored[:final_top_k]]
        return results

    if selected == "vector":
        from app.retrieval.llamaindex_backend import _query_vector_results

        dense = _query_vector_results(
            subset,
            repository_id=filters.repository_id,
            index_id=filters.index_id or (getattr(subset[0], "index_id", "") if subset else ""),
            collection_name=collection_name,
            query_text=query_text,
            top_k=vector_top_k,
            threshold=-1.0,
            head_commit=filters.commit_sha,
            max_chunks_per_file=None,
            metadata_filters=to_llamaindex_filters(filters),
            strict_metadata_filters=True,
        )
        if trace_out is not None:
            trace_out["per_variant"] = [{"variant_idx": 0, "bm25_candidates": 0, "vector_candidates": len(dense)}]
        results = sorted(
            (_to_unified(d, filters=filters, retrieval_source="vector",
                         score=float(d.score) if d.score is not None else 0.0,
                         contributions={"vector": True, "bm25": False})
             for d in dense),
            key=lambda r: -r.score,
        )
        return results[:final_top_k]

    # hybrid（默认，含多 query 加权 RRF）
    return enterprise_hybrid_retrieve(
        chunks, query_text=query_text, query_variants=query_variants, variant_decay=variant_decay,
        symbol_boost=symbol_boost, vector_weight=vector_weight, bm25_weight=bm25_weight,
        trace_out=trace_out, filters=filters, collection_name=collection_name,
        vector_top_k=vector_top_k, bm25_top_k=bm25_top_k, final_top_k=final_top_k)

