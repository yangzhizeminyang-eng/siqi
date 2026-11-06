// 企业 RAG 检索与评测类型（E3-08）。数据仅来自后端 /api/enterprise/rag，前端不做权限判断。
export type RagRetrievalMode = 'bm25' | 'vector' | 'hybrid' | 'rerank'

export interface RagSearchPayload {
  query: string
  project_id: string
  repository_id: string
  commit_sha?: string
  ref?: string
  source_types?: string[]
  retrieval_mode?: RagRetrievalMode
}

export interface RagChunk {
  chunk_id: string
  source_type: string
  file_path: string
  start_line: number
  end_line: number
  language: string
  symbol_name: string | null
  repository_id: string | null
  index_id: string | null
  commit_sha: string | null
  retrieval_source: string
  score: number
  fresh: boolean
  contributions: Record<string, boolean>
  metadata: Record<string, unknown>
  original_rank: number | null
  reranked_rank: number | null
  rerank_score: number | null
  rerank_reason: string | null
}

export interface RagTraceVariant {
  variant_idx: number
  vector_candidates?: number
  bm25_candidates?: number
}

export interface RagSearchResult {
  results: RagChunk[]
  query_variants: string[]
  variant_count: number
  retrieval_trace: RagTraceVariant[]
  rerank_enabled: boolean
  rerank_provider: string
  rerank_trace: unknown[] | null
}

export interface RagReportMeta {
  name: string
  retrieval_mode?: string
  validation_status?: 'verified' | 'local_only' | 'blocked' | string
  vector_backend?: string
  milvus_verified?: boolean
  fallback_used?: boolean
  needs_improvement?: boolean
  dataset_size?: number
  commit_sha?: string
  index_id?: string
  generated_at?: string
}

export interface RagReportListData {
  reports: RagReportMeta[]
  reports_dir: string
}

export interface RagFailedCase {
  case_id?: string
  query?: string
  tags?: string[]
  difficulty?: string
  expected_status?: number
  actual_status?: number
  status_passed?: boolean
  retrieval_passed?: boolean
  empty?: boolean
  fresh?: boolean
}

export interface RagReportDetail extends RagReportMeta {
  summary?: Record<string, number | string>
  recommended_thresholds?: Record<string, number>
  mode_comparison?: Record<string, Record<string, number | string>>
  mode_validation?: Record<string, Record<string, unknown>>
  metrics_by_tag?: Record<string, Record<string, number>>
  failed_cases?: RagFailedCase[]
  config_snapshot?: Record<string, unknown>
  config_snapshot_sha256?: string
  dataset_sha256?: string
  repository_url?: string
}