"""模型客户端抽象与统一异常。

所有模型调用失败统一抛 :class:`ModelClientError`，消息经过脱敏，绝不包含 API Key。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
import json
from urllib.error import HTTPError


class ModelClientError(Exception):
    """模型服务调用失败。``provider``/``stage`` 用于定位，``message`` 已脱敏。"""

    def __init__(self, provider: str, stage: str, message: str) -> None:
        self.provider = provider
        self.stage = stage
        self.message = message
        super().__init__(message)

    def __str__(self) -> str:  # pragma: no cover - 错误展示
        return f"[{self.provider}/{self.stage}] {self.message}"


class TextModelClient(ABC):
    """文本大模型客户端接口。"""

    provider: str = "text"

    @abstractmethod
    def generate(self, prompt: str, *, system: str | None = None) -> str:
        """返回模型生成的文本；失败抛 :class:`ModelClientError`。"""


class EmbeddingClient(ABC):
    """向量模型客户端接口。"""

    provider: str = "embedding"

    @abstractmethod
    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """批量编码文本为向量；失败抛 :class:`ModelClientError`。"""


def read_error_body(error: HTTPError) -> str:
    """读取 HTTP 错误响应体中的提示信息，失败则返回空串。"""
    try:
        body = error.read().decode("utf-8", errors="replace")
        data = json.loads(body)
        if isinstance(data, dict):
            return str(data.get("message") or data.get("error") or data.get("msg") or "")
        return ""
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return ""
