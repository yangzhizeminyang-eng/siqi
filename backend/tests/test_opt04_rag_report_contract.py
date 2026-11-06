"""OPT-04 RAG 报告复现契约测试（TDD：先红后绿）。

覆盖：报告固化仓库版本/数据集/索引/Embedding/Reranker/检索参数/依赖与配置 hash；
fallback/blocked/verified 区分；离线复跑稳定投影；同数据集对拍（回归/改善/环境漂移）；
v1 旧报告兼容；CLI --compare-with 端到端。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.enterprise_rag.evaluation import EvaluationCase, evaluate_dataset

NOW = datetime.now(timezone.utc)
_COMMIT = "c0ffee1234567890abcdef1234567890abcdef12"
BACKEND = Path(__file__).resolve().parents[1]


# ---------------- 辅助（与 E3-04 用例同构，自包含） ----------------

def _make_repo(repo_id, project_id, tenant_id, commit=_COMMIT):
    from app import main

    main.PROJECTS[project_id] = main.ProjectRecord(
        id=project_id, tenant_id=tenant_id, name=project_id, description="",
        created_at=NOW, updated_at=NOW)
    main.REPOSITORIES[repo_id] = main.RepositoryConfigRecord(
        id=repo_id, tenant_id=tenant_id, project_id=project_id, name=repo_id,
        url=f"https://gitee.com/demo/{repo_id}.git", default_branch="main",
        test_command="pytest", command_whitelist=["pytest"],
        approval_mode="single", risk_threshold="medium", max_files=10, max_retries=1,
        blocked_paths=[], created_at=NOW, updated_at=NOW)
    index = main.RepositoryIndexRecord(
        id=f"idx_{repo_id}", tenant_id=tenant_id, repository_id=repo_id, project_id=project_id,
        base_branch="main", commit_sha=commit, status="COMPLETED",
        requested_by="s", requested_by_name="s", requested_at=NOW, created_at=NOW, updated_at=NOW)
    main.REPOSITORY_INDEXES[index.id] = index
    main.LATEST_INDEX_BY_REPO[repo_id] = index.id
    main.CODE_CHUNKS[f"{repo_id}_c1"] = main.CodeChunkRecord(
        chunk_id=f"{repo_id}_c1", repository_id=repo_id, index_id=index.id,
        file_path="src/refund.py", chunk_index=0, content="def handle_refund(): raise ERR_401",
        language="python", symbol_name="handle_refund", start_line=1, end_line=2,
        commit_sha=commit, milvus_collection="", milvus_pk="", checksum="x")
    return index.id


def _ctx():
    from app.tenancy import store as ts
    from app.tenancy.context import build_context

    return build_context(ts.active_membership_for_user("user_admin"))


def _case(repo_id="repo_oc", project_id="proj_oc", case_id="c1"):
    return EvaluationCase(case_id=case_id, query="handle_refund ERR_401",
                          project_id=project_id, repository_id=repo_id,
                          commit_sha=_COMMIT, expected_chunk_ids=[f"{repo_id}_c1"])


@pytest.fixture()
def no_vector(monkeypatch):
    import app.retrieval.llamaindex_backend as lib

    monkeypatch.setattr(lib, "_query_vector_results", lambda *a, **k: [])


def _strip_latency(obj):
    if isinstance(obj, dict):
        return {k: _strip_latency(v) for k, v in obj.items() if "latency" not in k}
    if isinstance(obj, list):
        return [_strip_latency(v) for v in obj]
    return obj


# ---------------- 契约块：字段固化 ----------------

def test_report_carries_repro_contract(no_vector):
    _make_repo("repo_oc", "proj_oc", "tenant_default")
    report = evaluate_dataset(
        _ctx(), [_case()], top_k=8, retrieval_mode="bm25",
        repository_url="https://gitee.com/demo/repo_oc.git",
        index_identity={"commit_sha": _COMMIT, "index_id": "idx_repo_oc",
                        "collection_name": "coll_oc"},
    )
    assert report["report_schema_version"] == "rag-eval-report-v2"
    contract = report["repro_contract"]
    assert contract["schema_version"] == "rag-eval-repro-v1"
    assert contract["dataset"]["sha256"] == report["dataset_sha256"]
    assert contract["dataset"]["size"] == 1
    assert contract["repository"]["url"] == "https://gitee.com/demo/repo_oc"
    assert contract["repository"]["repository_ids"] == ["repo_oc"]
    assert contract["repository"]["project_ids"] == ["proj_oc"]
    assert contract["repository"]["versions"] == [_COMMIT]
    assert contract["index"]["index_ids"] == ["idx_repo_oc"]
    assert contract["index"]["collection_name"] == "coll_oc"
    assert contract["index"]["source"] == "cli_index_identity"
    assert contract["retrieval"]["mode"] == "bm25"
    assert contract["retrieval"]["top_k"] == 8
    assert contract["retrieval"]["filters"]["tenant_id"] == "tenant_default"
    assert contract["retrieval"]["filters"]["user_id"] == "user_admin"
    assert contract["retrieval"]["filters"]["repository_ids"] == ["repo_oc"]
    assert contract["reranker"]["provider"]
    assert contract["embedding"]["status"] in {"configured", "not_configured", "not_collected"}
    assert len(contract["config_sha256"]) == 64
    assert len(contract["deps_sha256"]) == 64
    assert contract["dependencies"]["python"]
    assert isinstance(contract["dependencies"]["packages"], dict)
    assert contract["validation"]["status"] in {"verified", "local_only", "blocked"}


def test_index_source_per_case_echo_without_cli_identity(no_vector):
    _make_repo("repo_echo", "proj_echo", "tenant_default")
    report = evaluate_dataset(_ctx(), [_case("repo_echo", "proj_echo")],
                              top_k=8, retrieval_mode="bm25")
    index = report["repro_contract"]["index"]
    assert index["index_ids"] == ["idx_repo_echo"]
    assert index["source"] == "per_case_echo"
    assert index["collection_name"] is None


def test_config_snapshot_mode_matches_actual_mode(no_vector):
    _make_repo("repo_mode", "proj_mode", "tenant_default")
    report = evaluate_dataset(_ctx(), [_case("repo_mode", "proj_mode")],
                              top_k=8, retrieval_mode="bm25")
    assert report["config_snapshot"]["retrieval_mode"] == "bm25"


# ---------------- 配置/依赖 hash：稳定且不泄密 ----------------

def test_config_digest_stable_and_sensitive_to_settings(no_vector, monkeypatch):
    _make_repo("repo_dig", "proj_dig", "tenant_default")
    cases = [_case("repo_dig", "proj_dig")]
    first = evaluate_dataset(_ctx(), cases, top_k=8, retrieval_mode="bm25")
    second = evaluate_dataset(_ctx(), cases, top_k=8, retrieval_mode="bm25")
    assert first["repro_contract"]["config_sha256"] == second["repro_contract"]["config_sha256"]

    monkeypatch.setenv("RAG_SYMBOL_BOOST", "2.5")
    third = evaluate_dataset(_ctx(), cases, top_k=8, retrieval_mode="bm25")
    assert third["repro_contract"]["config_sha256"] != first["repro_contract"]["config_sha256"]


def test_config_digest_excludes_credentials(no_vector, monkeypatch):
    _make_repo("repo_sec4", "proj_sec4", "tenant_default")
    cases = [_case("repo_sec4", "proj_sec4")]
    clean = evaluate_dataset(_ctx(), cases, top_k=8, retrieval_mode="bm25")
    monkeypatch.setenv("MILVUS_TOKEN", "secret-token-should-not-leak")
    tainted = evaluate_dataset(_ctx(), cases, top_k=8, retrieval_mode="bm25")
    assert tainted["repro_contract"]["config_sha256"] == clean["repro_contract"]["config_sha256"]
    raw = json.dumps(tainted, ensure_ascii=False).lower()
    assert "secret-token-should-not-leak" not in raw


# ---------------- fallback / blocked / verified 区分 ----------------

def test_validation_local_only_when_milvus_unavailable(no_vector, monkeypatch):
    monkeypatch.setattr("app.enterprise_rag.evaluation._milvus_status",
                        lambda: (False, "local_no_milvus"))
    _make_repo("repo_lo", "proj_lo", "tenant_default")
    report = evaluate_dataset(_ctx(), [_case("repo_lo", "proj_lo")],
                              top_k=8, retrieval_mode="hybrid")
    assert report["validation_status"] == "local_only"
    assert report["milvus_verified"] is False
    validation = report["repro_contract"]["validation"]
    assert validation["vector_backend"] == "local_no_milvus"
    assert validation["reasons"]


def test_local_milvus_lite_not_counted_as_fallback(no_vector, monkeypatch):
    """本地 Milvus Lite 实际参与检索：不冒充云端 verified，但也不得谎报 fallback。"""
    monkeypatch.setattr("app.enterprise_rag.evaluation._milvus_status",
                        lambda: (False, "local_milvus_lite"))
    _make_repo("repo_lite", "proj_lite", "tenant_default")
    report = evaluate_dataset(_ctx(), [_case("repo_lite", "proj_lite")],
                              top_k=8, retrieval_mode="hybrid")
    assert report["validation_status"] == "local_only"
    assert report["milvus_verified"] is False
    assert report["fallback_used"] is False
    validation = report["repro_contract"]["validation"]
    assert validation["vector_backend"] == "local_milvus_lite"
    assert validation["reasons"] == ["local_milvus_lite:not_cloud_verified"]


def test_milvus_status_detects_local_lite(monkeypatch):
    monkeypatch.setenv("USE_MILVUS", "true")
    import app.retrieval.llamaindex_backend as lib

    monkeypatch.setattr(lib, "check_milvus_health",
                        lambda timeout_ms=3000: {"status": "skipped", "reason": "local-milvus-lite"})
    from app.enterprise_rag.evaluation import _milvus_status

    assert _milvus_status() == (False, "local_milvus_lite")


def test_milvus_status_local_lite_requires_real_integration(monkeypatch):
    monkeypatch.setenv("USE_MILVUS", "true")
    import app.retrieval.llamaindex_backend as lib

    monkeypatch.setattr(lib, "check_milvus_health",
                        lambda timeout_ms=3000: {"status": "skipped", "reason": "local-milvus-lite"})
    monkeypatch.setattr(lib, "MilvusVectorStore", None)
    from app.enterprise_rag.evaluation import _milvus_status

    assert _milvus_status() == (False, "unavailable")


def test_validation_blocked_when_require_milvus(no_vector, monkeypatch):
    monkeypatch.setattr("app.enterprise_rag.evaluation._milvus_status",
                        lambda: (False, "unavailable"))
    _make_repo("repo_bl", "proj_bl", "tenant_default")
    report = evaluate_dataset(_ctx(), [_case("repo_bl", "proj_bl")],
                              top_k=8, retrieval_mode="hybrid", require_milvus=True)
    assert report["validation_status"] == "blocked"
    assert any("require_milvus" in r for r in report["repro_contract"]["validation"]["reasons"])


def test_validation_verified_when_milvus_ok(no_vector, monkeypatch):
    monkeypatch.setattr("app.enterprise_rag.evaluation._milvus_status",
                        lambda: (True, "milvus"))
    _make_repo("repo_ok", "proj_ok", "tenant_default")
    report = evaluate_dataset(_ctx(), [_case("repo_ok", "proj_ok")],
                              top_k=8, retrieval_mode="bm25")
    assert report["validation_status"] == "verified"
    assert report["repro_contract"]["validation"]["milvus_verified"] is True


# ---------------- 离线复跑：稳定投影 ----------------

def test_offline_rerun_stable_projection(no_vector):
    _make_repo("repo_st", "proj_st", "tenant_default")
    cases = [_case("repo_st", "proj_st")]
    first = evaluate_dataset(_ctx(), cases, top_k=8, retrieval_mode="bm25")
    second = evaluate_dataset(_ctx(), cases, top_k=8, retrieval_mode="bm25")
    assert _strip_latency(first) == _strip_latency(second)


# ---------------- 对拍：回归/改善/环境漂移 ----------------

def _report(case_states, *, dataset_sha="d1", config_sha="cfg-1", recall=0.5,
            mode="bm25", top_k=8, status="local_only", contract=True):
    report = {
        "dataset_sha256": dataset_sha,
        "summary": {"recall_at_k": recall, "precision_at_k": 0.3, "mrr": 0.4,
                    "ndcg_at_k": 0.5, "latency_p50_ms": 10.0, "latency_p95_ms": 20.0},
        "per_case_results": [
            {"case_id": cid, "passed": passed, "recall_at_k": 1.0 if passed else 0.0}
            for cid, passed in case_states
        ],
    }
    if contract:
        report["report_schema_version"] = "rag-eval-report-v2"
        report["repro_contract"] = {
            "schema_version": "rag-eval-repro-v1",
            "config_sha256": config_sha,
            "deps_sha256": "deps-1",
            "retrieval": {"mode": mode, "top_k": top_k},
            "validation": {"status": status},
            "embedding": {"model": "embedding-3", "dimension": 2048},
            "index": {"index_ids": ["idx_x"]},
            "repository": {"commit_sha": "c0mmit"},
        }
    return report


def test_compare_reports_detects_regressions_and_improvements():
    from app.enterprise_rag.repro_contract import compare_rag_eval_reports

    baseline = _report([("a", True), ("b", False), ("c", True)], recall=0.5)
    current = _report([("a", False), ("b", True), ("c", True)], recall=0.6)
    diff = compare_rag_eval_reports(baseline, current)
    assert diff["same_dataset"] is True
    assert [r["case_id"] for r in diff["regressions"]] == ["a"]
    assert [r["case_id"] for r in diff["improvements"]] == ["b"]
    assert diff["regression_count"] == 1 and diff["improvement_count"] == 1
    assert diff["metric_deltas"]["recall_at_k"] == pytest.approx(0.1)
    assert diff["drift_detected"] is False
    assert diff["environment_drift"] == {}


def test_compare_reports_flags_environment_drift():
    from app.enterprise_rag.repro_contract import compare_rag_eval_reports

    baseline = _report([("a", True)], config_sha="cfg-1", mode="bm25")
    current = _report([("a", True)], config_sha="cfg-2", mode="hybrid")
    diff = compare_rag_eval_reports(baseline, current)
    assert diff["drift_detected"] is True
    drift = diff["environment_drift"]
    assert drift["config_sha256"]["baseline"] == "cfg-1"
    assert drift["config_sha256"]["current"] == "cfg-2"
    assert drift["retrieval_mode"]["current"] == "hybrid"


def test_compare_reports_rejects_dataset_mismatch():
    from app.enterprise_rag.repro_contract import compare_rag_eval_reports

    with pytest.raises(ValueError):
        compare_rag_eval_reports(_report([("a", True)], dataset_sha="d1"),
                                 _report([("a", True)], dataset_sha="d2"))


def test_compare_handles_v1_report_without_contract():
    from app.enterprise_rag.repro_contract import compare_rag_eval_reports

    baseline = _report([("a", True)], contract=False)
    current = _report([("a", True)])
    diff = compare_rag_eval_reports(baseline, current)
    assert diff["environment_drift"]["contract"] == "not_collected"
    assert diff["drift_detected"] is True  # 契约缺失即视为不可比环境，如实标记
    assert diff["baseline_schema"] == "rag-eval-report-v1"


def test_compare_tracks_case_set_changes():
    from app.enterprise_rag.repro_contract import compare_rag_eval_reports

    baseline = _report([("a", True), ("b", True)])
    current = _report([("a", True), ("c", True)])
    diff = compare_rag_eval_reports(baseline, current)
    assert diff["missing_cases"] == ["b"]
    assert diff["new_cases"] == ["c"]


# ---------------- CLI 端到端：--compare-with ----------------

def test_cli_compare_with_end_to_end(tmp_path):
    env = {**os.environ}
    env.update({
        "APP_ENV": "local", "USE_SQLALCHEMY": "false", "USE_CELERY": "false",
        "USE_MILVUS": "false", "DATA_DIR": str(tmp_path / "data"),
        "PYTHONIOENCODING": "utf-8",
    })
    dataset = str(BACKEND / "evaluation" / "enterprise_rag" / "e3_05_baseline.jsonl")
    out_1, out_2, cmp_out = tmp_path / "r1.json", tmp_path / "r2.json", tmp_path / "cmp.json"
    base_cmd = [sys.executable, str(BACKEND / "scripts" / "evaluate_enterprise_rag.py"),
                dataset, "--memory-seed", "--mode", "bm25"]

    first = subprocess.run(base_cmd + ["-o", str(out_1)], cwd=str(tmp_path), env=env,
                           capture_output=True, text=True, encoding="utf-8", timeout=300)
    assert first.returncode == 0, first.stdout + first.stderr

    second = subprocess.run(
        base_cmd + ["-o", str(out_2), "--compare-with", str(out_1),
                    "--comparison-out", str(cmp_out)],
        cwd=str(tmp_path), env=env, capture_output=True, text=True, encoding="utf-8", timeout=300)
    assert second.returncode == 0, second.stdout + second.stderr

    report = json.loads(out_2.read_text(encoding="utf-8"))
    assert report["validation_status"] == "local_only"
    assert report["repro_contract"]["validation"]["status"] == "local_only"
    assert report["repro_contract"]["retrieval"]["mode"] == "bm25"

    diff = json.loads(cmp_out.read_text(encoding="utf-8"))
    assert diff["same_dataset"] is True
    assert diff["regression_count"] == 0
    assert diff["improvement_count"] == 0
    assert diff["drift_detected"] is False
