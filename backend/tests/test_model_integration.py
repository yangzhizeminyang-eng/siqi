"""真实模型接入测试套件。

覆盖：无 Key 走 mock/token-hash 兜底、DeepSeek 成功/超时/401、智谱 embedding 成功/批量、
embedding 阶段失败落到 error_stage=EMBEDDING、DeepSeek 分析字段合并且不破坏规则字段。
"""

from __future__ import annotations

import io
import json
from datetime import datetime, timezone
from urllib.error import HTTPError

import pytest

from app import main
from app.core import model_config
from app.model_clients import deepseek_client, zhipu_embedding_client
from app.model_clients.base import ModelClientError
from app.services import ai_analysis_service, embedding_service


def _fake_urlopen(body_bytes: bytes):
    class _Resp:
        def __init__(self, data: bytes) -> None:
            self._data = data

        def __enter__(self):
            return self

        def __exit__(self, *args) -> bool:
            return False

        def read(self) -> bytes:
            return self._data

    def _urlopen(request, timeout=None):
        return _Resp(body_bytes)

    return _urlopen


def _llm_settings(**overrides) -> model_config.LLMSettings:
    defaults = dict(
        provider="deepseek",
        model="deepseek-chat",
        base_url="https://api.deepseek.com",
        api_key="sk-test-key",
        timeout_seconds=5,
        max_retries=0,
    )
    defaults.update(overrides)
    return model_config.LLMSettings(**defaults)


def _embedding_settings(**overrides) -> model_config.EmbeddingSettings:
    defaults = dict(
        provider="zhipu",
        model="embedding-3",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        api_key="zhipu-test-key",
        dimensions=1024,
        timeout_seconds=5,
        max_retries=0,
    )
    defaults.update(overrides)
    return model_config.EmbeddingSettings(**defaults)


def _seed_task() -> main.TaskRecord:
    now = datetime.now(timezone.utc)
    repository = main.REPOSITORIES["repo_payment_service"]
    snapshot = main.IssueSnapshotRecord(
        title="优化列表页样式",
        body="列表页在窄屏下布局错乱，需要调整样式。",
        comments=[],
        labels=["ui"],
        raw_response={},
        fetched_at=now,
    )
    task = main.TaskRecord(
        id=f"TASK-{9000 + len(main.TASKS)}",
        version=1,
        project_id=repository.project_id,
        project_name=main.PROJECTS[repository.project_id].name,
        repository_id=repository.id,
        repository_name=repository.name,
        repository_url=repository.url,
        issue_id="ISTUB900",
        title="优化列表页样式",
        status="ANALYSIS_QUEUED",
        base_branch=repository.default_branch,
        test_command=repository.test_command,
        approval_owner_id=repository.approval_owner_id,
        approval_owner_name=repository.approval_owner_name,
        work_branch="agent/main/issue-ISTUB900-v1",
        issue_snapshot=snapshot,
        automation_state="running",
        manual_required=False,
        manual_reason="none",
        next_action="wait_analysis",
        events=[],
        created_by="user_developer",
        created_by_name="周航",
        created_at=now,
        updated_at=now,
    )
    main.TASKS[task.id] = task
    return task


# ---------- 无 Key：mock / token-hash 兜底 ----------

def test_no_key_embedding_uses_token_hash_mock():
    assert not embedding_service.is_configured()
    vectors = embedding_service.embed_texts(["支付回调记账", "hello world"])
    assert len(vectors) == 2
    assert all(len(vector) == 64 for vector in vectors)
    assert embedding_service.keyword_overlap_score("支付回调记账", "支付回调处理幂等") > 0


def test_no_key_analysis_fields_return_none():
    assert ai_analysis_service.generate_analysis_fields("anything") is None


def test_generate_analysis_falls_back_to_rule_provider(monkeypatch):
    task = _seed_task()
    monkeypatch.setattr(ai_analysis_service, "generate_analysis_fields", lambda prompt: None)
    result = main.generate_analysis(task)
    assert result.provider == "mvp-rule-based"


