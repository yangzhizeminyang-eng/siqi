"""RAG 检索质量增强：范围过滤、候选池扩展、chunk 去重和文件多样性。"""

from __future__ import annotations

from types import SimpleNamespace

from app.retrieval import llamaindex_backend as lb
from app.retrieval.bm25_backend import BM25Result, retrieve_bm25
from app.retrieval.query_expansion import expand_query
from app.retrieval.reranker import rerank_candidates


def _chunk(
    chunk_id: str,
    file_path: str,
    *,
    repository_id: str = "repo_a",
    index_id: str = "idx_a",
    content: str = "payment callback idempotency",
):
    return SimpleNamespace(
        chunk_id=chunk_id,
        repository_id=repository_id,
        index_id=index_id,
        file_path=file_path,
        chunk_index=0,
        content=content,
        language="python",
        symbol_name="handle_callback",
        start_line=1,
        end_line=10,
        commit_sha="commit_a",
        checksum="checksum",
    )


def test_metadata_filters_use_and_condition_for_repository_and_index():
    filters = lb._metadata_filters(repository_id="repo_a", index_id="idx_a")

    assert filters is not None
    assert len(filters.filters) == 2
    assert {(item.key, item.value) for item in filters.filters} == {
        ("repository_id", "repo_a"),
        ("index_id", "idx_a"),
    }


def test_keyword_retrieval_excludes_out_of_scope_chunks_and_diversifies_files(monkeypatch):
    monkeypatch.setattr(lb, "is_available", lambda: False)
    chunks = [
        _chunk("a1", "services/payment.py"),
        _chunk("a2", "services/payment.py", content="payment callback duplicate retry"),
        _chunk("a3", "services/payment.py", content="payment callback ledger"),
        _chunk("b1", "services/order.py", content="payment order retry"),
        _chunk("other", "secrets.py", repository_id="repo_other"),
        _chunk("old", "services/old.py", index_id="idx_old"),
    ]

    results = lb.query_index_from_chunks(
        chunks,
        repository_id="repo_a",
        index_id="idx_a",
        collection_name="unused",
        query_text="payment callback",
        top_k=3,
        threshold=0.0,
        head_commit="commit_a",
        max_chunks_per_file=1,
    )

    assert [item.chunk_id for item in results] == ["a1", "b1"]
    assert all(item.source == lb.SOURCE_FALLBACK for item in results)
    assert {item.file_path for item in results} == {"services/payment.py", "services/order.py"}


def test_dedupe_and_diversify_preserves_rank_order():
    results = [
        lb.RetrievedChunk("a1", "a.py", 1, 2, "python", None, "a", 0.9, "c", "i", True),
        lb.RetrievedChunk("a1", "a.py", 1, 2, "python", None, "a", 0.8, "c", "i", True),
        lb.RetrievedChunk("a2", "a.py", 3, 4, "python", None, "a", 0.7, "c", "i", True),
        lb.RetrievedChunk("b1", "b.py", 1, 2, "python", None, "b", 0.6, "c", "i", True),
    ]

    selected = lb._dedupe_and_diversify(results, top_k=3, max_chunks_per_file=1)

    assert [item.chunk_id for item in selected] == ["a1", "b1"]


def test_bm25_boosts_exact_symbol_and_path_match():
    chunks = [
        _chunk("exact", "services/refund.py", content="handle request"),
        _chunk("partial", "services/payment.py", content="handle_payment_callback fallback"),
    ]
    chunks[0].symbol_name = "handle_refund"
    chunks[1].symbol_name = "handle_payment"

    results = retrieve_bm25(chunks, "handle_payment_callback", top_k=2)

    assert results and results[0].chunk.chunk_id == "partial"
    assert len(results) == 1


def test_hybrid_fuses_dense_and_bm25_candidates(monkeypatch):
    semantic = _chunk("semantic", "services/payment.py", content="reconcile transaction replay safely")
    exact = _chunk("exact", "services/callback.py", content="handle_payment_callback")
    dense_result = lb.RetrievedChunk(
        "semantic", semantic.file_path, 1, 10, "python", semantic.symbol_name,
        semantic.content, 0.82, "commit_a", "idx_a", True, lb.SOURCE_MILVUS,
    )
    monkeypatch.setattr(lb, "_query_vector_results", lambda *args, **kwargs: [dense_result])
    monkeypatch.setattr(
        lb,
        "retrieve_bm25",
        lambda *args, **kwargs: [BM25Result(chunk=exact, score=3.0, rank=1)],
    )

    results = lb.query_index_from_chunks(
        [semantic, exact],
        repository_id="repo_a",
        index_id="idx_a",
        collection_name="unused",
        query_text="handle_payment_callback",
        top_k=2,
        threshold=0.3,
        head_commit="commit_a",
        retrieval_mode="hybrid",
        max_chunks_per_file=None,
    )

    assert [item.chunk_id for item in results] == ["semantic", "exact"]
    assert all(item.source == lb.SOURCE_HYBRID for item in results)
    assert results[0].score > results[1].score


def test_hybrid_mode_can_be_enabled_by_runtime_environment(monkeypatch):
    monkeypatch.setenv("RAG_RETRIEVAL_MODE", "hybrid")
    monkeypatch.setattr(lb, "_query_vector_results", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        lb,
        "retrieve_bm25",
        lambda *args, **kwargs: [
            BM25Result(chunk=_chunk("exact", "services/callback.py"), score=3.0, rank=1),
        ],
    )

    results = lb.query_index_from_chunks(
        [_chunk("exact", "services/callback.py")],
        repository_id="repo_a",
        index_id="idx_a",
        collection_name="unused",
        query_text="payment callback",
        top_k=1,
        threshold=0.3,
        head_commit="commit_a",
        max_chunks_per_file=None,
    )

    assert [item.chunk_id for item in results] == ["exact"]
    assert results[0].source == lb.SOURCE_HYBRID


