<script setup lang="ts">
import { FileSearch, LoaderCircle, RefreshCw, RotateCcw, Send, X } from '@lucide/vue'
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { ApiError, apiRequest } from '../services/api'
import { useAuthStore } from '../stores/auth'
import { useWorkspaceStore } from '../stores/workspace'
import type { CodeContext, ContextResumeResult, ManualContextResult, RepositoryIndex, RepositoryIndexStatus, SimilarTaskRef, Task, TaskContextsResult, TaskSimilarResult } from '../types/domain'

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

const props = defineProps<{ task: Task }>()
const emit = defineEmits<{ (event: 'refresh'): void }>()

const store = useWorkspaceStore()
const auth = useAuthStore()
const router = useRouter()

const contexts = ref<CodeContext[]>([])
const meta = ref<{ mode: 'auto' | 'manual'; empty_reason: string | null; fresh: boolean | null; threshold?: number; top_k?: number }>({ mode: 'auto', empty_reason: null, fresh: null })
const contextsLoading = ref(false)
const contextsError = ref('')
const recordError = ref('')
const manualOpen = ref(false)
const manualPathText = ref('')
const manualSubmitting = ref(false)
const manualError = ref('')
const resumeSubmitting = ref(false)
const resumeError = ref('')
const similarTasks = ref<SimilarTaskRef[]>([])
const similarLoading = ref(false)
let pollTimer: number | null = null
let lastTerminalStatus: RepositoryIndexStatus | null = null

const relatedIndexId = computed(() => props.task.relatedIndexId ?? null)
const repositoryId = computed(() => props.task.repositoryId ?? null)
const waitOnIndex = computed(() => Boolean(props.task.nextAction === 'wait_index' && relatedIndexId.value && repositoryId.value))
const indexFailedNode = computed(() => props.task.status === 'FAILED' && props.task.failureNode === '代码索引')
const record = computed(() => (relatedIndexId.value ? store.indexById(relatedIndexId.value) : null) ?? null)
const recordRunning = computed(() => Boolean(record.value && RUNNING_STAGES.includes(record.value.status)))
const contextCapableStatus = computed(() => !['CREATED'].includes(props.task.status))
const indexUrl = computed(() => (repositoryId.value && relatedIndexId.value ? `/api/repositories/${encodeURIComponent(repositoryId.value)}/indexes/${encodeURIComponent(relatedIndexId.value)}` : null))
const contextsUrl = computed(() => (props.task.id ? `/api/tasks/${encodeURIComponent(props.task.id)}/contexts?top_k=5` : null))
const manualUrl = computed(() => (props.task.id ? `/api/tasks/${encodeURIComponent(props.task.id)}/contexts/manual` : null))
const resumeUrl = computed(() => (props.task.id ? `/api/tasks/${encodeURIComponent(props.task.id)}/contexts/resume` : null))
const similarUrl = computed(() => (props.task.id ? `/api/tasks/${encodeURIComponent(props.task.id)}/similar` : null))
// DEV-017：仅上下文聚合阶段的 context_missing 走本面板补充；answer_required 由分析问题面板处理
const contextMissing = computed(() => Boolean(props.task.manualRequired && props.task.manualReason === 'context_missing'))
const contextsSource = computed(() => contexts.value)
const primaryContexts = computed(() => contextsSource.value.slice(0, 3))
const secondaryContexts = computed(() => contextsSource.value.slice(3))
const staleBanner = computed(() => {
  const indexFresh = record.value ? record.value.commit_sha === (props.task.baseCommitSha || record.value.commit_sha) : null
  const anyStale = contextsSource.value.length > 0 && contextsSource.value.some((item) => !item.fresh)
  return meta.value.fresh === false || anyStale || (record.value?.status === 'COMPLETED' && indexFresh === false)
})
const canOperate = computed(() => auth.hasPermission('task:operate'))
const canManageIndex = computed(() => auth.hasPermission('index:manage'))
const headerStatusText = computed(() => {
  if (waitOnIndex.value) return '任务等待仓库索引完成'
  if (recordRunning.value) return '索引任务处理中'
  if (indexFailedNode.value) return '任务在代码索引节点失败'
  if (contextsSource.value.length) return '核心相关文件'
  if (meta.value.empty_reason) return '检索无结果'
  return '代码上下文'
})
const contextSourceModeText = computed(() => {
  if (!contextsSource.value.length) return ''
  if (meta.value.mode === 'manual') return '人工指定来源'
  const topK = meta.value.top_k ?? contextsSource.value.length
  const threshold = meta.value.threshold !== undefined ? ` · 阈值 ${meta.value.threshold}` : ''
  return `自动检索 Top ${topK}${threshold}`
})

