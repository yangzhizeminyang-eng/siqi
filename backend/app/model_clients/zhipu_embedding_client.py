"""智谱 embedding-3 向量模型客户端（OpenAI 兼容 /embeddings）。"""

from __future__ import annotations

import json
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.core import model_config
from app.model_clients.base import EmbeddingClient, ModelClientError, read_error_body

_MAX_INPUTS_PER_REQUEST = 64


class ZhipuEmbeddingClient(EmbeddingClient):
    provider = "zhipu"

    def __init__(self, settings: model_config.EmbeddingSettings | None = None) -> None:
        self.settings = settings or model_config.load_embedding_settings()

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not self.settings.configured:
            raise ModelClientError(self.provider, "config", "未配置 ZHIPU_API_KEY，无法调用智谱 embedding")
        if not texts:
            return []
        url = f"{self.settings.base_url}/embeddings"
        vectors: list[list[float]] = []
        for start in range(0, len(texts), _MAX_INPUTS_PER_REQUEST):
            batch = list(texts[start : start + _MAX_INPUTS_PER_REQUEST])
            payload = {"model": self.settings.model, "input": batch}
            if self.settings.dimensions is not None:
                payload["dimensions"] = self.settings.dimensions
            vectors.extend(self._request_with_retries(url, payload, len(batch)))
        return vectors

    def _request_with_retries(self, url: str, payload: dict, expected: int) -> list[list[float]]:
        last_error: ModelClientError | None = None
        for attempt in range(self.settings.max_retries + 1):
            try:
                return self._request(url, payload, expected)
            except ModelClientError as error:
                last_error = error
                if attempt >= self.settings.max_retries or not _retryable(error.stage):
                    raise
                time.sleep(min(2 ** attempt, 4))
        assert last_error is not None
        raise last_error

    def _request(self, url: str, payload: dict, expected: int) -> list[list[float]]:
        request = Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.settings.api_key}",
                "User-Agent": "enterprise-agent-platform-mvp",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.settings.timeout_seconds) as response:
                body = json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            raise self._http_error(error)
        except URLError as error:
            raise ModelClientError(self.provider, "network", f"智谱 embedding 网络连接失败：{error.reason}")
        except TimeoutError:
            raise ModelClientError(self.provider, "timeout", "智谱 embedding 请求超时")
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, list) or len(data) != expected:
            raise ModelClientError(self.provider, "response", f"智谱 embedding 返回条数异常（期望 {expected}）")
        vectors: list[list[float]] = []
        for item in data:
            embedding = item.get("embedding") if isinstance(item, dict) else None
            if not isinstance(embedding, list) or not all(isinstance(v, (int, float)) for v in embedding):
                raise ModelClientError(self.provider, "response", "智谱 embedding 返回向量格式异常")
            vectors.append([float(v) for v in embedding])
        return vectors

    def _http_error(self, error: HTTPError) -> ModelClientError:
        detail = read_error_body(error)
        if error.code == 401:
            return ModelClientError(self.provider, "auth", "智谱 embedding 鉴权失败（HTTP 401），请检查 ZHIPU_API_KEY")
        if error.code == 429:
            return ModelClientError(self.provider, "rate_limit", "智谱 embedding 触发限流（HTTP 429）")
        if 500 <= error.code < 600:
            return ModelClientError(self.provider, "server", f"智谱 embedding 服务异常（HTTP {error.code}）")
        suffix = f"：{detail}" if detail else ""
        return ModelClientError(self.provider, "http", f"智谱 embedding 请求失败（HTTP {error.code}）{suffix}")


def _retryable(stage: str) -> bool:
    return stage in {"network", "timeout", "server", "rate_limit"}
