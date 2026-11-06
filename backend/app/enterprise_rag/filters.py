"""企业版 E3-01 过滤构造与应用（候选生成阶段受限）。"""

from __future__ import annotations

from typing import Any, Sequence

from app.enterprise_rag.models import RAGMetadataFilters

_DOC_EXTENSIONS = {".md", ".mdx", ".txt", ".rst", ".adoc", ".csv", ".yml", ".yaml", ".json", ".toml"}


def build_filters(*, tenant_id: str, project_id: str, repository_id: str,
                  commit_sha: str | None = None, ref: str | None = None,
                  source_types: list[str] | None = None,
                  index_id: str | None = None) -> RAGMetadataFilters:
    return RAGMetadataFilters(
        tenant_id=tenant_id,
        project_id=project_id,
        repository_id=repository_id,
        commit_sha=commit_sha,
        ref=ref,
        source_type=source_types,
        index_id=index_id,
    )


def source_type_of(chunk: Any) -> str:
    """推导切片的来源类型：优先取显式 source_type，否则按扩展名判断 code/document。"""
    explicit = getattr(chunk, "source_type", None)
    if explicit:
        return str(explicit)
    file_path = str(getattr(chunk, "file_path", "") or "").lower()
    if file_path and any(file_path.endswith(ext) for ext in _DOC_EXTENSIONS):
        return "document"
    # 没扩展名的路径或纯代码常规视为 code
    return "code"


def _match_any(value: str | None, allowed: list[str] | None) -> bool:
    if not allowed:
        return True
    return (value or "") in allowed


def filter_chunks(chunks: Sequence[Any], filters: RAGMetadataFilters) -> list[Any]:
    """候选生成阶段硬过滤：BM25 与向量都只在这份受限子集里召回。

    tenant_id / project_id 由仓库归属（仓库唯一属于一租户/一项目）在服务层强制，
    此处对 chunk 做 repository / index / commit / source_type 交集过滤。
    """
    out: list[Any] = []
    for chunk in chunks:
        if getattr(chunk, "repository_id", None) is not None:
            if str(getattr(chunk, "repository_id")) != str(filters.repository_id):
                continue
        else:
            # 旧测试对象缺 repository_id 时用 index_id 兜底
            if filters.index_id and getattr(chunk, "index_id", None) is not None \
                    and str(getattr(chunk, "index_id")) != str(filters.index_id):
                continue
        if filters.index_id and getattr(chunk, "index_id", None) is not None \
                and str(getattr(chunk, "index_id")) != str(filters.index_id):
            continue
        if filters.commit_sha and getattr(chunk, "commit_sha", None) \
                and str(getattr(chunk, "commit_sha")) != str(filters.commit_sha):
            continue
        if not _match_any(source_type_of(chunk), filters.source_type):
            continue
        out.append(chunk)
    return out


def to_llamaindex_filters(filters: RAGMetadataFilters) -> Any:
    """把向量库可表达的 repository/index/commit/source_type 下推到候选查询。

    tenant/project 由服务端仓库归属门强制，仍由 ``filter_chunks`` 进行应用层硬校验。
    """
    from app.retrieval.llamaindex_backend import FilterOperator, MetadataFilter, MetadataFilters

    if MetadataFilter is None or MetadataFilters is None or FilterOperator is None:
        return None
    entries = [
        MetadataFilter(key="repository_id", value=str(filters.repository_id), operator=FilterOperator.EQ),
    ]
    if filters.index_id:
        entries.append(MetadataFilter(key="index_id", value=str(filters.index_id), operator=FilterOperator.EQ))
    if filters.commit_sha:
        entries.append(MetadataFilter(key="commit_sha", value=str(filters.commit_sha), operator=FilterOperator.EQ))
    if filters.source_type:
        entries.append(MetadataFilter(
            key="source_type",
            value=list(filters.source_type),
            operator=FilterOperator.IN,
        ))
    return MetadataFilters(filters=entries)
