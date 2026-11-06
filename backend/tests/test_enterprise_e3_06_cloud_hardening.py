from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app import main
from app.core import model_config
from app.model_clients import zhipu_embedding_client
from app.retrieval import llamaindex_backend
from app.services import embedding_service
from evaluation.enterprise_rag import health_middle


def test_enterprise_collection_name_isolated_from_team(monkeypatch):
    monkeypatch.setenv("APP_ENV", "enterprise")
    monkeypatch.setenv("MILVUS_COLLECTION_PREFIX", "enterprise")
    assert main.index_collection_name("idx_hm_123") == "enterprise_code_idx_hm_123"

    monkeypatch.setenv("APP_ENV", "team")
    monkeypatch.setenv("MILVUS_COLLECTION_PREFIX", "team")
    assert main.index_collection_name("idx_team_123") == "team_code_idx_team_123"


def test_real_embedding_dimension_probe_rejects_missing_key(monkeypatch):
    monkeypatch.setattr(model_config, "embedding_configured", lambda: False)
    with pytest.raises(RuntimeError, match="ZHIPU_API_KEY"):
        embedding_service.probe_embedding_dimension()


def test_real_embedding_dimension_probe_returns_api_dimension(monkeypatch):
    monkeypatch.setattr(model_config, "embedding_configured", lambda: True)
    monkeypatch.setattr(
        model_config,
        "load_embedding_settings",
        lambda: model_config.EmbeddingSettings(
            provider="zhipu",
            model="embedding-3",
            base_url="https://example.invalid",
            api_key="configured-only-for-test",
            dimensions=3,
            timeout_seconds=5,
            max_retries=0,
        ),
    )
    monkeypatch.setattr(
        zhipu_embedding_client.ZhipuEmbeddingClient,
        "embed_texts",
        lambda self, texts: [[0.1, 0.2, 0.3] for _ in texts],
    )
    assert embedding_service.probe_embedding_dimension() == 3


def test_auto_embedding_dimension_is_resolved_from_probe(monkeypatch):
    monkeypatch.setenv("EMBEDDING_DIMENSIONS", "auto")
    monkeypatch.setattr(model_config, "embedding_configured", lambda: True)
    monkeypatch.setattr(
        model_config,
        "load_embedding_settings",
        lambda: model_config.EmbeddingSettings(
            provider="zhipu",
            model="embedding-3",
            base_url="https://example.invalid",
            api_key="configured-only-for-test",
            dimensions=None,
            timeout_seconds=5,
            max_retries=0,
        ),
    )
    monkeypatch.setattr(
        zhipu_embedding_client.ZhipuEmbeddingClient,
        "embed_texts",
        lambda self, texts: [[0.1, 0.2, 0.3] for _ in texts],
    )
    assert embedding_service.probe_embedding_dimension() == 3
    assert llamaindex_backend.embedding_dim() == 3


def test_embedding_settings_auto_does_not_bake_in_default_dimension(monkeypatch):
    monkeypatch.setenv("EMBEDDING_DIMENSIONS", "auto")
    settings = model_config.load_embedding_settings()
    assert settings.dimensions is None


def test_real_embedding_cache_reuses_vectors_across_calls(monkeypatch, tmp_path):
    """同一真实模型/文本应复用持久向量，避免重建索引时结果漂移。"""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(model_config, "embedding_configured", lambda: True)
    monkeypatch.setattr(
        model_config,
        "load_embedding_settings",
        lambda: model_config.EmbeddingSettings(
            provider="zhipu",
            model="embedding-3",
            base_url="https://example.invalid",
            api_key="configured-only-for-test",
            dimensions=3,
            timeout_seconds=5,
            max_retries=0,
        ),
    )
    calls: list[list[str]] = []

    def fake_embed(self, texts):
        calls.append(list(texts))
        return [[float(len(text)), 0.1, 0.2] for text in texts]

    monkeypatch.setattr(
        zhipu_embedding_client.ZhipuEmbeddingClient,
        "embed_texts",
        fake_embed,
    )

    first = embedding_service.embed_texts(["same text", "other text"])
    second = embedding_service.embed_texts(["same text", "other text"])

    assert second == first
    assert calls == [["same text", "other text"]]


