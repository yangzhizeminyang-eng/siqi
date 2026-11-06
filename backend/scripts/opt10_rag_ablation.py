#!/usr/bin/env python
"""OPT-10 RAG 检索参数消融 CLI：固定基线 + 子进程隔离的逐配置对比（本地/内存/脱敏）。

维度：
- top-k：final top-k（RAG_FINAL_TOP_K）与两路候选数（RAG_VECTOR_TOP_K/RAG_BM25_TOP_K）；
- 混合权重：RAG_HYBRID_VECTOR_WEIGHT / RAG_HYBRID_BM25_WEIGHT（0 = 禁用该路）；
- 符号 boost：RAG_SYMBOL_BOOST；reranker：RAG_RERANK_ENABLED；
- 切片参数：RAG_CHUNK_LINES / RAG_CHUNK_OVERLAP_LINES / RAG_CHUNK_MAX_CHARS
  （import-time 常量：必须子进程隔离生效；切片探针用确定性的脱敏合成语料，
   度量切片产物规模 + BM25 可检索性）。

产物：基线/逐配置 summary、样本级 delta（回归/改善）、失败样本、逐配置配置 hash、
环境事实块（validation_status 如实标注 local_only，不冒称云端质量）、确定性校验
（同一基线跑两次，忽略延迟字段）。

安全边界：子进程强制 USE_MILVUS=false / 本地 mock embedding / LLM 关闭；
真实生产/云端评测需另行显式确认，本脚本不执行。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

DEFAULT_DATASET = "evaluation/enterprise_rag/e3_05_baseline.jsonl"
CHILD_MARKER = "ABLATION_CHILD_JSON:"
SCHEMA_VERSION = "rag-ablation-report-v1"

# 子进程强制本地口径（脚本只做本地/内存消融；不触碰真实 Milvus/模型/外网）
_LOCAL_ENV = {
    "APP_ENV": "local",
    "USE_MILVUS": "false",
    "USE_SQLALCHEMY": "false",
    "USE_CELERY": "false",
    "MODEL_MOCK_FALLBACK": "true",
    "RAG_MOCK_FALLBACK": "true",
    "RAG_QUERY_LLM": "false",
    "PYTHONIOENCODING": "utf-8",
}

_SUMMARY_DELTA_KEYS = (
    "recall_at_k", "recall_at_5", "recall_at_10", "precision_at_k", "precision_at_5",
    "mrr", "ndcg_at_k", "ndcg_at_5", "passed_cases", "empty_result_rate", "freshness_rate",
)


# ---------------- 配置矩阵与 hash ----------------

def build_eval_configs(quick: bool = False) -> list[dict[str, Any]]:
    """固定基线 + 各维度变体；quick 仅保留覆盖各维度的一条代表配置（仍含 baseline）。"""
    full: list[dict[str, Any]] = [
        {"name": "baseline", "top_k": 8, "mode": "hybrid", "env": {}},
        {"name": "topk_4", "top_k": 4, "mode": "hybrid", "env": {"RAG_FINAL_TOP_K": "4"}},
        {"name": "topk_16", "top_k": 16, "mode": "hybrid", "env": {"RAG_FINAL_TOP_K": "16"}},
        {"name": "legs_small", "top_k": 8, "mode": "hybrid",
         "env": {"RAG_VECTOR_TOP_K": "10", "RAG_BM25_TOP_K": "5"}},
        {"name": "legs_large", "top_k": 8, "mode": "hybrid",
         "env": {"RAG_VECTOR_TOP_K": "40", "RAG_BM25_TOP_K": "20"}},
        {"name": "vector_only", "top_k": 8, "mode": "hybrid",
         "env": {"RAG_HYBRID_BM25_WEIGHT": "0.0"}},
        {"name": "bm25_only", "top_k": 8, "mode": "hybrid",
         "env": {"RAG_HYBRID_VECTOR_WEIGHT": "0.0"}},
        {"name": "vector_weight_2", "top_k": 8, "mode": "hybrid",
         "env": {"RAG_HYBRID_VECTOR_WEIGHT": "2.0"}},
        {"name": "bm25_weight_2", "top_k": 8, "mode": "hybrid",
         "env": {"RAG_HYBRID_BM25_WEIGHT": "2.0"}},
        {"name": "symbol_boost_0", "top_k": 8, "mode": "hybrid", "env": {"RAG_SYMBOL_BOOST": "0.0"}},
        {"name": "symbol_boost_2", "top_k": 8, "mode": "hybrid", "env": {"RAG_SYMBOL_BOOST": "2.0"}},
        {"name": "rerank_on", "top_k": 8, "mode": "hybrid", "env": {"RAG_RERANK_ENABLED": "true"}},
    ]
    if quick:
        return [c for c in full if c["name"] in {"baseline", "topk_4", "vector_only", "rerank_on"}]
    return full


def build_chunk_configs(quick: bool = False) -> list[dict[str, Any]]:
    full: list[dict[str, Any]] = [
        {"name": "max_chars_4000", "env": {}},
        {"name": "max_chars_1200", "env": {"RAG_CHUNK_MAX_CHARS": "1200"}},
        {"name": "max_chars_8000", "env": {"RAG_CHUNK_MAX_CHARS": "8000"}},
        {"name": "lines_20", "env": {"RAG_CHUNK_LINES": "20"}},
        {"name": "lines_80", "env": {"RAG_CHUNK_LINES": "80"}},
    ]
    if quick:
        keep = {"max_chars_4000", "max_chars_1200"}
        return [c for c in full if c["name"] in keep]
    return full


def config_sha256(knobs: dict[str, Any], dataset_sha256: str) -> str:
    """配置 hash：只覆盖「有效行为开关 + 数据集」，同配置可复算、异配置可区分。"""
    payload = {"knobs": {key: knobs[key] for key in sorted(knobs)}, "dataset_sha256": dataset_sha256}
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


# ---------------- 对比/确定性（纯函数） ----------------

def deltas_vs_baseline(baseline: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    base_cases = {c["case_id"]: c for c in baseline.get("per_case", [])}
    cur_cases = {c["case_id"]: c for c in current.get("per_case", [])}
    regressions: list[str] = []
    improvements: list[str] = []
    for case_id in sorted(cur_cases):
        if case_id not in base_cases:
            continue
        if base_cases[case_id].get("passed") and not cur_cases[case_id].get("passed"):
            regressions.append(case_id)
        elif not base_cases[case_id].get("passed") and cur_cases[case_id].get("passed"):
            improvements.append(case_id)
    base_summary = baseline.get("summary") or {}
    cur_summary = current.get("summary") or {}
    metric_deltas: dict[str, float] = {}
    for key in _SUMMARY_DELTA_KEYS:
        base_value, cur_value = base_summary.get(key), cur_summary.get(key)
        if isinstance(base_value, (int, float)) and isinstance(cur_value, (int, float)):
            metric_deltas[key] = round(cur_value - base_value, 4)
    return {
        "metric_deltas": metric_deltas,
        "regressions": regressions,
        "improvements": improvements,
        "missing_cases": sorted(set(base_cases) - set(cur_cases)),
        "new_cases": sorted(set(cur_cases) - set(base_cases)),
    }


def failure_samples(result: dict[str, Any], limit: int = 10) -> list[dict[str, Any]]:
    """失败样本（不敏感）：case_id + 状态 + 指标 + 归因分类，不含 query 正文外内容。"""
    samples: list[dict[str, Any]] = []
    for case in result.get("per_case", []):
        if case.get("passed"):
            continue
        reason = ("status_mismatch"
                  if case.get("actual_status") != case.get("expected_status") else "recall_zero")
        samples.append({
            "case_id": case.get("case_id"),
            "actual_status": case.get("actual_status"),
            "expected_status": case.get("expected_status"),
            "recall_at_k": case.get("recall_at_k"),
            "mrr": case.get("mrr"),
            "tags": case.get("tags") or [],
            "reason": reason,
        })
        if len(samples) >= limit:
            break
    return samples


def determinism_compare(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    """确定性校验：配置 hash / summary（忽略延迟）/ 样本级结果必须一致。"""
    differences: list[str] = []
    if left.get("config_sha256") != right.get("config_sha256"):
        differences.append(f"config_sha256: {left.get('config_sha256')} != {right.get('config_sha256')}")
    left_summary = left.get("summary") or {}
    right_summary = right.get("summary") or {}
    for key in sorted(set(left_summary) | set(right_summary)):
        if "latency" in key:
            continue
        if left_summary.get(key) != right_summary.get(key):
            differences.append(f"summary.{key}: {left_summary.get(key)} != {right_summary.get(key)}")
    left_cases = {c["case_id"]: (c.get("passed"), c.get("recall_at_k"), c.get("mrr"))
                  for c in left.get("per_case", [])}
    right_cases = {c["case_id"]: (c.get("passed"), c.get("recall_at_k"), c.get("mrr"))
                   for c in right.get("per_case", [])}
    if left_cases != right_cases:
        diff_ids = sorted(case_id for case_id in set(left_cases) | set(right_cases)
                          if left_cases.get(case_id) != right_cases.get(case_id))
        differences.append(f"per_case 不一致样本: {diff_ids[:10]}")
    return {"checked": True, "identical": not differences, "differences": differences[:20]}


# ---------------- 子进程执行 ----------------

def _child_env(config: dict[str, Any]) -> dict[str, str]:
    env = os.environ.copy()
    env.update(_LOCAL_ENV)
    env["ENV_FILE"] = str(BACKEND_ROOT / "tests" / ".env.hermetic-nonexistent")
    env.update({key: str(value) for key, value in config.get("env", {}).items()})
    return env


def _extract_child_payload(stdout: str, name: str) -> dict[str, Any]:
    for line in reversed(stdout.splitlines()):
        if line.startswith(CHILD_MARKER):
            return json.loads(line[len(CHILD_MARKER):])
    raise RuntimeError(f"配置 {name} 子进程未输出结果标记")


def _run_child(args: list[str], config: dict[str, Any], timeout: int) -> dict[str, Any]:
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), *args],
        cwd=str(BACKEND_ROOT), env=_child_env(config),
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(
            f"配置 {config['name']} 子进程失败(exit={proc.returncode}): {(proc.stderr or '')[-800:]}")
    return _extract_child_payload(proc.stdout or "", config["name"])


def run_eval_child(config: dict[str, Any], dataset: str, timeout: int) -> dict[str, Any]:
    return _run_child(
        ["--child", "--config", json.dumps(config, ensure_ascii=False), "--dataset", dataset],
        config, timeout)


def run_chunk_child(config: dict[str, Any], timeout: int) -> dict[str, Any]:
    return _run_child(["--child-chunk", "--config", json.dumps(config, ensure_ascii=False)],
                      config, timeout)


# ---------------- 子进程实现 ----------------

def _child_eval(config: dict[str, Any], dataset: str) -> int:
    from app.core.runtime_config import load_runtime_settings
    from app.enterprise_rag.evaluation import evaluate_dataset, load_cases
    from evaluation.enterprise_rag.seed import seed_baseline
    from scripts.evaluate_enterprise_rag import _build_context

    seed_baseline()
    cases = load_cases(dataset)
    ctx = _build_context("user_admin")
    report = evaluate_dataset(ctx, cases, top_k=int(config["top_k"]), retrieval_mode=str(config["mode"]))
    settings = load_runtime_settings()
    knobs = {
        "top_k": int(config["top_k"]),
        "mode": str(config["mode"]),
        "rag_vector_top_k": settings.rag_vector_top_k,
        "rag_bm25_top_k": settings.rag_bm25_top_k,
        "rag_final_top_k": settings.rag_final_top_k,
        "rag_symbol_boost": settings.rag_symbol_boost,
        "rag_hybrid_vector_weight": settings.rag_hybrid_vector_weight,
        "rag_hybrid_bm25_weight": settings.rag_hybrid_bm25_weight,
        "rag_rerank_enabled": bool(settings.rag_rerank_enabled),
        "rag_rerank_provider": settings.rag_rerank_provider,
        "rag_query_variant_decay": settings.rag_query_variant_decay,
        "rag_query_llm": bool(settings.rag_query_llm),
        "rag_query_rewrite_enabled": bool(settings.rag_query_rewrite_enabled),
        "use_milvus": bool(settings.use_milvus),
    }
    per_case = [{
        "case_id": case.get("case_id"),
        "passed": bool(case.get("passed")),
        "recall_at_k": case.get("recall_at_k"),
        "mrr": case.get("mrr"),
        "actual_status": case.get("actual_status"),
        "expected_status": case.get("expected_status"),
        "tags": case.get("tags") or [],
    } for case in report.get("per_case_results", [])]
    payload = {
        "kind": "eval",
        "name": config["name"],
        "env_requested": config.get("env", {}),
        "knobs": knobs,
        "dataset_sha256": report.get("dataset_sha256"),
        "summary": report.get("summary"),
        "validation_status": report.get("validation_status"),
        "vector_backend": report.get("vector_backend"),
        "milvus_verified": report.get("milvus_verified"),
        "fallback_used": report.get("fallback_used"),
        "per_case": per_case,
    }
    payload["config_sha256"] = config_sha256(knobs, payload["dataset_sha256"] or "")
    print(CHILD_MARKER + json.dumps(payload, ensure_ascii=False))
    return 0


# 切片探针：确定性脱敏合成语料（4 个模块，含唯一 marker token 与唯一符号名）
_PROBE_FILES = (
    ("src/payment/refund.py", "refund", "REFUND_MARKER_ALPHA"),
    ("src/payment/callback.py", "callback", "CALLBACK_MARKER_BETA"),
    ("src/api/orders.py", "orders", "ORDER_MARKER_GAMMA"),
    ("src/gateway/timeout.py", "gateway", "TIMEOUT_MARKER_DELTA"),
)


def _probe_module(tag: str, marker: str, filler: int = 60) -> str:
    lines = [f'"""{tag} demo module (synthetic, non-sensitive)."""', ""]
    for index in range(filler):
        lines += [f"def {tag}_util_{index}(value):", f"    return value + {index}", ""]
    lines += [f"def handle_{tag}(payload):",
              f"    # marker: {marker}",
              f"    return payload.get('{marker}', '')", ""]
    return "\n".join(lines)


def _child_chunk_probe(config: dict[str, Any]) -> int:
    from types import SimpleNamespace

    from app.retrieval import parsing
    from app.retrieval.bm25_backend import retrieve_bm25
    from evaluation.enterprise_rag.seed import BASELINE_COMMIT

    files = [SimpleNamespace(path=path, language="python", content=_probe_module(tag, marker))
             for path, tag, marker in _PROBE_FILES]
    chunks = parsing.parse_and_chunk(
        files, repository_id="repo_chunk_probe", index_id="idx_chunk_probe",
        commit_sha=BASELINE_COMMIT, blocked_paths=[])
    per_file: dict[str, int] = {}
    for chunk in chunks:
        per_file[chunk.file_path] = per_file.get(chunk.file_path, 0) + 1

    queries = []
    for path, tag, marker in _PROBE_FILES:
        queries.append({"query": marker, "expected_file": path})
        queries.append({"query": f"handle_{tag}", "expected_file": path})
    per_query = []
    ranks: list[int] = []
    for item in queries:
        results = retrieve_bm25(chunks, item["query"], top_k=8, symbol_boost=0.0)
        rank = 0
        for index, result in enumerate(results, start=1):
            if str(getattr(result.chunk, "file_path", "")) == item["expected_file"]:
                rank = index
                break
        per_query.append({"query": item["query"], "expected_file": item["expected_file"], "rank": rank})
        if rank:
            ranks.append(rank)
    signature = hashlib.sha256(json.dumps(
        [[chunk.file_path, chunk.start_line, chunk.end_line, chunk.checksum] for chunk in chunks],
        ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()[:16]
    knobs = {
        "max_chars": parsing.MAX_CHUNK_CHARS,
        "window_lines": parsing.INDEX_TARGET_WINDOW_LINES,
        "overlap_lines": parsing.INDEX_WINDOW_OVERLAP_LINES,
    }
    payload = {
        "kind": "chunk_probe",
        "name": config["name"],
        "env_requested": config.get("env", {}),
        "knobs": knobs,
        "chunk_count": len(chunks),
        "avg_chunk_chars": round(sum(len(c.content) for c in chunks) / max(1, len(chunks)), 1),
        "max_chunk_chars": max((len(c.content) for c in chunks), default=0),
        "per_file": per_file,
        "retrieval": {
            "queries": len(queries),
            "recall_at_8": round(len(ranks) / len(queries), 4),
            "mrr": round(sum(1.0 / rank for rank in ranks) / len(queries), 4),
            "per_query": per_query,
        },
        "signature": signature,
    }
    print(CHILD_MARKER + json.dumps(payload, ensure_ascii=False))
    return 0


# ---------------- 报告装配 ----------------

def assemble_report(eval_children: list[dict[str, Any]], chunk_children: list[dict[str, Any]],
                    determinism: dict[str, Any], dataset_path: str) -> dict[str, Any]:
    baseline = next(child for child in eval_children if child["name"] == "baseline")
    configs = [{
        "name": child["name"],
        "env_requested": child["env_requested"],
        "knobs": child["knobs"],
        "config_sha256": child["config_sha256"],
        "summary": child["summary"],
        "validation_status": child["validation_status"],
        "vector_backend": child["vector_backend"],
        "per_case": child["per_case"],
    } for child in eval_children]
    deltas = {child["name"]: deltas_vs_baseline(baseline, child)
              for child in eval_children if child["name"] != "baseline"}
    failures = {child["name"]: failure_samples(child, limit=10) for child in eval_children}

    base_signature = next((v["signature"] for v in chunk_children if v["name"] == "max_chars_4000"), None)
    chunk_variants = [{
        "name": v["name"],
        "env_requested": v["env_requested"],
        "knobs": v["knobs"],
        "chunk_count": v["chunk_count"],
        "avg_chunk_chars": v["avg_chunk_chars"],
        "max_chunk_chars": v["max_chunk_chars"],
        "per_file": v["per_file"],
        "retrieval": v["retrieval"],
        "signature": v["signature"],
        "same_as_baseline": bool(base_signature is not None and v["signature"] == base_signature),
    } for v in chunk_children]

    environment = {
        "execution": "local_in_memory",
        "validation_status": baseline["validation_status"],
        "vector_backend": baseline["vector_backend"],
        "milvus_verified": baseline["milvus_verified"],
        "fallback_used": baseline["fallback_used"],
        "use_milvus": baseline["knobs"].get("use_milvus"),
        "query_llm_enabled": baseline["knobs"].get("rag_query_llm"),
        "rerank_provider": baseline["knobs"].get("rag_rerank_provider"),
        "note": ("本地/内存/脱敏合成语料消融：validation_status=local_only 即非云端质量；"
                 "无真实模型/外网调用口径由子进程强制环境保证（USE_MILVUS=false / mock embedding / LLM 关闭）；"
                 "生产/云端评测需显式确认后另行执行"),
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset": {"path": dataset_path, "sha256": baseline.get("dataset_sha256")},
        "environment": environment,
        "configs": configs,
        "deltas_vs_baseline": deltas,
        "failure_samples": failures,
        "chunking": {
            "probe_corpus": "synthetic deterministic payment demo modules (non-sensitive, 4 files)",
            "variants": chunk_variants,
            "note": ("切片参数是 import-time 常量，必须子进程隔离生效；max_chars 决定 CodeSplitter 实际产物粒度；"
                     "行窗口参数在当前 llama-index 版本不参与切分，same_as_baseline=true 即实测无差异"),
        },
        "determinism": determinism,
        "limitations": [
            "本地内存态 + 脱敏合成语料（seed_baseline），不代表云端 Milvus/真实仓库质量",
            "子进程强制 USE_MILVUS=false；云端/生产评测需显式确认后另行执行",
            "切片探针只度量 BM25 可检索性（本地确定性词法），不含向量语义质量",
        ],
    }


# ---------------- 主流程 ----------------

def main() -> int:
    parser = argparse.ArgumentParser(description="OPT-10 RAG 检索参数消融（本地/内存/脱敏）")
    parser.add_argument("--out", default="", help="报告输出 JSON 路径（缺省只打印摘要到 stdout）")
    parser.add_argument("--dataset", default=DEFAULT_DATASET, help="评测集（默认 E3-05 固定基线）")
    parser.add_argument("--quick", action="store_true", help="减少配置数（仍含基线 + 确定性校验 + 切片探针）")
    parser.add_argument("--timeout", type=int, default=600, help="单配置子进程超时秒数")
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--child-chunk", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--config", default="", help=argparse.SUPPRESS)
    args = parser.parse_args()

    if args.child:
        return _child_eval(json.loads(args.config), args.dataset)
    if args.child_chunk:
        return _child_chunk_probe(json.loads(args.config))

    eval_configs = build_eval_configs(quick=args.quick)
    chunk_configs = build_chunk_configs(quick=args.quick)

    eval_children: list[dict[str, Any]] = []
    for config in eval_configs:
        eval_children.append(run_eval_child(config, args.dataset, args.timeout))
        print(f"[opt10] 检索配置完成: {config['name']}", file=sys.stderr)
    baseline_rerun = run_eval_child(eval_configs[0], args.dataset, args.timeout)
    print("[opt10] 基线复跑（确定性校验）完成", file=sys.stderr)
    determinism = determinism_compare(eval_children[0], baseline_rerun)

    chunk_children: list[dict[str, Any]] = []
    for config in chunk_configs:
        chunk_children.append(run_chunk_child(config, args.timeout))
        print(f"[opt10] 切片配置完成: {config['name']}", file=sys.stderr)

    report = assemble_report(eval_children, chunk_children, determinism, args.dataset)
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("消融报告已写入:", args.out)

    print(json.dumps({
        "dataset": report["dataset"],
        "baseline": {
            "config_sha256": report["configs"][0]["config_sha256"],
            "summary": report["configs"][0]["summary"],
        },
        "variants": [{
            "name": config["name"],
            "config_sha256": config["config_sha256"],
            "recall_at_k": (config["summary"] or {}).get("recall_at_k"),
            "mrr": (config["summary"] or {}).get("mrr"),
            "passed_cases": (config["summary"] or {}).get("passed_cases"),
        } for config in report["configs"]],
        "metric_deltas_vs_baseline": {name: delta["metric_deltas"]
                                      for name, delta in report["deltas_vs_baseline"].items()},
        "determinism": {"checked": report["determinism"]["checked"],
                        "identical": report["determinism"]["identical"]},
        "environment": {"execution": report["environment"]["execution"],
                        "validation_status": report["environment"]["validation_status"]},
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as error:  # noqa: BLE001
        print(f"消融执行失败: {error}", file=sys.stderr)
        sys.exit(2)
