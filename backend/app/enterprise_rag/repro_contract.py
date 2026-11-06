"""OPT-04 RAG 报告复现契约：把一次评测的“可复现边界”固化为报告字段，并提供离线对拍。

- 固化：仓库版本（commit/ref）、数据集 hash、索引标识、Embedding、Reranker、
  检索参数与过滤、依赖版本、RAG 配置 hash；
- 区分：verified / local_only / blocked（含原因），绝不把本地/降级结果说成云端可信；
- 对拍：同一 dataset_sha256 下输出案例回归/改善、指标 delta 与环境漂移；
  缺契约的旧报告如实标记 not_collected，不假装可比；
- 脱敏：不含 token/密码/正文；config_sha256 只覆盖 RAG 行为开关，不覆盖凭据与地址。
"""

from __future__ import annotations

import hashlib
import json
import platform
from importlib import metadata
from typing import Any, Sequence

REPRO_SCHEMA_VERSION = "rag-eval-repro-v1"
REPORT_SCHEMA_VERSION = "rag-eval-report-v2"

# 参与 config_sha256 的 RAG 行为开关（刻意不含 milvus_token / database_url / broker 等凭据与地址）
_RAG_SETTINGS_FIELDS = (
    "use_milvus", "rag_mock_fallback", "rag_retrieval_mode", "rag_query_expansion",
    "rag_rerank_enabled", "rag_rerank_max_candidates",
    "rag_vector_top_k", "rag_bm25_top_k", "rag_final_top_k",
    "rag_query_rewrite_enabled", "rag_query_max_variants", "rag_query_llm",
    "rag_query_variant_decay",
    "rag_rerank_provider", "rag_rerank_top_n", "rag_rerank_timeout_seconds",
    "rag_symbol_boost",
    "rag_hybrid_vector_weight", "rag_hybrid_bm25_weight",
    "rag_cross_encoder_enabled", "rag_cross_encoder_model", "rag_cross_encoder_device",
    "rag_cross_encoder_top_n", "rag_cross_encoder_batch_size",
    "rag_cross_encoder_timeout_seconds", "rag_cross_encoder_max_candidates",
    "rag_cross_encoder_fallback",
)

_DEP_PACKAGES = (
    "fastapi", "pydantic", "sqlalchemy", "numpy", "pymilvus", "llama-index-core",
)

_METRIC_KEYS = ("recall_at_k", "precision_at_k", "mrr", "ndcg_at_k",
                "latency_p50_ms", "latency_p95_ms")


def _sha256(payload: Any) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _dedupe(values) -> list[str]:
    return sorted({str(v).strip() for v in values if str(v or "").strip()})


def rag_settings_snapshot() -> dict[str, Any]:
    from app.core.runtime_config import load_runtime_settings

    settings = load_runtime_settings()
    return {field: getattr(settings, field, None) for field in _RAG_SETTINGS_FIELDS}


