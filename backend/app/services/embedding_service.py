"""Embedding 服务：真实智谱 embedding-3 与本地 token-hash 兜底。

- 配置 ``ZHIPU_API_KEY`` 时走智谱 embedding-3，返回维度由 ``EMBEDDING_DIMENSIONS`` 决定。
- 未配置时走确定性 token-hash 兜底（64 维），保证无 Key 时本地测试与验收桩可运行。
- 检索打分与向量模式对齐：真实向量用余弦相似度；兜底用查询词覆盖比例（关键词重叠）。
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path

from app.core import model_config
from app.core import runtime_config
from app.model_clients.base import ModelClientError
from app.model_clients.zhipu_embedding_client import ZhipuEmbeddingClient

_TOKEN_RE = re.compile(r"[a-z0-9_]+|[一-龥]")
_MOCK_EMBEDDING_DIM = 64
_LAST_REAL_EMBEDDING_DIMENSION: int | None = None
_REAL_EMBEDDING_CACHE_VERSION = "v1"
_REAL_EMBEDDING_CACHE: dict[str, list[float]] = {}


def is_configured() -> bool:
    return model_config.embedding_configured()


def _tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def mock_embed_texts(texts: list[str]) -> list[list[float]]:
    """确定性 token-hash 向量（64 维），与 MVP 原实现一致。"""
    vectors: list[list[float]] = []
    for text in texts:
        counts: dict[str, int] = {}
        for token in _tokens(text):
            counts[token] = counts.get(token, 0) + 1
        total = sum(counts.values()) or 1
        vector = [0.0] * _MOCK_EMBEDDING_DIM
        for token, count in counts.items():
            bucket = int(hashlib.md5(token.encode("utf-8")).hexdigest()[:8], 16) % _MOCK_EMBEDDING_DIM
            vector[bucket] += count / total
        vectors.append(vector)
    return vectors


def _cache_root() -> Path:
    return runtime_config.load_runtime_settings().data_path / "embedding-cache"


def _cache_namespace(settings: model_config.EmbeddingSettings) -> str:
    raw = json.dumps(
        {
            "v": _REAL_EMBEDDING_CACHE_VERSION,
            "provider": settings.provider,
            "model": settings.model,
            "base_url": settings.base_url,
            "dimensions": settings.dimensions,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _cache_key(settings: model_config.EmbeddingSettings, text: str) -> str:
    raw = json.dumps(
        {"namespace": _cache_namespace(settings), "text": text},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _cache_path(settings: model_config.EmbeddingSettings, key: str) -> Path:
    return _cache_root() / _cache_namespace(settings) / f"{key}.json"


def _read_cached_vector(settings: model_config.EmbeddingSettings, text: str) -> list[float] | None:
    key = _cache_key(settings, text)
    if key in _REAL_EMBEDDING_CACHE:
        return list(_REAL_EMBEDDING_CACHE[key])
    path = _cache_path(settings, key)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    vector = payload.get("embedding") if isinstance(payload, dict) else None
    if not isinstance(vector, list) or not all(isinstance(v, (int, float)) for v in vector):
        return None
    floats = [float(v) for v in vector]
    _REAL_EMBEDDING_CACHE[key] = floats
    return list(floats)


def _write_cached_vector(settings: model_config.EmbeddingSettings, text: str, vector: list[float]) -> None:
    key = _cache_key(settings, text)
    floats = [float(v) for v in vector]
    _REAL_EMBEDDING_CACHE[key] = floats
    path = _cache_path(settings, key)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"embedding": floats}, separators=(",", ":")), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        # 缓存只是确定性/性能优化，写失败不影响主检索链路。
        pass


def _embed_real_texts_cached(texts: list[str]) -> list[list[float]]:
    settings = model_config.load_embedding_settings()
    cached: dict[int, list[float]] = {}
    missing: list[tuple[int, str]] = []
    for index, text in enumerate(texts):
        vector = _read_cached_vector(settings, text)
        if vector is None:
            missing.append((index, text))
        else:
            cached[index] = vector
    if missing:
        fetched = ZhipuEmbeddingClient(settings).embed_texts([text for _, text in missing])
        for (index, text), vector in zip(missing, fetched):
            floats = [float(v) for v in vector]
            cached[index] = floats
            _write_cached_vector(settings, text, floats)
    return [cached[index] for index in range(len(texts))]


def embed_texts(texts: list[str]) -> list[list[float]]:
    if not is_configured():
        return mock_embed_texts(list(texts))
    real_texts = list(texts)
    try:
        return _embed_real_texts_cached(real_texts)
    except ModelClientError:
        if model_config.mock_fallback_enabled():
            return mock_embed_texts(real_texts)
        raise


def embed_query(text: str) -> list[float]:
    return embed_texts([text])[0]


def probe_embedding_dimension() -> int:
    """调用真实 embedding 服务并返回实际维度；不允许用 mock 向量充当探测结果。"""
    if not is_configured():
        raise RuntimeError("未配置 ZHIPU_API_KEY，无法探测真实 embedding 维度")
    global _LAST_REAL_EMBEDDING_DIMENSION
    settings = model_config.load_embedding_settings()
    vectors = ZhipuEmbeddingClient(settings).embed_texts(["enterprise-rag-dimension-probe"])
    if len(vectors) != 1 or not vectors[0]:
        raise RuntimeError("真实 embedding 返回为空，无法确认向量维度")
    actual = len(vectors[0])
    if settings.dimensions and actual != settings.dimensions:
        raise RuntimeError(
            f"EMBEDDING_DIMENSIONS={settings.dimensions} 与真实 embedding 返回维度 {actual} 不一致"
        )
    _LAST_REAL_EMBEDDING_DIMENSION = actual
    return actual


def last_real_embedding_dimension() -> int | None:
    return _LAST_REAL_EMBEDDING_DIMENSION


def keyword_overlap_score(query_text: str, content: str) -> float:
    """兜底相似度：查询词覆盖比例（0~1）。"""
    query_tokens = set(_tokens(query_text))
    if not query_tokens:
        return 0.0
    content_tokens = set(_tokens(content))
    if not content_tokens:
        return 0.0
    return len(query_tokens & content_tokens) / len(query_tokens)


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    norm_left = math.sqrt(sum(a * a for a in left))
    norm_right = math.sqrt(sum(b * b for b in right))
    if norm_left == 0.0 or norm_right == 0.0:
        return 0.0
    return dot / (norm_left * norm_right)
