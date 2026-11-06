"""企业版 E3-09 Cross-Encoder 精排专项测试。

逻辑测试用确定性桩模型（非指标数据），不联网下载真实模型；模型不可用/超时路径验证 fallback。
覆盖规格必查项：只重排过滤后候选、不重新检索、auditor 403、不可用/超时回退、空/单/超 max 候选、
batch 稳定、不改原 score、trace 无正文/凭据、默认关闭不改 E3-08/E3-07、可复现、报告区分 provider/fallback。
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import main
from app.core.runtime_config import load_runtime_settings
from app.enterprise_rag.cross_encoder import enterprise_cross_encoder_rerank
from app.enterprise_rag.models import EnterpriseRetrievedChunk
from app.enterprise_rag.reranker import enterprise_rerank
import app.enterprise_rag.cross_encoder as cross_encoder_mod

_COMMIT = "73e7e5497addc74f67fe49a875d7afdd8d8c190b"


def _chunk(chunk_id: str, symbol: str, path: str, score: float = 0.5) -> EnterpriseRetrievedChunk:
    return EnterpriseRetrievedChunk(
        chunk_id=chunk_id, source_type="code", file_path=path, start_line=1, end_line=2,
        language="python", symbol_name=symbol or None, repository_id="r1", index_id="in1",
        commit_sha=_COMMIT, retrieval_source="hybrid", score=score, fresh=True)


class _StubCE:
    """确定性桩：text 越短分数越高；可配置延时/异常用于超时与错误路径。"""
    config = SimpleNamespace(_name_or_path="stub-cross-encoder", model_type="stub")

    def __init__(self, delay: float = 0.0, fail: bool = False):
        self.delay = delay
        self.fail = fail

    def predict(self, pairs, **kw):
        import numpy as np
        if self.fail:
            raise RuntimeError("stub inference failure")
        if self.delay:
            time.sleep(self.delay)
        return np.array([-float(len(text)) for _, text in pairs])


def _chunks(n: int):
    return [_chunk(f"c{i}", f"sym{i}", f"src/{i}.py", score=float(n - i)) for i in range(n)]


@pytest.fixture()
def stub_model(monkeypatch):
    model = _StubCE()
    monkeypatch.setattr(cross_encoder_mod, "load_cross_encoder", lambda *a, **k: (model, ""))
    return model


def test_rerank_only_reorders_given_candidates_no_new_chunks(stub_model):
    results = _chunks(6)
    reranked, trace = enterprise_cross_encoder_rerank(
        results, query_text="q", enabled=True, model_name="stub", device="cpu",
        top_n=4, batch_size=2, timeout_seconds=5, max_candidates=6)
    assert {r.chunk_id for r in reranked} == {r.chunk_id for r in results}  # 不引新候选
    assert len(reranked) == len(results)
    assert trace["scored_count"] == 6
    assert trace["fallback_used"] is False


def test_rerank_does_not_mutate_original_score(stub_model):
    results = _chunks(4)
    reranked, _ = enterprise_cross_encoder_rerank(
        results, query_text="q", enabled=True, model_name="stub", device="cpu",
        top_n=4, batch_size=2, timeout_seconds=5, max_candidates=4)
    by_id = {r.chunk_id: r for r in reranked}
    for r in results:
        assert by_id[r.chunk_id].score == r.score  # 原始 score 不变
        assert by_id[r.chunk_id].rerank_score is not None  # 仅新增 rerank_score


def test_batch_size_stable_order(stub_model):
    a, _ = enterprise_cross_encoder_rerank(_chunks(7), query_text="q", enabled=True,
                                           model_name="s", device="cpu", top_n=5, batch_size=2,
                                           timeout_seconds=5, max_candidates=7)
    b, _ = enterprise_cross_encoder_rerank(_chunks(7), query_text="q", enabled=True,
                                           model_name="s", device="cpu", top_n=5, batch_size=8,
                                           timeout_seconds=5, max_candidates=7)
    assert [r.chunk_id for r in a] == [r.chunk_id for r in b]


def test_reproducible_same_input_same_order(stub_model):
    a, _ = enterprise_cross_encoder_rerank(_chunks(6), query_text="q", enabled=True,
                                           model_name="s", device="cpu", top_n=6, batch_size=3,
                                           timeout_seconds=5, max_candidates=6)
    b, _ = enterprise_cross_encoder_rerank(_chunks(6), query_text="q", enabled=True,
                                           model_name="s", device="cpu", top_n=6, batch_size=3,
                                           timeout_seconds=5, max_candidates=6)
    assert [r.chunk_id for r in a] == [r.chunk_id for r in b]


def test_max_candidates_capped_and_no_drop(stub_model):
    results = _chunks(20)
    reranked, trace = enterprise_cross_encoder_rerank(
        results, query_text="q", enabled=True, model_name="s", device="cpu",
        top_n=4, batch_size=4, timeout_seconds=5, max_candidates=5)
    assert trace["scored_count"] == 5  # 只打分前 5
    assert len(reranked) == 20  # 不丢候选
    assert {r.chunk_id for r in reranked} == {r.chunk_id for r in results}


def test_empty_and_single_candidate(stub_model):
    empty, t1 = enterprise_cross_encoder_rerank([], query_text="q", enabled=True,
                                                model_name="s", device="cpu", top_n=4,
                                                batch_size=2, timeout_seconds=5, max_candidates=8)
    assert empty == []
    one, t2 = enterprise_cross_encoder_rerank(_chunks(1), query_text="q", enabled=True,
                                              model_name="s", device="cpu", top_n=4,
                                              batch_size=2, timeout_seconds=5, max_candidates=8)
    assert len(one) == 1


def test_disabled_returns_none_fallback():
    res, trace = enterprise_cross_encoder_rerank(_chunks(3), query_text="q", enabled=False,
                                                 model_name="", device="cpu", top_n=4,
                                                 batch_size=2, timeout_seconds=5, max_candidates=8)
    assert res is None
    assert trace["fallback_used"] is True
    assert "disabled" in trace["fallback_reason"]


def test_model_unavailable_fallback():
    # 未配置模型（默认）：load_cross_encoder 返回 None
    res, trace = enterprise_cross_encoder_rerank(_chunks(3), query_text="q", enabled=True,
                                                 model_name="", device="cpu", top_n=4,
                                                 batch_size=2, timeout_seconds=5, max_candidates=8)
    assert res is None
    assert trace["fallback_used"] is True
    assert "provider_unavailable" in trace["fallback_reason"]


def test_timeout_falls_back(monkeypatch):
    slow = _StubCE(delay=1.4)
    monkeypatch.setattr(cross_encoder_mod, "load_cross_encoder", lambda *a, **k: (slow, ""))
    res, trace = enterprise_cross_encoder_rerank(_chunks(4), query_text="q", enabled=True,
                                                 model_name="s", device="cpu", top_n=4,
                                                 batch_size=2, timeout_seconds=1, max_candidates=8)
    assert res is None
    assert trace["fallback_used"] is True
    assert "timeout" in trace["fallback_reason"]


def test_inference_error_falls_back(monkeypatch):
    bad = _StubCE(fail=True)
    monkeypatch.setattr(cross_encoder_mod, "load_cross_encoder", lambda *a, **k: (bad, ""))
    res, trace = enterprise_cross_encoder_rerank(_chunks(4), query_text="q", enabled=True,
                                                 model_name="s", device="cpu", top_n=4,
                                                 batch_size=2, timeout_seconds=5, max_candidates=8)
    assert res is None
    assert trace["fallback_used"] is True
    assert "inference_error" in trace["fallback_reason"]


def test_trace_and_reason_no_code_body_or_credentials(stub_model):
    reranked, trace = enterprise_cross_encoder_rerank(_chunks(4), query_text="q", enabled=True,
                                                      model_name="s", device="cpu", top_n=4,
                                                      batch_size=2, timeout_seconds=5, max_candidates=8)
    blob = " ".join([str(trace), *(r.rerank_reason or "" for r in reranked)])
    for forbidden in ("content", "password", "api_key", "authorization", "secret=", "Bearer"):
        assert forbidden.lower() not in blob.lower()


def test_enterprise_rerank_cross_encoder_executes_with_stub(stub_model):
    results = _chunks(5)
    reranked, trace = enterprise_rerank(
        results, query_text="q", enabled=True, provider="cross_encoder", top_n=4, timeout_seconds=5,
        cross_encoder_enabled=True, cross_encoder_model="s", cross_encoder_batch_size=2,
        cross_encoder_max_candidates=5)
    assert trace["provider"] == "cross_encoder"
    assert trace["fallback_used"] is False
    assert all(r.rerank_score is not None for r in reranked)


def test_enterprise_rerank_cross_encoder_falls_back_when_unavailable():
    # 未配置模型 → 回退 heuristic，trace 标记 provider_attempted=cross_encoder + fallback
    results = _chunks(5)
    reranked, trace = enterprise_rerank(
        results, query_text="q", enabled=True, provider="cross_encoder", top_n=4, timeout_seconds=5,
        cross_encoder_enabled=True, cross_encoder_model="", cross_encoder_batch_size=2,
        cross_encoder_max_candidates=5)
    assert trace["provider"] == "heuristic"
    assert trace.get("provider_attempted") == "cross_encoder"
    assert trace.get("fallback_used") is True
    assert "provider_unavailable" in trace.get("fallback_reason", "")


def test_default_disabled_keeps_heuristic_behavior():
    # 默认 CE 关闭（enabled False, model 空）时，heuristic 行为与 E3-03 一致
    results = _chunks(4)
    reranked, trace = enterprise_rerank(
        results, query_text="q", enabled=True, provider="heuristic", top_n=4, timeout_seconds=5)
    assert trace["provider"] == "heuristic"
    assert all(r.rerank_reason for r in reranked)


def test_runtime_config_secure_defaults():
    s = load_runtime_settings()
    assert s.rag_cross_encoder_enabled is False
    assert s.rag_cross_encoder_model == ""
    assert s.rag_cross_encoder_device == "cpu"


def test_auditor_without_rag_search_403():
    c = TestClient(main.app)
    assert c.post("/api/auth/login", json={"account": "observer@company.com", "password": "Observer123!"}).status_code == 200
    r = c.post("/api/enterprise/rag/search", json={"query": "build_x", "project_id": "pA", "repository_id": "rA"})
    assert r.status_code == 403


def test_compare_report_distinguishes_provider_fallback(monkeypatch):
    import app.enterprise_rag.rerank_compare as rc

    def _fake_eval(ctx, cases, *, top_k, retrieval_mode, require_milvus):
        return {
            "dataset_sha256": "ds",
            "config_snapshot_sha256": "cs",
            "dataset_size": 1,
            "retrieval_mode": retrieval_mode,
            "summary": {"recall_at_k": 0.9, "precision_at_k": 0.3, "mrr": 0.8, "ndcg_at_k": 0.8,
                        "latency_p50_ms": 1, "latency_p95_ms": 2},
            "validation_status": "verified", "milvus_verified": True, "fallback_used": False,
            "needs_improvement": False, "index_id": "idx",
            "metrics_by_tag": {"symbol": {"count": 1, "recall_at_k": 0.9, "precision_at_k": 0.3,
                                          "mrr": 0.8, "ndcg_at_k": 0.8}},
            "failed_cases": [], "commit_sha": _COMMIT,
        }

    monkeypatch.setattr(rc, "evaluate_dataset", _fake_eval)
    # 未配置模型 → provider_unavailable / fallback
    monkeypatch.setattr(rc, "load_cross_encoder", lambda *a, **k: (None, "provider_unavailable: no model"))
    report = rc.compare_rerank(None, [], top_k=8, require_milvus=True, repository_url="u",
                               project_id="p", repository_id="r", cross_encoder_model="", cross_encoder_device="cpu")
    assert report["cross_encoder_metrics"]["status"] in ("provider_unavailable", "fallback")
    assert report["cross_encoder_metrics"]["fallback_used"] is True
    assert "baseline_metrics" in report and "heuristic_metrics" in report and "llm_metrics" in report
    assert "per_tag_metrics" in report and "mode_comparison" in report
    assert "generated_at" in report

    # 配置模型且可加载 → executed（用桩）
    stub = _StubCE()
    monkeypatch.setattr(rc, "load_cross_encoder", lambda *a, **k: (stub, ""))
    report2 = rc.compare_rerank(None, [], top_k=8, require_milvus=True, repository_url="u",
                                project_id="p", repository_id="r", cross_encoder_model="stub", cross_encoder_device="cpu")
    assert report2["cross_encoder_metrics"]["status"] == "executed"
    assert report2["cross_encoder_metrics"]["fallback_used"] is False
    assert report2["cross_encoder_metrics"]["model_name"] == "stub"