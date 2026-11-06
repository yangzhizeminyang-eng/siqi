"""OPT-10 RAG 检索参数消融：融合权重 / 切片参数 / 消融报告纯函数（先红后绿）。

覆盖：
- Hybrid 融合权重（vector/bm25）默认 1.0 时 RRF 公式与排序与旧行为完全一致（手工核算）；
- 权重 0 = 禁用该路（不查询、不产生候选）；缩放权重按比例作用于 RRF 分数；
- 运行时配置 RAG_HYBRID_VECTOR_WEIGHT / RAG_HYBRID_BM25_WEIGHT：映射、下限钳制、非法值回退默认；
- service 检索入口把权重透传给检索层；复现契约 settings 快照覆盖新增权重；
- parsing_config 切片参数（行窗口/重叠/最大字符）支持环境变量覆盖（子进程隔离验证 import-time 语义）；
- parse_and_chunk 实际按 MAX_CHUNK_CHARS 改变切片粒度；
- 消融脚本纯函数：配置 hash 稳定性/敏感性、delta 与失败样本、确定性比较（忽略延迟）。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

_BACKEND = Path(__file__).resolve().parents[1]
_COMMIT = "c" * 40


def _chunk(chunk_id, path, content, symbol=None, repo="reA", index="inA"):
    return SimpleNamespace(
        chunk_id=chunk_id, symbol_name=symbol, file_path=path, content=content,
        repository_id=repo, index_id=index, commit_sha=_COMMIT, source_type="code",
        start_line=1, end_line=2, language="python", chunk_index=0, checksum=chunk_id)


def _dense(chunk, score):
    from app.retrieval.llamaindex_backend import SOURCE_MILVUS, RetrievedChunk

    return RetrievedChunk(
        chunk.chunk_id, chunk.file_path, chunk.start_line, chunk.end_line,
        chunk.language, chunk.symbol_name, chunk.content, score,
        chunk.commit_sha, chunk.index_id, True, SOURCE_MILVUS)


def _filters():
    from app.enterprise_rag.filters import build_filters

    return build_filters(tenant_id="tenant_default", project_id="pA", repository_id="reA",
                         commit_sha=_COMMIT, source_types=None, index_id="inA")


def _patch_legs(monkeypatch, dense, lexical, calls=None):
    from app.retrieval import bm25_backend, llamaindex_backend

    def fake_dense(*args, **kwargs):
        if calls is not None:
            calls["dense"] += 1
        return list(dense)

    def fake_bm25(*args, **kwargs):
        if calls is not None:
            calls["bm25"] += 1
        return list(lexical)

    monkeypatch.setattr(llamaindex_backend, "_query_vector_results", fake_dense)
    monkeypatch.setattr(bm25_backend, "retrieve_bm25", fake_bm25)


def _legs_corpus():
    from app.retrieval.bm25_backend import BM25Result

    a = _chunk("A", "a.py", "alpha shared")
    b = _chunk("B", "b.py", "beta vector only")
    c = _chunk("C", "c.py", "gamma bm25 only")
    dense = [_dense(a, 0.9), _dense(b, 0.8)]
    lexical = [BM25Result(chunk=c, score=3.0, rank=1), BM25Result(chunk=a, score=1.0, rank=2)]
    return [a, b, c], dense, lexical


def _hybrid(corpus, **kwargs):
    from app.enterprise_rag.hybrid import enterprise_hybrid_retrieve

    return enterprise_hybrid_retrieve(
        corpus, query_text="alpha", filters=_filters(), collection_name="c",
        vector_top_k=20, bm25_top_k=10, final_top_k=8, **kwargs)


# ---------------- Hybrid 融合权重 ----------------

def test_hybrid_default_weights_preserve_formula(monkeypatch):
    from app.enterprise_rag.hybrid import RRF_K, enterprise_retrieve_by_mode

    corpus, dense, lexical = _legs_corpus()
    _patch_legs(monkeypatch, dense, lexical)

    res = enterprise_retrieve_by_mode(
        corpus, query_text="alpha", mode="hybrid", filters=_filters(),
        collection_name="c", vector_top_k=20, bm25_top_k=10, final_top_k=8)

    scores = {r.chunk_id: r.score for r in res}
    # 手工按 RRF 公式（K=60，默认权重 1.0）核算：
    # A: 1/(K+1) + 1/(K+2)，support=max(0.9, bm25(1.0)=0.5)=0.9
    # B: 1/(K+2)，support=0.8；C: 1/(K+1)，support=bm25(3.0)=0.75
    assert scores["A"] == round((1 / (RRF_K + 1) + 1 / (RRF_K + 2)) * (1 + 0.9 * 0.5), 6)
    assert scores["B"] == round((1 / (RRF_K + 2)) * (1 + 0.8 * 0.5), 6)
    assert scores["C"] == round((1 / (RRF_K + 1)) * (1 + 0.75 * 0.5), 6)
    assert [r.chunk_id for r in res] == ["A", "B", "C"]
    assert {r.chunk_id: r.retrieval_source for r in res} == {"A": "hybrid", "B": "vector", "C": "bm25"}


def test_hybrid_default_equals_explicit_unit_weights(monkeypatch):
    corpus, dense, lexical = _legs_corpus()
    _patch_legs(monkeypatch, dense, lexical)

    implicit = _hybrid(corpus)
    explicit = _hybrid(corpus, vector_weight=1.0, bm25_weight=1.0)
    assert [(r.chunk_id, r.score) for r in implicit] == [(r.chunk_id, r.score) for r in explicit]


def test_hybrid_zero_bm25_weight_disables_lexical_leg(monkeypatch):
    corpus, dense, lexical = _legs_corpus()
    calls = {"dense": 0, "bm25": 0}
    _patch_legs(monkeypatch, dense, lexical, calls=calls)

    res = _hybrid(corpus, bm25_weight=0.0)

    assert calls["bm25"] == 0  # 禁用路不再查询
    assert [r.chunk_id for r in res] == ["A", "B"]
    assert all(r.contributions.get("bm25") is False for r in res)


def test_hybrid_zero_vector_weight_disables_dense_leg(monkeypatch):
    corpus, dense, lexical = _legs_corpus()
    calls = {"dense": 0, "bm25": 0}
    _patch_legs(monkeypatch, dense, lexical, calls=calls)

    res = _hybrid(corpus, vector_weight=0.0)

    assert calls["dense"] == 0
    # bm25-only：C 的 bm25 分数/置信度高于 A，C 排前
    assert [r.chunk_id for r in res] == ["C", "A"]
    assert all(r.contributions.get("vector") is False for r in res)


def test_hybrid_weights_scale_rrf_scores(monkeypatch):
    corpus, dense, _ = _legs_corpus()
    _patch_legs(monkeypatch, dense, [])

    base = _hybrid(corpus, vector_weight=1.0, bm25_weight=1.0)
    scaled = _hybrid(corpus, vector_weight=2.0, bm25_weight=1.0)
    base_scores = {r.chunk_id: r.score for r in base}
    scaled_scores = {r.chunk_id: r.score for r in scaled}
    for chunk_id in base_scores:
        # 两次 6 位舍入的累计误差上限 ~1.5e-6
        assert abs(scaled_scores[chunk_id] - base_scores[chunk_id] * 2) <= 2e-6


# ---------------- 运行时配置映射 ----------------

def test_settings_hybrid_weight_env_mapping_and_clamp(monkeypatch):
    from app.core.runtime_config import load_runtime_settings

    monkeypatch.setenv("RAG_HYBRID_VECTOR_WEIGHT", "1.5")
    monkeypatch.setenv("RAG_HYBRID_BM25_WEIGHT", "-2")
    settings = load_runtime_settings()
    assert settings.rag_hybrid_vector_weight == 1.5
    assert settings.rag_hybrid_bm25_weight == 0.0  # 负权重钳制到 0（= 禁用该路）


def test_settings_hybrid_weight_defaults_and_malformed(monkeypatch):
    from app.core.runtime_config import load_runtime_settings

    monkeypatch.delenv("RAG_HYBRID_VECTOR_WEIGHT", raising=False)
    monkeypatch.delenv("RAG_HYBRID_BM25_WEIGHT", raising=False)
    settings = load_runtime_settings()
    assert settings.rag_hybrid_vector_weight == 1.0
    assert settings.rag_hybrid_bm25_weight == 1.0

    monkeypatch.setenv("RAG_HYBRID_VECTOR_WEIGHT", "not-a-number")
    assert load_runtime_settings().rag_hybrid_vector_weight == 1.0  # 非法值回退默认


def test_service_entry_passes_hybrid_weights(monkeypatch):
    from evaluation.enterprise_rag.seed import seed_baseline
    from evaluation.enterprise_rag.seed import BASELINE_COMMIT
    from scripts.evaluate_enterprise_rag import _build_context

    seed_baseline()
    monkeypatch.setenv("RAG_HYBRID_VECTOR_WEIGHT", "1.25")
    monkeypatch.setenv("RAG_HYBRID_BM25_WEIGHT", "0.5")
    captured: dict = {}

    def spy(*args, **kwargs):
        captured.update(kwargs)
        return []

    from app.enterprise_rag import service
    from app.enterprise_rag.models import EnterpriseRagSearchPayload

    monkeypatch.setattr(service, "enterprise_retrieve_by_mode", spy)
    ctx = _build_context("user_admin")
    payload = EnterpriseRagSearchPayload(
        query="handle_refund", project_id="proj_demo_payment",
        repository_id="repo_demo_payment", commit_sha=BASELINE_COMMIT)
    service.enterprise_rag_search(ctx, payload)

    assert captured.get("vector_weight") == 1.25
    assert captured.get("bm25_weight") == 0.5


def test_repro_contract_snapshot_covers_hybrid_weights(monkeypatch):
    from app.enterprise_rag import repro_contract

    assert "rag_hybrid_vector_weight" in repro_contract._RAG_SETTINGS_FIELDS
    assert "rag_hybrid_bm25_weight" in repro_contract._RAG_SETTINGS_FIELDS
    monkeypatch.delenv("RAG_HYBRID_BM25_WEIGHT", raising=False)
    snapshot = repro_contract.rag_settings_snapshot()
    assert snapshot["rag_hybrid_bm25_weight"] == 1.0


# ---------------- 切片参数（parsing_config） ----------------

_PARSE_CONFIG_CODE = (
    "import app.retrieval.parsing_config as pc;"
    "print(pc.INDEX_TARGET_WINDOW_LINES, pc.INDEX_WINDOW_OVERLAP_LINES, pc.MAX_CHUNK_CHARS)")


def _run_py(code: str, env_extra: dict) -> str:
    env = os.environ.copy()
    env.update(env_extra)
    out = subprocess.run([sys.executable, "-c", code], cwd=str(_BACKEND), env=env,
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-800:]
    return out.stdout.strip().splitlines()[-1]


def test_parsing_config_env_override_subprocess():
    line = _run_py(_PARSE_CONFIG_CODE, {
        "RAG_CHUNK_LINES": "20", "RAG_CHUNK_OVERLAP_LINES": "3", "RAG_CHUNK_MAX_CHARS": "1500"})
    assert line == "20 3 1500"


def test_parsing_config_invalid_falls_back_and_clamps():
    assert _run_py(_PARSE_CONFIG_CODE, {"RAG_CHUNK_LINES": "abc"}) == "40 10 4000"
    assert _run_py(_PARSE_CONFIG_CODE,
                   {"RAG_CHUNK_OVERLAP_LINES": "-5", "RAG_CHUNK_MAX_CHARS": "0"}) == "40 1 1"


def _big_source(n: int = 200) -> str:
    lines = ['"""synthetic chunk probe module (non-sensitive)."""', ""]
    for i in range(n):
        lines += [f"def probe_fn_{i}(value):", f"    return value + {i}", ""]
    return "\n".join(lines)


def test_parse_and_chunk_honors_max_chunk_chars(monkeypatch):
    from app.retrieval import parsing

    files = [SimpleNamespace(path="src/probe.py", language="python", content=_big_source())]
    monkeypatch.setattr(parsing, "MAX_CHUNK_CHARS", 4000)
    big = parsing.parse_and_chunk(files, repository_id="r", index_id="i",
                                  commit_sha="c1", blocked_paths=[])
    monkeypatch.setattr(parsing, "MAX_CHUNK_CHARS", 600)
    small = parsing.parse_and_chunk(files, repository_id="r", index_id="i",
                                    commit_sha="c1", blocked_paths=[])

    assert len(big) >= 2
    assert len(small) > len(big)
    assert all(len(c.content) <= 600 for c in small)


# ---------------- 消融脚本纯函数 ----------------

def _knobs(**overrides):
    base = {
        "top_k": 8, "mode": "hybrid", "rag_vector_top_k": 20, "rag_bm25_top_k": 10,
        "rag_final_top_k": 8, "rag_symbol_boost": 1.0, "rag_hybrid_vector_weight": 1.0,
        "rag_hybrid_bm25_weight": 1.0, "rag_rerank_enabled": False,
    }
    base.update(overrides)
    return base


def test_ablation_config_sha_stable_and_sensitive():
    from scripts import opt10_rag_ablation as ab

    digest = ab.config_sha256(_knobs(), "ds1")
    assert digest == ab.config_sha256(_knobs(), "ds1")
    assert len(digest) == 16
    assert ab.config_sha256(_knobs(rag_hybrid_bm25_weight=0.0), "ds1") != digest
    assert ab.config_sha256(_knobs(), "ds2") != digest


def test_ablation_build_configs_cover_required_dimensions():
    from scripts import opt10_rag_ablation as ab

    configs = ab.build_eval_configs(quick=False)
    names = [c["name"] for c in configs]
    assert names[0] == "baseline"
    assert len(names) == len(set(names))
    env_keys = {key for c in configs for key in c.get("env", {})}
    assert {"RAG_FINAL_TOP_K", "RAG_HYBRID_VECTOR_WEIGHT", "RAG_HYBRID_BM25_WEIGHT",
            "RAG_SYMBOL_BOOST", "RAG_RERANK_ENABLED", "RAG_VECTOR_TOP_K"} <= env_keys
    assert all(isinstance(v, str) for c in configs for v in c.get("env", {}).values())
    quick = [c["name"] for c in ab.build_eval_configs(quick=True)]
    assert "baseline" in quick and len(quick) < len(names)


def test_ablation_deltas_and_failure_samples():
    from scripts import opt10_rag_ablation as ab

    baseline = {
        "summary": {"recall_at_k": 0.8, "mrr": 0.7},
        "per_case": [{"case_id": "q1", "passed": True, "recall_at_k": 1.0, "mrr": 1.0},
                     {"case_id": "q2", "passed": False, "recall_at_k": 0.0, "mrr": 0.0,
                      "actual_status": 200, "expected_status": 200, "tags": ["code"]}],
    }
    current = {
        "summary": {"recall_at_k": 0.9, "mrr": 0.75},
        "per_case": [{"case_id": "q1", "passed": False, "recall_at_k": 0.0, "mrr": 0.0,
                      "actual_status": 200, "expected_status": 200, "tags": ["code"]},
                     {"case_id": "q2", "passed": True, "recall_at_k": 1.0, "mrr": 1.0}],
    }
    delta = ab.deltas_vs_baseline(baseline, current)
    assert delta["regressions"] == ["q1"]
    assert delta["improvements"] == ["q2"]
    assert delta["metric_deltas"]["recall_at_k"] == 0.1
    samples = ab.failure_samples(current, limit=5)
    assert samples and samples[0]["case_id"] == "q1"
    assert samples[0]["actual_status"] == 200


def test_ablation_determinism_compare_ignores_latency():
    from scripts import opt10_rag_ablation as ab

    left = {"config_sha256": "h1",
            "summary": {"recall_at_k": 0.8, "latency_p50_ms": 1.0, "latency_p95_ms": 2.0},
            "per_case": [{"case_id": "q1", "passed": True, "recall_at_k": 1.0, "mrr": 1.0}]}
    right = {"config_sha256": "h1",
             "summary": {"recall_at_k": 0.8, "latency_p50_ms": 9.0, "latency_p95_ms": 9.0},
             "per_case": [{"case_id": "q1", "passed": True, "recall_at_k": 1.0, "mrr": 1.0}]}
    assert ab.determinism_compare(left, right)["identical"] is True

    changed = json.loads(json.dumps(right))
    changed["per_case"][0]["recall_at_k"] = 0.5
    result = ab.determinism_compare(left, changed)
    assert result["identical"] is False
    assert result["differences"]
