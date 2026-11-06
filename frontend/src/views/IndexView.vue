<script setup lang="ts">
import { AlertTriangle, CheckCircle2, Database, LoaderCircle, RefreshCw, RotateCcw, Search, X } from '@lucide/vue'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ApiError, apiRequest } from '../services/api'
import { useAuthStore } from '../stores/auth'
import TaskFilterSelect from '../components/TaskFilterSelect.vue'
import type { TaskFilterOption } from '../components/TaskFilterSelect.vue'
import type { IndexTriggerResult, Project, RepositoryConfig, RepositoryIndex, RepositoryIndexStatus } from '../types/domain'
import { connectRealtime, subscribeRealtime } from '../services/realtime'

const RUNNING_STAGES: RepositoryIndexStatus[] = ['PENDING', 'SYNCING', 'PARSING', 'EMBEDDING', 'STORING']

const INDEX_STATUS_TEXT: Record<RepositoryIndexStatus, string> = {
  PENDING: '排队中',
  SYNCING: '同步仓库',
  PARSING: '解析切片',
  EMBEDDING: '向量化中',
  STORING: '存储入库',
  COMPLETED: '已完成',
  FAILED: '失败',
}

const auth = useAuthStore()
const route = useRoute()
const keyword = ref('')
const loading = ref(false)
const submitting = ref(false)
const projects = ref<Project[]>([])
const repositories = ref<RepositoryConfig[]>([])
const selectedRepositoryId = ref('')
const indexes = ref<RepositoryIndex[]>([])
const notice = ref<{ tone: 'success' | 'info' | 'danger'; title: string; detail: string } | null>(null)
const modal = ref<{ mode: 'create' | 'rebuild' } | null>(null)
const detailIndex = ref<RepositoryIndex | null>(null)
const branchInput = ref('')
const commitInput = ref('')
let pollTimer: number | null = null
let unsubscribeRealtime: (() => void) | undefined

const selectedRepository = computed(() => repositories.value.find((item) => item.id === selectedRepositoryId.value) ?? null)
const repositoryOptions = computed<TaskFilterOption[]>(() => repositories.value.map((item) => ({
  value: item.id,
  label: `${item.name} · ${item.default_branch}`,
})))
const runningCount = computed(() => indexes.value.filter((item) => RUNNING_STAGES.includes(item.status)).length)
const filteredIndexes = computed(() =>
  indexes.value.filter((item) => `${item.commit_sha}${item.status}${item.requested_by_name}${item.base_branch}${item.error_message ?? ''}`.toLowerCase().includes(keyword.value.toLowerCase())),
)
const latestCompleted = computed(() => indexes.value.find((item) => item.status === 'COMPLETED') ?? null)