def test_query_expansion_keeps_original_and_extracts_code_terms(monkeypatch):
    monkeypatch.setattr("app.retrieval.query_expansion.model_config.llm_configured", lambda: False)

    queries = expand_query("支付回调 handle_payment_callback ERR_401", max_queries=3)

    assert queries[0] == "支付回调 handle_payment_callback ERR_401"
    assert len(queries) <= 3
    assert any("handle_payment_callback" in query for query in queries)


def test_llm_query_rewrite_is_optional_and_parsed(monkeypatch):
    monkeypatch.setattr("app.retrieval.query_expansion.model_config.llm_configured", lambda: True)
    monkeypatch.setattr(
        "app.retrieval.query_expansion.DeepSeekTextClient.generate",
        lambda self, prompt, system=None: '{"queries":["payment idempotency","callback retry"]}',
    )

    queries = expand_query("支付回调重复记账", max_queries=3)

    assert queries == ["支付回调重复记账", "payment idempotency", "callback retry"]


def test_hybrid_query_expansion_merges_results_from_multiple_queries(monkeypatch):
    original = _chunk("original", "services/payment.py", content="payment callback")
    expanded = _chunk("expanded", "services/retry.py", content="idempotency retry")

    def dense_results(chunks, **kwargs):
        if kwargs["query_text"] == "original":
            return [
                lb.RetrievedChunk(
                    "original", original.file_path, 1, 10, "python", None,
                    original.content, 0.8, "commit_a", "idx_a", True, lb.SOURCE_MILVUS,
                )
            ]
        return [
            lb.RetrievedChunk(
                "expanded", expanded.file_path, 1, 10, "python", None,
                expanded.content, 0.8, "commit_a", "idx_a", True, lb.SOURCE_MILVUS,
            )
        ]

    monkeypatch.setattr(lb, "_query_vector_results", dense_results)
    monkeypatch.setattr(lb, "retrieve_bm25", lambda *args, **kwargs: [])
    monkeypatch.setattr(lb, "expand_query", lambda *args, **kwargs: ["original", "idempotency retry"])

    results = lb.query_index_from_chunks(
        [original, expanded],
        repository_id="repo_a",
        index_id="idx_a",
        collection_name="unused",
        query_text="original",
        top_k=2,
        threshold=0.3,
        head_commit="commit_a",
        retrieval_mode="hybrid",
        query_expansion=True,
        max_chunks_per_file=None,
    )

    assert [item.chunk_id for item in results] == ["original", "expanded"]


def test_llm_reranker_reorders_only_existing_candidate_ids(monkeypatch):
    candidates = [
        lb.RetrievedChunk("a", "a.py", 1, 2, "python", None, "a", 0.8, "c", "i", True),
        lb.RetrievedChunk("b", "b.py", 1, 2, "python", None, "b", 0.7, "c", "i", True),
    ]
    monkeypatch.setattr("app.retrieval.reranker.model_config.llm_configured", lambda: True)
    monkeypatch.setattr(
        "app.retrieval.reranker.DeepSeekTextClient.generate",
        lambda self, prompt, system=None: '{"ranked_chunk_ids":["missing","b"]}',
    )

    results = rerank_candidates("find b", candidates)

    assert [item.chunk_id for item in results] == ["b", "a"]


def test_llm_reranker_keeps_rrf_order_when_model_is_unavailable(monkeypatch):
    candidates = [
        lb.RetrievedChunk("a", "a.py", 1, 2, "python", None, "a", 0.8, "c", "i", True),
        lb.RetrievedChunk("b", "b.py", 1, 2, "python", None, "b", 0.7, "c", "i", True),
    ]
    monkeypatch.setattr("app.retrieval.reranker.model_config.llm_configured", lambda: False)

    results = rerank_candidates("find b", candidates)

    assert [item.chunk_id for item in results] == ["a", "b"]


def test_hybrid_rerank_is_opt_in(monkeypatch):
    first = _chunk("first", "services/first.py")
    second = _chunk("second", "services/second.py")
    monkeypatch.setattr(
        lb,
        "_query_vector_results",
        lambda *args, **kwargs: [
            lb.RetrievedChunk("first", first.file_path, 1, 10, "python", None, first.content, 0.8, "commit_a", "idx_a", True),
            lb.RetrievedChunk("second", second.file_path, 1, 10, "python", None, second.content, 0.7, "commit_a", "idx_a", True),
        ],
    )
    monkeypatch.setattr(lb, "retrieve_bm25", lambda *args, **kwargs: [])
    monkeypatch.setattr(lb, "rerank_candidates", lambda query, candidates, **kwargs: list(reversed(candidates)))

    results = lb.query_index_from_chunks(
        [first, second],
        repository_id="repo_a",
        index_id="idx_a",
        collection_name="unused",
        query_text="payment callback",
        top_k=2,
        threshold=0.3,
        head_commit="commit_a",
        retrieval_mode="hybrid",
        rerank=True,
        max_chunks_per_file=None,
    )

    assert [item.chunk_id for item in results] == ["second", "first"]
