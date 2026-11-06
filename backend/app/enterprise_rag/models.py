"""企业版 E3-01：企业 RAG 查询边界与统一结果契约。

本层把租户/项目/仓库等强过滤与 Hybrid（BM25+向量）检索收拢为可审计的服务，
不改变团队版 RAG 链路（main.run_task_retrieval / query_index_from_chunks 原样保留）。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

RetrievalSource = Literal["vector", "bm25", "hybrid"]
EvalRetrievalMode = Literal["bm25", "vector", "hybrid", "rerank"]
EvalExecutionScope = Literal["isolated", "production"]


class RAGMetadataFilters(BaseModel):
    """统一过滤条件：候选生成阶段就受限（作用于 BM25 与向量）。"""

    tenant_id: str
    project_id: str
    repository_id: str
    commit_sha: str | None = None
    ref: str | None = None
    source_type: list[str] | None = None
    index_id: str | None = None


class EnterpriseRetrievedChunk(BaseModel):
    """企业 Hybrid 检索统一候选结构。"""

    chunk_id: str
    source_type: str = "code"
    file_path: str
    start_line: int = 0
    end_line: int = 0
    language: str = ""
    symbol_name: str | None = None
    repository_id: str | None = None
    index_id: str | None = None
    commit_sha: str | None = None
    retrieval_source: RetrievalSource = "hybrid"
    score: float = 0.0
    fresh: bool = True
    # 说明结果来自哪些检索路：vector/bm25 贡献，供调试与可解释性
    contributions: dict[str, bool] = Field(default_factory=dict)
    metadata: dict = Field(default_factory=dict)
    # E3-03 Rerank 可解释字段（原始/重排名次、重排分、重排原因）
    original_rank: int | None = None
    reranked_rank: int | None = None
    rerank_score: float | None = None
    rerank_reason: str | None = None


class EnterpriseRagSearchPayload(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    project_id: str
    repository_id: str
    commit_sha: str | None = None
    ref: str | None = None
    source_types: list[str] | None = None
    retrieval_mode: str | None = None  # bm25 | vector | hybrid | rerank（默认 None -> 后端配置）


class EnterpriseRagEvaluationPayload(BaseModel):
    """OPT-08 在线评测触发请求。

    - dataset：评测集文件名（backend/evaluation/enterprise_rag/ 白名单内的 .json/.jsonl）；
    - execution_scope=isolated（默认）：仅内存种子数据（E3-05 基线），不碰运行态/外部服务；
    - execution_scope=production：需要 confirm_production=true 显式确认，且按 require_milvus
      语义执行（Milvus 不可用 → blocked，绝不静默回退冒称 verified）。
    """

    dataset: str = Field(default="e3_05_baseline.jsonl", min_length=1, max_length=120)
    retrieval_mode: EvalRetrievalMode = "hybrid"
    top_k: int = Field(default=8, ge=1, le=50)
    execution_scope: EvalExecutionScope = "isolated"
    confirm_production: bool = False