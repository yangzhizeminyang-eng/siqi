<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { FlaskConical, Search, ShieldCheck, AlertTriangle, LoaderCircle, RotateCcw } from '@lucide/vue'
import { useAuthStore } from '../stores/auth'
import { useTenancyStore } from '../stores/tenancy'
import { useRagStore } from '../stores/rag'
import { hasAccessRequirement } from '../services/accessControl'
import TaskFilterSelect from '../components/TaskFilterSelect.vue'
import type { RagRetrievalMode } from '../types/rag'

const auth = useAuthStore()
const tenancy = useTenancyStore()
const rag = useRagStore()

const canAccess = computed(() => hasAccessRequirement(auth, tenancy, ['rag:search']))

// 检索表单
const query = ref('')
const projectId = ref('proj_health_middle')
const repositoryId = ref('repo_health_middle')
const commitSha = ref('73e7e5497addc74f67fe49a875d7afdd8d8c190b')
const ref_ = ref('')
const retrievalMode = ref<RagRetrievalMode>('hybrid')
const sourceCode = ref(true)
const sourceDoc = ref(false)
const modes: { value: RagRetrievalMode; label: string }[] = [
  { value: 'bm25', label: 'BM25' },
  { value: 'vector', label: '向量' },
  { value: 'hybrid', label: 'Hybrid（默认）' },
  { value: 'rerank', label: 'Rerank' },
]

const selectedReportName = ref('')

async function runSearch(): Promise<void> {
  if (!query.value.trim()) return
  const sourceTypes: string[] = []
  if (sourceCode.value) sourceTypes.push('code')
  if (sourceDoc.value) sourceTypes.push('document')
  await rag.runSearch({
    query: query.value.trim(),
    project_id: projectId.value.trim(),
    repository_id: repositoryId.value.trim(),
    commit_sha: commitSha.value.trim() || undefined,
    ref: ref_.value.trim() || undefined,
    retrieval_mode: retrievalMode.value,
    ...(sourceTypes.length ? { source_types: sourceTypes } : {}),
  })
}

async function pickReport(name: string): Promise<void> {
  selectedReportName.value = name
  await rag.loadReport(name)
}

function fmt(v: unknown): string {
  if (typeof v === 'number') return Number.isInteger(v) ? String(v) : v.toFixed(4)
  if (v === null || v === undefined) return '—'
  return String(v)
}

function statusLabel(status?: string): string {
  return (rag.statusLabels as Record<string, string>)[status ?? ''] ?? '未知'
}

onMounted(() => {
  if (canAccess.value) void rag.loadReports()
})
</script>