# ---------- DeepSeek 文本模型 ----------

def test_parse_fields_handles_markdown_fences():
    raw = '以下是分析：\n```json\n{"overview": "概述", "findings": ["f1"], "uncertainties": ["u1"]}\n```'
    fields = ai_analysis_service._parse_fields(raw)
    assert fields == {"overview": "概述", "findings": ["f1"], "uncertainties": ["u1"]}


def test_parse_fields_rejects_missing_overview():
    assert ai_analysis_service._parse_fields('{"findings": [], "uncertainties": []}') is None


def test_deepseek_generate_success(monkeypatch):
    response = {"choices": [{"message": {"content": "hello from deepseek"}}]}
    monkeypatch.setattr(deepseek_client, "urlopen", _fake_urlopen(json.dumps(response).encode("utf-8")))
    client = deepseek_client.DeepSeekTextClient(_llm_settings())
    assert client.generate("hello") == "hello from deepseek"


def test_deepseek_timeout_raises_model_error(monkeypatch):
    def _urlopen(request, timeout=None):
        raise TimeoutError("timed out")

    monkeypatch.setattr(deepseek_client, "urlopen", _urlopen)
    client = deepseek_client.DeepSeekTextClient(_llm_settings())
    with pytest.raises(ModelClientError) as error:
        client.generate("hello")
    assert error.value.provider == "deepseek"
    assert error.value.stage == "timeout"


def test_deepseek_401_raises_auth_error_without_leaking_key(monkeypatch):
    def _urlopen(request, timeout=None):
        raise HTTPError(
            "https://api.deepseek.com/chat/completions",
            401,
            "Unauthorized",
            None,
            io.BytesIO(b'{"error":{"message":"invalid api key"}}'),
        )

    monkeypatch.setattr(deepseek_client, "urlopen", _urlopen)
    client = deepseek_client.DeepSeekTextClient(_llm_settings())
    with pytest.raises(ModelClientError) as error:
        client.generate("hello")
    assert error.value.stage == "auth"
    assert "401" in error.value.message
    assert "sk-test-key" not in error.value.message


# ---------- 智谱 embedding ----------

def test_zhipu_embed_success_and_batch(monkeypatch):
    response = {"data": [{"embedding": [0.1, 0.2]}, {"embedding": [0.3, 0.4]}]}
    captured_payload = {}

    def _urlopen(request, timeout=None):
        nonlocal captured_payload
        captured_payload = json.loads(request.data.decode("utf-8"))
        return _fake_urlopen(json.dumps(response).encode("utf-8"))(request, timeout)

    monkeypatch.setattr(zhipu_embedding_client, "urlopen", _urlopen)
    client = zhipu_embedding_client.ZhipuEmbeddingClient(_embedding_settings())
    vectors = client.embed_texts(["文本一", "文本二"])
    assert vectors == [[0.1, 0.2], [0.3, 0.4]]
    assert captured_payload["dimensions"] == 1024


def test_zhipu_embed_splits_requests_at_provider_limit(monkeypatch):
    payload_sizes = []
    payload_inputs = []

    def _urlopen(request, timeout=None):
        payload = json.loads(request.data.decode("utf-8"))
        payload_sizes.append(len(payload["input"]))
        payload_inputs.extend(payload["input"])
        response = {
            "data": [
                {"embedding": [float(index)]}
                for index, _ in enumerate(payload["input"])
            ]
        }
        return _fake_urlopen(json.dumps(response).encode("utf-8"))(request, timeout)

    monkeypatch.setattr(zhipu_embedding_client, "urlopen", _urlopen)
    client = zhipu_embedding_client.ZhipuEmbeddingClient(_embedding_settings())
    texts = [f"文本-{index}" for index in range(65)]

    vectors = client.embed_texts(texts)

    assert payload_sizes == [64, 1]
    assert payload_inputs == texts
    assert len(vectors) == len(texts)


