import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useRagStore } from '../rag'

const { ragSearchMock, listReportsMock, getReportMock } = vi.hoisted(() => ({
  ragSearchMock: vi.fn(),
  listReportsMock: vi.fn(),
  getReportMock: vi.fn(),
}))

vi.mock('../../services/rag', () => ({
  ragSearch: ragSearchMock,
  listRagReports: listReportsMock,
  getRagReport: getReportMock,
}))

const result = {
  results: [],
  query_variants: ['q'],
  variant_count: 1,
  retrieval_trace: [],
  rerank_enabled: false,
  rerank_provider: 'heuristic',
  rerank_trace: null,
}

const reportMeta = { name: 'r.json', validation_status: 'verified', retrieval_mode: 'hybrid', dataset_size: 67, milvus_verified: true, fallback_used: false, needs_improvement: false }
const reportDetail = {
  ...reportMeta,
  summary: { recall_at_k: 0.9, precision_at_k: 0.35, mrr: 0.82 },
  mode_comparison: { bm25: { recall_at_k: 0.87, precision_at_k: 0.2 } },
  metrics_by_tag: { symbol: { count: 50, recall_at_k: 0.9 } },
  failed_cases: [{ case_id: 'hm001', query: 'build_x', tags: ['symbol'], status_passed: true, retrieval_passed: false }],
}

describe('useRagStore 企业 RAG 控制台 store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    ragSearchMock.mockReset()
    listReportsMock.mockReset()
    getReportMock.mockReset()
  })
  afterEach(() => vi.restoreAllMocks())

  it('runSearch 成功写入结果', async () => {
    ragSearchMock.mockResolvedValue(result)
    const store = useRagStore()
    const ok = await store.runSearch({ query: 'build_login_response', project_id: 'p', repository_id: 'r' })
    expect(ok).toBe(true)
    expect(store.searchReady).toBe(true)
    expect(store.searchResult?.query_variants[0]).toBe('q')
    expect(store.searchError).toBe('')
  })

  it('runSearch 失败置错误', async () => {
    ragSearchMock.mockRejectedValue(new Error('检索失败'))
    const store = useRagStore()
    const ok = await store.runSearch({ query: 'x', project_id: 'p', repository_id: 'r' })
    expect(ok).toBe(false)
    expect(store.searchReady).toBe(false)
    expect(store.searchError).toContain('检索失败')
  })

  it('loadReports 写入列表 / 空 / 错误', async () => {
    const store = useRagStore()
    listReportsMock.mockResolvedValueOnce({ reports: [reportMeta], reports_dir: '/r' })
    await store.loadReports()
    expect(store.reports).toHaveLength(1)
    expect(store.loadingReports).toBe(false)

    listReportsMock.mockResolvedValueOnce({ reports: [], reports_dir: '/r' })
    await store.loadReports()
    expect(store.reports).toHaveLength(0)

    listReportsMock.mockRejectedValueOnce(new Error('列表失败'))
    await store.loadReports()
    expect(store.reportsError).toContain('列表失败')
  })

  it('loadReport 成功/失败并保留状态字段', async () => {
    const store = useRagStore()
    getReportMock.mockResolvedValueOnce(reportDetail)
    const ok = await store.loadReport('r.json')
    expect(ok).toBe(true)
    expect(store.selectedReport?.validation_status).toBe('verified')
    expect(store.selectedReport?.needs_improvement).toBe(false)

    getReportMock.mockRejectedValueOnce(new Error('报告不存在'))
    const ok2 = await store.loadReport('nope.json')
    expect(ok2).toBe(false)
    expect(store.selectedReport).toBeNull()
    expect(store.reportError).toContain('报告不存在')
  })
})