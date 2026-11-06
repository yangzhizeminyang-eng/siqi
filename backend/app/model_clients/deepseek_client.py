"""DeepSeek 文本模型客户端（OpenAI 兼容 /chat/completions）。

错误统一转 :class:`ModelClientError`，鉴权失败（401）不重试，网络/5xx/429 按
``max_retries`` 指数退避重试。
"""

from __future__ import annotations

import json
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.core import model_config
from app.model_clients.base import ModelClientError, TextModelClient, read_error_body


class DeepSeekTextClient(TextModelClient):
    provider = "deepseek"

    def __init__(self, settings: model_config.LLMSettings | None = None) -> None:
        self.settings = settings or model_config.load_llm_settings()

    def generate(self, prompt: str, *, system: str | None = None) -> str:
        if not self.settings.configured:
            raise ModelClientError(self.provider, "config", "未配置 LLM_API_KEY，无法调用 DeepSeek")
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        payload = {
            "model": self.settings.model,
            "messages": messages,
            "temperature": 0.2,
            "stream": False,
        }
        url = f"{self.settings.base_url}/chat/completions"
        last_error: ModelClientError | None = None
        for attempt in range(self.settings.max_retries + 1):
            try:
                return self._request(url, payload)
            except ModelClientError as error:
                last_error = error
                if attempt >= self.settings.max_retries or not _retryable(error.stage):
                    raise
                time.sleep(min(2 ** attempt, 4))
        assert last_error is not None
        raise last_error

    def _request(self, url: str, payload: dict) -> str:
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
            raise ModelClientError(self.provider, "network", f"DeepSeek 网络连接失败：{error.reason}")
        except TimeoutError:
            raise ModelClientError(self.provider, "timeout", "DeepSeek 请求超时")
        choices = body.get("choices") if isinstance(body, dict) else None
        if not isinstance(choices, list) or not choices:
            raise ModelClientError(self.provider, "response", "DeepSeek 响应缺少 choices")
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or not content.strip():
            raise ModelClientError(self.provider, "response", "DeepSeek 响应内容为空")
        return content.strip()

    def _http_error(self, error: HTTPError) -> ModelClientError:
        detail = read_error_body(error)
        if error.code == 401:
            return ModelClientError(self.provider, "auth", "DeepSeek 鉴权失败（HTTP 401），请检查 LLM_API_KEY")
        if error.code == 429:
            return ModelClientError(self.provider, "rate_limit", "DeepSeek 触发限流（HTTP 429）")
        if 500 <= error.code < 600:
            return ModelClientError(self.provider, "server", f"DeepSeek 服务异常（HTTP {error.code}）")
        suffix = f"：{detail}" if detail else ""
        return ModelClientError(self.provider, "http", f"DeepSeek 请求失败（HTTP {error.code}）{suffix}")


def _retryable(stage: str) -> bool:
    return stage in {"network", "timeout", "server", "rate_limit"}