def test_zhipu_401_raises_auth_error_without_leaking_key(monkeypatch):
    def _urlopen(request, timeout=None):
        raise HTTPError(
            "https://open.bigmodel.cn/api/paas/v4/embeddings",
            401,
            "Unauthorized",
            None,
            io.BytesIO(b'{"error":{"message":"invalid key"}}'),
        )

    monkeypatch.setattr(zhipu_embedding_client, "urlopen", _urlopen)
    client = zhipu_embedding_client.ZhipuEmbeddingClient(_embedding_settings())
    with pytest.raises(ModelClientError) as error:
        client.embed_texts(["文本"])
    assert error.value.stage == "auth"
    assert "zhipu-test-key" not in error.value.message


def test_embedding_zhipu_failure_falls_back_to_mock(monkeypatch):
    monkeypatch.setattr(model_config, "embedding_configured", lambda: True)
    monkeypatch.setattr(model_config, "mock_fallback_enabled", lambda: True)

    def failing_embed(self, texts):
        raise ModelClientError("zhipu", "server", "智谱 embedding 服务异常")

    monkeypatch.setattr(zhipu_embedding_client.ZhipuEmbeddingClient, "embed_texts", failing_embed)
    vectors = embedding_service.embed_texts(["支付回调"])
    assert len(vectors) == 1
    assert len(vectors[0]) == 64


def test_embedding_zhipu_failure_raises_when_fallback_disabled(monkeypatch):
    monkeypatch.setattr(model_config, "embedding_configured", lambda: True)
    monkeypatch.setattr(model_config, "mock_fallback_enabled", lambda: False)

    def failing_embed(self, texts):
        raise ModelClientError("zhipu", "server", "智谱 embedding 服务异常")

    monkeypatch.setattr(zhipu_embedding_client.ZhipuEmbeddingClient, "embed_texts", failing_embed)
    with pytest.raises(ModelClientError) as error:
        embedding_service.embed_texts(["支付回调"])
    assert error.value.stage == "server"


# ---------- embedding 阶段失败落到 error_stage=EMBEDDING ----------

def test_embedding_stage_failure_records_error_stage(monkeypatch):
    repository = main.REPOSITORIES["repo_payment_service"]
    developer = main.USERS["user_developer"]
    monkeypatch.setattr(main, "INDEX_STAGE_SECONDS", 0)
    monkeypatch.setattr(main, "fetch_gitee_branch_head", lambda repo, base_branch=None: "a" * 40)
    monkeypatch.setattr(
        main,
        "sync_repository_snapshot",
        lambda repo, commit_sha: [main.CodeFile(path="svc.py", language="python", content="def handle():\n    pass\n")],
    )
    record = main.new_repository_index(
        repository=repository,
        commit_sha="a" * 40,
        base_branch=repository.default_branch,
        requested_by=developer,
        requested_at=datetime.now(timezone.utc),
    )

    def failing_embed(texts):
        raise RuntimeError("embedding 服务不可用")

    monkeypatch.setattr(main, "embed_chunks", failing_embed)
    while record.status in main.INDEX_RUNNING_STAGES:
        main.progress_single_repository_index(record)
    assert record.status == "FAILED"
    assert record.error_stage == "EMBEDDING"
    assert "embedding 服务不可用" in (record.error_message or "")


# ---------- DeepSeek 分析字段合并：规则字段不受影响 ----------

def test_generate_analysis_merges_llm_fields_and_keeps_rules(monkeypatch):
    task = _seed_task()
    monkeypatch.setattr(
        ai_analysis_service,
        "generate_analysis_fields",
        lambda prompt: {"overview": "LLM 概述", "findings": ["LLM 发现1"], "uncertainties": []},
    )
    result = main.generate_analysis(task)
    assert result.provider == "deepseek"
    assert result.overview == "LLM 概述"
    assert result.findings == ["LLM 发现1"]
    assert result.suggested_risk_level == "medium"
    assert len(result.decision_summary) == 3
    assert result.sources
    assert isinstance(result, main.AnalysisResult)