<template>
  <section class="page-content rag-console enterprise-rag">
    <div class="page-toolbar page-toolbar--stacked">
      <div>
        <h1>企业 RAG 控制台</h1>
        <p class="page-subtext">检索与企业离线评测报告查看（权限由后端 rag:search 权威判定）。</p>
      </div>
      <span class="rag-console__scope">当前租户：{{ tenancy.displayActiveName }}</span>
    </div>

    <div v-if="!canAccess" class="page-notice page-notice--warning">
      <span class="page-notice__icon"><ShieldCheck :size="17" /></span>
      <div class="page-notice__body">
        <strong>无权限</strong>：当前角色不具备「rag:search」能力，无法访问企业 RAG。审计人员默认不可检索企业代码。
      </div>
    </div>

    <template v-else>
      <div class="rag-console__grid">
        <!-- 检索面板 -->
        <section class="panel rag-console__panel rag-console__panel--search" aria-label="企业 RAG 检索">
          <header class="panel__header">
            <h2 class="panel__title"><Search :size="16" /> 检索</h2>
          </header>
          <form class="rag-console__form" @submit.prevent="runSearch">
            <label class="field-label field-label--primary"><span>查询内容</span>
              <input v-model="query" class="text-input" :disabled="rag.searching" placeholder="如 build_login_response / /api/ocr/upload / ERR_401" required />
            </label>
            <div class="rag-console__row">
              <label class="field-label"><span>项目 ID</span><input v-model="projectId" class="text-input" /></label>
              <label class="field-label"><span>仓库 ID</span><input v-model="repositoryId" class="text-input" /></label>
            </div>
            <div class="rag-console__row">
              <label class="field-label"><span>Commit SHA</span><input v-model="commitSha" class="text-input" placeholder="留空按 ref" /></label>
              <label class="field-label"><span>Ref</span><input v-model="ref_" class="text-input" placeholder="如 main" /></label>
            </div>
            <div class="rag-console__row rag-console__row--wrap">
              <fieldset class="rag-console__fieldset rag-console__fieldset--mode">
                <legend>检索模式</legend>
                <span class="rag-console__select-wrap">
                  <TaskFilterSelect
                    v-model="retrievalMode"
                    :options="modes"
                    ariaLabel="检索模式"
                    :disabled="rag.searching"
                    class="rag-console__mode-select"
                  />
                </span>
              </fieldset>
              <fieldset class="rag-console__fieldset rag-console__fieldset--source">
                <legend>来源类型</legend>
                <label class="check"><input v-model="sourceCode" type="checkbox" /> 代码</label>
                <label class="check"><input v-model="sourceDoc" type="checkbox" /> 文档</label>
              </fieldset>
            </div>
            <div class="rag-console__actions">
              <button type="submit" class="btn btn--primary" :disabled="rag.searching" :aria-busy="rag.searching">
                <Search :size="14" />
                {{ rag.searching ? '检索中…' : '检索' }}
              </button>
            </div>
          </form>

          <div v-if="rag.searchError" class="page-notice page-notice--danger">
            <span class="page-notice__icon"><AlertTriangle :size="16" /></span>
            <div class="page-notice__body">{{ rag.searchError }}</div>
          </div>

          <div v-if="rag.searching" class="rag-console__loading" aria-live="polite"><LoaderCircle class="spin" :size="16" /> 正在检索…</div>
          <div v-else-if="rag.searchReady && rag.searchResult" class="rag-console__results">
            <div class="rag-console__meta">
              <span class="rag-console__meta-item">变体 {{ rag.searchResult.variant_count }}</span>
              <span v-if="rag.searchResult.query_variants?.length" class="rag-console__meta-item rag-console__variants">{{ rag.searchResult.query_variants.join(' / ') }}</span>
              <span v-if="rag.searchResult.rerank_enabled" class="rag-console__meta-item">重排：{{ rag.searchResult.rerank_provider }}</span>
            </div>
            <div v-if="!rag.searchResult.results.length" class="rag-console__empty">
              <strong>没有匹配结果</strong>
              <span>尝试更换关键词、Commit 或检索模式后重新查询。</span>
            </div>
            <div v-else class="table-wrap rag-console__table-wrap">
              <table class="data-table rag-console__table">
              <thead>
                <tr><th>#</th><th>文件</th><th>符号</th><th>来源</th><th>fresh</th><th>score</th><th>精排原因</th></tr>
              </thead>
              <tbody>
                <tr v-for="(r, i) in rag.searchResult.results" :key="r.chunk_id + i">
                  <td>{{ i + 1 }}</td>
                  <td><code>{{ r.file_path }}:{{ r.start_line }}-{{ r.end_line }}</code></td>
                  <td>{{ r.symbol_name || '—' }}</td>
                  <td><span class="pill" :class="'pill--' + r.retrieval_source">{{ r.retrieval_source }}</span></td>
                  <td>{{ r.fresh ? 'fresh' : 'stale' }}</td>
                  <td>{{ Number(r.score).toFixed(4) }}</td>
                  <td class="rag-trace">{{ r.rerank_reason || '—' }}</td>
                </tr>
              </tbody>
              </table>
            </div>
          </div>
        </section>

        <!-- 评测报告面板 -->
        <section class="panel rag-console__panel rag-console__panel--reports" aria-label="企业 RAG 评测报告">
          <header class="panel__header">
            <h2 class="panel__title"><FlaskConical :size="16" /> 评测报告</h2>
            <button class="btn btn--ghost" :disabled="rag.loadingReports" title="刷新报告列表" @click="rag.loadReports()">
              <RotateCcw :size="14" />
            </button>
          </header>

          <div v-if="rag.loadingReports" class="rag-console__loading" aria-live="polite"><LoaderCircle class="spin" :size="16" /> 加载报告列表…</div>
          <div v-else-if="rag.reportsError" class="page-notice page-notice--danger">
            <span class="page-notice__icon"><AlertTriangle :size="16" /></span>
            <div class="page-notice__body">{{ rag.reportsError }}</div>
          </div>
          <div v-else-if="!rag.reports.length" class="rag-console__empty">
            <strong>暂无评测报告</strong>
            <span>完成离线评测后，报告会按验证状态出现在这里。</span>
          </div>
          <div v-else class="rag-console__report-list" role="listbox" :aria-label="'报告列表'">
            <button
              v-for="rep in rag.reports"
              :key="rep.name"
              class="report-item"
              :class="{ 'report-item--active': selectedReportName === rep.name }"
              type="button"
              @click="pickReport(rep.name)"
            >
              <span class="report-item__name">{{ rep.name }}</span>
              <span class="pill" :class="'pill--' + rep.validation_status">{{ statusLabel(rep.validation_status) }}</span>
              <span class="report-item__meta">{{ rep.retrieval_mode }} · {{ rep.dataset_size ?? '?' }} 条</span>
            </button>
          </div>

          <div v-if="rag.loadingReport" class="rag-console__loading" aria-live="polite"><LoaderCircle class="spin" :size="16" /> 加载报告…</div>
          <div v-else-if="rag.reportError" class="page-notice page-notice--danger">
            <span class="page-notice__icon"><AlertTriangle :size="16" /></span>
            <div class="page-notice__body">{{ rag.reportError }}</div>
          </div>
          <div v-else-if="rag.selectedReport" class="rag-console__report">
            <div class="rag-console__report-top">
              <h3 class="report-title">{{ rag.selectedReport.name }}</h3>
              <span class="pill" :class="'pill--' + rag.selectedReport.validation_status">
                {{ rag.selectedReport.validation_status }} · {{ statusLabel(rag.selectedReport.validation_status) }}
              </span>
            </div>
            <div v-if="rag.selectedReport.milvus_verified" class="report-badge report-badge--ok">真实 Milvus 已验证（milvus_verified=true / fallback=false）</div>
            <div v-else class="report-badge report-badge--warn">未验证为真实 Milvus：请以 verified 状态为准，勿当达标</div>
            <div v-if="rag.selectedReport.needs_improvement" class="page-notice page-notice--warning">
              <span class="page-notice__icon"><AlertTriangle :size="16" /></span>
              <div class="page-notice__body">needs_improvement：该报告仍需继续优化，不能显示为达标。</div>
            </div>

            <div class="rag-console__summary" v-if="rag.selectedReport.summary">
              <div v-for="(v, k) in rag.selectedReport.summary" :key="k" class="metric">
                <span class="metric-label">{{ k }}</span>
                <span class="metric-value">{{ fmt(v) }}</span>
              </div>
            </div>

            <template v-if="rag.selectedReport.mode_comparison && Object.keys(rag.selectedReport.mode_comparison).length">
              <h4 class="report-section">mode_comparison</h4>
              <div class="table-wrap rag-console__table-wrap">
                <table class="data-table">
                <thead><tr><th>模式</th><th>recall@K</th><th>precision@K</th><th>mrr</th><th>ndcg@K</th></tr></thead>
                <tbody>
                  <tr v-for="(s, m) in rag.selectedReport.mode_comparison" :key="m">
                    <td>{{ m }}</td>
                    <td>{{ fmt(s.recall_at_k) }}</td>
                    <td>{{ fmt(s.precision_at_k) }}</td>
                    <td>{{ fmt(s.mrr) }}</td>
                    <td>{{ fmt(s.ndcg_at_k) }}</td>
                  </tr>
                </tbody>
                </table>
              </div>
            </template>

            <template v-if="rag.selectedReport.metrics_by_tag && Object.keys(rag.selectedReport.metrics_by_tag).length">
              <h4 class="report-section">metrics_by_tag</h4>
              <div class="table-wrap rag-console__table-wrap">
                <table class="data-table">
                <thead><tr><th>标签</th><th>条数</th><th>recall@K</th><th>precision@K</th><th>mrr</th><th>ndcg@K</th></tr></thead>
                <tbody>
                  <tr v-for="(t, tag) in rag.selectedReport.metrics_by_tag" :key="tag">
                    <td>{{ tag }}</td>
                    <td>{{ t.count }}</td>
                    <td>{{ fmt(t.recall_at_k) }}</td>
                    <td>{{ fmt(t.precision_at_k) }}</td>
                    <td>{{ fmt(t.mrr) }}</td>
                    <td>{{ fmt(t.ndcg_at_k) }}</td>
                  </tr>
                </tbody>
                </table>
              </div>
            </template>

            <template v-if="rag.selectedReport.failed_cases?.length">
              <h4 class="report-section">failed_cases（{{ rag.selectedReport.failed_cases.length }}）</h4>
              <div class="table-wrap rag-console__table-wrap">
                <table class="data-table">
                <thead><tr><th>case</th><th>query</th><th>标签</th><th>期望状态</th><th>实际状态</th><th>status</th><th>retrieval</th></tr></thead>
                <tbody>
                  <tr v-for="(c, i) in rag.selectedReport.failed_cases" :key="i">
                    <td>{{ c.case_id ?? i }}</td>
                    <td class="rag-trace"><code>{{ c.query }}</code></td>
                    <td>{{ (c.tags || []).join(', ') }}</td>
                    <td>{{ c.expected_status }}</td>
                    <td>{{ c.actual_status }}</td>
                    <td>{{ c.status_passed ? 'pass' : 'fail' }}</td>
                    <td>{{ c.retrieval_passed ? 'pass' : 'fail' }}</td>
                  </tr>
                </tbody>
                </table>
              </div>
            </template>
            <div v-else-if="rag.selectedReport.failed_cases !== undefined" class="rag-console__empty rag-console__empty--inline">
              <strong>无失败样本</strong>
              <span>当前报告中的样本均未进入失败列表。</span>
            </div>
          </div>
        </section>
      </div>
    </template>
  </section>
