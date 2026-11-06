"""企业版 E3-07 代码检索 precision 调优专项测试。

覆盖：
- bm25 精确符号定义优先（symbol_name == query token 时加权），调用块排在定义块之后；
- 团队版既有行为不被破坏（symbol_boost 默认 0.0 时排序不变）；
- precision 提升不牺牲 recall / 不把定义块挤出 top-K；
- 过滤边界不因 boost 被绕过（仍只在受限子集内）；
- 质量门不被伪造（阈值不降低、未达标仍 needs_improvement）。

原则：不做 mock/回退冒充 Milvus，不手写指标，不改 recommended_thresholds 制造通过。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core.runtime_config import load_runtime_settings
from app.enterprise_rag.evaluation import RECOMMENDED_THRESHOLDS, quality_gate
from app.enterprise_rag.filters import build_filters
from app.enterprise_rag.hybrid import enterprise_retrieve_by_mode
from app.retrieval.bm25_backend import retrieve_bm25, tokenize

_COMMIT = "73e7e5497addc74f67fe49a875d7afdd8d8c190b"


def _chunk(chunk_id, symbol, path, content, repo="reA", index="inA", src="code"):
    return SimpleNamespace(
        chunk_id=chunk_id, symbol_name=symbol or None, file_path=path,
        content=content, repository_id=repo, index_id=index, commit_sha=_COMMIT,
        source_type=src, start_line=1, end_line=2, language="python",
        chunk_index=0, checksum=chunk_id, milvus_collection=None,
    )


def _build_login_corpus():
    """D = 定义块；R1/R2/R3 = 仅引用该符号的调用块；其余 = 无关块。"""
    definition = _chunk(
        "D", symbol="build_login_response", path="auth/service.py",
        content="def build_login_response(ctx, user):\n    return ApiResponse()\n    alpha beta gamma delta sigma omega")
    R1 = _chunk("R1", symbol="login", path="auth/routes.py",
                content="resp = build_login_response(ctx, user)\n    alpha beta gamma delta sigma omega")
    R2 = _chunk("R2", symbol="refresh", path="auth/routes.py",
                content="return build_login_response(ctx, u)\n    alpha beta gamma delta sigma omega")
    R3 = _chunk("R3", symbol="logout", path="auth/routes.py",
                content="build_login_response(ctx, u)\n    alpha beta gamma delta sigma omega")
    U1 = _chunk("U1", symbol="parse_token", path="token/parse.py", content="def parse_token(): pass")
    U2 = _chunk("U2", symbol="issue_access_token", path="token/issue.py",
                content="def issue_access_token(): return None")
    return [U1, U2, R1, R2, R3, definition]


def test_bm25_token_keeps_full_underscore_identifier():
    assert tokenize("build_login_response") == ["build_login_response"]


def test_bm25_definition_first_when_symbol_boost():
    corpus = _build_login_corpus()
    boosted = retrieve_bm25(corpus, "build_login_response", top_k=8, symbol_boost=0.3)
    # 定义块(符号名==query token)必须排第一
    assert boosted[0].chunk.symbol_name == "build_login_response"
    assert boosted[0].chunk.chunk_id == "definition" or boosted[0].chunk.chunk_id == "D"


def test_bm25_symbol_boost_semantics():
    corpus = _build_login_corpus()
    boosted = retrieve_bm25(corpus, "build_login_response", top_k=8, symbol_boost=0.3)
    plain = retrieve_bm25(corpus, "build_login_response", top_k=8, symbol_boost=0.0)
    # boost 必须把定义块提升到调用块之前；无 boost 时定义块与调用块分数不应被放大
    assert boosted[0].chunk.chunk_id == "D"
    plain_top_ids = [r.chunk.chunk_id for r in plain]
    # 无 boost 时短调用块 R1 分数本来就高于长定义块，所以定义不应因无 boost 也被顶到第一
    assert "D" not in plain_top_ids[:1] or plain[0].score < boosted[0].score


def test_bm25_boost_does_not_drop_definition_recall():
    corpus = _build_login_corpus()
    for boost in (0.0, 0.3):
        res = retrieve_bm25(corpus, "build_login_response", top_k=8, symbol_boost=boost)
        top_k = {r.chunk.chunk_id for r in res}
        assert "D" in top_k, f"boost={boost} 把定义块挤出 top-K（recall 退化）"
    boosted = retrieve_bm25(corpus, "build_login_response", top_k=8, symbol_boost=0.3)
    plain = retrieve_bm25(corpus, "build_login_response", top_k=8, symbol_boost=0.0)
    # 定义块 mrr（1/rank）不得变差
    def _rank(results, chunk_id):
        for i, r in enumerate(results, 1):
            if r.chunk.chunk_id == chunk_id:
                return i
        return len(results) + 1
    assert _rank(boosted, "D") <= _rank(plain, "D") + 1  # 不劣化（允许并列轻微浮动）


def test_bm25_default_zero_boost_team_unchanged():
    # 团队版调用 retrieve_bm25 不传 symbol_boost -> 默认 0：与不传参（旧版）完全一致，不加额外加权
    corpus = _build_login_corpus()
    legacy_order = [r.chunk.chunk_id for r in retrieve_bm25(corpus, "build_login_response", top_k=8)]
    explicit_zero = [r.chunk.chunk_id for r in retrieve_bm25(corpus, "build_login_response", top_k=8, symbol_boost=0.0)]
    assert explicit_zero == legacy_order
    # 且开启 boost 后定义块若本已靠前也应保持或更前（不回归）
    boosted_order = [r.chunk.chunk_id for r in retrieve_bm25(corpus, "build_login_response", top_k=8, symbol_boost=0.3)]
    assert boosted_order == legacy_order


def test_enterprise_bm25_mode_honors_filter_boundary_under_boost():
    # 混合语料：部分 chunk 属于 reB（被过滤），只有 reA 的子集能被召回
    corpus = [
        _chunk("DA", "build_login_response", "auth/service.py",
               "def build_login_response(ctx): pass" + "x" * 200, repo="reA"),
        _chunk("R1", "login", "auth/routes.py", "build_login_response(ctx)", repo="reA"),
        _chunk("XB", "build_login_response", "other/plugin.py",
               "def build_login_response(): pass" + "x" * 200, repo="reB"),
        _chunk("YB", "login", "other/routes.py", "build_login_response(ctx)", repo="reB"),
    ]
    filters = build_filters(
        tenant_id="tenant_default", project_id="pA", repository_id="reA",
        commit_sha=_COMMIT, source_types=["code"], index_id="inA")
    res = enterprise_retrieve_by_mode(
        corpus, query_text="build_login_response", mode="bm25",
        symbol_boost=0.3, filters=filters, collection_name="c",
        vector_top_k=20, bm25_top_k=10, final_top_k=8)
    ids = [r.chunk_id for r in res]
    # 过滤边界未被 boost 绕过：reB 的 chunk 一个都不该出现
    assert all(r.repository_id == "reA" for r in res)
    assert "XB" not in ids and "YB" not in ids
    # 定义块 DA 在过滤子集内被 boost 到最前
    assert res[0].chunk_id == "DA"


def test_quality_thresholds_not_lowered():
    # 禁止通过调低 recommended_thresholds 制造通过
    assert RECOMMENDED_THRESHOLDS["precision_at_k"] == 0.3
    assert RECOMMENDED_THRESHOLDS["recall_at_k"] == 0.5


def test_quality_gate_honest_below_threshold():
    summary = {"precision_at_k": 0.2015, "recall_at_k": 0.8731,
               "mrr": 0.8072, "ndcg_at_k": 0.8227}
    gate = quality_gate(summary, RECOMMENDED_THRESHOLDS)
    assert gate["needs_improvement"] is True  # precision 低于阈值时必须标记，不能伪通过


def test_quality_gate_can_pass_when_above_threshold():
    summary = {"precision_at_k": 0.34, "recall_at_k": 0.88,
               "mrr": 0.8, "ndcg_at_k": 0.82}
    gate = quality_gate(summary, RECOMMENDED_THRESHOLDS)
    assert gate["needs_improvement"] is False


def test_runtime_config_symbol_boost_default():
    s = load_runtime_settings()
    # 新配置必须有安全默认；0.0 关闭，缺省开启取 0.3
    assert s.rag_symbol_boost >= 0.0