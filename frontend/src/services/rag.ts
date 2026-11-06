import { apiRequest } from './api'
import type {
  RagReportDetail,
  RagReportListData,
  RagSearchPayload,
  RagSearchResult,
} from '../types/rag'

export function ragSearch(payload: RagSearchPayload): Promise<RagSearchResult> {
  return apiRequest<RagSearchResult>('/api/enterprise/rag/search', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function listRagReports(): Promise<RagReportListData> {
  return apiRequest<RagReportListData>('/api/enterprise/rag/reports')
}

export function getRagReport(name: string): Promise<RagReportDetail> {
  return apiRequest<RagReportDetail>(`/api/enterprise/rag/reports/${encodeURIComponent(name)}`)
}