from types import SimpleNamespace

import pytest

from app.retrieval.evaluation import (
    RetrievalEvalCase,
    evaluate_retrieval,
    load_eval_cases,
    load_eval_dataset,
)


def _result(chunk_id: str, file_path: str):
    return SimpleNamespace(chunk_id=chunk_id, file_path=file_path)


def test_evaluate_retrieval_computes_recall_precision_mrr_and_ndcg():
    cases = [
        RetrievalEvalCase("case-a", "payment", expected_file_paths=("payment.py", "ledger.py")),
        RetrievalEvalCase("case-b", "refund", expected_chunk_ids=("refund-chunk",)),
    ]
    results = {
        "payment": [_result("p1", "payment.py"), _result("x", "other.py"), _result("l1", "ledger.py")],
        "refund": [_result("x", "other.py"), _result("refund-chunk", "refund.py")],
    }

    metrics = evaluate_retrieval(cases, lambda query, top_k: results[query][:top_k], ks=(1, 3))

    assert metrics.total_cases == 2
    assert metrics.recall_at == {"1": 0.25, "3": 1.0}
    assert metrics.precision_at == {"1": 0.5, "3": 0.5}
    assert metrics.mrr == 0.75
    assert metrics.ndcg_at["1"] == 0.5
    assert metrics.ndcg_at["3"] == pytest.approx(0.775325, abs=0.000001)
    assert metrics.cases[1].first_relevant_rank == 2


def test_load_eval_cases_validates_dataset(tmp_path):
    dataset = tmp_path / "cases.json"
    dataset.write_text(
        '{"cases":[{"id":"one","query":"find callback","expected_file_paths":["callback.py"]}]}',
        encoding="utf-8",
    )

    cases = load_eval_cases(dataset)

    assert cases == [
        RetrievalEvalCase("one", "find callback", expected_file_paths=("callback.py",)),
    ]


def test_load_eval_cases_rejects_unlabeled_case(tmp_path):
    dataset = tmp_path / "bad.json"
    dataset.write_text('{"cases":[{"id":"one","query":"find callback"}]}', encoding="utf-8")

    with pytest.raises(ValueError, match="预期相关文件/chunk"):
        load_eval_cases(dataset)


def test_load_eval_dataset_binds_cases_to_a_repository_index_version(tmp_path):
    dataset = tmp_path / "bound-cases.json"
    dataset.write_text(
        """{
          "name": "payment-service-baseline",
          "repository_id": "repo_payment",
          "index_id": "idx_payment",
          "commit_sha": "abc123",
          "cases": [
            {"id": "one", "query": "find callback", "expected_file_paths": ["callback.py"]}
          ]
        }""",
        encoding="utf-8",
    )

    loaded = load_eval_dataset(dataset)

    assert loaded.name == "payment-service-baseline"
    assert loaded.repository_id == "repo_payment"
    assert loaded.index_id == "idx_payment"
    assert loaded.commit_sha == "abc123"
    assert loaded.cases == (
        RetrievalEvalCase("one", "find callback", expected_file_paths=("callback.py",)),
    )


def test_load_eval_dataset_rejects_partial_version_binding(tmp_path):
    dataset = tmp_path / "partial-cases.json"
    dataset.write_text(
        '{"repository_id":"repo_payment","cases":[{"id":"one","query":"find callback","expected_file_paths":["callback.py"]}]}',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="同时提供"):
        load_eval_dataset(dataset)


def test_evaluate_retrieval_counts_multiple_chunks_of_one_file_once():
    case = RetrievalEvalCase("case-a", "settings", expected_file_paths=("config.py",))
    results = [
        _result("config-1", "config.py"),
        _result("config-2", "config.py"),
        _result("other", "other.py"),
    ]

    metrics = evaluate_retrieval([case], lambda _query, top_k: results[:top_k], ks=(3,))

    assert metrics.recall_at == {"3": 1.0}
    assert metrics.precision_at == {"3": pytest.approx(1 / 3)}
    assert metrics.ndcg_at == {"3": 1.0}
