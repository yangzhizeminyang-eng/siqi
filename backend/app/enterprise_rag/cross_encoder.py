"""企业版 E3-09 Cross-Encoder 精排 provider。

安全与边界（硬约束）：
- 只对已通过 tenant/project/repo/commit|ref/source_type/index + rag:search 过滤的候选做重排，绝不重新检索；
- 重排输入是 enterprise_rag_search 已返回的候选（不含代码正文），因此**默认根本不持有/不发送企业代码正文**；
  打分仅用 file_path / symbol_name / language / 行区间 摘要（脱敏）；
- 本地/私有模型运行时优先；无依赖或模型不可加载/超时/输出异常 → 稳定回退 heuristic，并在 trace 记录原因，
  fallback 绝不冒充 cross_encoder；
- 不修改原始 score/metadata/租户边界；只新增 rerank_score/rerank_reason，并返回可解释 trace（无 token/凭据/正文）。
"""

from __future__ import annotations

import concurrent.futures
import time
from typing import Any, Sequence

from app.enterprise_rag.models import EnterpriseRetrievedChunk

# 进程内模型缓存（按 model+device），避免评测/服务逐请求重复加载；失败不缓存。
_MODEL_CACHE: dict[tuple[str, str], Any] = {}


def load_cross_encoder(model_name: str, device: str) -> tuple[Any | None, str]:
    """加载本地/私有 Cross-Encoder 模型；不可用返回 (None, reason)。不联网下载。"""
    if not model_name:
        return None, "provider_unavailable: RAG_CROSS_ENCODER_MODEL 为空（未配置模型）"
    key = (model_name, device)
    if key in _MODEL_CACHE:
        return _MODEL_CACHE[key], ""
    try:
        from sentence_transformers import CrossEncoder  # 延迟导入：缺依赖时走 fallback
    except Exception as error:  # noqa: BLE001
        return None, f"provider_unavailable: 缺少 sentence-transformers 依赖: {error}"
    try:
        model = CrossEncoder(model_name, device=device)
        _MODEL_CACHE[key] = model
        return model, ""
    except Exception as error:  # noqa: BLE001
        return None, f"provider_unavailable: 模型加载失败: {error}"


def _model_version(model: Any) -> str:
    try:
        cfg = getattr(model, "config", None) or {}
        return str(getattr(cfg, "_name_or_path", "") or getattr(cfg, "model_type", "") or "")
    except Exception:  # noqa: BLE001
        return ""


def _candidate_text(result: EnterpriseRetrievedChunk, summary_cap: int) -> str:
    """脱敏候选摘要：只用 file/symbol/语言/行区间，不包含代码正文/token/凭据。"""
    parts = [f"file={result.file_path}"]
    if result.symbol_name:
        parts.append(f"symbol={result.symbol_name}")
    parts.append(f"lang={result.language or 'unknown'}")
    parts.append(f"lines={result.start_line}-{result.end_line}")
    text = " | ".join(parts)
    return text[: max(0, summary_cap)]


def _score_batch(model: Any, query: str, texts: list[str], batch_size: int) -> list[float]:
    pairs = [(query, text) for text in texts]
    scores: list[float] = []
    for start in range(0, len(pairs), max(1, batch_size)):
        batch = pairs[start:start + batch_size]
        preds = model.predict(batch, show_progress_bar=False, convert_to_numpy=True)
        scores.extend(float(p) for p in preds)
    return scores


def enterprise_cross_encoder_rerank(
    results: Sequence[EnterpriseRetrievedChunk],
    *,
    query_text: str,
    enabled: bool,
    model_name: str,
    device: str,
    top_n: int,
    batch_size: int,
    timeout_seconds: int,
    max_candidates: int,
    summary_cap: int = 240,
) -> tuple[list[EnterpriseRetrievedChunk] | None, dict]:
    """对已有候选做 CE 精排；任何失败返回 (None, trace) 供调用方回退 heuristic。

    返回 None 表示调用方应回退（绝不冒充 cross_encoder）。trace 仅含摘要。
    """
    base = {
        "provider": "cross_encoder",
        "enabled": bool(enabled),
        "model_name": model_name or "",
        "candidate_count": len(results),
    }
    if not enabled:
        return None, {**base, "fallback_used": True, "fallback_reason": "disabled: RAG_CROSS_ENCODER_ENABLED=false"}
    if len(results) < 2:
        return list(results), {**base, "scored_count": len(results),
                               "fallback_used": False, "reason": "insufficient_candidates"}
    ordered = list(results)
    model, load_error = load_cross_encoder(model_name, device)
    if model is None:
        return None, {**base, "fallback_used": True, "fallback_reason": load_error}

    candidates = ordered[: max(1, min(max_candidates, len(ordered)))]
    texts = [_candidate_text(r, summary_cap) for r in candidates]
    started = time.perf_counter()
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(_score_batch, model, query_text[:500], texts, batch_size)
            scores = future.result(timeout=max(1, timeout_seconds))
    except concurrent.futures.TimeoutError:
        return None, {**base, "fallback_used": True, "fallback_reason": "timeout: cross-encoder 推理超时"}
    except Exception as error:  # noqa: BLE001
        return None, {**base, "fallback_used": True, "fallback_reason": f"inference_error: {error}"}

    if not scores or len(scores) != len(candidates):
        return None, {**base, "fallback_used": True, "fallback_reason": "inference_error: 输出长度不匹配"}

    latency_ms = round((time.perf_counter() - started) * 1000.0, 1)
    scored = sorted(zip(scores, candidates), key=lambda item: -item[0])
    head = scored[: max(1, min(top_n, len(scored)))]
    tail = scored[len(head):]
    scored_pairs = list(head) + list(tail)
    reranked = [r for _, r in scored_pairs]
    for score, result in scored_pairs:
        result.rerank_score = round(float(score), 6)
        result.rerank_reason = f"cross_encoder:score={round(float(score), 4)}"
    # 未进入 max_candidates 的其余结果原序保留（只重排，不丢候选）
    reranked.extend(list(ordered[len(candidates):]))
    trace = {
        **base,
        "model_version": _model_version(model),
        "scored_count": len(candidates),
        "fallback_used": False,
        "fallback_reason": "",
        "latency_ms": latency_ms,
        "top_n": len(head),
    }
    return reranked, trace