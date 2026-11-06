"""企业版 E3-04 离线评测基线测试。"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from app.enterprise_rag.evaluation import (
    EvaluationCase,
    evaluate_dataset,
    load_cases,
    mrr,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
)

NOW = datetime.now(timezone.utc)
_COMMIT = "a1b2c3d4e5f678901234567890abcdef12345678"


# ---------------- 纯指标 ----------------

def test_recall_at_k_correct():
    # ranked hits 在第 1、3 位；K=5，目标数 2
    assert recall_at_k([1, 3], n_targets=2, k=5) == 1.0
    assert recall_at_k([3], n_targets=2, k=2) == 0.0  # K=2 只含第1位未命中
    assert recall_at_k([3, 5], n_targets=2, k=4) == 0.5


def test_precision_at_k_correct():
    hits = [2, 4]  # 命中排名
    assert precision_at_k(hits, k=4) == 0.5


def test_mrr_correct():
    assert mrr([]) == 0.0
    assert mrr([3]) == pytest.approx(1 / 3, abs=1e-3)
    assert mrr([1]) == 1.0


def test_ndcg_at_k_correct():
    # 第1、3位命中，相关数2，K=3
    dcg = 1.0 / math_log2(2) + 1.0 / math_log2(4)
    idcg = 1.0 / math_log2(2) + 1.0 / math_log2(3)
    assert ndcg_at_k([1, 3], n_relevant=2, k=3) == pytest.approx(round(dcg / idcg, 4))
    # 无命中
    assert ndcg_at_k([], n_relevant=2, k=3) == 0.0


def math_log2(v):
    import math

    return math.log2(v)


# ---------------- 数据集读取 ----------------

def test_load_cases_from_jsonl(tmp_path):
    p = tmp_path / "ds.jsonl"
    p.write_text(
        '{"query":"q1","project_id":"p","repository_id":"r","commit_sha":"c",'
        '"expected_chunk_ids":["a"],"tags":["code"]}\n'
        '{"query":"q2","project_id":"p","repository_id":"r","ref":"main","expected_status":422}\n',
        encoding="utf-8",
    )
    cases = load_cases(str(p))
    assert len(cases) == 2
    assert cases[0].expected_status == 200 and cases[1].expected_status == 422


def test_load_cases_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_cases(str(tmp_path / "nope.json"))


def test_load_cases_bad_json(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not valid", encoding="utf-8")
    with pytest.raises(ValueError, match="解析失败"):
        load_cases(str(p))


def test_load_cases_unknown_field_or_missing_version(tmp_path):
    p = tmp_path / "x.json"
    p.write_text('[{"query":"q","project_id":"p","repository_id":"r","commit_sha":"c","extra":1}]',
                 encoding="utf-8")
    with pytest.raises(ValueError, match="未知字段"):
        load_cases(str(p))
    p.write_text('[{"query":"q","project_id":"p","repository_id":"r"}]', encoding="utf-8")
    with pytest.raises(ValueError, match="commit_sha 或 ref"):
        load_cases(str(p))


# ---------------- 端到端：复用 enterprise_rag_search ----------------

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
    # FIX-02：评测复用真实检索；测试索引显式登记 ready 生命周期。
    from app.enterprise_rag import index_lifecycle

    index_lifecycle.register_build(
        index_id=index.id, tenant_id=tenant_id, repository_id=repo_id,
        project_id=project_id, base_branch="main", commit_sha=commit,
        collection_name=index.milvus_collection or "")
    index_lifecycle.mark_ready(index, chunk_count=1)
    main.CODE_CHUNKS[f"{repo_id}_c1"] = main.CodeChunkRecord(
        chunk_id=f"{repo_id}_c1", repository_id=repo_id, index_id=index.id,
        file_path="src/refund.py", chunk_index=0, content="def handle_refund(): raise ERR_401",
        language="python", symbol_name="handle_refund", start_line=1, end_line=2,
        commit_sha=commit, milvus_collection="", milvus_pk="", checksum="x")
    return index.id


def _ctx(tenant_id="tenant_default"):
    from app.tenancy import store as ts
    from app.tenancy.context import build_context

    membership = ts.active_membership_for_user("user_admin")
    member_ctx = build_context(membership)
    # user_admin 激活于 default；跨租户样本在 case 层面通过仓库归属触发 403
    return member_ctx


def test_end_to_end_reuses_enterprise_search_and_metrics(monkeypatch):
    import app.retrieval.llamaindex_backend as lib

    monkeypatch.setattr(lib, "_query_vector_results", lambda *a, **k: [])
    _make_repo("repo_ev", "proj_ev", "tenant_default", _COMMIT)
    cases = [
        EvaluationCase(query="handle_refund ERR_401", project_id="proj_ev", repository_id="repo_ev",
                       commit_sha=_COMMIT, expected_chunk_ids=["repo_ev_c1"], tags=["code"]),
        EvaluationCase(query="nonexistent_symbol_zzz", project_id="proj_ev", repository_id="repo_ev",
                       commit_sha=_COMMIT, expected_chunk_ids=["repo_ev_c1"]),  # empty
    ]
    report = evaluate_dataset(_ctx(), cases, top_k=8)
    summary = report["summary"]
    # 命中样本 recall>0，另一样本空
    assert summary["success_cases"] == 2
    assert summary["recall_at_k"] > 0
    assert summary["empty_result_rate"] == 0.5
    assert report["metrics_by_tag"]["code"]["count"] == 1


def test_freshness_and_boundary_statuses():
    import app.retrieval.llamaindex_backend as lib

    _make_repo("repo_fr", "proj_fr", "tenant_default", _COMMIT)
    cases = [
        EvaluationCase(query="handle_refund", project_id="proj_fr", repository_id="repo_fr",
                       commit_sha=_COMMIT, expected_chunk_ids=["repo_fr_c1"]),              # fresh
        EvaluationCase(query="handle_refund", project_id="proj_fr", repository_id="repo_fr",
                       commit_sha="0" * 40, expected_chunk_ids=["repo_fr_c1"]),             # stale
        # 跨租户：repo 属 default，上下文 user_admin 属 default；改查一个不存在 tenant → 仍 403? 用独立租户仓库模拟
        EvaluationCase(query="x", project_id="proj_other", repository_id="repo_other",
                       commit_sha=_COMMIT, expected_status=404 if False else 403),
    ]
    _make_repo("repo_other", "proj_other", "tenant_b", _COMMIT)  # 属另一租户 -> 交叉 403
    report = evaluate_dataset(_ctx(), cases, top_k=8)
    fresh = report["summary"]["freshness_rate"]
    assert 0 < fresh < 1
    assert report["summary"]["unauthorized_pass_rate"] == 1.0  # 403 样本通过


def test_report_contains_no_sensitive_fields(monkeypatch):
    import app.retrieval.llamaindex_backend as lib

    monkeypatch.setattr(lib, "_query_vector_results", lambda *a, **k: [])
    _make_repo("repo_sec", "proj_sec", "tenant_default", _COMMIT)
    cases = [EvaluationCase(query="handle_refund", project_id="proj_sec", repository_id="repo_sec",
                            commit_sha=_COMMIT, expected_chunk_ids=["repo_sec_c1"])]
    report = evaluate_dataset(_ctx(), cases, top_k=8)
    raw = json.dumps(report, ensure_ascii=False).lower()
    for sensitive in ("password", "jwt", "cookie", "recovery", "secret_key", "def "):
        assert sensitive not in raw


# ---------------- E3-04-HARDEN-01：L1-L4 收口 ----------------

def _auditor_ctx():
    from app.tenancy import context as tc
    from app.tenancy import store as ts

    return tc.build_context(ts.active_membership_for_user("user_observer"))


def test_evaluate_case_rejects_auditor_without_rag_search():
    from fastapi import HTTPException

    from app.enterprise_rag.evaluation import evaluate_case

    _make_repo("repo_h1", "proj_h1", "tenant_default", _COMMIT)
    case = EvaluationCase(query="handle_refund", project_id="proj_h1", repository_id="repo_h1",
                          commit_sha=_COMMIT)
    with pytest.raises(HTTPException) as exc:
        evaluate_case(_auditor_ctx(), case, top_k=8)
    assert exc.value.status_code == 403  # auditor 无 rag:search -> 纵深拒绝


def test_recall_not_understated_by_repeated_or_empty_targets(monkeypatch):
    import app.retrieval.llamaindex_backend as lib

    monkeypatch.setattr(lib, "_query_vector_results", lambda *a, **k: [])
    _make_repo("repo_h2", "proj_h2", "tenant_default", _COMMIT)
    case = EvaluationCase(query="handle_refund", project_id="proj_h2", repository_id="repo_h2",
                          commit_sha=_COMMIT,
                          expected_chunk_ids=["repo_h2_c1", "repo_h2_c1", ""])
    report = evaluate_dataset(_ctx(), [case], top_k=8)
    assert report["per_case_results"][0]["recall_at_k"] == 1.0  # 重复/空串不放大分母


def test_empty_200_marked_retrieval_failed_not_passed(monkeypatch):
    import app.retrieval.llamaindex_backend as lib

    monkeypatch.setattr(lib, "_query_vector_results", lambda *a, **k: [])
    _make_repo("repo_h3", "proj_h3", "tenant_default", _COMMIT)
    case = EvaluationCase(query="nonexistent_symbol_zzz_h3", project_id="proj_h3", repository_id="repo_h3",
                          commit_sha=_COMMIT, expected_chunk_ids=["repo_h3_c1"])
    result = evaluate_dataset(_ctx(), [case], top_k=8)["per_case_results"][0]
    assert result["status_passed"] is True
    assert result["retrieval_passed"] is False
    assert result["passed"] is False


def test_metrics_by_tag_includes_precision(monkeypatch):
    import app.retrieval.llamaindex_backend as lib

    monkeypatch.setattr(lib, "_query_vector_results", lambda *a, **k: [])
    _make_repo("repo_h4", "proj_h4", "tenant_default", _COMMIT)
    case = EvaluationCase(query="handle_refund", project_id="proj_h4", repository_id="repo_h4",
                          commit_sha=_COMMIT, expected_chunk_ids=["repo_h4_c1"], tags=["code"])
    report = evaluate_dataset(_ctx(), [case], top_k=8)
    assert "precision_at_k" in report["metrics_by_tag"]["code"]