def dependencies_snapshot() -> dict[str, Any]:
    packages: dict[str, str] = {}
    for name in _DEP_PACKAGES:
        try:
            packages[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            packages[name] = "not_installed"
    return {"python": platform.python_version(), "packages": packages}


def embedding_identity() -> dict[str, Any]:
    """Embedding 身份（只用配置与已缓存维度，不触发真实探针/外网调用）。"""
    try:
        from app.core import model_config

        settings = model_config.load_embedding_settings()
        dimension = getattr(settings, "dimensions", None)
        if dimension is None:
            try:
                from app.services import embedding_service

                dimension = embedding_service.last_real_embedding_dimension()
            except Exception:  # noqa: BLE001
                dimension = None
        configured = bool(model_config.embedding_configured())
        return {
            "provider": settings.provider, "model": settings.model,
            "dimension": dimension, "configured": configured,
            "status": "configured" if configured else "not_configured",
        }
    except Exception as error:  # noqa: BLE001
        return {"provider": None, "model": None, "dimension": None, "configured": False,
                "status": "not_collected", "reason": type(error).__name__}


def reranker_snapshot() -> dict[str, Any]:
    from app.core.runtime_config import load_runtime_settings

    settings = load_runtime_settings()
    return {
        "enabled": bool(settings.rag_rerank_enabled),
        "provider": settings.rag_rerank_provider,
        "top_n": settings.rag_rerank_top_n,
        "timeout_seconds": settings.rag_rerank_timeout_seconds,
        "max_candidates": settings.rag_rerank_max_candidates,
        "cross_encoder": {
            "enabled": bool(settings.rag_cross_encoder_enabled),
            "model": settings.rag_cross_encoder_model,
            "device": settings.rag_cross_encoder_device,
            "top_n": settings.rag_cross_encoder_top_n,
            "timeout_seconds": settings.rag_cross_encoder_timeout_seconds,
            "fallback": settings.rag_cross_encoder_fallback,
        },
    }


def build_repro_contract(
    ctx,
    cases: Sequence[Any],
    *,
    top_k: int,
    retrieval_mode: str,
    dataset_sha256: str,
    index_identity: dict | None = None,
    case_index_ids: Sequence[str] | None = None,
    repository_url: str = "",
    validation: dict | None = None,
) -> dict[str, Any]:
    identity = dict(index_identity or {})
    identity_ids = [identity.get("index_id")] if identity.get("index_id") else []
    merged_ids = _dedupe([*identity_ids, *(case_index_ids or [])])
    if identity:
        index_source = "cli_index_identity"
    elif merged_ids:
        index_source = "per_case_echo"
    else:
        index_source = "not_collected"

    versions = _dedupe((c.commit_sha or c.ref) for c in cases)
    repository = {
        "url": (repository_url or "").rstrip("/").removesuffix(".git") or None,
        "project_ids": _dedupe(c.project_id for c in cases),
        "repository_ids": _dedupe(c.repository_id for c in cases),
        "versions": versions,
        "commit_sha": identity.get("commit_sha") or (versions[0] if len(versions) == 1 else None),
    }
    retrieval = {
        "mode": retrieval_mode,
        "top_k": top_k,
        "filters": {
            "tenant_id": getattr(ctx, "tenant_id", None),
            "user_id": getattr(ctx, "user_id", None),
            "role": getattr(ctx, "role", None),
            "project_ids": _dedupe(c.project_id for c in cases),
            "repository_ids": _dedupe(c.repository_id for c in cases),
            "versions": versions,
            "source_types": _dedupe(st for c in cases for st in (c.source_types or [])),
        },
        "settings": rag_settings_snapshot(),
    }
    dependencies = dependencies_snapshot()
    contract = {
        "schema_version": REPRO_SCHEMA_VERSION,
        "repository": repository,
        "dataset": {"sha256": dataset_sha256, "size": len(cases)},
        "index": {
            "index_ids": merged_ids,
            "index_commit_sha": identity.get("commit_sha") or None,
            "collection_name": identity.get("collection_name") or None,
            "source": index_source,
        },
        "embedding": embedding_identity(),
        "reranker": reranker_snapshot(),
        "retrieval": retrieval,
        "validation": dict(validation) if validation else {"status": "not_collected"},
        "dependencies": dependencies,
        "config_sha256": _sha256({
            "retrieval": {"mode": retrieval_mode, "top_k": top_k},
            "settings": retrieval["settings"],
        }),
        "deps_sha256": _sha256(dependencies),
    }
    return contract


# ---------------- 离线对拍 ----------------

def _contract_fields(contract: dict) -> dict[str, Any]:
    return {
        "config_sha256": contract.get("config_sha256"),
        "deps_sha256": contract.get("deps_sha256"),
        "retrieval_mode": (contract.get("retrieval") or {}).get("mode"),
        "top_k": (contract.get("retrieval") or {}).get("top_k"),
        "validation_status": (contract.get("validation") or {}).get("status"),
        "embedding_model": (contract.get("embedding") or {}).get("model"),
        "embedding_dimension": (contract.get("embedding") or {}).get("dimension"),
        "index_ids": (contract.get("index") or {}).get("index_ids"),
        "commit_sha": (contract.get("repository") or {}).get("commit_sha"),
    }


def _environment_drift(baseline: dict, current: dict) -> dict[str, Any]:
    base_contract, cur_contract = baseline.get("repro_contract"), current.get("repro_contract")
    if not isinstance(base_contract, dict) or not isinstance(cur_contract, dict):
        return {"contract": "not_collected"}
    base_fields, cur_fields = _contract_fields(base_contract), _contract_fields(cur_contract)
    return {
        key: {"baseline": base_fields[key], "current": cur_fields[key]}
        for key in base_fields if base_fields[key] != cur_fields[key]
    }


def compare_rag_eval_reports(baseline: dict, current: dict) -> dict[str, Any]:
    """同一 dataset_sha256 下对拍：案例回归/改善、指标 delta、环境漂移。

    数据集不同直接拒绝（应视为新基线）；缺复现契约的旧报告把环境漂移记为 not_collected，
    提醒“无可比性证据”，绝不静默当作相同环境。
    """
    if not isinstance(baseline, dict) or not isinstance(current, dict):
        raise ValueError("对拍需要两个报告 dict")
    base_dataset, cur_dataset = baseline.get("dataset_sha256"), current.get("dataset_sha256")
    if not base_dataset or not cur_dataset or base_dataset != cur_dataset:
        raise ValueError("对拍必须基于同一 dataset_sha256（数据集已变更，请重设基线）")

    base_cases = {c.get("case_id"): bool(c.get("passed"))
                  for c in (baseline.get("per_case_results") or [])}
    cur_cases = {c.get("case_id"): bool(c.get("passed"))
                 for c in (current.get("per_case_results") or [])}
    regressions: list[dict] = []
    improvements: list[dict] = []
    for case_id in sorted(cur_cases):
        if case_id not in base_cases:
            continue
        if base_cases[case_id] and not cur_cases[case_id]:
            regressions.append({"case_id": case_id})
        elif not base_cases[case_id] and cur_cases[case_id]:
            improvements.append({"case_id": case_id})

    base_summary = baseline.get("summary") or {}
    cur_summary = current.get("summary") or {}
    metric_deltas: dict[str, float] = {}
    for key in _METRIC_KEYS:
        base_value, cur_value = base_summary.get(key), cur_summary.get(key)
        if isinstance(base_value, (int, float)) and isinstance(cur_value, (int, float)):
            metric_deltas[key] = round(cur_value - base_value, 4)

    drift = _environment_drift(baseline, current)
    return {
        "same_dataset": True,
        "dataset_sha256": base_dataset,
        "baseline_schema": baseline.get("report_schema_version") or "rag-eval-report-v1",
        "current_schema": current.get("report_schema_version") or "rag-eval-report-v1",
        "regression_count": len(regressions),
        "regressions": regressions,
        "improvement_count": len(improvements),
        "improvements": improvements,
        "missing_cases": sorted(set(base_cases) - set(cur_cases)),
        "new_cases": sorted(set(cur_cases) - set(base_cases)),
        "metric_deltas": metric_deltas,
        "environment_drift": drift,
        "drift_detected": bool(drift),
    }
