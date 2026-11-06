"""LlamaIndex + Milvus 检索后端。

目标：
- 用 LlamaIndex 统一节点 / 检索编排；
- 用 Milvus 保存向量；Milvus 不可用时的降级路径**必须显式标注**，不得谎报为 milvus；
- 降级由 RAG_MOCK_FALLBACK 控制：测试/本地 true 允许内联/内存/关键词降级并告警，
  生产 false 时 Milvus 失败直接抛错，避免静默降级。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

from app.core import model_config, runtime_config
from app.retrieval.bm25_backend import BM25Result, retrieve_bm25
from app.retrieval.query_expansion import expand_query
from app.retrieval.reranker import rerank_candidates
from app.services import embedding_service

logger = logging.getLogger("backend.rag")

try:  # pragma: no cover - 依赖存在时走真链路
    from llama_index.core import StorageContext, VectorStoreIndex
    from llama_index.core.base.embeddings.base import BaseEmbedding
    from llama_index.core.schema import TextNode
    from llama_index.core.vector_stores.types import FilterOperator, MetadataFilter, MetadataFilters
    from llama_index.core.vector_stores.simple import SimpleVectorStore
    from llama_index.vector_stores.milvus import MilvusVectorStore
except Exception:  # pragma: no cover - 依赖缺失时退回纯内存
    StorageContext = None  # type: ignore[assignment]
    VectorStoreIndex = None  # type: ignore[assignment]
    BaseEmbedding = object  # type: ignore[assignment]
    TextNode = None  # type: ignore[assignment]
    FilterOperator = None  # type: ignore[assignment]
    MetadataFilter = None  # type: ignore[assignment]
    MetadataFilters = None  # type: ignore[assignment]
    SimpleVectorStore = None  # type: ignore[assignment]
    MilvusVectorStore = None  # type: ignore[assignment]

# 检索结果的候选来源（在本模块内自洽；投递到 main.RetrievalContext 时映射 milvus/fallback）
SOURCE_MILVUS = "milvus"
SOURCE_FALLBACK = "fallback"  # Milvus 不可用时的显式降级来源（内存/关键词），不再谎报 milvus
SOURCE_HYBRID = "hybrid"
RETRIEVAL_CANDIDATE_MULTIPLIER = 4
RETRIEVAL_CANDIDATE_MAX = 50
DEFAULT_MAX_CHUNKS_PER_FILE = 2
RRF_K = 60
HYBRID_DENSE_WEIGHT = 0.6
HYBRID_BM25_WEIGHT = 0.4
QUERY_VARIANT_WEIGHT_DECAY = 0.85


@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: str
    file_path: str
    start_line: int
    end_line: int
    language: str
    symbol_name: str | None
    content: str
    score: float | None
    commit_sha: str
    index_id: str
    fresh: bool
    source: str = SOURCE_MILVUS


def _fallback_allowed() -> bool:
    """Milvus 不可用时是否允许降级（测试/本地）；生产置 false 则失败即抛错。"""
    return runtime_config.load_runtime_settings().rag_mock_fallback


def is_available() -> bool:
    return VectorStoreIndex is not None and StorageContext is not None and TextNode is not None


def _chunk_value(chunk: Any, name: str, default: Any = None) -> Any:
    return getattr(chunk, name, default)


def _source_type_of(chunk: Any) -> str:
    explicit = _chunk_value(chunk, "source_type")
    if explicit:
        return str(explicit)
    suffix = Path(str(_chunk_value(chunk, "file_path", "") or "")).suffix.lower()
    return "document" if suffix in {".md", ".mdx", ".txt", ".rst", ".adoc", ".csv", ".yml", ".yaml", ".json", ".toml"} else "code"


def _scope_chunks(chunks: Sequence[Any], *, repository_id: str, index_id: str) -> list[Any]:
    """应用层范围兜底：生产 CodeChunkRecord 必须匹配仓库和索引，旧测试对象缺字段时保持兼容。"""
    scoped: list[Any] = []
    for chunk in chunks:
        chunk_repository = _chunk_value(chunk, "repository_id")
        chunk_index = _chunk_value(chunk, "index_id")
        if chunk_repository is not None and str(chunk_repository) != str(repository_id):
            continue
        if chunk_index is not None and str(chunk_index) != str(index_id):
            continue
        scoped.append(chunk)
    return scoped


def _metadata_filters(*, repository_id: str, index_id: str) -> Any:
    """构造 LlamaIndex 过滤器；不支持时返回 None，由应用层范围过滤继续兜底。"""
    if MetadataFilter is None or MetadataFilters is None or FilterOperator is None:
        return None
    return MetadataFilters(
        filters=[
            MetadataFilter(key="repository_id", value=str(repository_id), operator=FilterOperator.EQ),
            MetadataFilter(key="index_id", value=str(index_id), operator=FilterOperator.EQ),
        ]
    )


def _dedupe_and_diversify(
    results: Sequence[RetrievedChunk],
    *,
    top_k: int,
    max_chunks_per_file: int | None,
) -> list[RetrievedChunk]:
    """按 chunk 去重并限制单文件占比，避免最终上下文被同一文件相似片段挤满。"""
    if top_k <= 0:
        return []
    per_file_limit = max_chunks_per_file if max_chunks_per_file is None else max(1, max_chunks_per_file)
    seen_chunk_ids: set[str] = set()
    file_counts: dict[str, int] = {}
    selected: list[RetrievedChunk] = []
    for result in results:
        if result.chunk_id in seen_chunk_ids:
            continue
        seen_chunk_ids.add(result.chunk_id)
        file_path = result.file_path or result.chunk_id
        if per_file_limit is not None and file_counts.get(file_path, 0) >= per_file_limit:
            continue
        file_counts[file_path] = file_counts.get(file_path, 0) + 1
        selected.append(result)
        if len(selected) >= top_k:
            break
    return selected


def _candidate_top_k(top_k: int) -> int:
    return min(
        max(top_k, top_k * RETRIEVAL_CANDIDATE_MULTIPLIER),
        RETRIEVAL_CANDIDATE_MAX,
    )


def _stable_sort_retrieved(results: Sequence[RetrievedChunk]) -> list[RetrievedChunk]:
    """Keep equal-score vector hits deterministic across Milvus query runs."""
    return sorted(
        results,
        key=lambda item: (
            -(float(item.score) if item.score is not None else float("-inf")),
            item.file_path or "",
            int(item.start_line or 0),
            int(item.end_line or 0),
            item.chunk_id or "",
        ),
    )


def _bm25_confidence(score: float) -> float:
    """把 BM25 原始分数压缩到 0~1，便于和已有阈值一起做最低相关性门禁。"""
    return score / (1.0 + score) if score > 0.0 else 0.0


def _query_vector_results(
    chunks: Sequence[Any],
    *,
    repository_id: str,
    index_id: str,
    collection_name: str,
    query_text: str,
    top_k: int,
    threshold: float,
    head_commit: str | None,
    max_chunks_per_file: int | None,
    candidate_top_k: int | None = None,
    metadata_filters: Any | None = None,
    strict_metadata_filters: bool = False,
) -> list[RetrievedChunk]:
    """单路向量召回；hybrid 模式会把 threshold 放宽后再统一做相关性门禁。"""
    scoped_chunks = _scope_chunks(chunks, repository_id=repository_id, index_id=index_id)
    if not scoped_chunks:
        return []
    if not is_available():
        return _keyword_results(
            scoped_chunks,
            index_id=index_id,
            query_text=query_text,
            top_k=top_k,
            threshold=threshold,
            head_commit=head_commit,
            max_chunks_per_file=max_chunks_per_file,
        )

    vector_store = build_vector_store(collection_name, overwrite=False)
    backend_milvus = MilvusVectorStore is not None and isinstance(vector_store, MilvusVectorStore)
    retrieval_top_k = candidate_top_k or _candidate_top_k(top_k)
    try:
        # 跨进程/重启后 Milvus collection 已存在但处于 released 时，先显式 load 再检索（幂等）
        if backend_milvus:
            ensure_milvus_collection_loaded(vector_store, collection_name)
        index = VectorStoreIndex.from_vector_store(vector_store, embed_model=LlamaIndexEmbeddingAdapter())
        retriever_kwargs: dict[str, Any] = {"similarity_top_k": retrieval_top_k}
        if backend_milvus:
            filters = metadata_filters or _metadata_filters(repository_id=repository_id, index_id=index_id)
            if filters is not None:
                retriever_kwargs["filters"] = filters
        try:
            retriever = index.as_retriever(**retriever_kwargs)
            results = retriever.retrieve(query_text)
        except Exception as error:
            # 团队旧 collection 可兼容回退；企业严格过滤绝不改为无过滤全量向量查询。
            if "filters" not in retriever_kwargs:
                raise
            if strict_metadata_filters:
                if not _fallback_allowed():
                    raise RuntimeError("企业 RAG 元数据过滤不可用，拒绝无过滤向量查询") from error
                logger.warning("[rag] 企业元数据过滤不可用（%s），显式降级为受限关键词检索", error)
                return _keyword_results(
                    scoped_chunks,
                    index_id=index_id,
                    query_text=query_text,
                    top_k=top_k,
                    threshold=threshold,
                    head_commit=head_commit,
                    max_chunks_per_file=max_chunks_per_file,
                )
            logger.warning("[rag] Milvus metadata filter 不可用（%s），改用应用层范围过滤", error)
            retriever_kwargs.pop("filters", None)
            retriever = index.as_retriever(**retriever_kwargs)
            results = retriever.retrieve(query_text)
    except Exception as error:  # noqa: BLE001
        if not _fallback_allowed():
            raise
        logger.warning("[rag] 向量检索失败（%s），已显式降级为内存关键词检索（source=%s）", error, SOURCE_FALLBACK)
        return _keyword_results(
            scoped_chunks,
            index_id=index_id,
            query_text=query_text,
            top_k=top_k,
            threshold=threshold,
            head_commit=head_commit,
            max_chunks_per_file=max_chunks_per_file,
        )

    source = SOURCE_MILVUS if backend_milvus else SOURCE_FALLBACK
    if not backend_milvus:
        logger.warning("[rag] 检索基于内存 SimpleVectorStore 而非 Milvus（source=%s），请确认生产已配置 Milvus", SOURCE_FALLBACK)

    retrieved: list[RetrievedChunk] = []
    for item in results:
        node = item.node
        meta = getattr(node, "metadata", {}) or {}
        score = getattr(item, "score", None)
        try:
            score = float(score) if score is not None else None
        except Exception:
            score = None
        if score is not None and score < threshold:
            continue
        retrieved.append(
            RetrievedChunk(
                chunk_id=str(meta.get("chunk_id") or node.node_id),
                file_path=str(meta.get("file_path") or ""),
                start_line=int(meta.get("start_line") or 0),
                end_line=int(meta.get("end_line") or 0),
                language=str(meta.get("language") or ""),
                symbol_name=meta.get("symbol_name"),
                content=getattr(node, "text", "") or "",
                score=score,
                commit_sha=str(meta.get("commit_sha") or ""),
                index_id=str(meta.get("index_id") or index_id),
                fresh=str(meta.get("commit_sha") or "") == (head_commit or str(meta.get("commit_sha") or "")),
                source=source,
            )
        )
    # 过滤器是向量库侧的第一道边界，应用层再按传入的 chunk 快照做一次范围校验。
    scoped_ids = {
        str(_chunk_value(chunk, "chunk_id"))
        for chunk in scoped_chunks
        if _chunk_value(chunk, "chunk_id") is not None
    }
    if scoped_ids:
        retrieved = [item for item in retrieved if item.chunk_id in scoped_ids]
    retrieved = _stable_sort_retrieved(retrieved)
    return _dedupe_and_diversify(
        retrieved,
        top_k=top_k,
        max_chunks_per_file=max_chunks_per_file,
    )


def _hybrid_results(
    chunks: Sequence[Any],
    *,
    repository_id: str,
    index_id: str,
    collection_name: str,
    query_text: str,
    top_k: int,
    threshold: float,
    head_commit: str | None,
    max_chunks_per_file: int | None,
    query_variants: Sequence[str] | None = None,
    rerank: bool = False,
    rerank_max_candidates: int = 20,
) -> list[RetrievedChunk]:
    """BM25 + dense 多路召回后使用加权 RRF 融合，再按有效相关性做门禁。"""
    scoped_chunks = _scope_chunks(chunks, repository_id=repository_id, index_id=index_id)
    if not scoped_chunks:
        return []
    candidate_top_k = _candidate_top_k(top_k)
    variants = [query_text]
    for variant in query_variants or []:
        normalized = " ".join((variant or "").split()).strip()
        if normalized and normalized not in variants:
            variants.append(normalized)
    by_chunk: dict[str, dict[str, Any]] = {}
    for variant_index, variant in enumerate(variants):
        variant_weight = QUERY_VARIANT_WEIGHT_DECAY**variant_index
        dense_results = _query_vector_results(
            scoped_chunks,
            repository_id=repository_id,
            index_id=index_id,
            collection_name=collection_name,
            query_text=variant,
            top_k=candidate_top_k,
            threshold=-1.0,
            head_commit=head_commit,
            max_chunks_per_file=None,
            candidate_top_k=candidate_top_k,
        )
        lexical_results = retrieve_bm25(scoped_chunks, variant, top_k=candidate_top_k)
        for rank, result in enumerate(dense_results, start=1):
            state = by_chunk.setdefault(
                result.chunk_id,
                {"chunk": None, "dense": None, "bm25": 0.0, "rrf": 0.0},
            )
            state["chunk"] = result
            state["dense"] = result
            state["rrf"] += variant_weight * HYBRID_DENSE_WEIGHT / (RRF_K + rank)
        for lexical in lexical_results:
            chunk_id = str(_chunk_value(lexical.chunk, "chunk_id"))
            state = by_chunk.setdefault(
                chunk_id,
                {"chunk": lexical.chunk, "dense": None, "bm25": 0.0, "rrf": 0.0},
            )
            state["chunk"] = state["dense"] or lexical.chunk
            state["bm25"] = max(float(state["bm25"]), float(lexical.score))
            state["rrf"] += variant_weight * HYBRID_BM25_WEIGHT / (RRF_K + lexical.rank)

    candidates: list[tuple[float, float, dict[str, Any]]] = []
    for state in by_chunk.values():
        dense = state["dense"]
        dense_score = float(dense.score) if dense and dense.score is not None else 0.0
        lexical_score = _bm25_confidence(float(state["bm25"]))
        support_score = max(dense_score, lexical_score)
        if support_score >= threshold:
            candidates.append((float(state["rrf"]), support_score, state))
    candidates.sort(
        key=lambda item: (
            -item[0],
            -item[1],
            str(_chunk_value(item[2]["chunk"], "file_path", "")),
            int(_chunk_value(item[2]["chunk"], "start_line", 0) or 0),
        )
    )

    results: list[RetrievedChunk] = []
    for _, support_score, state in candidates:
        base = state["chunk"]
        dense = state["dense"]
        results.append(
            RetrievedChunk(
                chunk_id=str(_chunk_value(base, "chunk_id")),
                file_path=str(_chunk_value(base, "file_path", "")),
                start_line=int(_chunk_value(base, "start_line", 0) or 0),
                end_line=int(_chunk_value(base, "end_line", 0) or 0),
                language=str(_chunk_value(base, "language", "")),
                symbol_name=_chunk_value(base, "symbol_name"),
                content=str(_chunk_value(base, "content", "") or ""),
                score=support_score,
                commit_sha=str(_chunk_value(base, "commit_sha", "") or ""),
                index_id=index_id,
                fresh=str(_chunk_value(base, "commit_sha", "") or "") == (head_commit or str(_chunk_value(base, "commit_sha", "") or "")),
                source=SOURCE_HYBRID,
            )
        )
    if rerank:
        results = rerank_candidates(
            query_text,
            results,
            max_candidates=rerank_max_candidates,
        )
    return _dedupe_and_diversify(
        results,
        top_k=top_k,
        max_chunks_per_file=max_chunks_per_file,
    )


def embedding_dim() -> int:
    if model_config.embedding_configured():
        configured = model_config.load_embedding_settings().dimensions
        cached = embedding_service.last_real_embedding_dimension()
        if cached is not None:
            return cached
        if configured is not None:
            return configured
        return embedding_service.probe_embedding_dimension()
    return 64


class LlamaIndexEmbeddingAdapter(BaseEmbedding):
    """把现有 embedding_service 包成 LlamaIndex 可用的 embedding 模型。"""

    model_name: str = "project-embedding-bridge"

    def _get_query_embedding(self, query: str) -> list[float]:
        return embedding_service.embed_query(query)

    async def _aget_query_embedding(self, query: str) -> list[float]:
        return self._get_query_embedding(query)

    def _get_text_embedding(self, text: str) -> list[float]:
        return embedding_service.embed_query(text)

    async def _aget_text_embedding(self, text: str) -> list[float]:
        return self._get_text_embedding(text)

    def _get_text_embeddings(self, texts: list[str]) -> list[list[float]]:
        return embedding_service.embed_texts(list(texts))

    async def _aget_text_embeddings(self, texts: list[str]) -> list[list[float]]:
        return self._get_text_embeddings(list(texts))


def _vector_store_uri() -> str:
    settings = runtime_config.load_runtime_settings()
    uri = settings.milvus_path
    if not uri.startswith(("http://", "https://", "grpc://")):
        Path(uri).parent.mkdir(parents=True, exist_ok=True)
    return uri


def build_vector_store(collection_name: str, *, overwrite: bool = False) -> Any:
    settings = runtime_config.load_runtime_settings()
    wants_milvus = MilvusVectorStore is not None and settings.use_milvus
    if wants_milvus:
        try:
            return MilvusVectorStore(
                uri=_vector_store_uri(),
                token=settings.milvus_token,
                collection_name=collection_name,
                dim=embedding_dim(),
                overwrite=overwrite,
                use_async_client=False,
            )
        except Exception as error:  # noqa: BLE001 - Milvus 构造失败：按开关决定抛错或显式降级
            if not _fallback_allowed():
                raise RuntimeError(f"Milvus 不可用且 RAG_MOCK_FALLBACK=false，拒绝静默降级：{error}") from error
            logger.warning("[rag] Milvus 不可用（%s），已显式降级为内存 SimpleVectorStore（RAG_MOCK_FALLBACK=true）", error)
    if SimpleVectorStore is None:
        raise RuntimeError("LlamaIndex vector store unavailable")
    return SimpleVectorStore()


def _keyword_results(
    chunks: Sequence[Any],
    *,
    index_id: str,
    query_text: str,
    top_k: int,
    threshold: float,
    head_commit: str | None,
    max_chunks_per_file: int | None = DEFAULT_MAX_CHUNKS_PER_FILE,
) -> list[RetrievedChunk]:
    scored = []
    for chunk in chunks:
        score = embedding_service.keyword_overlap_score(query_text, _chunk_value(chunk, "content", ""))
        if score >= threshold:
            scored.append((score, chunk))
    scored.sort(key=lambda item: item[0], reverse=True)
    candidates = [
        RetrievedChunk(
            chunk_id=_chunk_value(chunk, "chunk_id"),
            file_path=_chunk_value(chunk, "file_path"),
            start_line=_chunk_value(chunk, "start_line"),
            end_line=_chunk_value(chunk, "end_line"),
            language=_chunk_value(chunk, "language"),
            symbol_name=_chunk_value(chunk, "symbol_name"),
            content=_chunk_value(chunk, "content", ""),
            score=score,
            commit_sha=_chunk_value(chunk, "commit_sha"),
            index_id=index_id,
            fresh=_chunk_value(chunk, "commit_sha") == (head_commit or _chunk_value(chunk, "commit_sha")),
            source=SOURCE_FALLBACK,
        )
        for score, chunk in scored[: max(top_k, top_k * RETRIEVAL_CANDIDATE_MULTIPLIER)]
    ]
    return _dedupe_and_diversify(
        candidates,
        top_k=top_k,
        max_chunks_per_file=max_chunks_per_file,
    )


def ensure_milvus_collection_loaded(vector_store, collection_name: str) -> None:
    """跨进程/重启后，Milvus collection 已存在但处于 released 时显式 load，保证可检索。

    - 幂等：对已 load 的 collection 再次 load 为空操作，同进程已加载不受影响。
    - 容错：优先用 vector_store 持有的 pymilvus MilvusClient（`client`/`_milvus_client`）load，
      不硬编码单一私有字段；退而用稳定公共 API `pymilvus.utility`。
    - 失败：抛出，由调用方按 RAG_MOCK_FALLBACK 走降级/抛错。
    """
    client = getattr(vector_store, "client", None) or getattr(vector_store, "_milvus_client", None)
    if client is not None and hasattr(client, "load_collection"):
        has_collection = getattr(client, "has_collection", None)
        if callable(has_collection) and not has_collection(collection_name):
            return
        client.load_collection(collection_name)
        return
    try:
        from pymilvus import utility

        if utility.has_collection(collection_name):
            utility.load_collection(collection_name)
    except Exception as error:
        raise RuntimeError(f"Milvus collection {collection_name} 无法 load（released）：{error}") from error


def build_nodes_from_chunks(chunks: Sequence[Any], *, repository_id: str, index_id: str) -> list[Any]:
    if TextNode is None:
        raise RuntimeError("LlamaIndex TextNode unavailable")
    nodes: list[Any] = []
    for chunk in chunks:
        metadata = {
            "repository_id": repository_id,
            "index_id": index_id,
            "chunk_id": _chunk_value(chunk, "chunk_id"),
            "file_path": _chunk_value(chunk, "file_path"),
            "chunk_index": _chunk_value(chunk, "chunk_index"),
            "language": _chunk_value(chunk, "language"),
            "symbol_name": _chunk_value(chunk, "symbol_name"),
            "start_line": _chunk_value(chunk, "start_line"),
            "end_line": _chunk_value(chunk, "end_line"),
            "commit_sha": _chunk_value(chunk, "commit_sha"),
            "source_type": _source_type_of(chunk),
            "checksum": _chunk_value(chunk, "checksum"),
        }
        nodes.append(
            TextNode(
                id_=_chunk_value(chunk, "chunk_id"),
                text=_chunk_value(chunk, "content", ""),
                metadata=metadata,
            )
        )
    return nodes


def build_index_from_chunks(
    chunks: Sequence[Any],
    *,
    repository_id: str,
    index_id: str,
    collection_name: str,
    overwrite: bool = False,
) -> Any:
    if not is_available():
        return None
    vector_store = build_vector_store(collection_name, overwrite=overwrite)
    storage_context = StorageContext.from_defaults(vector_store=vector_store)
    nodes = build_nodes_from_chunks(chunks, repository_id=repository_id, index_id=index_id)
    return VectorStoreIndex(nodes, storage_context=storage_context, embed_model=LlamaIndexEmbeddingAdapter())


def query_index_from_chunks(
    chunks: Sequence[Any],
    *,
    repository_id: str,
    index_id: str,
    collection_name: str,
    query_text: str,
    top_k: int,
    threshold: float,
    head_commit: str | None,
    max_chunks_per_file: int | None = DEFAULT_MAX_CHUNKS_PER_FILE,
    retrieval_mode: str | None = None,
    query_expansion: bool | None = None,
    rerank: bool | None = None,
) -> list[RetrievedChunk]:
    settings = runtime_config.load_runtime_settings()
    mode = (retrieval_mode or settings.rag_retrieval_mode or "vector").lower()
    if mode == "hybrid":
        expansion_enabled = (
            settings.rag_query_expansion
            if query_expansion is None
            else query_expansion
        )
        rerank_enabled = settings.rag_rerank_enabled if rerank is None else rerank
        variants = expand_query(query_text, max_queries=3, use_llm=True)[1:] if expansion_enabled else []
        return _hybrid_results(
            chunks,
            repository_id=repository_id,
            index_id=index_id,
            collection_name=collection_name,
            query_text=query_text,
            top_k=top_k,
            threshold=threshold,
            head_commit=head_commit,
            max_chunks_per_file=max_chunks_per_file,
            query_variants=variants,
            rerank=rerank_enabled,
            rerank_max_candidates=settings.rag_rerank_max_candidates,
        )
    if mode != "vector":
        logger.warning("[rag] 未知 RAG_RETRIEVAL_MODE=%s，回退 vector 模式", mode)
    return _query_vector_results(
        chunks,
        repository_id=repository_id,
        index_id=index_id,
        collection_name=collection_name,
        query_text=query_text,
        top_k=top_k,
        threshold=threshold,
        head_commit=head_commit,
        max_chunks_per_file=max_chunks_per_file,
    )


def check_milvus_health(timeout_ms: int = 2000) -> dict:
    """轻量探活 Milvus：供 /api/health 暴露依赖状态，容器崩溃（Exited 134）可被监控/编排发现。

    - 不抛错、不阻塞主流程；
    - use_milvus=false 或本地 Lite 文件：返回 skipped（本地兜底，非故障）；
    - 远程 URI：用 pymilvus 做一次 list_collections 级别的轻量调用，区分 ok/down。
    """
    settings = runtime_config.load_runtime_settings()
    if not settings.use_milvus:
        return {"status": "skipped", "reason": "use_milvus=false"}
    uri = settings.milvus_path
    if not uri.startswith(("http://", "https://", "grpc://")):
        return {"status": "skipped", "reason": "local-milvus-lite", "uri": uri}
    try:
        from pymilvus import MilvusClient

        client = MilvusClient(uri=uri, token=settings.milvus_token or None, timeout=timeout_ms)
        try:
            client.list_collections()
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass
        return {"status": "ok", "uri": uri}
    except Exception as error:  # noqa: BLE001
        logger.warning("[rag] Milvus 健康探活失败（%s）", error)
        return {"status": "down", "uri": uri, "error": str(error)[:300]}


def inspect_milvus_collection(collection_name: str, *, expected_dim: int | None = None) -> dict:
    """读取 collection/index 的脱敏运行证据，不返回 URI、Token 或代码内容。"""
    settings = runtime_config.load_runtime_settings()
    evidence = {
        "milvus_health": "unavailable",
        "milvus_server_available": False,
        "collection_exists": False,
        "collection_name": collection_name,
        "vector_dimension": None,
        "expected_vector_dimension": expected_dim,
        "dimension_match": False,
        "index_exists": False,
        "index_names": [],
        "query_success": False,
    }
    if not settings.use_milvus or not settings.milvus_path.startswith(("http://", "https://", "grpc://")):
        evidence["milvus_health"] = "disabled_or_local"
        return evidence
    try:
        from pymilvus import MilvusClient

        client = MilvusClient(uri=settings.milvus_path, token=settings.milvus_token or None, timeout=5)
        try:
            evidence["milvus_health"] = "ok"
            evidence["milvus_server_available"] = True
            if not client.has_collection(collection_name):
                return evidence
            evidence["collection_exists"] = True
            description = client.describe_collection(collection_name) or {}
            for field in description.get("fields", []):
                params = field.get("params") or {}
                if params.get("dim") is not None:
                    evidence["vector_dimension"] = int(params["dim"])
                    break
            if evidence["vector_dimension"] is not None and expected_dim is not None:
                evidence["dimension_match"] = evidence["vector_dimension"] == expected_dim
            names = client.list_indexes(collection_name) or []
            evidence["index_names"] = [str(name) for name in names]
            evidence["index_exists"] = bool(names)
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                close()
    except Exception as error:  # noqa: BLE001
        evidence["error_class"] = type(error).__name__
        evidence["error"] = str(error)[:200]
    return evidence