</template>

<style scoped>
.rag-console__grid { display: grid; grid-template-columns: minmax(420px, .9fr) minmax(560px, 1.2fr); gap: 18px; align-items: start; }
.rag-console .page-toolbar { margin-bottom: 22px; }
.rag-console .panel__title { display: flex; align-items: center; gap: 8px; font-size: 16px; }
.rag-console .panel__title svg { color: #85aefc; }
.rag-console__scope { display: inline-flex; align-items: center; min-height: 28px; padding: 0 10px; border: 1px solid var(--theme-border, #dbe3ec); border-radius: 7px; color: var(--theme-text-muted, #65758a); background: var(--theme-surface-subtle, #f7f9fc); font-size: 12px; font-weight: 700; white-space: nowrap; }
@media (max-width: 1080px) { .rag-console__grid { grid-template-columns: 1fr; } }
.rag-console__panel { position: relative; display: flex; flex-direction: column; gap: 0; overflow: hidden; }
.rag-console__panel--search { z-index: 3; overflow: visible; }
.rag-console__panel--reports { z-index: 1; }
.rag-console__panel > .rag-console__form,
.rag-console__panel > .rag-console__results,
.rag-console__panel > .rag-console__report-list,
.rag-console__panel > .rag-console__report,
.rag-console__panel > .rag-console__loading,
.rag-console__panel > .page-notice,
.rag-console__panel > .rag-console__empty { margin-right: 16px; margin-left: 16px; }
.rag-console__panel > .panel__header { margin: 0; padding: 16px 18px 15px; }
.rag-console__form { display: grid; gap: 14px; margin-bottom: 16px; padding-top: 16px; }
.rag-console__row { display: flex; gap: 12px; min-width: 0; }
.rag-console__row > * { min-width: 0; }
.rag-console__row--wrap { flex-wrap: wrap; }
.field-label { display: grid; gap: 5px; min-width: 0; flex: 1 1 0; color: var(--theme-text-muted, #65758a); font-size: 12px; font-weight: 700; }
.field-label--primary > span { color: var(--theme-text, #1f2f46); }
.text-input { width: 100%; min-height: 38px; padding: 8px 10px; border: 1px solid var(--theme-border, #dbe3ec); border-radius: 7px; background: var(--control-bg, var(--theme-surface-base, #fff)); color: var(--theme-text, #1f2f46); box-shadow: inset 0 1px 1px rgba(15, 23, 42, .025); transition: border-color .18s ease, box-shadow .18s ease, background-color .18s ease; }
.text-input::placeholder { color: var(--theme-text-faint, #8795a8); }
.text-input:focus-visible { border-color: var(--theme-border-hover, #cddbf0); outline: 2px solid var(--brand, #2249d6); outline-offset: 1px; }
.rag-console__fieldset { min-width: 0; border: 1px solid var(--theme-border, #dbe3ec); border-radius: 8px; padding: 7px 10px 9px; display: flex; gap: 12px; align-items: center; background: var(--theme-surface-subtle, #f7f9fc); }
.rag-console__fieldset--mode { flex: 1.15 1 190px; }
.rag-console__fieldset--source { flex: 1 1 170px; gap: 8px; }
.rag-console__fieldset legend { padding: 0 4px; color: var(--theme-text-muted, #65758a); font-size: 11px; font-weight: 700; }
.rag-console__select-wrap { display: block; min-width: 0; flex: 1 1 auto; }
.rag-console__select-wrap :deep(.rag-console__mode-select) { position: relative; z-index: 5; }
.rag-console__select-wrap :deep(.rag-console__mode-select) { width: 100%; min-width: 0; }
.rag-console__select-wrap :deep(.rag-console__mode-select .task-filter-select__trigger) { min-height: 38px; padding-left: 11px; border-color: var(--theme-border, #dbe3ec); border-radius: 7px; color: var(--theme-text, #1f2f46); background: var(--control-bg, var(--theme-surface-base, #fff)); box-shadow: inset 0 1px 1px rgba(15, 23, 42, .025); font-size: 13px; }
.rag-console__select-wrap :deep(.rag-console__mode-select .task-filter-select__trigger:hover),
.rag-console__select-wrap :deep(.rag-console__mode-select.is-open .task-filter-select__trigger) { border-color: var(--brand, #2249d6); color: var(--theme-text-heading, #18263a); background: var(--control-bg, var(--theme-surface-base, #fff)); box-shadow: 0 0 0 2px color-mix(in srgb, var(--brand, #2249d6) 18%, transparent), inset 0 1px 1px rgba(15, 23, 42, .025); }
.rag-console__select-wrap :deep(.rag-console__mode-select .task-filter-select__trigger:focus-visible) { border-color: var(--brand, #2249d6); box-shadow: 0 0 0 2px color-mix(in srgb, var(--brand, #2249d6) 22%, transparent), inset 0 1px 1px rgba(15, 23, 42, .025); }
.rag-console__select-wrap :deep(.rag-console__mode-select .task-filter-select__menu) { left: 0; width: 100%; min-width: 204px; padding: 6px; border-color: color-mix(in srgb, var(--brand, #2249d6) 34%, var(--panel-border, #2a4055)); border-radius: 8px; background: var(--panel-bg, #101a2b); box-shadow: 0 18px 34px rgba(0, 0, 0, .26), 0 6px 14px rgba(0, 0, 0, .12); }
.rag-console__select-wrap :deep(.rag-console__mode-select .task-filter-select__option) { min-height: 36px; padding: 0 10px; border-radius: 6px; color: var(--text-base, #c7d4e3); font-size: 13px; }
.rag-console__select-wrap :deep(.rag-console__mode-select .task-filter-select__option.is-selected) { color: var(--text-strong, #edf4ff); background: color-mix(in srgb, var(--brand, #2249d6) 14%, transparent); font-weight: 700; }
.rag-console__select-wrap :deep(.rag-console__mode-select .task-filter-select__option:hover),
.rag-console__select-wrap :deep(.rag-console__mode-select .task-filter-select__option.is-hovered),
.rag-console__select-wrap :deep(.rag-console__mode-select .task-filter-select__option.is-active:not(.is-selected)) { border-color: color-mix(in srgb, var(--brand, #2249d6) 42%, transparent); color: var(--text-strong, #edf4ff); background: color-mix(in srgb, var(--brand, #2249d6) 22%, transparent); }
.rag-console__select-wrap :deep(.rag-console__mode-select .task-filter-select__option.is-selected svg) { color: var(--brand, #7aa7ff); }
.rag-console__select-wrap :deep(.rag-console__mode-select .task-filter-select__chevron) { color: var(--theme-text-muted, #65758a); }
.rag-console__select-wrap :deep(.rag-console__mode-select.is-open .task-filter-select__chevron) { color: var(--brand, #7aa7ff); }
.check { display: inline-flex; gap: 7px; align-items: center; min-height: 36px; padding: 0 9px; border: 1px solid transparent; border-radius: 7px; color: var(--theme-text-muted, #65758a); background: transparent; font-size: 13px; font-weight: 600; cursor: pointer; transition: border-color .18s ease, background-color .18s ease, color .18s ease; }
.check:hover { border-color: var(--theme-border-hover, #cddbf0); background: var(--theme-surface-hover, #f4f8ff); color: var(--theme-text, #1f2f46); }
.check:has(input:checked) { border-color: color-mix(in srgb, var(--brand, #2249d6) 30%, transparent); background: color-mix(in srgb, var(--brand, #2249d6) 11%, transparent); color: var(--theme-text-heading, #18263a); }
.check input { width: 15px; height: 15px; margin: 0; accent-color: var(--brand, #2249d6); }
.rag-console__actions { display: flex; gap: 8px; }
.btn { display: inline-flex; align-items: center; justify-content: center; gap: 6px; min-height: 36px; padding: 0 14px; border-radius: 7px; border: 1px solid transparent; cursor: pointer; font-weight: 700; }
.btn--primary { background: var(--brand, #2249d6); color: #fff; box-shadow: 0 5px 12px rgba(34, 73, 214, .16); }
.btn--primary:disabled { opacity: .6; cursor: not-allowed; }
.btn--ghost { background: var(--theme-surface-subtle, #f7f9fc); color: var(--theme-text, #1f2f46); border-color: var(--theme-border, #dbe3ec); }
.btn:focus-visible { outline: 2px solid var(--brand, #2249d6); outline-offset: 2px; }
.rag-console__loading { display: flex; align-items: center; gap: 8px; color: var(--theme-text-muted, #65758a); padding: 10px 0; }
.rag-console__panel > .rag-console__loading { margin-top: 14px; margin-bottom: 16px; }
.rag-console__meta { display: flex; flex-wrap: wrap; gap: 7px; align-items: center; font-size: 12px; color: var(--theme-text-muted, #65758a); }
.rag-console__meta-item { display: inline-flex; align-items: center; min-height: 24px; max-width: 100%; padding: 0 8px; border: 1px solid var(--theme-border-subtle, #e9eef4); border-radius: 5px; background: var(--theme-surface-subtle, #f7f9fc); }
.rag-console__variants { overflow: hidden; font-family: ui-monospace, monospace; text-overflow: ellipsis; white-space: nowrap; }
.rag-console__results { display: grid; gap: 12px; margin-bottom: 16px; }
.rag-console__empty { display: grid; gap: 4px; min-height: 112px; align-content: center; padding: 16px 0; color: var(--theme-text-muted, #65758a); }
.rag-console__empty strong { color: var(--theme-text, #1f2f46); font-size: 13px; }
.rag-console__empty span { font-size: 11px; line-height: 16px; }
.rag-console__empty--inline { min-height: 60px; }
.rag-console__table code, .rag-trace { font-family: ui-monospace, monospace; font-size: 12px; }
.rag-console__table-wrap { min-width: 0; overflow: auto; border: 1px solid var(--theme-border-subtle, #e9eef4); border-radius: 8px; }
.rag-console__table { min-width: 760px; }
.rag-console__table thead th { position: sticky; top: 0; z-index: 1; background: var(--theme-surface-header, #f7faff); }
.pill { display: inline-flex; align-items: center; min-height: 23px; padding: 2px 8px; border: 1px solid transparent; border-radius: 6px; font-size: 11px; font-weight: 700; white-space: nowrap; }
.pill--hybrid { background: #0B806A; } .pill--vector { background: #1D4ED8; } .pill--bm25 { background: #7C3AED; }
.pill--rerank { background: #B45309; }
.pill--verified { border-color: rgba(47, 214, 164, .36); color: #6de3bb; background: rgba(11, 128, 106, .2); }
.pill--local_only { border-color: rgba(235, 153, 68, .4); color: #f6bd78; background: rgba(180, 83, 9, .2); }
.pill--blocked { border-color: rgba(247, 117, 108, .4); color: #ffaaa3; background: rgba(180, 35, 24, .2); }
.rag-console__report { display: flex; flex-direction: column; gap: 12px; }
.rag-console__report-top { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
.report-title { font-size: 15px; font-weight: 800; }
.report-badge { padding: 7px 10px; border: 1px solid transparent; border-radius: 7px; font-size: 12px; font-weight: 700; line-height: 17px; }
.report-badge--ok { border-color: var(--theme-accent-border, #b9cef1); background: var(--theme-accent-soft, #eef4ff); color: var(--theme-link, #2f61c6); }
.report-badge--warn { border-color: var(--theme-warning-border, #f5d28a); background: var(--theme-warning-surface, #fff7e6); color: var(--theme-warning-text, #8a5b00); }
.rag-console__summary { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 8px 12px; }
.metric { display: grid; gap: 3px; min-width: 0; padding: 9px 10px; border: 1px solid var(--theme-border-subtle, #e9eef4); border-radius: 7px; background: var(--theme-surface-subtle, #f7f9fc); }
.metric-label { overflow-wrap: anywhere; color: var(--theme-text-muted, #65758a); font-size: 11px; text-transform: uppercase; letter-spacing: .02em; }
.metric-value { color: var(--theme-text, #1f2f46); font-family: ui-monospace, monospace; font-size: 16px; font-weight: 800; }
.report-section { margin: 6px 0 0; padding-top: 4px; border-top: 1px solid var(--theme-border-subtle, #e9eef4); color: var(--theme-text-muted, #65758a); font-size: 12px; text-transform: uppercase; }
.rag-console__report-list { display: flex; flex-direction: column; gap: 7px; margin-top: 14px; margin-bottom: 16px; }
.report-item { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 5px 10px; align-items: center; min-height: 62px; padding: 10px 12px; border: 1px solid var(--theme-border, #dbe3ec); border-radius: 8px; background: var(--theme-surface-raised, var(--theme-surface-base, #fff)); color: var(--theme-text, #1f2f46); text-align: left; cursor: pointer; transition: border-color .18s ease, background-color .18s ease, transform .18s ease; }
.report-item:hover, .report-item:focus-visible { border-color: rgba(133, 174, 252, .62); outline: none; background: var(--theme-surface-hover, #f4f8ff); transform: translateY(-1px); }
.report-item--active { border-color: rgba(133, 174, 252, .72); background: var(--theme-surface-hover, #f4f8ff); }
.report-item__name { min-width: 0; overflow: hidden; font-family: ui-monospace, monospace; font-size: 12px; font-weight: 700; line-height: 18px; text-overflow: ellipsis; white-space: nowrap; }
.report-item__meta { grid-column: 1 / -1; color: var(--theme-text-muted, #65758a); font-size: 11px; line-height: 16px; }
.spin { animation: rsp 1s linear infinite; }
@keyframes rsp { to { transform: rotate(360deg); } }
@media (max-width: 680px) {
  .rag-console__row {
    flex-direction: column;
  }
  .rag-console__row--wrap {
    flex-direction: row;
  }
  .rag-console__fieldset {
    flex: 1 1 100%;
    min-width: 0;
  }
  .rag-console__actions .btn {
    width: 100%;
  }
  .rag-console__report-top {
    align-items: flex-start;
    flex-direction: column;
  }
  .rag-console__summary {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
@media (max-width: 420px) {
  .rag-console__summary {
    grid-template-columns: 1fr;
  }
}
</style>
