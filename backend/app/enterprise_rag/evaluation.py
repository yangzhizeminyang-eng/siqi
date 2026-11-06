"""企业版 E3-04 离线评测基线：Recall@K / Precision@K / MRR / NDCG@K / Freshness / Empty / Unauthorized。

- 评估集结构可脱敏可复现（query / project_id / repository_id / commit_sha|ref / expected_chunk_ids /
  expected_file_paths / source_types / tags / difficulty / expected_status）。
- 从 JSON / JSONL 文件读取；缺字段或非法时给出清晰错误。
- 复用 enterprise_rag_search（企业授权 + MetadataFilters + Hybrid 链路），不新写绕过权限/过滤的检索。
- 入口要求 ctx 持有 rag:search（复用 require_tenant_permission），否则视为越权拒绝。
- 指标用确定性纯函数实现；dry-run / 内存态可测，不依赖真实 Milvus 或外部模型。
- 报告不含代码正文、token、cookie、password、JWT、恢复码。
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any, Sequence

from pydantic import BaseModel, Field, ValidationError

from app.core.runtime_config import load_runtime_settings
from app.enterprise_rag.repro_contract import REPORT_SCHEMA_VERSION, build_repro_contract
from app.tenancy.permissions import RAG_SEARCH, require_tenant_permission

# 可脱敏评估集字段
_CASE_FIELDS = {
    "case_id", "query", "project_id", "repository_id", "commit_sha", "ref",
    "expected_chunk_ids", "expected_file_paths", "source_types", "tags", "difficulty",
    "rationale", "source_provenance", "expected_status",
}
_KNOWN_STATUSES = {200, 403, 422}
CaseField = Any


class EvaluationCase(BaseModel):
    """单条评测样本。"""

    case_id: str = ""
    rationale: str = ""
    source_provenance: dict = Field(default_factory=dict)
    query: str
    project_id: str
    repository_id: str
    commit_sha: str | None = None
    ref: str | None = None
    expected_chunk_ids: list[str] = Field(default_factory=list)
    expected_file_paths: list[str] = Field(default_factory=list)
    source_types: list[str] | None = None
    tags: list[str] = Field(default_factory=list)
    difficulty: str = "medium"
    expected_status: int = 200

    def version_bound(self) -> bool:
        return bool((self.commit_sha or "").strip() or (self.ref or "").strip())


# ---------------- 指标（纯函数，确定性） ----------------


def _hit_at(ranked, expected_chunk_ids: set[str], expected_file_paths: set[str]) -> list[int]:
    """返回 ranked 中每个命中项的 1-based rank 列表。"""
    ranks: list[int] = []
    for idx, result in enumerate(ranked, start=1):
        chunk_id = getattr(result, "chunk_id", None) or (result.get("chunk_id") if isinstance(result, dict) else None)
        file_path = getattr(result, "file_path", None) or (result.get("file_path") if isinstance(result, dict) else None)
        hit = (chunk_id and chunk_id in expected_chunk_ids) or (file_path and file_path in expected_file_paths)
        if hit:
            ranks.append(idx)
    return ranks


def recall_at_k(ranked_hits: list[int], n_targets: int, k: int) -> float:
    # 命中按 rank 计（_hit_at 保证 rank 唯一）；同一期望真值被多个切片命中时只算 1 个，
    # cap 在 n_targets，避免同一文件多切片命中把 recall 顶超 1.0。
    if n_targets <= 0:
        return 1.0 if ranked_hits else 0.0
    matched_in_k = sum(1 for r in ranked_hits if r <= k)
    return round(min(matched_in_k, n_targets) / n_targets, 4)


def precision_at_k(ranked_hits: list[int], k: int) -> float:
    if k <= 0:
        return 0.0
    return round(sum(1 for r in ranked_hits if r <= k) / k, 4)


def mrr(ranked_hits: list[int]) -> float:
    if not ranked_hits:
        return 0.0
    return round(1.0 / ranked_hits[0], 4)


def ndcg_at_k(ranked_hits: list[int], n_relevant: int, k: int) -> float:
    if k <= 0:
        return 0.0
    dcg = 0.0
    for rank in ranked_hits:
        if rank <= k:
            dcg += 1.0 / math.log2(rank + 1)
    ideal_count = min(k, max(0, n_relevant))
    idcg = sum(1.0 / math.log2(i + 1) for i in range(1, ideal_count + 1))
    if idcg <= 0:
        return 0.0
    return round(dcg / idcg, 4)


def _case_metrics(results: Sequence[Any], case: EvaluationCase, k: int) -> dict:
    expected_ids = {x for x in (case.expected_chunk_ids or []) if x and x.strip()}
    expected_paths = {x for x in (case.expected_file_paths or []) if x and x.strip()}
    n_targets = len(expected_ids) + len(expected_paths)
    hits = _hit_at(results, expected_ids, expected_paths)
    return {
        "recall_at_k": recall_at_k(hits, n_targets, k),
        "recall_at_5": recall_at_k(hits, n_targets, 5),
        "recall_at_10": recall_at_k(hits, n_targets, 10),
        "precision_at_k": precision_at_k(hits, k),
        "precision_at_5": precision_at_k(hits, 5),
        "mrr": mrr(hits),
        "ndcg_at_k": ndcg_at_k(hits, n_relevant=len(hits), k=k),
        "ndcg_at_5": ndcg_at_k(hits, n_relevant=len(hits), k=5),
        "matched": len(hits),
    }


# ---------------- 数据集读取 ----------------


def load_cases(path: str) -> list[EvaluationCase]:
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"评测集文件不存在: {path}")
    raw: Any
    try:
        text = file_path.read_text(encoding="utf-8")
        if file_path.suffix.lower() == ".jsonl":
            raw = [json.loads(line) for line in text.splitlines() if line.strip()]
        else:
            data = json.loads(text)
            raw = data if isinstance(data, list) else data.get("cases", [])
    except (json.JSONDecodeError, OSError) as error:
        raise ValueError(f"评测集 JSON/JSONL 解析失败: {error}") from error
    if not isinstance(raw, list):
        raise ValueError("评测集顶层应为 case 列表或含 cases 字段的对象")
    cases: list[EvaluationCase] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"第 {index} 条评测样本不是 JSON 对象")
        unknown = set(item) - _CASE_FIELDS
        if unknown:
            raise ValueError(f"第 {index} 条样本含未知字段: {sorted(unknown)}")
        try:
            case = EvaluationCase(**item)
        except ValidationError as error:
            raise ValueError(f"第 {index} 条样本字段非法: {error.errors()}") from error
        if not case.query.strip():
            raise ValueError(f"第 {index} 条样本 query 为空")
        if case.expected_status not in _KNOWN_STATUSES:
            raise ValueError(f"第 {index} 条样本 expected_status 非法: {case.expected_status}")
        if case.expected_status == 200 and not case.version_bound():
            raise ValueError(f"第 {index} 条样本 expected_status=200 必须提供 commit_sha 或 ref")
        cases.append(case)
    if not cases:
        raise ValueError("评测集为空")
    return cases


# ---------------- 评测执行（复用企业 RAG 搜索服务） ----------------


def evaluate_case(ctx, case: EvaluationCase, *, top_k: int, retrieval_mode: str | None = None) -> dict:
    """复用 enterprise_rag_search；捕获 403/422 等预期状态，不新写检索链路。

    纵深防御：即使有人绕过 evaluate_dataset 直接调用本函数，也要求 ctx 持有 rag:search（越权即拒绝）。
    """
    require_tenant_permission(ctx, RAG_SEARCH)
    from fastapi import HTTPException

    from app.enterprise_rag.models import EnterpriseRagSearchPayload
    from app.enterprise_rag.service import enterprise_rag_search

    payload = EnterpriseRagSearchPayload(
        query=case.query,
        project_id=case.project_id,
        repository_id=case.repository_id,
        commit_sha=(case.commit_sha or "").strip() or None,
        ref=(case.ref or "").strip() or None,
        source_types=case.source_types,
        retrieval_mode=retrieval_mode,
    )
    actual_status = 200
    data: dict | None = None
    _t0 = __import__("time").perf_counter()
    try:
        # 企业检索服务本身强制 tenant 归属 + MetadataFilters + rag:search（由调用侧前置）
        result = enterprise_rag_search(ctx, payload)
        data = result
    except HTTPException as error:
        actual_status = error.status_code
    latency_ms = round((__import__("time").perf_counter() - _t0) * 1000.0, 1)
    status_passed = actual_status == case.expected_status

    results = data.get("results", []) if data else []
    metrics = _case_metrics(results, case, top_k) if data and actual_status == 200 else {
        "recall_at_k": 0.0, "precision_at_k": 0.0, "mrr": 0.0, "ndcg_at_k": 0.0, "matched": 0,
    }
    fresh = bool(data.get("fresh")) if data else False
    empty = (actual_status == 200) and not results
    retrieval_passed = (case.expected_status == 200) and (metrics["recall_at_k"] > 0)
    passed = status_passed and (retrieval_passed or case.expected_status != 200)
    return {
        "case_index": None,  # 外层回填
        "case_id": case.case_id,
        "query": case.query,
        "project_id": case.project_id,
        "repository_id": case.repository_id,
        "version": case.commit_sha or case.ref,
        "tags": case.tags,
        "difficulty": case.difficulty,
        "expected_status": case.expected_status,
        "actual_status": actual_status,
        "status_passed": status_passed,
        "retrieval_passed": bool(retrieval_passed),
        "empty": empty,
        "fresh": fresh,
        **metrics,
        "passed": bool(passed),
        "latency_ms": latency_ms,
        "index_id": (data.get("filters") or {}).get("index_id") if data else None,
        "detail_versions": repr([]),  # 无敏感
    }


def _non_sensitive(case: dict) -> dict:
    """报告安全化：仅保留元数据与指标，不含代码正文/token/凭据。"""
    allow = {"case_id", "query", "project_id", "repository_id", "version", "tags", "difficulty", "case_index"}
    out = {key: case[key] for key in allow if key in case}
    for key in ("expected_status", "actual_status", "status_passed", "retrieval_passed", "empty", "fresh", "passed",
                "recall_at_k", "recall_at_5", "recall_at_10", "precision_at_k", "precision_at_5",
                "mrr", "ndcg_at_k", "ndcg_at_5", "matched", "latency_ms", "index_id"):
        out[key] = case.get(key)
    return out


def evaluate_dataset(
    ctx,
    cases: Sequence[EvaluationCase],
    *,
    top_k: int = 8,
    retrieval_mode: str | None = None,
    require_milvus: bool = False,
    index_identity: dict | None = None,
    repository_url: str = "",
) -> dict:
    """整体评估；入口要求 ctx 持有 rag:search（复用企业权限），否则越权拒绝。"""
    require_tenant_permission(ctx, RAG_SEARCH)

    per_case: list[dict] = []
    for index, case in enumerate(cases):
        result = evaluate_case(ctx, case, top_k=top_k, retrieval_mode=retrieval_mode)
        result["case_index"] = index
        per_case.append(result)

    success = [c for c in per_case if c["actual_status"] == 200]
    n = len(per_case)
    avg = lambda key: round(sum(c.get(key, 0.0) for c in per_case) / max(1, n), 4)
    recall = avg("recall_at_k")
    precision = avg("precision_at_k")
    mrr_avg = avg("mrr")
    ndcg = avg("ndcg_at_k")

    unauthorized_cases = [c for c in per_case if c["expected_status"] in (403, 422)]
    boundary_pass_rate = round(
        sum(1 for c in unauthorized_cases if c["status_passed"]) / max(1, len(unauthorized_cases)), 4
    )
    empty_rate = round(sum(1 for c in success if c["empty"]) / max(1, len(success)), 4)
    fresh_rate = round(sum(1 for c in success if c["fresh"]) / max(1, len(success)), 4)

    # 聚合标签
    metrics_by_tag: dict[str, dict[str, Any]] = {}
    for tag in sorted({t for c in per_case for t in c.get("tags", [])}):
        tagged = [c for c in per_case if tag in c.get("tags", [])]
        metrics_by_tag[tag] = {
            "count": len(tagged),
            "recall_at_k": round(sum(c["recall_at_k"] for c in tagged) / len(tagged), 4),
            "precision_at_k": round(sum(c["precision_at_k"] for c in tagged) / len(tagged), 4),
            "mrr": round(sum(c["mrr"] for c in tagged) / len(tagged), 4),
            "ndcg_at_k": round(sum(c["ndcg_at_k"] for c in tagged) / len(tagged), 4),
        }

    failed_cases = [_non_sensitive(c) for c in per_case if not c["passed"]]
    latency_values = [c.get("latency_ms", 0.0) for c in per_case if c.get("latency_ms")]
    summary = {
        "total_cases": n,
        "passed_cases": sum(1 for c in per_case if c["passed"]),
        "success_cases": len(success),
        "recall_at_k": recall,
        "recall_at_5": avg("recall_at_5"),
        "recall_at_10": avg("recall_at_10"),
        "precision_at_k": precision,
        "precision_at_5": avg("precision_at_5"),
        "mrr": mrr_avg,
        "ndcg_at_k": ndcg,
        "ndcg_at_5": avg("ndcg_at_5"),
        "freshness_rate": fresh_rate,
        "empty_result_rate": empty_rate,
        "unauthorized_pass_rate": boundary_pass_rate,
        "latency_p50_ms": _pct(latency_values, 50),
        "latency_p95_ms": _pct(latency_values, 95),
    }
    gate = quality_gate(summary)
    _main_mode = (retrieval_mode or "hybrid").lower()
    milvus_ok, vector_backend = _milvus_status()
    backend_available = vector_backend in {"milvus", "local_milvus_lite"}
    fallback_used = not backend_available
    if require_milvus and not milvus_ok:
        validation_status = "blocked"
    elif not milvus_ok:
        validation_status = "local_only"  # 无真实 Milvus：内存/降级结果只当本地参考，不算云端 verified
    elif fallback_used:
        validation_status = "local_only"
    else:
        validation_status = "verified"
    first = cases[0] if cases else None
    _canon = json.dumps(sorted((c.model_dump(mode="json") for c in cases), key=lambda x: x.get("case_id", "")),
                        ensure_ascii=False, separators=(",", ":"))
    _dataset_sha = hashlib.sha256(_canon.encode("utf-8")).hexdigest()
    _config_digest = hashlib.sha256(json.dumps(
        {"top_k": top_k, "retrieval_mode": _main_mode},
        ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    validation_reasons: list[str] = []
    if require_milvus and not milvus_ok:
        validation_reasons.append("require_milvus=true 但 Milvus 不可用")
    if not milvus_ok:
        if vector_backend == "local_milvus_lite":
            validation_reasons.append("local_milvus_lite:not_cloud_verified")
        else:
            validation_reasons.append(f"milvus_unavailable:{vector_backend}")
    elif fallback_used:
        validation_reasons.append(f"vector_backend_not_milvus:{vector_backend}")
    report = {
        "dataset_sha256": _dataset_sha,
        "config_snapshot_sha256": _config_digest,
        "dataset_size": len(cases),
        "retrieval_mode": _main_mode,
        "vector_backend": vector_backend,
        "milvus_verified": bool(milvus_ok and not fallback_used),
        "fallback_used": bool(fallback_used),
        "validation_status": validation_status,
        "commit_sha": getattr(first, "commit_sha", None) or getattr(first, "ref", None) if first else None,
        "summary": summary,
        "recommended_thresholds": RECOMMENDED_THRESHOLDS,
        "needs_improvement": gate["needs_improvement"],
        "per_case_results": [_non_sensitive(c) for c in per_case],
        "failed_cases": failed_cases,
        "metrics_by_tag": metrics_by_tag,
        "config_snapshot": {
            "top_k": top_k,
            "retrieval_mode": _main_mode,
            "dry_run_in_memory": _in_memory_mode(),
            "symbol_boost": float(getattr(load_runtime_settings(), "rag_symbol_boost", 0.0)),
            "cross_encoder_enabled": bool(getattr(load_runtime_settings(), "rag_cross_encoder_enabled", False)),
            "cross_encoder_model": str(getattr(load_runtime_settings(), "rag_cross_encoder_model", "") or ""),
            "cross_encoder_device": str(getattr(load_runtime_settings(), "rag_cross_encoder_device", "cpu") or "cpu"),
        },
        "report_schema_version": REPORT_SCHEMA_VERSION,
    }
    report["repro_contract"] = build_repro_contract(
        ctx, cases,
        top_k=top_k, retrieval_mode=_main_mode, dataset_sha256=_dataset_sha,
        index_identity=index_identity,
        case_index_ids=[c.get("index_id") for c in per_case if c.get("index_id")],
        repository_url=repository_url,
        validation={
            "status": validation_status, "vector_backend": vector_backend,
            "milvus_verified": bool(milvus_ok and not fallback_used),
            "fallback_used": bool(fallback_used), "reasons": validation_reasons,
        },
    )
    if index_identity:
        report.update(index_identity)
    return report


def _in_memory_mode() -> bool:
    try:
        from app.db.repositories import persistence_enabled

        return not persistence_enabled()
    except Exception:  # noqa: BLE001
        return True


def write_report(report: dict, out_path: str) -> str:
    Path(out_path).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return os.path.realpath(out_path)


# ---------------- 质量门槛（不硬说达标，仅标记 needs_improvement） ----------------
# 默认建议阈值（按 top_k 的 summary 指标口径）
RECOMMENDED_THRESHOLDS: dict[str, float] = {
    "recall_at_k": 0.5,
    "precision_at_k": 0.3,
    "mrr": 0.4,
    "ndcg_at_k": 0.5,
}


def quality_gate(summary: dict, thresholds: dict[str, float] | None = None) -> dict:
    """返回 below<需改进项> 与 needs_improvement。低于阈值只标记，不让脚本伪通过。"""
    thresholds = thresholds or RECOMMENDED_THRESHOLDS
    below: dict[str, float] = {}
    for key, threshold in thresholds.items():
        value = summary.get(key)
        if isinstance(value, (int, float)) and value < threshold:
            below[key] = value
    return {"thresholds": dict(thresholds), "below": below,
            "needs_improvement": bool(below)}


def _milvus_status() -> tuple[bool, str]:
    """返回 (milvus_available, vector_backend)。Milvus 不可用/未启用时永远不视为 verified。"""
    try:
        from app.core import runtime_config

        settings = runtime_config.load_runtime_settings()
        if not settings.use_milvus:
            return False, "local_no_milvus"
        from app.retrieval.llamaindex_backend import check_milvus_health

        health = check_milvus_health(timeout_ms=3000)
        if health.get("status") == "ok":
            return True, "milvus"
        if health.get("status") == "skipped" and health.get("reason") == "local-milvus-lite":
            from app.retrieval.llamaindex_backend import MilvusVectorStore

            if MilvusVectorStore is None:
                return False, "unavailable"
            return False, "local_milvus_lite"
        return False, "unavailable"
    except Exception:  # noqa: BLE001
        return False, "unavailable"


def _pct(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = int(round((len(ordered) - 1) * pct / 100.0))
    return round(ordered[index], 1)

