// 企业 RAG 控制台 store（E3-08）。所有数据来自后端权威接口（rag:search 由服务端强制）。
import { defineStore } from 'pinia'
import { getRagReport, listRagReports, ragSearch } from '../services/rag'
import type {
  RagReportDetail,
  RagReportListData,
  RagReportMeta,
  RagSearchPayload,
  RagSearchResult,
} from '../types/rag'

export const useRagStore = defineStore('rag', {
  state: () => ({
    searchReady: false,
    searchResult: null as RagSearchResult | null,
    searching: false,
    searchError: '',
    // 报告
    reports: [] as RagReportMeta[],
    reportsDir: '',
    loadingReports: false,
    reportsError: '',
    selectedReport: null as RagReportDetail | null,
    loadingReport: false,
    reportError: '',
  }),
  getters: {
    statusLabels() {
      return { verified: '已通过真实 Milvus 验证', local_only: '仅在本地/内存环境评测', blocked: 'Milvus 不可用，已阻断' }
    },
  },
  actions: {
    async runSearch(payload: RagSearchPayload): Promise<boolean> {
      this.searching = true
      this.searchError = ''
      try {
        this.searchResult = await ragSearch(payload)
        this.searchReady = true
        return true
      } catch (err) {
        this.searchReady = false
        this.searchError = err instanceof Error ? err.message : '检索失败'
        return false
      } finally {
        this.searching = false
      }
    },
    async loadReports(): Promise<void> {
      this.loadingReports = true
      this.reportsError = ''
      try {
        const data: RagReportListData = await listRagReports()
        this.reports = data.reports ?? []
        this.reportsDir = data.reports_dir ?? ''
      } catch (err) {
        this.reportsError = err instanceof Error ? err.message : '加载报告列表失败'
      } finally {
        this.loadingReports = false
      }
    },
    async loadReport(name: string): Promise<boolean> {
      this.loadingReport = true
      this.reportError = ''
      try {
        this.selectedReport = await getRagReport(name)
        return true
      } catch (err) {
        this.selectedReport = null
        this.reportError = err instanceof Error ? err.message : '加载报告失败'
        return false
      } finally {
        this.loadingReport = false
      }
    },
  },
})