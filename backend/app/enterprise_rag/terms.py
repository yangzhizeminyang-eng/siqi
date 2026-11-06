"""企业版 E3-02 Query Rewrite / 多路 Query Expansion。

- 复用 app/retrieval/query_expansion.expand_query（原始 Query 始终在首位，LLM 优先、失败确定性回退）。
- 额外补充技术术语变体（点分/路径/下划线/驼峰桥接），提升函数名/错误码/接口路径/配置 key 的 BM25 精确覆盖。
- 用户 query 一律视为数据：不做指令执行；只输出候选 string。
- 限制变体数量、单条长度与总字符数，去重、清理空串，输出稳定可复现。
"""

from __future__ import annotations

import re

from app.core import model_config  # noqa: F401  # 供测试 monkeypatch llm_configured
from app.model_clients.deepseek_client import DeepSeekTextClient  # noqa: F401  # 供测试 monkeypatch
from app.retrieval.query_expansion import expand_query

_DEFAULT_MAX_VARIANTS = 3
_DEFAULT_MAX_VARIANT_LEN = 200
_DEFAULT_TOTAL_CHAR_CAP = 1200

# 代码术语：函数/类/错误码/点分路径/接口路径/配置 key/下划线符号
_CODE_TOKEN_RE = re.compile(
    r"[A-Za-z_$][A-Za-z0-9_$]*(?:[./:-][A-Za-z0-9_$.-]+)*"
    r"|[A-Z][A-Z0-9_]{2,}"
    r"|\d{3,}"
)


def _normalize(value: str) -> str:
    return " ".join((value or "").split()).strip()


def _split_identifier(token: str) -> list[str]:
    """按 点/斜杠/连字符/下划线 拆分，并处理驼峰分段。"""
    parts = re.split(r"[./:._-]+", token)
    words: list[str] = []
    for part in parts:
        if not part:
            continue
        # 驼峰分段：handlePaymentCallback -> handle / Payment / Callback
        camel = re.split(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", part)
        for seg in camel:
            if seg:
                words.append(seg)
    return words


def technical_variants(query_text: str) -> list[str]:
    """从原始 query 提取技术术语变体（含点分/下划线桥接），去重、稳定排序。"""
    original = _normalize(query_text)
    if not original:
        return []
    variants: list[str] = []
    seen: set[str] = {original.lower()}
    for token in _CODE_TOKEN_RE.findall(original):
        token = token.strip()
        if len(token) < 2:
            continue
        low = token.lower()
        # 先派生点分/下划线/驼峰桥接变体（即便 token==original 也要生成，否则单符号桥接丢失）
        words = _split_identifier(token)
        alternates: list[str] = []
        if len(words) > 1:
            underscore = "_".join(words).lower()
            joined = "".join(words).lower()
            dotted = ".".join(words).lower()
            seen_local = set()
            for alt in (underscore, joined, dotted):
                if alt and alt.lower() not in seen and alt.lower() not in seen_local:
                    seen_local.add(alt.lower())
                    alternates.append(alt)
        if low not in seen:
            seen.add(low)
            variants.append(token)
        for alt in alternates:
            seen.add(alt.lower())
            variants.append(alt)
    return variants


def build_query_variants(
    query_text: str,
    *,
    max_queries: int = _DEFAULT_MAX_VARIANTS,
    allow_llm: bool = False,
    enabled: bool = True,
    max_variant_len: int = _DEFAULT_MAX_VARIANT_LEN,
    total_char_cap: int = _DEFAULT_TOTAL_CHAR_CAP,
) -> list[str]:
    """返回 [原始 query, ...扩展变体]。原始 query 恒在首位；去重 + 数量/长度/字符总量受限。"""
    original = _normalize(query_text)
    if not original:
        return []
    if not enabled:
        return [original[:max_variant_len]]

    # 复用 query_expansion：LLM 优先（模型不可用/超时/非法输出自动回退确定性）
    base = expand_query(original, max_queries=max_queries, use_llm=allow_llm)
    technical = technical_variants(original)

    collected: list[str] = []
    seen: set[str] = set()
    for value in [*base, *technical]:
        candidate = _normalize(value)[:max_variant_len]
        if not candidate or candidate.lower() in seen:
            continue
        seen.add(candidate.lower())
        collected.append(candidate)
        if len(collected) >= max_queries:
            break
    if not collected:
        collected = [original[:max_variant_len]]

    # 原始 query 必须排第一（replace 首位为规范化原文，去重）
    if collected and collected[0].lower() != original.lower():
        collected = [original[:max_variant_len], *[v for v in collected if v.lower() != original.lower()]]

    # 总字符上限（保留靠前、原 query 优先）
    total = 0
    bounded: list[str] = []
    for value in collected[:max_queries]:
        total += len(value)
        if total > total_char_cap:
            break
        bounded.append(value)
    return bounded or [original[:max_variant_len]]