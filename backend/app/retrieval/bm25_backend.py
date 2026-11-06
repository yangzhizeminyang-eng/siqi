"""轻量 BM25 关键词召回。

该模块只负责对当前索引快照中的 chunks 做 lexical ranking，不依赖数据库或 LlamaIndex。
代码路径和符号名会重复加权，帮助精确匹配函数名、错误码和文件名。
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Sequence

_TOKEN_RE = re.compile(r"[a-z0-9_]+|[一-龥]")


@dataclass(frozen=True)
class BM25Result:
    chunk: Any
    score: float
    rank: int


def tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall((text or "").lower())


def _chunk_text(chunk: Any) -> str:
    file_path = str(getattr(chunk, "file_path", "") or "")
    symbol_name = str(getattr(chunk, "symbol_name", "") or "")
    content = str(getattr(chunk, "content", "") or "")
    # 文件路径和符号名是代码检索中的高价值信号，重复出现相当于字段 boost。
    return " ".join([file_path, file_path, symbol_name, symbol_name, content])


def retrieve_bm25(
    chunks: Sequence[Any],
    query_text: str,
    *,
    top_k: int,
    k1: float = 1.2,
    b: float = 0.75,
    symbol_boost: float = 0.0,
) -> list[BM25Result]:
    """返回正分 BM25 候选，结果按分数降序排列。

    symbol_boost>0 时：若 chunk.symbol_name 与某个 query token 完全一致（函数/类定义命中），
    对其分数加权，让“定义块”优先于“仅引用该符号的调用块”。默认 0.0（不影响团队版既有行为）。
    """
    if top_k <= 0:
        return []
    query_tokens = tokenize(query_text)
    if not query_tokens:
        return []

    documents = [(chunk, tokenize(_chunk_text(chunk))) for chunk in chunks]
    non_empty = [(chunk, tokens) for chunk, tokens in documents if tokens]
    if not non_empty:
        return []

    document_frequency: dict[str, int] = {}
    for _, tokens in non_empty:
        for token in set(tokens):
            document_frequency[token] = document_frequency.get(token, 0) + 1
    total_documents = len(non_empty)
    average_length = sum(len(tokens) for _, tokens in non_empty) / total_documents
    query_terms = set(query_tokens)
    exact_symbols = set(query_tokens) if symbol_boost > 0.0 else set()

    scored: list[tuple[float, Any]] = []
    for chunk, tokens in non_empty:
        term_frequency: dict[str, int] = {}
        for token in tokens:
            term_frequency[token] = term_frequency.get(token, 0) + 1
        document_length = len(tokens)
        score = 0.0
        for term in query_terms:
            frequency = term_frequency.get(term, 0)
            if not frequency:
                continue
            df = document_frequency.get(term, 0)
            idf = math.log(1.0 + (total_documents - df + 0.5) / (df + 0.5))
            denominator = frequency + k1 * (1.0 - b + b * document_length / average_length)
            score += idf * (frequency * (k1 + 1.0) / denominator)
        if score > 0.0:
            if exact_symbols:
                symbol = str(getattr(chunk, "symbol_name", "") or "").strip().lower()
                if symbol in exact_symbols:
                    score *= (1.0 + symbol_boost)
            scored.append((score, chunk))

    scored.sort(
        key=lambda item: (
            -item[0],
            str(getattr(item[1], "file_path", "") or ""),
            int(getattr(item[1], "start_line", 0) or 0),
            str(getattr(item[1], "chunk_id", "") or ""),
        )
    )
    return [
        BM25Result(chunk=chunk, score=score, rank=rank)
        for rank, (score, chunk) in enumerate(scored[:top_k], start=1)
    ]
