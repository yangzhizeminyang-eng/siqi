"""模型接入配置：从环境变量读取 DeepSeek / 智谱 embedding 相关配置。

安全约定：
- API Key 只从环境变量读取，绝不写入代码、测试、文档或提交记录。
- 任何错误信息、日志都不得包含 Key；本模块只暴露 ``configured`` 布尔值与脱敏后的别名。
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_optional_int(name: str, default: int | None = None) -> int | None:
    raw = _env(name)
    if not raw or raw.lower() == "auto":
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _env_bool(name: str, default: bool = True) -> bool:
    raw = _env(name)
    if not raw:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class LLMSettings:
    provider: str
    model: str
    base_url: str
    api_key: str
    timeout_seconds: int
    max_retries: int

    @property
    def configured(self) -> bool:
        return bool(self.api_key)


@dataclass(frozen=True)
class EmbeddingSettings:
    provider: str
    model: str
    base_url: str
    api_key: str
    dimensions: int | None
    timeout_seconds: int
    max_retries: int

    @property
    def configured(self) -> bool:
        return bool(self.api_key)


def load_llm_settings() -> LLMSettings:
    return LLMSettings(
        provider=_env("LLM_PROVIDER", "deepseek"),
        model=_env("LLM_MODEL", "deepseek-chat"),
        base_url=_env("LLM_BASE_URL", "https://api.deepseek.com").rstrip("/"),
        api_key=_env("LLM_API_KEY"),
        timeout_seconds=_env_int("MODEL_TIMEOUT_SECONDS", 60),
        max_retries=_env_int("MODEL_MAX_RETRIES", 2),
    )


def load_embedding_settings() -> EmbeddingSettings:
    return EmbeddingSettings(
        provider=_env("EMBEDDING_PROVIDER", "zhipu"),
        model=_env("ZHIPU_MODEL", "embedding-3"),
        base_url=_env("ZHIPU_BASE_URL", "https://open.bigmodel.cn/api/paas/v4").rstrip("/"),
        api_key=_env("ZHIPU_API_KEY"),
        # ``auto`` must remain unresolved until the live provider returns a vector.
        # A baked-in default here can create a Milvus collection with an unverified
        # dimension and makes the real-dimension probe meaningless.
        dimensions=_env_optional_int("EMBEDDING_DIMENSIONS", None),
        timeout_seconds=_env_int("MODEL_TIMEOUT_SECONDS", 60),
        max_retries=_env_int("MODEL_MAX_RETRIES", 2),
    )


def llm_configured() -> bool:
    return load_llm_settings().configured


MODEL_TOOL_CHOICE_INCOMPATIBLE = "MODEL_TOOL_CHOICE_INCOMPATIBLE"


def probe_model_tool_choice(settings: LLMSettings | None = None) -> dict:
    """真实最小 tool-calling/structured-output 探测（硬门禁）。

    - 成功 → {"compat": "COMPATIBLE", "model": ...}
    - API 400 “Thinking mode does not support this tool_choice” 或含 tool_choice → MODEL_TOOL_CHOICE_INCOMPATIBLE
    - 其他错误 → OTHER_ERROR（绝不捕获后当成功）
    不经 deterministic/mock；真实调用配置模型。
    """
    if settings is None:
        settings = load_llm_settings()
    from pydantic import BaseModel

    class _Probe(BaseModel):
        ok: bool

    from langchain.agents import create_agent
    from langchain_openai import ChatOpenAI
    model = ChatOpenAI(model=settings.model, base_url=settings.base_url,
                       api_key=settings.api_key, temperature=0.0,
                       timeout=settings.timeout_seconds, max_retries=settings.max_retries)
    agent = create_agent(model=model, tools=[], system_prompt="只返回准确 JSON。",
                         response_format=_Probe, name="probe")
    try:
        agent.invoke({"messages": [("human", "respond with ok=true")]})
        return {"compat": "COMPATIBLE", "model": settings.model}
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        if "tool_choice" in msg or "Thinking mode" in msg:
            return {"compat": MODEL_TOOL_CHOICE_INCOMPATIBLE, "model": settings.model, "error": msg[:160]}
        return {"compat": "OTHER_ERROR", "model": settings.model, "error": msg[:160]}


def embedding_configured() -> bool:
    return load_embedding_settings().configured


def mock_fallback_enabled() -> bool:
    return _env_bool("MODEL_MOCK_FALLBACK", True)


def mask_secret(secret: str) -> str:
    """脱敏展示：只保留末尾 4 位，绝不回显完整 Key。"""
    if not secret:
        return ""
    if len(secret) <= 4:
        return "****"
    return f"****{secret[-4:]}"
