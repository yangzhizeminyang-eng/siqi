"""企业版 E3-08 企业 RAG 控制台后端接口测试。

覆盖：
- rag:search 有权限角色可读报告列表/详情（admin/tenant_owner）；
- 无 rag:search 角色（auditor）被拒 403；
- 报告名路径穿越被拒、非 JSON / 不存在 400/404；
- verified / local_only / blocked 状态与 needs_improvement/milvus_verified 不被混淆；
- 详情投影脱敏（无 per_case/代码正文）。
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app import main
import app.enterprise_rag.routes as rag_routes

ACCOUNTS = {
    "admin": ("admin@company.com", "Admin123!"),     # tenant_owner -> rag:search
    "auditor": ("observer@company.com", "Observer123!"),  # auditor -> 无 rag:search
}


def _client(who: str) -> TestClient:
    c = TestClient(main.app)
    email, pwd = ACCOUNTS[who]
    assert c.post("/api/auth/login", json={"account": email, "password": pwd}).status_code == 200
    return c


def _report(name: str, status: str, **overrides) -> dict:
    base = {
        "dataset_size": 67,
        "retrieval_mode": "hybrid",
        "vector_backend": "milvus" if status == "verified" else "local",
        "milvus_verified": status == "verified",
        "fallback_used": status != "verified",
        "validation_status": status,
        "needs_improvement": status != "verified",
        "commit_sha": "73e7e5497addc74f67fe49a875d7afdd8d8c190b",
        "index_id": "idx_hm",
        "summary": {"total_cases": 67, "recall_at_k": 0.9, "precision_at_k": 0.35, "mrr": 0.82, "ndcg_at_k": 0.8},
        "recommended_thresholds": {"precision_at_k": 0.3},
        "mode_comparison": {"bm25": {"recall_at_k": 0.87, "precision_at_k": 0.2, "mrr": 0.8, "ndcg_at_k": 0.82}},
        "metrics_by_tag": {"symbol": {"count": 50, "recall_at_k": 0.9, "precision_at_k": 0.3, "mrr": 0.9, "ndcg_at_k": 0.9}},
        "failed_cases": [
            {"case_id": "hm001", "query": "build_x", "tags": ["symbol"], "expected_status": 200,
             "actual_status": 200, "status_passed": True, "retrieval_passed": False}
        ],
        "config_snapshot": {"top_k": 8, "retrieval_mode": "hybrid", "dry_run_in_memory": False},
        "config_snapshot_sha256": "abc",
        "dataset_sha256": "def",
        "repository_url": "https://gitee.com/yang-zhizemin/health-middle",
    }
    base.update(overrides)
    return base


@pytest.fixture()
def reports(tmp_path, monkeypatch):
    """用一个临时 REPORTS_DIR 替代真实 reports 目录（不污染真实报告）。"""
    for name, status in (("r_verified.json", "verified"), ("r_local.json", "local_only"), ("r_blocked.json", "blocked")):
        (tmp_path / name).write_text(json.dumps(_report(name, status)), encoding="utf-8")
    monkeypatch.setattr(rag_routes, "REPORTS_DIR", tmp_path)
    return tmp_path


def test_authorized_reads_report_list(reports):
    c = _client("admin")
    resp = c.get("/api/enterprise/rag/reports")
    assert resp.status_code == 200
    data = resp.json()["data"]
    names = {r["name"] for r in data["reports"]}
    assert {"r_verified.json", "r_local.json", "r_blocked.json"} <= names
    by = {r["name"]: r for r in data["reports"]}
    assert by["r_verified.json"]["validation_status"] == "verified"
    assert by["r_verified.json"]["milvus_verified"] is True
    assert by["r_local.json"]["validation_status"] == "local_only"
    assert by["r_blocked.json"]["validation_status"] == "blocked"


def test_authorized_reads_report_detail_projection(reports):
    c = _client("admin")
    resp = c.get("/api/enterprise/rag/reports/r_verified.json")
    assert resp.status_code == 200
    d = resp.json()["data"]
    assert d["validation_status"] == "verified"
    assert d["milvus_verified"] is True and d["fallback_used"] is False
    assert "mode_comparison" in d and "metrics_by_tag" in d and "failed_cases" in d
    assert "summary" in d and d["recommended_thresholds"] == {"precision_at_k": 0.3}
    # 投影脱敏：不暴露 per_case 全量/代码正文
    assert "per_case_results" not in d


def test_status_not_confused_local_and_blocked(reports):
    c = _client("admin")
    loc = c.get("/api/enterprise/rag/reports/r_local.json").json()["data"]
    blk = c.get("/api/enterprise/rag/reports/r_blocked.json").json()["data"]
    assert loc["validation_status"] == "local_only" and loc["milvus_verified"] is False and loc["fallback_used"] is True
    assert blk["validation_status"] == "blocked"
    assert loc["needs_improvement"] is True  # 未 verified 不得假装达标


def test_auditor_denied_report_access(reports):
    c = _client("auditor")
    assert c.get("/api/enterprise/rag/reports").status_code == 403
    assert c.get("/api/enterprise/rag/reports/r_verified.json").status_code == 403


def test_report_path_traversal_rejected(reports):
    c = _client("admin")
    # HTTP 层：URL 解析会把 %2F 解码并把 ../ 归一化，故穿越类请求被拒为 4xx（400/404 均可，均不允许读）
    for path in ("../secret.json", "../config", "..%2F..%2Fetc%2Fpasswd.json"):
        assert c.get(f"/api/enterprise/rag/reports/{path}").status_code in (400, 404), path
    # 不存在的安全名 -> 404；非 .json 后缀 -> 400
    assert c.get("/api/enterprise/rag/reports/nope.json").status_code == 404
    assert c.get("/api/enterprise/rag/reports/config").status_code == 400


def test_resolve_report_file_rejects_traversal(reports):
    # 单元级：正则拒绝含路径分隔符/../ 的名字；容器不允许越出 REPORTS_DIR
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        rag_routes._resolve_report_file("../secret.json")
    with pytest.raises(HTTPException):
        rag_routes._resolve_report_file("a/b/c.json")
    with pytest.raises(HTTPException):
        rag_routes._resolve_report_file("sub/../../etc/passwd.json")
    # 合法名解析到目录内
    assert rag_routes._resolve_report_file("r_verified.json").name == "r_verified.json"


def test_corrupt_report_json_400(reports, tmp_path):
    (tmp_path / "broken.json").write_text("{not-json", encoding="utf-8")
    c = _client("admin")
    assert c.get("/api/enterprise/rag/reports/broken.json").status_code == 400


def test_search_still_gated_rag_search():
    # 检索端点仍由 rag:search 权威门禁；auditor 拒绝（既有行为回归）
    c_aud = _client("auditor")
    r = c_aud.post("/api/enterprise/rag/search", json={
        "query": "build_user", "project_id": "pA", "repository_id": "rA"})
    assert r.status_code == 403