function shortSha(sha: string) {
  return sha.length > 12 ? sha.slice(0, 12) : sha
}

function scoreText(score: number | null) {
  return score === null || score === undefined ? '手动' : `${Math.round(score * 100)}%`
}

function apiMessage(error: unknown, fallback: string) {
  return error instanceof ApiError ? error.message : fallback
}

function syncFromTask() {
  contexts.value = props.task.codeContexts ? [...props.task.codeContexts] : []
  meta.value = { mode: 'auto', empty_reason: null, fresh: null }
  contextsError.value = ''
}

watch(
  () => [props.task.id, props.task.status, props.task.codeContexts?.length ?? 0],
  () => {
    syncFromTask()
    manualError.value = ''
    resumeError.value = ''
    similarTasks.value = props.task.similarTaskRefs ? [...props.task.similarTaskRefs] : []
  },
  { immediate: true },
)

watch(
  waitOnIndex,
  (waiting) => {
    if (waiting) {
      lastTerminalStatus = null
      void fetchIndexRecord()
      startPolling()
    } else {
      stopPolling()
    }
  },
  { immediate: true },
)

onBeforeUnmount(() => {
  stopPolling()
})

function startPolling() {
  if (pollTimer !== null) return
  pollTimer = window.setInterval(() => void fetchIndexRecord(), 3000)
}

function stopPolling() {
  if (pollTimer !== null) {
    window.clearInterval(pollTimer)
    pollTimer = null
  }
}

async function fetchIndexRecord() {
  if (!indexUrl.value) return
  recordError.value = ''
  try {
    const next = await apiRequest<RepositoryIndex>(indexUrl.value)
    store.mergeRepositoryIndex(next)
    if (next.status === 'COMPLETED' || next.status === 'FAILED') {
      if (lastTerminalStatus !== next.status) {
        lastTerminalStatus = next.status
        emit('refresh')
      }
    }
  } catch (error) {
    recordError.value = apiMessage(error, '索引状态同步失败')
  }
}

async function refreshContexts() {
  if (!contextsUrl.value || contextsLoading.value) return
  contextsLoading.value = true
  contextsError.value = ''
  try {
    const result = await apiRequest<TaskContextsResult>(contextsUrl.value)
    contexts.value = result.contexts ?? []
    meta.value = {
      mode: result.mode ?? 'auto',
      empty_reason: result.empty_reason ?? null,
      fresh: result.fresh ?? null,
      threshold: result.threshold,
      top_k: result.top_k,
    }
    if (!contexts.value.length && !result.empty_reason) {
      meta.value.empty_reason = '检索未返回任何代码片段'
    }
  } catch (error) {
    contextsError.value = apiMessage(error, '检索上下文失败')
  } finally {
    contextsLoading.value = false
  }
}

function toggleManual() {
  manualOpen.value = !manualOpen.value
  manualError.value = ''
}

