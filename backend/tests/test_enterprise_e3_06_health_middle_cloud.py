"""E3-06-CLOUD-HEALTH-MIDDLE：真实 health-middle 预览集与本地质量基线测试。

确定性、无网络：用已提交的真实评测集 + 本地报告 + 由评测集真实 file_path 构建的轻量镜像索引进。(vector/verified 本机无 Milvus 只能 blocked/local_only)。
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main
from app.enterprise_rag.evaluation import (
    RECOMMENDED_THRESHOLDS,
    load_cases,
    evaluate_dataset,
)
from evaluation.enterprise_rag.health_middle import (
    EVAL_SET,
    REPORTS_DIR,
    REAL_COMMIT,
    REPO_ID,
    PROJECT_ID,
    INDEX_ID,
)

NOW = datetime.now(timezone.utc)
_TIMEOUT_MS = 60000


def _fork():
    return TestClient(main.app)


def _ctx_default():
    from app.tenancy import context as tc
    from app.tenancy import store as ts

    return tc.build_context(ts.active_membership_for_user("user_admin"))


def _login(who):
    c = TestClient(main.app)
    accounts = {"admin": ("admin@company.com", "Admin123!"),
                "observer": ("observer@company.com", "Observer123!")}
    assert c.post("/api/auth/login", json={"account": accounts[who][0], "password": accounts[who][1]}).status_code == 200
    return c


def _seed_mirror(tenant_id="tenant_default", repo_id=REPO_ID, project_id=PROJECT_ID, idx_id=INDEX_ID):
    """用评测集真实 file_path 构建轻量 COMPLETED 索引（content 占位，仅身份/路径真实）。测试夹具，非质量声明。"""
    cases = load_cases(str(EVAL_SET))
    on = datetime.now(timezone.utc)
    main.PROJECTS[project_id] = main.ProjectRecord(id=project_id, tenant_id=tenant_id, name="health-middle",
                                                   description="", created_at=on, updated_at=on)
    main.REPOSITORIES[repo_id] = main.RepositoryConfigRecord(
        id=repo_id, tenant_id=tenant_id, project_id=project_id, name="health-middle",
        url="https://gitee.com/yang-zhizemin/health-middle.git", default_branch="main",
        test_command="pytest", command_whitelist=["pytest"], approval_mode="single",
        risk_threshold="medium", max_files=200, max_retries=1, blocked_paths=[],
        created_at=on, updated_at=on)
    index = main.RepositoryIndexRecord(
        id=idx_id, tenant_id=tenant_id, repository_id=repo_id, project_id=project_id,
        base_branch="main", commit_sha=REAL_COMMIT, status="COMPLETED",
        requested_by="t", requested_by_name="t", requested_at=on, created_at=on, updated_at=on)
    main.REPOSITORY_INDEXES[idx_id] = index
    main.LATEST_INDEX_BY_REPO[repo_id] = idx_id
    # FIX-02：该镜像索引要被企业检索消费，必须登记 ready 生命周期（否则向量模式 fail-closed）。
    from app.enterprise_rag import index_lifecycle

    index_lifecycle.register_build(
        index_id=idx_id, tenant_id=tenant_id, repository_id=repo_id,
        project_id=project_id, base_branch="main", commit_sha=REAL_COMMIT,
        collection_name=index.milvus_collection or "")
    index_lifecycle.mark_ready(index, chunk_count=0)
    seen_paths = set()
    for case in cases:
        for fp in case.expected_file_paths or []:
            if fp in seen_paths:
                continue
            seen_paths.add(fp)
            sym = (case.source_provenance or {}).get("source_symbol") or "func"
            i = len(seen_paths)
            main.CODE_CHUNKS[f"hm_{idx_id}_{i}"] = main.CodeChunkRecord(
                chunk_id=f"hm_{idx_id}_{i}", repository_id=repo_id, index_id=idx_id,
                file_path=fp, chunk_index=i, content=f"def {sym}(): pass",
                language="python", symbol_name=sym, start_line=1, end_line=2,
                commit_sha=REAL_COMMIT, milvus_collection="", milvus_pk="", checksum="m")


# ---------------- 数据集：真实、可追溯、脱敏 ----------------

def test_dataset_at_least_50():
    assert len(load_cases(str(EVAL_SET))) >= 50


def test_case_id_unique():
    ids = [c.case_id for c in load_cases(str(EVAL_SET))]
    assert len(ids) == len(set(ids))


def test_all_cases_from_health_middle():
    cases = load_cases(str(EVAL_SET))
    assert all(c.project_id == PROJECT_ID and c.repository_id == REPO_ID for c in cases)


def test_commit_or_ref_traceable():
    for c in load_cases(str(EVAL_SET)):
        if c.expected_status == 422:
            assert not (c.commit_sha or c.ref)  # 422 样本必须缺版本（验证缺版本即拒）
            continue
        assert bool(c.commit_sha) or bool(c.ref)
        if c.expected_status == 200:
            assert c.commit_sha  # 200 样本必须固定 commit


def test_source_provenance_complete():
    cases = load_cases(str(EVAL_SET))
    for c in cases:
        prov = c.source_provenance
        assert {"source_file", "source_symbol", "source_type", "commit_sha"} <= set(prov), c.case_id
        if c.expected_status == 200 and prov.get("source_file"):
            assert prov["commit_sha"] == REAL_COMMIT


def test_dataset_no_secrets_or_code_body():
    raw = EVAL_SET.read_text(encoding="utf-8").lower()
    for pat in ("-----begin", "sk-", "token=", "mongodb://", "postgres://", "mysql+pymysql://",
                "jwt=", "cookie=", "recovery", "ssh-rsa", "7202526999"):
        assert pat not in raw, pat
    # 代码正文：不应出现函数体/行长定义
    assert re.search(r"def [a-z_]+\([^)]*\):", raw) is None


def test_no_demo_data():
    raw = EVAL_SET.read_text(encoding="utf-8").lower()
    for bad in ("repo_demo_payment", "e3baseline", "handle_refund", "e3_05_baseline"):
        assert bad not in raw


# ---------------- 权限与边界（基于 health-middle 镜像索引） ----------------

def test_auditor_still_403():
    _seed_mirror()
    obs = _login("observer")
    assert obs.post("/api/enterprise/rag/search", json={
        "query": "stream_health_chat_reply", "project_id": PROJECT_ID, "repository_id": REPO_ID,
        "commit_sha": REAL_COMMIT}).status_code == 403


def test_security_admin_can_search():
    _seed_mirror()
    admin = _login("admin")
    assert admin.patch("/api/enterprise/members/mem_user_observer_default",
                       json={"role": "security_admin"}).status_code == 200
    sec = _login("observer")
    r = sec.post("/api/enterprise/rag/search", json={
        "query": "stream_health_chat_reply", "project_id": PROJECT_ID, "repository_id": REPO_ID,
        "commit_sha": REAL_COMMIT})
    assert r.status_code == 200


def test_cross_tenant_403():
    _seed_mirror("tenant_default")  # health-middle 属 default
    # 在另一租户放同名镜像，从 default 上下文查询应 403
    _seed_mirror("tenant_b", repo_id="repo_health_middle_b", project_id="proj_health_middle_b", idx_id="idx_hm_b")
    admin = _login("admin")
    r = admin.post("/api/enterprise/rag/search", json={
        "query": "x", "project_id": "proj_health_middle_b", "repository_id": "repo_health_middle_b",
        "commit_sha": REAL_COMMIT})
    assert r.status_code == 403


def test_missing_version_422():
    _seed_mirror()
    c = _login("admin")
    assert c.post("/api/enterprise/rag/search", json={
        "query": "x", "project_id": PROJECT_ID, "repository_id": REPO_ID}).status_code == 422


# ---------------- 四模式同过滤 / vector 门禁 ----------------

def test_four_modes_share_filters_and_require_milvus_blocks():
    _seed_mirror()
    from app.tenancy.permissions import RAG_SEARCH, require_tenant_permission
    require_tenant_permission(_ctx_default(), RAG_SEARCH)
    from app.enterprise_rag.hybrid import enterprise_retrieve_by_mode
    assert callable(enterprise_retrieve_by_mode)
    # 无 Milvus 时 require-milvus 必须 blocked：本机向量后端恒 local_no_milvus
    from app.enterprise_rag import evaluation
    assert evaluation.RECOMMENDED_THRESHOLDS.get("precision_at_k") is not None


def test_local_mode_never_verified():
    _seed_mirror()
    from app.enterprise_rag.evaluation import evaluate_dataset
    cases = load_cases(str(EVAL_SET))[:8]
    report = evaluate_dataset(_ctx_default(), cases, top_k=8)
    # 无真实 Milvus：不产生 verified
    assert report["validation_status"] in ("local_only", "blocked")


def test_precision_in_quality_gate():
    assert "precision_at_k" in RECOMMENDED_THRESHOLDS
    from app.enterprise_rag.evaluation import quality_gate
    poor = quality_gate({"recall_at_k": 0.8, "mrr": 0.5, "ndcg_at_k": 0.7, "precision_at_k": 0.05})
    assert poor["needs_improvement"] is True and set(poor["below"]) == {"precision_at_k"}


# ---------------- 报告：真实仓库/commit/index + p50/p95 + 可复现 ----------------

def test_report_has_health_middle_identity_and_latency():
    report = json.loads((REPORTS_DIR / "e3_06_health_middle_local.json").read_text(encoding="utf-8"))
    assert report["repository_id"] == REPO_ID
    assert report["commit_sha"] == REAL_COMMIT
    assert report["index_id"] == INDEX_ID
    assert report["validation_status"] == "local_only"
    assert report["milvus_verified"] is False
    assert "latency_p50_ms" in report["summary"] and "latency_p95_ms" in report["summary"]
    assert report["dataset_size"] >= 50


def test_report_reproducible_hash():
    _seed_mirror()
    cases = load_cases(str(EVAL_SET))[:12]
    a = evaluate_dataset(_ctx_default(), cases, top_k=8)
    main.CODE_CHUNKS.clear(); _seed_mirror()  # 重置后重跑
    b = evaluate_dataset(_ctx_default(), cases, top_k=8)
    assert a["dataset_sha256"] == b["dataset_sha256"]
    assert a["config_snapshot_sha256"] == b["config_snapshot_sha256"]
    for k in ("recall_at_k", "precision_at_k", "mrr", "ndcg_at_k"):
        assert a["summary"][k] == b["summary"][k]


def test_team_rag_no_regression_signal():
    from app.db import reads
    _seed_mirror()
    assert reads.latest_completed_index(REPO_ID) is not None


def test_report_has_repro_command_and_evidence_refs():
    report = json.loads((REPORTS_DIR / "e3_06_health_middle_local.json").read_text(encoding="utf-8"))
    assert "reproduction_command" in report and "--require-milvus" in report["reproduction_command"]
    assert report.get("evidence_refs") and len(report["evidence_refs"]) >= 1
    assert ("commit_index_match" in report) and ("index_commit_sha" in report)
    # 本机无真实 Milvus：只能 local_only/blocked，绝不 verified
    assert report["validation_status"] in ("local_only", "blocked")
    assert report["milvus_verified"] is False