function fmt(value: string | null | undefined) {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  const pad = (num: number) => String(num).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

function shortSha(sha: string) {
  return sha.length > 12 ? sha.slice(0, 12) : sha
}

function statusTone(status: RepositoryIndexStatus) {
  if (status === 'COMPLETED') return 'success'
  if (status === 'FAILED') return 'danger'
  return 'info'
}

function isRunning(status: RepositoryIndexStatus) {
  return RUNNING_STAGES.includes(status)
}

function statusText(item: RepositoryIndex) {
  if (item.error_stage && item.status === 'FAILED') return `${INDEX_STATUS_TEXT[item.status]}（${item.error_stage} 阶段）`
  return INDEX_STATUS_TEXT[item.status]
}

function noticeOf(title: string, detail: string, tone: 'success' | 'info' | 'danger' = 'success') {
  notice.value = { tone, title, detail }
}

function apiMessage(error: unknown, fallback: string) {
  return error instanceof ApiError ? error.message : fallback
}

async function loadRepositories() {
  loading.value = true
  try {
    projects.value = await apiRequest<Project[]>('/api/projects')
    const all: RepositoryConfig[] = []
    for (const project of projects.value) {
      const list = await apiRequest<RepositoryConfig[]>(`/api/repositories?project_id=${encodeURIComponent(project.id)}`)
      all.push(...list)
    }
    repositories.value = all
    const queryId = typeof route.query.repository_id === 'string' ? route.query.repository_id : ''
    const preferred = all.find((item) => item.id === queryId) ?? all.find((item) => item.name === queryId) ?? all[0]
    if (preferred) {
      selectedRepositoryId.value = preferred.id
    }
  } catch (error) {
    noticeOf('仓库配置加载失败', apiMessage(error, '请确认后端服务已启动且账号有 index:view 权限'), 'danger')
  } finally {
    loading.value = false
  }
}

async function loadIndexes() {
  if (!selectedRepositoryId.value) return
  loading.value = true
  try {
    const records = await apiRequest<RepositoryIndex[]>(`/api/repositories/${encodeURIComponent(selectedRepositoryId.value)}/indexes`)
    indexes.value = records
    syncPolling()
  } catch (error) {
    noticeOf('索引历史加载失败', apiMessage(error, '请稍后重试'), 'danger')
  } finally {
    loading.value = false
  }
}

function syncPolling() {
  if (runningCount.value > 0 && pollTimer === null) {
    pollTimer = window.setInterval(() => void loadIndexes(), 3000)
  } else if (runningCount.value === 0 && pollTimer !== null) {
    window.clearInterval(pollTimer)
    pollTimer = null
  }
}

function stopPolling() {
  if (pollTimer !== null) {
    window.clearInterval(pollTimer)
    pollTimer = null
  }
}

watch(selectedRepositoryId, () => {
  indexes.value = []
  void loadIndexes()
})

onMounted(() => {
  void loadRepositories()
  connectRealtime()
  unsubscribeRealtime = subscribeRealtime((event) => {
    if (event.resource_type === 'repository_index' || event.type === 'repository_index.updated') {
      void loadIndexes()
    }
  })
})

onBeforeUnmount(() => {
  stopPolling()
  unsubscribeRealtime?.()
  unsubscribeRealtime = undefined
})

function openModal(mode: 'create' | 'rebuild', record: RepositoryIndex | null = null) {
  const branch = record?.base_branch ?? selectedRepository.value?.default_branch ?? 'main'
  branchInput.value = branch
  commitInput.value = mode === 'rebuild' ? '' : (record && record.status !== 'FAILED' ? record.commit_sha : '')
  modal.value = { mode }
  detailIndex.value = null
}

async function submitModal() {
  if (!modal.value || !selectedRepositoryId.value || submitting.value) return
  submitting.value = true
  const { mode } = modal.value
  const body: { base_branch?: string; commit_sha?: string; force_rebuild?: boolean } = {}
  const branch = branchInput.value.trim()
  if (branch) body.base_branch = branch
  const commit = commitInput.value.trim()
  if (commit) body.commit_sha = commit
  if (mode === 'create') body.force_rebuild = false
  try {
    const path = `/api/repositories/${encodeURIComponent(selectedRepositoryId.value)}/index${mode === 'rebuild' ? '/rebuild' : ''}`
    const result = await apiRequest<IndexTriggerResult>(path, { method: 'POST', body: JSON.stringify(body) })
    const actionText = mode === 'rebuild' ? '索引重建任务已创建' : '索引任务已创建'
    noticeOf(
      result.repository_name
        ? `${result.repository_name} · ${actionText}`
        : actionText,
      `Commit ${shortSha(result.commit_sha)} · 异步排队执行，无需停留等待；刷新页面或本页自动轮询可查看进度。`,
    )
    modal.value = null
    indexes.value = []
    void loadIndexes()
  } catch (error) {
    noticeOf('操作失败', apiMessage(error, '请稍后重试'), 'danger')
  } finally {
    submitting.value = false
  }
}

async function retryIndex(item: RepositoryIndex) {
  try {
    const record = await apiRequest<RepositoryIndex>(
      `/api/repositories/${encodeURIComponent(item.repository_id)}/indexes/${encodeURIComponent(item.id)}/retry`,
      { method: 'POST' },
    )
    noticeOf('已重新排队', `索引任务 ${item.id} 已重置为排队状态，将自动重新执行。`)
    void loadIndexes()
    void record
  } catch (error) {
    noticeOf('重试失败', apiMessage(error, '仅失败状态的索引任务可以重试'), 'danger')
  }
}

function inspect(item: RepositoryIndex) {
  detailIndex.value = item
}

function manualRefresh() {
  noticeOf('索引状态已刷新', runningCount.value > 0 ? `当前有 ${runningCount.value} 个任务在处理中，页面将每 3 秒自动同步。` : '已同步仓库索引任务状态。', 'info')
  void loadIndexes()
}
</script>

<template>
  <section class="page-content index-page">
    <div class="page-toolbar page-toolbar--stacked">
      <div>
        <p>按仓库与 Commit 管理索引版本，避免使用过期上下文。</p>
        <div class="index-page__repo-bar">
          <label>
            <span>仓库</span>
            <TaskFilterSelect v-model="selectedRepositoryId" :options="repositoryOptions" ariaLabel="仓库" :disabled="!repositories.length || loading" class="index-repository-select" />
          </label>
          <span v-if="selectedRepository" class="outline-tag">{{ selectedRepository.default_branch }}</span>
          <span v-if="runningCount" class="status-tag info">处理中 {{ runningCount }} 个 · 3s 自动刷新</span>
        </div>
      </div>
      <div class="toolbar-actions">
        <button class="button button--secondary" :disabled="loading" @click="manualRefresh"><RefreshCw :size="16" :class="{ 'is-spinning': loading }" /> {{ loading ? '同步中' : '刷新' }}</button>
        <button v-if="auth.hasPermission('index:manage')" class="button button--secondary" :disabled="!selectedRepository" @click="openModal('rebuild')"><RotateCcw :size="16" /> 重建索引</button>
        <button v-if="auth.hasPermission('index:manage')" class="button button--primary" :disabled="!selectedRepository" @click="openModal('create')"><Database :size="16" /> 触发索引</button>
      </div>
    </div>

    <div v-if="notice" class="page-notice" :class="`page-notice--${notice.tone}`">
      <span class="page-notice__icon">
        <CheckCircle2 v-if="notice.tone === 'success'" :size="16" />
        <AlertTriangle v-else-if="notice.tone === 'danger'" :size="16" />
        <RefreshCw v-else :size="16" />
      </span>
      <div class="page-notice__body">
        <strong>{{ notice.title }}</strong>
        <p>{{ notice.detail }}</p>
      </div>
      <button class="icon-button" aria-label="关闭提示" @click="notice = null"><X :size="18" /></button>
    </div>

    <article class="panel">
      <header class="panel__header">
        <div>
          <h2>仓库索引</h2>
          <p>同一仓库同一 Commit 只允许一个索引任务运行；过期索引仅供人工查看来源，不会被自动 Diff 使用。</p>
        </div>
        <label class="search-field"><Search :size="15" /><input v-model="keyword" placeholder="筛选 Commit / 状态 / 发起人" /></label>
      </header>
      <div class="table-wrap">
        <table class="data-table data-table--index">
          <thead>
            <tr>
              <th>Commit SHA</th>
              <th>分支</th>
              <th>状态</th>
              <th>文件数</th>
              <th>切片数</th>
              <th>进度</th>
              <th>发起人</th>
              <th>失败原因</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in filteredIndexes" :key="item.id" @click="inspect(item)">
              <td>
                <strong class="mono" :title="item.commit_sha">{{ shortSha(item.commit_sha) }}</strong>
                <small>{{ item.mode === 'full' ? '全量索引' : '增量索引' }}{{ item.retry_count > 0 ? ` · 已重试 ${item.retry_count} 次` : '' }}</small>
              </td>
              <td class="mono">{{ item.base_branch }}</td>
              <td><span class="status-tag" :class="statusTone(item.status)">{{ statusText(item) }}</span></td>
              <td>{{ item.total_files }}</td>
              <td>{{ item.chunk_count }}</td>
              <td class="mono">{{ isRunning(item.status) || item.status === 'COMPLETED' ? `${item.processed_files} / ${item.total_files}` : '—' }}</td>
              <td>
                <strong>{{ item.requested_by_name }}</strong>
                <small>{{ fmt(item.requested_at) }}</small>
              </td>
              <td class="table-error" :title="item.error_message ?? ''">{{ item.status === 'FAILED' ? item.error_message || '索引任务失败' : '—' }}</td>
              <td>
                <template v-if="item.status === 'FAILED' && auth.hasPermission('index:manage')">
                  <button class="text-button" @click.stop="retryIndex(item)">重试</button>
                  <span class="index-row-divider">·</span>
                </template>
                <button class="text-button" @click.stop="inspect(item)">详情</button>
              </td>
            </tr>
            <tr v-if="!loading && !filteredIndexes.length">
              <td colspan="9" class="index-empty-cell">
                {{ keyword ? '没有匹配的索引记录' : selectedRepository ? '该仓库还没有索引记录，点击右上角「触发索引」创建第一个索引任务。' : '请先选择仓库（仓库配置在项目配置页维护）。' }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </article>

    <div v-if="modal && auth.hasPermission('index:manage')" class="modal-backdrop" @click.self="modal = null">
      <section class="confirm-modal index-create-modal">
        <header>
          <div>
            <p class="section-kicker">{{ modal.mode === 'rebuild' ? '高影响操作' : '创建索引' }}</p>
            <h2>{{ modal.mode === 'rebuild' ? '确认重建索引？' : '为仓库生成新的索引任务' }}</h2>
          </div>
          <button class="icon-button" @click="modal = null"><X :size="18" /></button>
        </header>
        <div class="confirm-modal__body index-create-grid">
          <div class="index-create-form">
            <div v-if="modal.mode === 'rebuild'" class="risk-callout">
              <AlertTriangle :size="19" />
              <div>
                <strong>重建会生成新的检索版本</strong>
                <p>新索引完成后才会被新任务复用；当前旧索引仍保留，仅供人工查看检索来源（不会被自动分析使用）。</p>
              </div>
            </div>
            <div v-else class="form-alert">
              <AlertTriangle :size="17" />
              <span>处理为异步排队执行：触发后无需停留等待，可离开本页稍后查看进度与结果。</span>
            </div>
            <div class="form-grid">
              <label class="form-grid__full"><span>仓库</span><input :value="selectedRepository?.name ?? ''" disabled /></label>
              <label><span>分支</span><input v-model="branchInput" class="mono" placeholder="默认 main" /></label>
              <label><span>Commit SHA（可空）</span><input v-model="commitInput" class="mono" :placeholder="modal.mode === 'rebuild' ? '留空 = 分支最新 Commit' : '留空 = 分支最新 Commit'" /></label>
            </div>
            <p class="index-create-hint">{{ modal.mode === 'rebuild' ? '同一仓库同一 Commit 的完成索引不会被重复创建；重建始终创建新版本任务。' : '若该 Commit 已有运行中或已完成的索引，后端会直接返回现有任务，不会重复排队。' }}</p>
          </div>
          <aside class="index-create-preview" :class="{ 'index-rebuild-preview': modal.mode === 'rebuild' }">
            <p class="section-kicker">{{ modal.mode === 'rebuild' ? '影响预览' : '当前最新完成' }}</p>
            <h3 class="mono">{{ selectedRepository?.name ?? '—' }}</h3>
            <dl v-if="latestCompleted" class="compact-dl">
              <div><dt>Commit</dt><dd class="mono">{{ shortSha(latestCompleted.commit_sha) }}</dd></div>
              <div><dt>文件 / 切片</dt><dd>{{ latestCompleted.total_files }} / {{ latestCompleted.chunk_count }}</dd></div>
              <div><dt>完成时间</dt><dd>{{ fmt(latestCompleted.finished_at) }}</dd></div>
            </dl>
            <p v-else class="index-create-hint">该仓库还没有完成过索引任务。</p>
            <div class="index-create-preview__note">
              <strong>{{ modal.mode === 'rebuild' ? '重建模式：full' : '触发任务' }}</strong>
              <p>{{ selectedRepository?.default_branch ?? 'main' }} · {{ commitInput.trim() ? shortSha(commitInput.trim()) : '分支最新' }}</p>
            </div>
          </aside>
        </div>
        <footer>
          <button class="button button--secondary" :disabled="submitting" @click="modal = null">取消</button>
          <button class="button button--primary" :disabled="submitting" @click="submitModal">
            <LoaderCircle v-if="submitting" class="button__spinner" :size="16" />
            <Database v-else :size="16" />
            {{ submitting ? '提交中' : modal.mode === 'rebuild' ? '确认重建' : '确认触发' }}
          </button>
        </footer>
      </section>
    </div>

    <div v-if="detailIndex" class="modal-backdrop" @click.self="detailIndex = null">
      <section class="confirm-modal index-detail-modal">
        <header>
          <div>
            <p class="section-kicker">索引详情</p>
            <h2 class="mono">{{ detailIndex.id }}</h2>
          </div>
          <button class="icon-button" @click="detailIndex = null"><X :size="18" /></button>
        </header>
        <div class="confirm-modal__body index-detail-body">
          <div class="index-detail-meta">
            <p class="index-detail-meta__path mono">{{ detailIndex.base_branch }} · {{ detailIndex.commit_sha }}</p>
            <div class="tag-row">
              <span class="status-tag" :class="statusTone(detailIndex.status)">{{ statusText(detailIndex) }}</span>
              <span class="outline-tag">{{ detailIndex.mode === 'full' ? '全量' : '增量' }}</span>
              <span v-if="detailIndex.status === 'FAILED' && detailIndex.error_stage" class="outline-tag">失败阶段：{{ detailIndex.error_stage }}</span>
            </div>
          </div>
          <dl class="compact-dl index-detail-stats">
            <div><dt>文件数 / 处理</dt><dd class="mono">{{ detailIndex.total_files }} / {{ detailIndex.processed_files }}</dd></div>
            <div><dt>切片数</dt><dd>{{ detailIndex.chunk_count }}</dd></div>
            <div><dt>发起人</dt><dd>{{ detailIndex.requested_by_name }}</dd></div>
            <div><dt>发起 / 开始 / 完成</dt><dd class="mono">{{ fmt(detailIndex.requested_at) }} / {{ fmt(detailIndex.started_at) }} / {{ fmt(detailIndex.finished_at) }}</dd></div>
            <div><dt>失败原因</dt><dd class="mono index-detail-error">{{ detailIndex.status === 'FAILED' ? detailIndex.error_message || '索引任务失败' : '—' }}</dd></div>
          </dl>
        </div>
        <footer>
          <button class="button button--secondary" @click="detailIndex = null">关闭</button>
          <button v-if="detailIndex.status === 'FAILED' && auth.hasPermission('index:manage')" class="button button--secondary" @click="retryIndex(detailIndex); detailIndex = null">重试索引</button>
          <button v-if="auth.hasPermission('index:manage')" class="button button--primary" @click="openModal('rebuild', detailIndex)">重建索引</button>
        </footer>
      </section>
    </div>
  </section>
</template>