async function submitManual() {
  if (!manualUrl.value || manualSubmitting.value) return
  const paths = manualPathText.value
    .split(/\r?\n/)
    .map((line) => line.trim().replace(/^\.\//, '').replace(/\/$/, ''))
    .filter(Boolean)
  manualError.value = ''
  if (!paths.length) {
    manualError.value = '请填写至少一个文件路径'
    return
  }
  if (paths.length > 8) {
    manualError.value = '单次最多指定 8 个文件路径'
    return
  }
  manualSubmitting.value = true
  try {
    const result = await apiRequest<ManualContextResult>(manualUrl.value, {
      method: 'POST',
      body: JSON.stringify({ file_paths: paths }),
    })
    contexts.value = result.contexts ?? []
    meta.value = { mode: 'manual', empty_reason: null, fresh: result.fresh }
    manualOpen.value = false
    manualPathText.value = ''
    if (result.resumed) {
      ElMessage.success('已补充代码上下文，任务恢复自动流转')
      emit('refresh')
    } else {
      ElMessage.success(`已按 ${paths.length} 个文件生成人工核对上下文`)
    }
  } catch (error) {
    manualError.value = apiMessage(error, '手动指定文件失败')
  } finally {
    manualSubmitting.value = false
  }
}

async function resumeContexts() {
  if (!resumeUrl.value || resumeSubmitting.value) return
  resumeSubmitting.value = true
  resumeError.value = ''
  try {
    const result = await apiRequest<ContextResumeResult>(resumeUrl.value, { method: 'POST', body: '{}' })
    contexts.value = result.contexts ?? []
    meta.value = { mode: 'auto', empty_reason: result.resumed ? null : result.empty_reason ?? '仍未命中达到阈值的代码片段', fresh: null }
    if (result.resumed) {
      ElMessage.success('已检索到代码上下文，任务恢复自动流转')
      emit('refresh')
    } else if (result.index_status && RUNNING_STAGES.includes(result.index_status)) {
      ElMessage.info('仓库索引仍在处理中，完成后任务会自动继续')
    } else {
      ElMessage.warning('重新检索仍未命中，可手动指定文件或重建索引后继续')
    }
  } catch (error) {
    resumeError.value = apiMessage(error, '重新检索失败')
  } finally {
    resumeSubmitting.value = false
  }
}

async function fetchSimilarTasks() {
  if (!similarUrl.value) return
  similarLoading.value = true
  try {
    const result = await apiRequest<TaskSimilarResult>(similarUrl.value)
    similarTasks.value = result.similar_tasks ?? []
  } catch {
    // 历史相似任务是参考增强项，拉取失败静默，不影响上下文主面板
    similarTasks.value = props.task.similarTaskRefs ? [...props.task.similarTaskRefs] : []
  } finally {
    similarLoading.value = false
  }
}

async function retryRelatedIndex() {
  if (!record.value || !repositoryId.value) return
  try {
    const next = await apiRequest<RepositoryIndex>(
      `/api/repositories/${encodeURIComponent(repositoryId.value)}/indexes/${encodeURIComponent(record.value.id)}/retry`,
      { method: 'POST' },
    )
    store.mergeRepositoryIndex(next)
    ElMessage.success('失败索引已重新排队，任务会在索引完成后自动继续')
  } catch (error) {
    recordError.value = apiMessage(error, '索引重试失败')
  }
}

function goIndexes() {
  router.push({ path: '/indexes', query: repositoryId.value ? { repository_id: repositoryId.value } : {} })
}
</script>

<template>
  <section class="detail-section code-context-section">
    <header>
      <div>
        <p class="section-kicker">代码上下文</p>
        <h3>{{ headerStatusText }}</h3>
      </div>
      <span v-if="recordRunning || waitOnIndex" class="status-tag info">索引进度中</span>
      <span v-else-if="contextsSource.length" class="status-tag success">{{ contextsSource.length }} 段</span>
    </header>

    <div v-if="contextsLoading" class="code-context-loading"><LoaderCircle class="button__spinner" :size="15" /> 正在检索仓库代码上下文…</div>
    <div v-if="contextsError" class="form-alert code-context-alert"><X :size="16" /><span>{{ contextsError }}</span></div>

    <div v-if="waitOnIndex || recordRunning" class="code-context-index">
      <div v-if="record" class="code-context-index__head">
        <span class="status-tag" :class="record.status === 'COMPLETED' ? 'success' : record.status === 'FAILED' ? 'danger' : 'info'">{{ INDEX_STATUS_TEXT[record.status] }}</span>
        <span class="outline-tag mono">{{ record.base_branch }}</span>
        <span class="outline-tag mono" :title="record.commit_sha">{{ shortSha(record.commit_sha) }}</span>
      </div>
      <dl v-if="record" class="compact-dl">
        <div><dt>阶段</dt><dd>{{ record.error_stage || INDEX_STATUS_TEXT[record.status] }}{{ record.status === 'FAILED' && record.error_stage ? ' 阶段失败' : '' }}</dd></div>
        <div><dt>文件 / 切片</dt><dd class="mono">{{ record.processed_files }} / {{ record.total_files }} · {{ record.chunk_count }} 切片</dd></div>
        <div><dt>索引任务</dt><dd class="mono">{{ record.id }}</dd></div>
      </dl>
      <p class="code-context-index__hint">
        {{ waitOnIndex ? '仓库索引完成后，Agent 会按 Issue 自动检索相关代码并继续分析，无需人工介入。' : '索引任务处理中，完成后等待中的任务会自动注入检索上下文。' }}
      </p>
      <p v-if="record?.status === 'FAILED'" class="code-context-index__error">
        {{ record.error_message || '索引任务失败' }}
      </p>
      <div v-if="record?.status === 'FAILED' && canManageIndex" class="code-context-index__actions">
        <button class="button button--secondary" @click="retryRelatedIndex"><RotateCcw :size="15" /> 重试索引</button>
        <button class="button button--secondary" @click="goIndexes">去索引管理</button>
      </div>
      <p v-if="recordError" class="form-subtitle">{{ recordError }}<button class="text-button" @click="fetchIndexRecord">再试一次</button></p>
    </div>

    <div v-else-if="indexFailedNode" class="code-context-index">
      <p class="code-context-index__error"><strong>任务在「代码索引」节点失败</strong>{{ task.failureReason ? `：${task.failureReason}` : '' }}。任务已暂停自动流转，可在顶部「一键重试」恢复。</p>
      <div class="code-context-index__actions">
        <button class="button button--secondary" @click="goIndexes">查看仓库索引</button>
        <button v-if="record?.status === 'FAILED' && canManageIndex" class="button button--secondary" @click="retryRelatedIndex"><RotateCcw :size="15" /> 重试该索引</button>
      </div>
    </div>

    <div v-else-if="contextsSource.length" class="code-context-list">
      <div v-if="staleBanner" class="code-context-stale">
        <FileSearch :size="15" />
        <span>检索来源索引已过期（Commit 落后于仓库最新），以下片段仅供人工核对，不会被自动分析使用。</span>
        <RouterLink v-if="canManageIndex" :to="{ path: '/indexes', query: repositoryId ? { repository_id: repositoryId } : {} }" class="text-link">重建索引</RouterLink>
      </div>
      <p class="code-context-summary">
        默认展示最相关的 {{ primaryContexts.length }} 项，用来判断 AI 分析依据是否可信。{{ contextSourceModeText }}
      </p>
      <ul class="context-list">
        <li v-for="item in primaryContexts" :key="item.chunk_id">
          <span class="context-score">{{ scoreText(item.score) }}</span>
          <div>
            <strong class="mono">{{ item.file_path }}<template v-if="item.symbol_name"> · {{ item.symbol_name }}</template></strong>
            <small class="code-context-meta">
              L{{ item.start_line }}-{{ item.end_line }} · {{ shortSha(item.commit_sha) }}
              <template v-if="item.source === 'manual'"><span class="outline-tag code-context-tag">人工指定</span></template>
              <template v-else-if="item.source === 'fallback'"><span class="outline-tag code-context-tag">显式降级</span></template>
              <template v-else><span class="outline-tag code-context-tag">向量检索</span></template>
              <span v-if="!item.fresh" class="status-tag warning">过期</span>
            </small>
            <p class="code-context-preview" :title="item.content">{{ item.content }}</p>
          </div>
        </li>
      </ul>
      <details v-if="secondaryContexts.length" class="code-context-more">
        <summary><span>展开其余 {{ secondaryContexts.length }} 段上下文</span></summary>
        <ul class="context-list">
          <li v-for="item in secondaryContexts" :key="item.chunk_id">
            <span class="context-score">{{ scoreText(item.score) }}</span>
            <div>
              <strong class="mono">{{ item.file_path }}<template v-if="item.symbol_name"> · {{ item.symbol_name }}</template></strong>
              <small class="code-context-meta">
                L{{ item.start_line }}-{{ item.end_line }} · {{ shortSha(item.commit_sha) }}
                <template v-if="item.source === 'manual'"><span class="outline-tag code-context-tag">人工指定</span></template>
                <template v-else-if="item.source === 'fallback'"><span class="outline-tag code-context-tag">显式降级</span></template>
                <template v-else><span class="outline-tag code-context-tag">向量检索</span></template>
                <span v-if="!item.fresh" class="status-tag warning">过期</span>
              </small>
              <p class="code-context-preview" :title="item.content">{{ item.content }}</p>
            </div>
          </li>
        </ul>
      </details>
      <p v-if="meta.empty_reason" class="form-subtitle">{{ meta.empty_reason }}</p>
    </div>

    <div v-else-if="contextCapableStatus" class="code-context-empty">
      <p v-if="contextMissing" class="code-context-missing-title">
        <strong>自动分析已暂停：上下文不足</strong>
      </p>
      <p>
        {{ contextMissing
          ? (meta.empty_reason || '自动检索未命中达到相似度阈值的代码片段，任务不会失败；重新检索、手动指定文件或重建索引补齐上下文后会自动继续分析。')
          : (meta.empty_reason || (task.status === 'FAILED' ? '任务尚未获得可用的代码检索来源。' : '当前任务没有可展示的代码上下文，可重新检索或手动指定文件补充来源。')) }}
      </p>
      <p v-if="resumeError" class="field-error">{{ resumeError }}</p>
      <div v-if="meta.threshold !== undefined || meta.top_k !== undefined" class="form-subtitle">
        {{ meta.mode === 'manual' ? '手动指定来源' : `自动检索 Top-K ${meta.top_k ?? '-'} · 相似度阈值 ${meta.threshold ?? '-'}` }}
      </div>
      <div class="code-context-empty__actions">
        <button
          v-if="contextMissing && canOperate && resumeUrl"
          class="button button--primary"
          :disabled="resumeSubmitting"
          @click="resumeContexts"
        >
          <LoaderCircle v-if="resumeSubmitting" class="button__spinner" :size="15" />
          <FileSearch v-else :size="15" />
          {{ resumeSubmitting ? '检索中' : '重新检索并继续' }}
        </button>
        <button v-else-if="contextsUrl" class="button button--secondary" :disabled="contextsLoading" @click="refreshContexts">
          <LoaderCircle v-if="contextsLoading" class="button__spinner" :size="15" />
          <FileSearch v-else :size="15" />
          {{ contextsLoading ? '检索中' : '重新检索' }}
        </button>
        <button v-if="canOperate && manualUrl" class="button button--secondary" @click="toggleManual">{{ manualOpen ? '收起' : '手动指定文件' }}</button>
        <button v-if="canManageIndex" class="button button--ghost" @click="goIndexes">重建索引</button>
      </div>
    </div>

    <div v-else class="code-context-empty">
      <p>任务创建后将先拉取 Issue，再按仓库索引状态复用或自动排队建立索引；完成后这里会展示检索到的代码片段。</p>
    </div>

    <div v-if="similarTasks.length" class="similar-task-list">
      <header>
        <h4>历史相似任务参考<span v-if="similarLoading" class="form-subtitle">（检索中…）</span></h4>
        <span class="similar-task-head__right">
          <span class="form-subtitle">仅参考，不会自动套用旧 Diff</span>
          <button
            class="similar-task-refresh"
            type="button"
            :disabled="similarLoading"
            :aria-label="similarLoading ? '正在刷新历史相似任务' : '刷新历史相似任务'"
            :title="similarLoading ? '正在刷新历史相似任务' : '刷新历史相似任务'"
            @click="fetchSimilarTasks"
          >
            <RefreshCw :size="14" :class="{ 'is-spinning': similarLoading }" />
          </button>
        </span>
      </header>
      <ul>
        <li v-for="item in similarTasks" :key="`${item.task_id}-${item.version}`">
          <RouterLink class="similar-task-link" :to="`/tasks/${encodeURIComponent(item.task_id)}`">
            <span class="similar-task-title">{{ item.title }}</span>
            <small class="mono">{{ item.task_id }} · v{{ item.version }} · {{ item.status }} · 相似度 {{ Math.round(item.score * 100) }}%</small>
            <small v-if="item.summary" class="similar-task-summary">{{ item.summary }}</small>
          </RouterLink>
        </li>
      </ul>
    </div>

    <div v-if="manualOpen" class="code-context-manual">
      <label>
        <span>文件路径（每行一个，最多 8 个）</span>
        <textarea v-model="manualPathText" placeholder="services/payment/callback.py&#10;services/payment/validator.py" class="mono" />
      </label>
      <p class="form-subtitle">按所选仓库最近完成的索引 Commit 生成人工核对上下文；不存在的路径会被拒绝。</p>
      <p v-if="manualError" class="field-error">{{ manualError }}</p>
      <footer class="code-context-manual__actions">
        <button class="button button--secondary" :disabled="manualSubmitting" @click="manualOpen = false"><X :size="15" /> 取消</button>
        <button class="button button--primary" :disabled="manualSubmitting" @click="submitManual">
          <LoaderCircle v-if="manualSubmitting" class="button__spinner" :size="15" />
          <Send v-else :size="15" />
          {{ manualSubmitting ? '提交中' : '生成人工上下文' }}
        </button>
      </footer>
    </div>
  </section>
</template>

<style scoped>
.code-context-missing-title {
  margin-bottom: 4px;
  color: var(--theme-status-warning);
}

.code-context-summary {
  margin: 0;
  color: var(--task-muted);
  font-size: 12px;
  line-height: 19px;
}

.code-context-more {
  border-top: 1px solid var(--task-border-soft);
  padding-top: 8px;
}

.code-context-more summary {
  display: flex;
  min-height: 30px;
  align-items: center;
  justify-content: space-between;
  color: var(--task-accent);
  cursor: pointer;
  font-size: 12px;
  font-weight: 700;
  list-style: none;
}

.code-context-more summary::-webkit-details-marker {
  display: none;
}

.code-context-more summary::after {
  width: 7px;
  height: 7px;
  border-right: 1.5px solid currentColor;
  border-bottom: 1.5px solid currentColor;
  transform: rotate(45deg);
  transition: transform .16s ease;
  content: "";
}

.code-context-more[open] summary::after {
  transform: rotate(225deg);
}

.code-context-more .context-list {
  margin-top: 8px;
}

.similar-task-list {
  margin-top: 12px;
  padding: 10px 12px;
  border: 1px solid var(--task-border-soft);
  border-radius: 8px;
  background: var(--task-surface-subtle);
}

.similar-task-list header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 6px;
}

.similar-task-head__right {
  display: inline-flex;
  align-items: baseline;
  gap: 8px;
}

.similar-task-list h4 {
  margin: 0;
  color: var(--task-text);
  font-size: 13px;
  line-height: 18px;
}

.similar-task-list ul {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.similar-task-link {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 6px 8px;
  border: 1px solid transparent;
  border-radius: 6px;
  text-decoration: none;
  color: var(--task-text);
  transition: border-color .16s ease, background-color .16s ease;
}

.similar-task-link:hover {
  border-color: var(--task-border);
  background: var(--task-surface-muted);
}

.similar-task-title {
  font-size: 13px;
  font-weight: 600;
  line-height: 18px;
  overflow-wrap: anywhere;
}

.similar-task-link > small:not(.similar-task-summary) {
  color: var(--task-faint);
  font-size: 10px;
  line-height: 15px;
  overflow-wrap: anywhere;
}

.similar-task-summary {
  display: -webkit-box;
  overflow: hidden;
  color: var(--task-muted);
  font-size: 12px;
  line-height: 18px;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 3;
}

@media (max-width: 760px) {
  .similar-task-list {
    margin-top: 10px;
    padding: 9px;
  }

  .similar-task-list header {
    align-items: flex-start;
    flex-direction: column;
    gap: 4px;
  }

  .similar-task-head__right {
    width: 100%;
    align-items: center;
    justify-content: space-between;
  }
}
</style>
