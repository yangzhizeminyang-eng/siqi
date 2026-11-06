"""企业版 E3-03 Rerank 精排基线。

- 默认 deterministic heuristic 重排：精确符号/路径/错误码/API 路径/函数名命中优先、fresh 优先、
  保留 hybrid 原始分数为基础分；只对 top N 重排，不删除候选（其余保持原顺序追加）。
- 可预留 LLM rerank provider；模型不可用 / 超时 / 非法输出时稳定回退 heuristic，不让检索失败。
- 只操作已有候选的"排序"，不生成新上下文，不改变检索边界（rerank 发生在受限子集召回之后）。
- rerank_trace / rerank_reason 仅记术语命中 / fresh 等摘要，不含代码正文、token 或敏感配置。
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Sequence

from app.enterprise_rag.models import EnterpriseRetrievedChunk
from app.enterprise_rag.terms import technical_variants

# heuristic 命中加权（集中在后端模块，前端/路由不散落）
_BOOST_SYMBOL = 0.40   # 精确符号/函数名命中
_BOOST_PATH = 0.30     # 文件路径 / API 路径命中
_BOOST_FRESH = 0.10    # fresh 优先


def _matches(needle: str, field: str | None) -> bool:
    if not field:
        return False
    hay = field.lower()
    return needle in hay


def _term_set(query_text: str) -> list[str]:
    terms = [t.lower() for t in technical_variants(query_text)]
    # 补充原始 query 的词元，保证常见命中判定
    for word in (query_text or "").lower().split():
        if word and word not in terms:
            terms.append(word)
    return terms


def _heuristic_score(result: EnterpriseRetrievedChunk, terms: list[str]) -> tuple[float, str]:
    base = float(result.score or 0.0)
    boosts = 0.0
    reasons: list[str] = [f"base:{round(base, 4)}"]
    symbol = result.symbol_name or ""
    path = result.file_path or ""
    for term in terms:
        if not term or len(term) < 2:
            continue
        if term in symbol.lower():
            if "符号" not in "".join(reasons):
                boosts += _BOOST_SYMBOL
                reasons.append(f"符号:{term}")
        elif "/" in term or "." in term or "_" in term:
            if term in path.lower():
                boosts += _BOOST_PATH
                reasons.append(f"路径:{term}")
    if result.fresh:
        boosts += _BOOST_FRESH
        reasons.append("fresh")
    return base + boosts, "+".join(reasons)


def _heuristic_rerank(results: list[EnterpriseRetrievedChunk], query_text: str,
                      top_n: int) -> tuple[list[EnterpriseRetrievedChunk], list[dict]]:
    terms = _term_set(query_text)
    scored: list[tuple[float, EnterpriseRetrievedChunk]] = []
    for result in results:
        rerank_score, reason = _heuristic_score(result, terms)
        scored.append((rerank_score, result))
    scored.sort(key=lambda item: -item[0])
    ordered = [result for _, result in scored]
    for result in ordered:
        sval, reason_val = _heuristic_score(result, terms)
        result.rerank_score = round(sval, 6)
        result.rerank_reason = reason_val
    head = ordered[:top_n]
    tail = ordered[top_n:]
    trace = [
        {"chunk_id": result.chunk_id, "reason": _heuristic_score(result, terms)[1]}
        for result in ordered
    ]
    return head + tail, trace


def _llm_rerank(results: list[EnterpriseRetrievedChunk], query_text: str,
                top_n: int, timeout_seconds: int) -> list[EnterpriseRetrievedChunk] | None:
    """LLM 精排（可选）：模型不可用/超时/非法输出返回 None -> 由调用方回退 heuristic。

    只发送 file/symbol 摘要，不发送代码正文（企业默认不把切片正文送外部模型）。
    """
    try:
        from app.core import model_config
        from app.model_clients.deepseek_client import DeepSeekTextClient

        if len(results) < 2 or not model_config.llm_configured():
            return None
        limit = max(1, min(top_n, len(results)))
        prefix = results[:limit]
        ids = {r.chunk_id for r in prefix if r.chunk_id}
        if len(ids) < 2:
            return None
        lines = ["请按与查询的相关性排序候选项，只回显候选中已存在的 id。", f"Query:{query_text[:1000]}",
                 "候选(chunk/file/symbol)："]
        for r in prefix:
            lines.append(f"- {r.chunk_id} | {r.file_path} | {r.symbol_name or ''}")
        lines.append('只输出 JSON：{"ranked_chunk_ids":[...]}')
        try:
            import json
            from app.model_clients.base import ModelClientError
            from app.retrieval.reranker import _extract_json_object

            settings = replace(
                model_config.load_llm_settings(),
                timeout_seconds=max(1, int(timeout_seconds or 1)),
            )
            raw = DeepSeekTextClient(settings).generate("\n".join(lines), system="你是代码检索精排器，只输出 JSON。")
            block = _extract_json_object(raw)
            data = json.loads(block) if block else None
            ranked = data.get("ranked_chunk_ids") if isinstance(data, dict) else None
            if not isinstance(ranked, list):
                return None
            by_id = {r.chunk_id: r for r in prefix}
            ordered = [by_id[i] for i in ranked if i in by_id]
            if not ordered:
                return None
            ordered.extend(r for r in prefix if r.chunk_id not in ranked)
            return ordered + results[limit:]
        except Exception:  # noqa: BLE001 超时/解析失败 -> heuristic
            return None
    except Exception:  # noqa: BLE001
        return None


def enterprise_rerank(
    results: Sequence[EnterpriseRetrievedChunk],
    *,
    query_text: str,
    enabled: bool,
    provider: str,
    top_n: int,
    timeout_seconds: int,
    cross_encoder_enabled: bool = False,
    cross_encoder_model: str = "",
    cross_encoder_device: str = "cpu",
    cross_encoder_batch_size: int = 8,
    cross_encoder_max_candidates: int = 32,
) -> tuple[list[EnterpriseRetrievedChunk], dict]:
    """企业 Rerank 入口：仅重排已有候选，任何 provider 失败都回退 heuristic，不让检索失败。

    provider=cross_encoder 时，CE 不可用/超时/异常 → 回退 heuristic 并记录 fallback_reason（不冒充 CE）。
    """
    ordered = list(results)
    for index, result in enumerate(ordered, start=1):
        result.original_rank = index
        result.reranked_rank = index

    if not enabled or len(ordered) < 2:
        return ordered, {"enabled": bool(enabled), "provider": provider, "reranked": False}

    reranked: list[EnterpriseRetrievedChunk] | None = None
    trace: list[dict] = []
    provider_used = "heuristic"
    if provider == "llm":
        candidate = _llm_rerank(ordered, query_text, top_n, timeout_seconds)
        if candidate is not None:
            reranked, trace = candidate, [{"chunk_id": r.chunk_id, "reason": "llm"} for r in candidate]
            provider_used = "llm"
        # LLM 失败/不可用 -> 回退 heuristic（下一分支兜底）
    elif provider == "cross_encoder":
        from app.enterprise_rag import cross_encoder

        ce_result, ce_trace = cross_encoder.enterprise_cross_encoder_rerank(
            ordered,
            query_text=query_text,
            enabled=cross_encoder_enabled,
            model_name=cross_encoder_model,
            device=cross_encoder_device,
            top_n=top_n,
            batch_size=cross_encoder_batch_size,
            timeout_seconds=timeout_seconds,
            max_candidates=cross_encoder_max_candidates,
        )
        if ce_result is not None:
            reranked, trace = ce_result, ce_trace
            provider_used = "cross_encoder"
        else:
            # 稳定回退 heuristic，并记录 fallback 原因（CE 不假装执行）
            reranked, heuristic_reasons = _heuristic_rerank(ordered, query_text, top_n)
            trace = {
                "provider": "heuristic",
                "provider_attempted": "cross_encoder",
                "fallback_used": True,
                "fallback_reason": ce_trace.get("fallback_reason", "unavailable"),
                "reranked": True,
                "top_n": top_n,
                "reasons": heuristic_reasons,
            }
            provider_used = "heuristic"
    if reranked is None:
        reranked, trace = _heuristic_rerank(ordered, query_text, top_n)
        provider_used = "heuristic"

    for index, result in enumerate(reranked, start=1):
        result.reranked_rank = index
    return reranked, {
        "enabled": True,
        "provider": provider_used,
        "reranked": True,
        "top_n": top_n,
        "reasons": trace if isinstance(trace, list) else trace.get("reasons", []),
        **({k: v for k, v in trace.items() if k != "reasons"} if isinstance(trace, dict) else {}),
    }