def test_zhipu_auto_dimension_omits_dimensions_parameter(monkeypatch):
    captured: dict = {}
    settings = model_config.EmbeddingSettings(
        provider="zhipu",
        model="embedding-3",
        base_url="https://example.invalid",
        api_key="configured-only-for-test",
        dimensions=None,
        timeout_seconds=5,
        max_retries=0,
    )

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b'{"data":[{"embedding":[0.1,0.2,0.3]}]}'

    def _urlopen(request, timeout=None):
        captured.update(__import__("json").loads(request.data.decode("utf-8")))
        return _Response()

    monkeypatch.setattr(zhipu_embedding_client, "urlopen", _urlopen)
    assert zhipu_embedding_client.ZhipuEmbeddingClient(settings).embed_texts(["probe"]) == [[0.1, 0.2, 0.3]]
    assert "dimensions" not in captured


def test_vector_results_use_deterministic_tie_breaking():
    results = [
        llamaindex_backend.RetrievedChunk(
            chunk_id="b",
            file_path="backend/z.py",
            start_line=10,
            end_line=12,
            language="python",
            symbol_name="b",
            content="",
            score=0.5,
            commit_sha="c",
            index_id="i",
            fresh=True,
        ),
        llamaindex_backend.RetrievedChunk(
            chunk_id="a",
            file_path="backend/a.py",
            start_line=10,
            end_line=12,
            language="python",
            symbol_name="a",
            content="",
            score=0.5,
            commit_sha="c",
            index_id="i",
            fresh=True,
        ),
    ]

    ordered = llamaindex_backend._stable_sort_retrieved(results)

    assert [item.chunk_id for item in ordered] == ["a", "b"]


def test_cli_report_includes_embedding_identity(monkeypatch):
    from scripts import evaluate_enterprise_rag as cli

    monkeypatch.setattr(
        model_config,
        "load_embedding_settings",
        lambda: model_config.EmbeddingSettings(
            provider="zhipu",
            model="embedding-3",
            base_url="https://example.invalid",
            api_key="configured-only-for-test",
            dimensions=None,
            timeout_seconds=5,
            max_retries=0,
        ),
    )
    monkeypatch.setattr(llamaindex_backend, "embedding_dim", lambda: 2048)

    report = cli._attach_embedding_identity({})

    assert report["embedding_provider"] == "zhipu"
    assert report["embedding_model"] == "embedding-3"
    assert report["embedding_dimension"] == 2048


def test_health_middle_chunks_use_repo_head_and_enterprise_collection(monkeypatch, tmp_path):
    repo = tmp_path / "health-middle"
    (repo / "backend" / "app").mkdir(parents=True)
    (repo / "backend" / "app" / "example.py").write_text(
        "def health_check():\n    return 'ok'\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.email=test@example.com",
            "-c",
            "user.name=Test",
            "commit",
            "-q",
            "-m",
            "seed",
        ],
        check=True,
    )
    commit = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    monkeypatch.setenv("APP_ENV", "enterprise")
    monkeypatch.setenv("MILVUS_COLLECTION_PREFIX", "enterprise")
    monkeypatch.setenv("USE_MILVUS", "false")

    chunks = health_middle.build_real_index(repo, persist_vector=False)

    assert chunks
    assert {chunk.commit_sha for chunk in chunks} == {commit}
    assert {chunk.index_id for chunk in chunks} == {f"idx_health_middle_{commit[:7]}"}
    assert {chunk.milvus_collection for chunk in chunks} == {
        f"enterprise_code_idx_health_middle_{commit[:7]}"
    }
