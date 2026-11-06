#!/usr/bin/env python
"""企业 RAG 离线评测（E3-04）CLI。

用法：
    python -m scripts.evaluate_enterprise_rag DATASET.jsonl -o report.json \
        [--user user_admin] [--top-k 8]
从 JSON/JSONL 评测集作为企业 RAG 评测，复用 enterprise_rag_search（授权 + 过滤 + Hybrid），
输出 summary / per_case / failed / metrics_by_tag / config_snapshot 报告，不含敏感信息。

dry-run / 内存态：默认不依赖真实 Milvus；如需 Milvus 请按部署配置 USE_MILVUS=true + MILVUS_URI。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


def _attach_embedding_identity(report: dict) -> dict:
    """Add non-sensitive provider/model/dimension evidence to a report."""
    from app.core import model_config
    from app.retrieval.llamaindex_backend import embedding_dim

    settings = model_config.load_embedding_settings()
    report["embedding_provider"] = settings.provider
    report["embedding_model"] = settings.model
    report["embedding_dimension"] = embedding_dim()
    report["embedding_live_configured"] = bool(settings.configured)
    return report


def _build_context(user_id: str):
    """构造持有 rag:search 的企业上下文（tenant owner/security admin 等）。"""
    from app.tenancy import store
    from app.tenancy.context import build_context

    membership = store.active_membership_for_user(user_id)
    if membership is None or membership.status != "active":
        raise SystemExit(f"用户 {user_id} 无可用激活租户成员关系")
    return build_context(membership)


def main() -> int:
    parser = argparse.ArgumentParser(description="企业 RAG 离线评测（E3-04）")
    parser.add_argument("dataset", help="评测集文件路径（.json 或 .jsonl）")
    parser.add_argument("-o", "--out", default="", help="报告输出路径（默认打印 summary 到 stdout）")
    parser.add_argument("--user", default="user_admin", help="用于评测的激活成员用户 id（须持有 rag:search）")
    parser.add_argument("--top-k", type=int, default=8)
    parser.add_argument("--memory-seed", action="store_true",
                        help="先种入 E3-05 脱敏 demo 仓库（内存态），再评测（可复现基线）")
    parser.add_argument("--dry-run", action="store_true",
                        help="dry-run：默认内存态执行，不连真实 Milvus；该开关仅为显式声明")
    parser.add_argument("--mode", choices=["bm25", "vector", "hybrid", "rerank"], default="hybrid",
                        help="检索模式（共享同一企业过滤链）")
    parser.add_argument("--all-modes", action="store_true",
                        help="对 bm25/vector/hybrid/rerank 各跑一次并写入 mode_comparison")
    parser.add_argument("--require-milvus", action="store_true",
                        help="Milvus 不可用即失败（blocked），绝不静默回退冒充云端质量")
    parser.add_argument("--commit-sha", default="", help="可选版本边界（缺省以样本/索引为准）")
    parser.add_argument("--repository-id", default="", help="可选仓库过滤（不影响样本边界校验）")
    parser.add_argument("--project-id", default="", help="可选项目过滤")
    parser.add_argument("--repository-url", default="https://gitee.com/yang-zhizemin/health-middle",
                        help="规范仓库 URL（统一去 .git）")
    parser.add_argument("--repo-dir", default="",
                        help="真实 health-middle 克隆目录；给定则用公开 health_middle.build_real_index 准备真实索引（非私有/测试/旧 demo）")
    parser.add_argument("--rerank-compare", action="store_true",
                        help="E3-09：同数据集/commit/collection 对比 baseline / heuristic / llm / cross_encoder 四档，产出对比报告")
    parser.add_argument("--cross-encoder-model", default="", help="E3-09 Cross-Encoder 模型名/路径（本地/私有；空则从 RAG_CROSS_ENCODER_MODEL 读取）")
    parser.add_argument("--cross-encoder-device", default="cpu", help="E3-09 推理设备（cpu 默认）")
    parser.add_argument("--compare-with", default="",
                        help="OPT-04：与既有报告做复现对拍（须同 dataset_sha256；输出回归/改善/环境漂移）")
    parser.add_argument("--comparison-out", default="", help="OPT-04：对拍结果 JSON 输出路径（可选）")
    args = parser.parse_args()

    real_service_requested = bool(
        (args.require_milvus
         and os.getenv("USE_MILVUS", "").strip().lower() not in {"0", "false", "no"})
        or args.repo_dir
        or args.rerank_compare
        or os.getenv("APP_ENV", "").strip().lower()
        in {"team", "production", "prod", "enterprise"}
    )
    if real_service_requested:
        from scripts.authorization_gate import require_authorization

        require_authorization(
            os.getenv("AUTHORIZATION_OPERATION", ""),
            os.getenv("AUTHORIZATION_LOG", "") or None,
        )

    from app.enterprise_rag.evaluation import evaluate_dataset, load_cases, write_report

    cases = load_cases(args.dataset)
    index_chunks = []
    index_identity: dict[str, str] = {}

    if args.repo_dir:
        from evaluation.enterprise_rag.health_middle import build_real_index

        index_chunks = build_real_index(
            Path(args.repo_dir),
            tenant_id="tenant_default",
            persist_vector=True if args.require_milvus else None,
            require_milvus=args.require_milvus,
        )
        if index_chunks:
            index_identity = {
                "commit_sha": str(index_chunks[0].commit_sha),
                "index_id": str(index_chunks[0].index_id),
                "collection_name": str(index_chunks[0].milvus_collection),
            }
            current_commits = {
                str(case.commit_sha)
                for case in cases
                if case.expected_status == 200
                and "stale" not in case.tags
                and case.commit_sha
            }
            if current_commits and current_commits != {index_identity["commit_sha"]}:
                raise SystemExit(
                    "评测集 commit 与 health-middle 工作树 HEAD 不一致；"
                    "请先重新生成该 commit 对应评测集"
                )
    elif args.memory_seed:
        from evaluation.enterprise_rag.seed import seed_baseline

        seed_baseline()  # 仅 demo 样本复现用；与 health-middle 无关
    if args.repository_id:
        cases = [c for c in cases if c.repository_id == args.repository_id]
    if args.project_id:
        cases = [c for c in cases if c.project_id == args.project_id]
    ctx = _build_context(args.user)

    if args.rerank_compare:
        from app.enterprise_rag.rerank_compare import compare_rerank, write_compare_report

        comparison = compare_rerank(
            ctx, cases, top_k=args.top_k, require_milvus=args.require_milvus,
            repository_url=args.repository_url, project_id=args.project_id,
            repository_id=args.repository_id, index_identity=index_identity,
            cross_encoder_model=args.cross_encoder_model,
            cross_encoder_device=args.cross_encoder_device,
        )
        if args.out:
            write_compare_report(comparison, args.out)
            print("E3-09 对比报告已写入:", args.out)
        else:
            print(json.dumps({
                "baseline": comparison["baseline_metrics"],
                "heuristic": comparison["heuristic_metrics"],
                "llm": comparison["llm_metrics"],
                "cross_encoder": comparison["cross_encoder_metrics"],
            }, ensure_ascii=False, indent=2))
        return 0

    modes = ["bm25", "vector", "hybrid", "rerank"] if args.all_modes else [args.mode]
    reports = []
    for mode in modes:
        report = evaluate_dataset(ctx, cases, top_k=args.top_k, retrieval_mode=mode,
                                  require_milvus=args.require_milvus,
                                  index_identity=index_identity or None,
                                  repository_url=args.repository_url)
        reports.append(report)
    report = reports[0]
    if len(reports) > 1:
        report["mode_comparison"] = {
            r["retrieval_mode"]: r["summary"] for r in reports
        }
        report["mode_validation"] = {
            r["retrieval_mode"]: {
                "validation_status": r.get("validation_status"),
                "vector_backend": r.get("vector_backend"),
                "milvus_verified": r.get("milvus_verified"),
                "fallback_used": r.get("fallback_used"),
            }
            for r in reports
        }
    report["repository_url"] = args.repository_url.rstrip().removesuffix(".git")
    if index_identity:
        report["index_commit_sha"] = index_identity["commit_sha"]
        report["commit_index_match"] = report.get("commit_sha") in ("", index_identity["commit_sha"])
        report["cloud_evidence"] = "evaluation/enterprise_rag/reports/e3_06_health_middle_cloud_evidence.json"
    if args.repo_dir:
        _attach_embedding_identity(report)
    all_modes_verified = all(r.get("validation_status") == "verified" for r in reports)
    if args.require_milvus and not all_modes_verified:
        report["validation_status"] = "blocked"
        report["milvus_verified"] = False
        report["fallback_used"] = True
    summary = report["summary"]
    print(json.dumps({
        "dataset_size": report.get("dataset_size"),
        "retrieval_mode": report.get("retrieval_mode"),
        "vector_backend": report.get("vector_backend"),
        "milvus_verified": report.get("milvus_verified"),
        "fallback_used": report.get("fallback_used"),
        "validation_status": report.get("validation_status"),
        "summary": summary,
        "needs_improvement": report.get("needs_improvement"),
    }, ensure_ascii=False, indent=2))
    if args.out:
        write_report(report, args.out)
        if args.repo_dir:
            from evaluation.enterprise_rag.health_middle import write_cloud_evidence

            write_cloud_evidence(
                report,
                collection_name=index_identity.get("collection_name"),
                query_success=all(r.get("validation_status") == "verified" for r in reports),
            )
        print("报告已写入")
    if args.compare_with:
        from app.enterprise_rag.repro_contract import compare_rag_eval_reports

        baseline = json.loads(Path(args.compare_with).read_text(encoding="utf-8"))
        comparison = compare_rag_eval_reports(baseline, report)
        if args.comparison_out:
            Path(args.comparison_out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.comparison_out).write_text(
                json.dumps(comparison, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print("对拍结果已写入:", args.comparison_out)
        print(json.dumps(comparison, ensure_ascii=False, indent=2))
    if args.require_milvus and (report.get("validation_status") == "blocked" or not all_modes_verified):
        print("ERROR: --require-milvus 指定但 Milvus 不可用，结果不视为云端真实质量。", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit as exc:
        raise exc
    except Exception as error:  # noqa: BLE001
        print(f"评测失败: {error}", file=sys.stderr)
        sys.exit(2)
