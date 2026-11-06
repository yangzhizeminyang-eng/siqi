"""E3-09 Cross-Encoder 对比评测：同一数据集/commit/collection/embedding/top-k/过滤/权限 下，
对比 baseline(hybrid 无重排) / heuristic / llm / cross_encoder 四档，产出可复现对比报告。

安全：四档都走 enterprise_rag_search（复用 enterprise_rag_search 授权 + MetadataFilters + Hybrid），
不新写绕过过滤的检索；Cross-Encoder 只重排已过滤候选、不发代码正文；不可用/超时回退 heuristic 并记录。
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone

from app.enterprise_rag.cross_encoder import load_cross_encoder
from app.enterprise_rag.evaluation import evaluate_dataset

_ENV_KEYS = (
    "RAG_RERANK_ENABLED", "RAG_RERANK_PROVIDER",
    "RAG_CROSS_ENCODER_ENABLED", "RAG_CROSS_ENCODER_MODEL", "RAG_CROSS_ENCODER_DEVICE",
    "RAG_CROSS_ENCODER_BATCH_SIZE", "RAG_CROSS_ENCODER_TIMEOUT_SECONDS", "RAG_CROSS_ENCODER_MAX_CANDIDATES",
)


def _set_env(pairs):
    saved = {key: os.environ.get(key) for key in _ENV_KEYS}
    for key, value in pairs:
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = str(value)
    return saved


def _restore_env(saved):
    for key, value in saved.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def _run_once(ctx, cases, *, top_k, require_milvus, mode, provider, ce_enabled, ce_model, ce_device):
    saved = _set_env([
        ("RAG_RERANK_ENABLED", "true" if provider else "false"),
        ("RAG_RERANK_PROVIDER", provider or ""),
        ("RAG_CROSS_ENCODER_ENABLED", "true" if ce_enabled else "false"),
        ("RAG_CROSS_ENCODER_MODEL", ce_model or ""),
        ("RAG_CROSS_ENCODER_DEVICE", ce_device or "cpu"),
    ])
    try:
        started = time.perf_counter()
        report = evaluate_dataset(ctx, cases, top_k=top_k, retrieval_mode=mode, require_milvus=require_milvus)
        latency_ms = round((time.perf_counter() - started) * 1000.0, 1)
        return report, latency_ms
    finally:
        _restore_env(saved)


def compare_rerank(ctx, cases, *, top_k, require_milvus, repository_url, project_id, repository_id,
                   index_identity=None, cross_encoder_model="", cross_encoder_device="cpu") -> dict:
    """跑四档并汇总为 E3-09 对比报告 dict（不含敏感信息）。"""
    ce_model = (cross_encoder_model or "").strip()
    ce_device = (cross_encoder_device or "cpu").strip()
    ce_enabled = bool(ce_model)

    baseline, bl_lat = _run_once(ctx, cases, top_k=top_k, require_milvus=require_milvus,
                                 mode="hybrid", provider=None, ce_enabled=False, ce_model="", ce_device="cpu")
    heuristic, he_lat = _run_once(ctx, cases, top_k=top_k, require_milvus=require_milvus,
                                  mode="rerank", provider="heuristic", ce_enabled=False, ce_model="", ce_device="cpu")
    llm, llm_lat = _run_once(ctx, cases, top_k=top_k, require_milvus=require_milvus,
                             mode="rerank", provider="llm", ce_enabled=False, ce_model="", ce_device="cpu")
    probe_model, probe_reason = load_cross_encoder(ce_model, ce_device) if ce_enabled else (None, "disabled: 未配置模型")
    ce, ce_lat = _run_once(ctx, cases, top_k=top_k, require_milvus=require_milvus,
                           mode="rerank", provider="cross_encoder", ce_enabled=ce_enabled,
                           ce_model=ce_model, ce_device=ce_device)
    model_version = ""
    if probe_model is not None:
        from app.enterprise_rag.cross_encoder import _model_version
        model_version = _model_version(probe_model)
    if probe_model is not None:
        ce_status = "executed"
        ce_fallback = False
        ce_reason = ""
    elif not ce_model:
        ce_status = "provider_unavailable"
        ce_fallback = True
        ce_reason = probe_reason
    else:
        ce_status = "fallback"
        ce_fallback = True
        ce_reason = probe_reason

    from app.core import model_config
    from app.retrieval.llamaindex_backend import embedding_dim
    emb = model_config.load_embedding_settings()

    def _metrics(report):
        return {
            "recall_at_k": report["summary"].get("recall_at_k"),
            "precision_at_k": report["summary"].get("precision_at_k"),
            "mrr": report["summary"].get("mrr"),
            "ndcg_at_k": report["summary"].get("ndcg_at_k"),
            "validation_status": report.get("validation_status"),
            "milvus_verified": report.get("milvus_verified"),
            "fallback_used": report.get("fallback_used"),
        }

    first = baseline
    return {
        "dataset_sha256": first.get("dataset_sha256"),
        "config_snapshot_sha256": first.get("config_snapshot_sha256"),
        "dataset_size": first.get("dataset_size"),
        "repository_url": repository_url,
        "repository_id": repository_id,
        "project_id": project_id,
        "commit_sha": first.get("commit_sha"),
        "collection_name": (index_identity or {}).get("collection_name") or first.get("index_id"),
        "index_id": first.get("index_id"),
        "embedding_provider": getattr(emb, "provider", ""),
        "embedding_model": getattr(emb, "model", ""),
        "embedding_dimension": embedding_dim(),
        "baseline_metrics": {**_metrics(baseline), "latency_ms": bl_lat},
        "heuristic_metrics": {**_metrics(heuristic), "latency_ms": he_lat},
        "llm_metrics": {**_metrics(llm), "latency_ms": llm_lat},
        "cross_encoder_metrics": {
            **_metrics(ce), "latency_ms": ce_lat,
            "status": ce_status, "fallback_used": bool(ce_fallback),
            "fallback_reason": ce_reason, "model_name": ce_model,
            "model_version": model_version, "device": ce_device,
        },
        "mode_comparison": {
            "baseline_hybrid": first["summary"],
            "heuristic_rerank": heuristic["summary"],
            "llm_rerank": llm["summary"],
            "cross_encoder_rerank": ce["summary"],
        },
        "per_tag_metrics": {
            "baseline_hybrid": baseline.get("metrics_by_tag"),
            "cross_encoder": ce.get("metrics_by_tag"),
        },
        "latency_p50_p95_ms": {
            "baseline": [baseline["summary"].get("latency_p50_ms"), baseline["summary"].get("latency_p95_ms")],
            "heuristic": [heuristic["summary"].get("latency_p50_ms"), heuristic["summary"].get("latency_p95_ms")],
            "llm": [llm["summary"].get("latency_p50_ms"), llm["summary"].get("latency_p95_ms")],
            "cross_encoder": [ce["summary"].get("latency_p50_ms"), ce["summary"].get("latency_p95_ms")],
        },
        "model_loading": {
            "status": ce_status, "model_name": ce_model, "device": ce_device,
            "model_version": model_version, "reason": ce_reason,
        },
        "fallback_used": bool(ce_fallback),
        "needs_improvement": ce.get("needs_improvement"),
        "failed_cases": ce.get("failed_cases"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def write_compare_report(report: dict, out_path: str) -> str:
    import json as _json
    from pathlib import Path as _P
    _P(out_path).parent.mkdir(parents=True, exist_ok=True)
    _P(out_path).write_text(_json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path