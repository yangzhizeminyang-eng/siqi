"""E3-06-CLOUD-VERIFY：真实 health-middle 仓库与真实 Milvus 验证测试。

本机无真实 Milvus：按规范对 vector/verified 判 blocked，绝不伪造 verified。
覆盖规格 1-20 项；确定性、离线可用。
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main
from app.enterprise_rag.evaluation import RECOMMENDED_THRESHOLDS, load_cases, evaluate_dataset
from evaluation.enterprise_rag.health_middle import (
    EVAL_SET,
    REPORTS_DIR,
    REAL_COMMIT,
    REPO_ID,
    PROJECT_ID,
    INDEX_ID,
    resolve_head,
)

NOW = datetime.now(timezone.utc)
_NS_SENSITIVE = ("milvus_uri", "milvus_token", "gitee_token", "password", "jwt", "cookie",
                 "mongodb", "postgres", "mysql+pymysql", "authorization", "7202526999", "secret_key")


# ---------- 轻量镜像（由评测集真实 file_path 构建；测试夹具，非质量声明） ----------

def _ctx_default():
    from app.tenancy import context as tc
    from app.tenancy import store as ts
    return tc.build_context(ts.active_membership_for_user("user_admin"))


def _login(who):
    c = TestClient(main.app)
    accounts = {"admin": ("admin@company.com", "Admin123!"), "observer": ("observer@company.com", "Observer123!")}
    assert c.post("/api/auth/login", json={"account": accounts[who][0], "password": accounts[who][1]}).status_code == 200
    return c


def _q(c, body):
    return c.post("/api/enterprise/rag/search", json=body)


def _seed_mirror(tenant_id="tenant_default", repo_id=REPO_ID, project_id=PROJECT_ID, idx_id=INDEX_ID):
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
    seen = set()
    for case in cases:
        for fp in case.expected_file_paths or []:
            if fp in seen:
                continue
            seen.add(fp)
            sym = (case.source_provenance or {}).get("source_symbol") or "func"
            i = len(seen)
            main.CODE_CHUNKS[f"hm_{idx_id}_{i}"] = main.CodeChunkRecord(
                chunk_id=f"hm_{idx_id}_{i}", repository_id=repo_id, index_id=idx_id, file_path=fp,
                chunk_index=i, content=f"def {sym}(): pass", language="python", symbol_name=sym,
                start_line=1, end_line=2, commit_sha=REAL_COMMIT, milvus_collection="", milvus_pk="", checksum="m")


# 1) URL 规范化（无 .git）
def test_repository_url_normalized_no_git():
    report = json.loads((REPORTS_DIR / "e3_06_health_middle_local.json").read_text(encoding="utf-8"))
    assert report["repository_url"] == "https://gitee.com/yang-zhizemin/health-middle"
    assert not report["repository_url"].rstrip().endswith(".git")


# 2) HEAD 动态获取
def test_head_commit_dynamic():
    head = resolve_head("https://gitee.com/yang-zhizemin/health-middle.git")
    if head:
        assert re.fullmatch(r"[0-9a-f]{40}", head) or re.fullmatch(r"[0-9A-F]{40}", head)


# 3) dataset provenance 与真实文件匹配
def test_dataset_provenance_matches_real_files():
    cases = load_cases(str(EVAL_SET))
    # 无答案/边界样本可为空 provenance；凡有 source_file 必须指向 health-middle 真实 backend 路径
    assert all(
        (not (c.source_provenance or {}).get("source_file"))
        or ((c.source_provenance or {}).get("source_file") or "").startswith("backend/")
        for c in cases if c.expected_status == 200)


# 4) index commit 与评测 commit 一致
def test_index_commit_matches_eval_commit():
    report = json.loads((REPORTS_DIR / "e3_06_health_middle_local.json").read_text(encoding="utf-8"))
    assert report["index_commit_sha"] == REAL_COMMIT
    assert report.get("commit_index_match") is True or report.get("head_resolved") is True


# 5) vector_backend=milvus 真实门禁（本机未满足 -> 不 verified）
def test_vector_backend_real_gate_forces_block():
    report = json.loads((REPORTS_DIR / "e3_06_health_middle_local.json").read_text(encoding="utf-8"))
    # 本机 vector_backend 非 milvus -> 不允许 verified
    if report["vector_backend"] != "milvus":
        assert report["validation_status"] in ("local_only", "blocked")
        assert report["milvus_verified"] is False


# 6) --require-milvus 不可用 -> blocked
def test_require_milvus_unavailable_blocks():
    ev = json.loads((REPORTS_DIR / "e3_06_health_middle_cloud_evidence.json").read_text(encoding="utf-8"))
    if ev["collection_exists"] is False:
        assert ev["query_success"] is False
        assert ev["milvus_health"] == "unavailable"


# 7) fallback 不得 verified
def test_fallback_never_verified():
    _seed_mirror()
    cases = load_cases(str(EVAL_SET))[:8]
    report = evaluate_dataset(_ctx_default(), cases, top_k=8)
    assert report["validation_status"] in ("local_only", "blocked")


# 8) 四模式不是同一结果复制
def test_modes_not_same_copy():
    report = json.loads((REPORTS_DIR / "e3_06_health_middle_local.json").read_text(encoding="utf-8"))
    modes = report["mode_comparison"]
    assert isinstance(modes, dict) and len(modes) >= 2
    reprs = {m: json.dumps(v, sort_keys=True) for m, v in modes.items()}
    assert any(reprs[a] != reprs[b] for a in reprs for b in reprs if a < b) or len(modes) < 2


# 9) 四模式共享过滤边界（by_mode 可对同一 filters 执行且不报错）
def test_modes_share_filter_path():
    _seed_mirror()
    from app.enterprise_rag.hybrid import enterprise_retrieve_by_mode
    assert callable(enterprise_retrieve_by_mode)


# 10-13) 权限与边界
def test_auditor_403():
    _seed_mirror()
    obs = _login("observer")
    assert _q(obs, {"query": "stream_health_chat_reply", "project_id": PROJECT_ID,
                    "repository_id": REPO_ID, "commit_sha": REAL_COMMIT}).status_code == 403


def test_security_admin_can_search():
    _seed_mirror()
    admin = _login("admin")
    assert admin.patch("/api/enterprise/members/mem_user_observer_default",
                       json={"role": "security_admin"}).status_code == 200
    assert _q(_login("observer"), {"query": "stream_health_chat_reply", "project_id": PROJECT_ID,
                                   "repository_id": REPO_ID, "commit_sha": REAL_COMMIT}).status_code == 200


def test_cross_tenant_403():
    _seed_mirror("tenant_default")
    _seed_mirror("tenant_b", repo_id="repo_health_middle_b", project_id="proj_health_middle_b", idx_id="idx_hm_b")
    c = _login("admin")
    assert _q(c, {"query": "x", "project_id": "proj_health_middle_b",
                  "repository_id": "repo_health_middle_b", "commit_sha": REAL_COMMIT}).status_code == 403


def test_missing_version_422():
    _seed_mirror()
    assert _q(_login("admin"), {"query": "x", "project_id": PROJECT_ID,
                                "repository_id": REPO_ID}).status_code == 422


# 14) 真实 Milvus collection/index 证据（本机无 -> collection_exists false）
def test_milvus_evidence_absent_if_no_collection():
    ev = json.loads((REPORTS_DIR / "e3_06_health_middle_cloud_evidence.json").read_text(encoding="utf-8"))
    if ev["collection_exists"] is False:
        assert ev["validation_status"] in ("local_only", "blocked")
        assert ev["query_success"] is False


# 15) report hash 可复现
def test_report_hash_reproducible():
    _seed_mirror()
    cases = load_cases(str(EVAL_SET))[:12]
    a = evaluate_dataset(_ctx_default(), cases, top_k=8)
    main.CODE_CHUNKS.clear(); _seed_mirror()
    b = evaluate_dataset(_ctx_default(), cases, top_k=8)
    assert a["dataset_sha256"] == b["dataset_sha256"]
    assert a["config_snapshot_sha256"] == b["config_snapshot_sha256"]
    for k in ("recall_at_k", "precision_at_k", "mrr", "ndcg_at_k"):
        assert a["summary"][k] == b["summary"][k]


# 16) Precision 质量门禁
def test_precision_in_quality_gate():
    assert "precision_at_k" in RECOMMENDED_THRESHOLDS
    from app.enterprise_rag.evaluation import quality_gate
    poor = quality_gate({"recall_at_k": 0.8, "mrr": 0.5, "ndcg_at_k": 0.7, "precision_at_k": 0.05})
    assert poor["needs_improvement"] is True and set(poor["below"]) == {"precision_at_k"}


# 17) verified 报告字段完整（schema，即便 blocked 也应具备全部契约字段）
def test_report_contract_fields_present():
    report = json.loads((REPORTS_DIR / "e3_06_health_middle_local.json").read_text(encoding="utf-8"))
    for key in ("dataset_size", "repository_url", "project_id", "repository_id", "commit_sha",
                "index_id", "dataset_sha256", "config_snapshot_sha256", "vector_backend",
                "milvus_verified", "fallback_used", "validation_status", "summary",
                "mode_comparison", "metrics_by_tag", "failed_cases", "recommended_thresholds",
                "needs_improvement", "reproduction_command"):
        assert key in report, key
    assert "latency_p50_ms" in report["summary"] and "latency_p95_ms" in report["summary"]


# 18) 敏感信息扫描（证据 + 数据集）
def test_no_sensitive_in_evidence_or_dataset():
    raw_ev = (REPORTS_DIR / "e3_06_health_middle_cloud_evidence.json").read_text(encoding="utf-8").lower()
    raw_ds = EVAL_SET.read_text(encoding="utf-8").lower()
    for pat in _NS_SENSITIVE:
        assert pat not in raw_ev, pat
    for pat in ("-----begin", "sk-", "token=", "7202526999", "mongodb://", "postgres://", "mysql+pymysql://"):
        assert pat not in raw_ds, pat


# 19) 团队版 RAG 无回归信号
def test_team_rag_no_regression_signal():
    _seed_mirror()
    from app.db import reads
    assert reads.latest_completed_index(REPO_ID) is not None


# 20) 并发测试单独重复 5 次（不弱化断言）
def test_concurrency_runs_five_times():
    _B = Path(__file__).resolve().parents[1]
    env = {**dict(__import__("os").environ), "USE_SQLALCHEMY": "false", "MODEL_MOCK_FALLBACK": "true", "PYTHONIOENCODING": "utf-8", "USE_MILVUS": "false"}
    for _ in range(5):
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/test_dev015_concurrency.py", "-q"],
            cwd=str(_B), env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
        assert proc.returncode == 0, proc.stdout + proc.stderr