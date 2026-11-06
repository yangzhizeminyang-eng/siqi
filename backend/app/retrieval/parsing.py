"""LlamaIndex 代码解析 / 切片 / 元数据增强 seam（解析·切片主链 = LlamaIndex，非自定义行窗口）。

归属口径（诚实）：
- **解析 + 切片 = LlamaIndex**：CodeFile → `llama_index.core.Document`（metadata: repository_id/index_id/file_path/language/commit_sha）→
  `CodeSplitter`（代码，tree-sitter；不可用则显式回退 `TokenTextSplitter` 并告警）或 `SentenceSplitter`（markdown/文本）→ `TextNode` 文本序列。
- **业务元数据增强 = 本模块 `CodeMetadataAdapter`**：用原文件行序把每个切片回填 `start_line/end_line/symbol_name/chunk_index/checksum`，
  拼装成下游 `CodeChunkRecord`（行锚定、确定性，召回与来源展示依赖它）。
- **业务过滤（不属于 RAG parser 主链路）保留**：`path_blocked`（.git/node_modules/dist/build/.env/仓库 blocked_paths）+ 二进制过滤。
- 节点化 / 入库 / 检索仍走 LlamaIndex（`llamaindex_backend.build_nodes_from_chunks → TextNode → VectorStoreIndex`）。
- 依赖边界：本模块不 import `main` 的切片逻辑/常量；窗口/大小参数来自 `parsing_config.py`；仅懒加载 `main.CodeChunkRecord`。
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
from typing import Any

from app.retrieval.parsing_config import (
    INDEX_FILE_LINE_LIMIT,
    INDEX_FILE_SIZE_LIMIT,
    INDEX_WINDOW_OVERLAP_LINES,
    INDEX_TARGET_WINDOW_LINES,
    MAX_CHUNK_CHARS,
)

logger = logging.getLogger("backend.rag-parsing")

# 二进制/不可解析扩展（配合 path_blocked 过滤）
BINARY_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".woff", ".woff2", ".ttf", ".eot",
    ".pdf", ".zip", ".gz", ".jar", ".class", ".pyc", ".so", ".dll", ".exe", ".bin", ".lock",
}

# 用 LlamaIndex CodeSplitter（tree-sitter）支持的语言 → Language 名映射（subset）
_CODELANG_MAP = {
    "python": "python", "javascript": "javascript", "js": "javascript",
    "typescript": "typescript", "ts": "typescript", "tsx": "typescript", "jsx": "javascript",
    "go": "go", "golang": "go", "java": "java", "ruby": "ruby", "rust": "rust",
    "c": "c", "cpp": "cpp", "csharp": "c_sharp", "php": "php", "sql": "sql",
    "bash": "bash", "shell": "bash", "kotlin": "kotlin", "swift": "swift",
    "python3": "python", "html": "html", "css": "css", "scss": "css", "less": "css",
}

_MARKDOWN_LANGS = {"markdown", "md", "text", "txt", "text/plain", "yml", "yaml", "json", "toml", "xml", "proto"}


def extract_symbol_name(content: str) -> str | None:
    """业务元数据增强：从切片/原文提取符号名（def/class/func/function/模块级常量）。"""
    match = re.search(r"^\s*(?:async\s+def|def|class|func|function|public\s+(?:class|static|async)?\s*\w+\s+[\w]+)\s+([A-Za-z_$][\w$]*)", content, re.MULTILINE)
    if match:
        return match.group(1)
    match = re.search(r"^\s*([A-Za-z_$][\w$]*)\s*=\s*(?:function|const|class)\b", content, re.MULTILINE)
    return match.group(1) if match else None


def path_blocked(path: str, blocked_paths: list[str]) -> bool:
    """业务过滤（非 RAG parser 主链路）：二进制 / .git / node_modules·dist·build / .env / 仓库 blocked_paths。"""
    normalized = path.strip().replace("\\", "/")
    if normalized.startswith("./"):
        normalized = normalized[2:]
    normalized = normalized.lstrip("/")
    extension = os.path.splitext(normalized)[1].lower()
    lowered = normalized.lower()
    if extension in BINARY_EXTENSIONS:
        return True
    if "/.git/" in f"/{lowered}" or lowered.startswith(".git/") or lowered == ".git":
        return True
    for segment in ("node_modules/", "/dist/", "/build/"):
        if f"/{lowered}".find(segment) >= 0:
            return True
    if lowered.endswith(".env") or lowered.startswith(".env.") or lowered == ".env":
        return True
    for pattern in blocked_paths:
        trimmed = pattern.strip()
        if not trimmed:
            continue
        if trimmed.startswith(".") or trimmed.endswith("/"):
            if lowered.startswith(trimmed.lower()) or f"/{lowered}".find(trimmed.lower()) >= 0:
                return True
        elif f"/{lowered}".find(f"/{trimmed.lower()}") >= 0:
            return True
    return False


def _is_markdown(language: str, path: str) -> bool:
    lang = (language or "").lower()
    if lang in _MARKDOWN_LANGS:
        return True
    return os.path.splitext(path)[1].lower() in {".md", ".mdx", ".txt", ".json", ".yml", ".yaml", ".toml", ".xml", ".proto"}


def _build_document(file: Any, *, repository_id: str, index_id: str, commit_sha: str):
    from llama_index.core import Document

    return Document(text=file.content, metadata={
        "repository_id": repository_id,
        "index_id": index_id,
        "file_path": file.path,
        "language": file.language,
        "commit_sha": commit_sha,
    })


def _pick_splitter(language: str, path: str):
    """选 LlamaIndex NodeParser：代码→CodeSplitter(tree-sitter)，不可用→TokenTextSplitter；markdown/文本→SentenceSplitter。"""
    from llama_index.core.node_parser import SentenceSplitter, TokenTextSplitter

    if _is_markdown(language, path):
        return SentenceSplitter(chunk_size=1600, chunk_overlap=200)
    code_lang = _CODELANG_MAP.get((language or "").lower()) or _CODELANG_MAP.get(os.path.splitext(path)[1].lstrip(".").lower())
    if code_lang:
        try:
            from llama_index.core.node_parser import CodeSplitter

            return CodeSplitter(language=code_lang, chunk_lines=INDEX_TARGET_WINDOW_LINES,
                                chunk_lines_overlap=INDEX_WINDOW_OVERLAP_LINES, max_chars=MAX_CHUNK_CHARS)
        except Exception as error:  # noqa: BLE001 - tree-sitter 语法不可用/下载失败时显式回退 NodeParser
            logger.warning("[rag-parsing] CodeSplitter(tree-sitter) 对 %s 不可用（%s），代用 TokenTextSplitter", code_lang, error)
    return TokenTextSplitter(chunk_size=600, chunk_overlap=60, separator="\n")


def _split_to_texts(document: Any, splitter) -> list[str]:
    try:
        nodes = splitter.get_nodes_from_documents([document])
        return [getattr(n, "text", str(n)) for n in nodes]
    except Exception as error:  # noqa: BLE001 - CodeSplitter(tree-sitter) 下载/解析失败时回退 TokenTextSplitter(NodeParser)
        from llama_index.core.node_parser import TokenTextSplitter

        logger.warning("[rag-parsing] %s 切片失败（%s），代用 TokenTextSplitter", type(splitter).__name__, error)
        fallback = TokenTextSplitter(chunk_size=600, chunk_overlap=60, separator="\n")
        nodes = fallback.get_nodes_from_documents([document])
        return [getattr(n, "text", str(n)) for n in nodes]


def _assign_line_ranges(chunks_texts: list[str], original_lines: list[str]) -> list[tuple[int, int]]:
    """metadata adapter：用原文件行序回填每个切片的 [start_line, end_line]。

    - 按「前序消费」在工作池指针后逐切片查找首行（verbatim 或前缀容错），得到 start；end = start + 切片行数 - 1（封顶文件行）。
    - 对 CodeSplitter/SentenceSplitter 可能丢弃的行（空行/分隔）用前向查找 + 容错兜底，保证行锚定且近似准确。
    """
    ranges: list[tuple[int, int]] = []
    cursor = 0
    total = len(original_lines)
    for text in chunks_texts:
        clines = text.splitlines()
        nonempty = [l for l in clines if l.strip()]
        if not nonempty:
            continue
        first = nonempty[0]
        i = cursor
        while i < total and first not in original_lines[i] and original_lines[i] != first:
            i += 1
        start = i + 1 if i < total else min(cursor + 1, total)
        end = min(total, max(start, i + len(clines)))
        ranges.append((start, end))
        cursor = max(cursor, i + 1) if i < total else cursor
        if cursor >= total:
            cursor = total
    return ranges


def parse_and_chunk(
    files: list[Any],
    *,
    repository_id: str,
    index_id: str,
    commit_sha: str,
    blocked_paths: list[str],
    collection_name: str | None = None,
) -> list[Any]:
    """主链 PARSING 阶段（LlamaIndex 解析·切片 + CodeMetadataAdapter 回填业务元数据）。

    - 业务过滤（path_blocked/二进制/.env/.git）先于 LlamaIndex 主链执行；
    - 由 Document(NodeParser) 产出切片文本，再以 CodeMetadataAdapter 补 start_line/end_line/symbol_name/chunk_index/checksum，
      拼装 CodeChunkRecord（mlivus_collection 默认 code_{index_id}），供下游 LlamaIndex 节点化 + Embedding + 入库。
    """
    from app import main as _m  # 懒加载：仅需 record 类型，避免 import 期循环

    CodeChunkRecord = _m.CodeChunkRecord
    collection = collection_name or f"code_{index_id}"

    chunks: list[Any] = []
    for file in files:
        if path_blocked(file.path, blocked_paths):
            continue
        if len(file.content.encode("utf-8", errors="ignore")) > INDEX_FILE_SIZE_LIMIT:
            continue
        lines = file.content.splitlines()
        if len(lines) > INDEX_FILE_LINE_LIMIT:
            continue

        document = _build_document(file, repository_id=repository_id, index_id=index_id, commit_sha=commit_sha)
        splitter = _pick_splitter(file.language, file.path)
        try:
            texts = _split_to_texts(document, splitter)
        except Exception as error:  # noqa: BLE001 - NaiveFile 解析异常显式回退到单文件整段（不丢元数据）
            logger.warning("[rag-parsing] LlamaIndex 切片失败（%s），对 %s 回退整文件单切片", error, file.path)
            texts = [file.content] if file.content.strip() else []
        texts = [t for t in texts if t and t.strip()]
        if not texts:
            continue

        ranges = _assign_line_ranges(texts, lines)
        for chunk_index, (text, (start_line, end_line)) in enumerate(zip(texts, ranges)):
            text = text.strip()
            if not text:
                continue
            checksum = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
            chunk_id = f"chunk_{hashlib.md5(f'{repository_id}:{commit_sha}:{file.path}:{start_line}'.encode('utf-8')).hexdigest()[:12]}"
            chunks.append(
                CodeChunkRecord(
                    chunk_id=chunk_id,
                    repository_id=repository_id,
                    index_id=index_id,
                    file_path=file.path,
                    chunk_index=chunk_index,
                    content=text[:MAX_CHUNK_CHARS],
                    language=file.language,
                    symbol_name=extract_symbol_name(text) or extract_symbol_name(file.content),
                    start_line=start_line,
                    end_line=end_line,
                    commit_sha=commit_sha,
                    milvus_collection=collection,
                    milvus_pk=chunk_id,
                    checksum=checksum,
                )
            )
    return chunks