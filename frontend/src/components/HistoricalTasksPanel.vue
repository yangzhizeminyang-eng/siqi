<script setup lang="ts">
import { History, ChevronRight, ExternalLink, Loader2, SearchX } from '@lucide/vue'
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ApiError } from '../services/api'
import { getDataSourceMode } from '../services/dataSourceMode'
import { searchHistoricalTasks } from '../services/historyTasks'
import type { HistoricalTaskReference } from '../types/domain'

const props = withDefaults(
  defineProps<{
    taskId?: string
    query: string
    projectId?: string
  }>(),
  { taskId: '', projectId: '' },
)

const router = useRouter()
const isReal = computed(() => getDataSourceMode() === 'real')
const items = ref<HistoricalTaskReference[]>([])
const loading = ref(false)
const error = ref('')
const loaded = ref(false)
const openDetail = ref<HistoricalTaskReference | null>(null)

const SIMILARITY_LABELS: Record<string, string> = {
  issue: '需求',
  context: '上下文',
  analysis: '分析',
  plan: '计划',
  diff: 'Diff',
  test: '测试',
  pr: 'PR',
  replay: '回放',
}

async function load() {
  if (!isReal.value || (!props.taskId && !props.projectId)) return
  loading.value = true
  error.value = ''
  loaded.value = false
  try {
    const result = await searchHistoricalTasks({
      projectId: props.projectId || undefined,
      taskId: props.taskId || undefined,
      query: props.query || '',
      limit: 5,
    })
    items.value = Array.isArray(result) ? result : []
  } catch (err) {
    error.value = err instanceof ApiError ? err.message : '历史任务检索失败'
    items.value = []
  } finally {
    loading.value = false
    loaded.value = true
  }
}

function openTask(ref_: HistoricalTaskReference) {
  router.push(`/tasks/${ref_.taskId}`)
}

function pct(score: number | null) {
  if (score === null || score === undefined) return '最近完成'
  return `${Math.round((score || 0) * 100)}%`
}

function similarityLabel(score: number | null) {
  if (score === null || score === undefined) return '最近完成'
  return `相似度 ${pct(score)}`
}

function similarityHint(score: number | null) {
  if (score === null || score === undefined) return '按完成时间推荐的历史任务，不含相似度评分。'
  return '基于当前任务与历史任务的需求、上下文和分析内容计算，用于排序；不代表任务进度或复用成功率。'
}

onMounted(load)
watch(() => props.taskId, load)
</script>

<template>
  <section v-if="isReal" class="panel history-refs">
    <header class="panel__header">
      <div>
        <h2><History :size="16" class="inline-icon" /> 历史相似任务</h2>
        <p>已完成任务的知识参考，仅供复用参考，不会自动应用旧 Diff。</p>
      </div>
    </header>

    <div v-if="loading" class="history-refs__state"><Loader2 :size="16" class="spin" /> 检索历史任务中…</div>
    <div v-else-if="error" class="history-refs__state history-refs__state--error"><SearchX :size="16" /> {{ error }}</div>
    <div v-else-if="!Array.isArray(items) || !items.length" class="history-refs__state">暂无相似历史任务。</div>
    <ul v-else class="history-refs__list">
      <li v-for="item in items" :key="item.taskId" class="history-refs__item">
        <div class="history-refs__main">
          <div class="history-refs__head">
            <strong :title="item.title">{{ item.title }}</strong>
          </div>
          <span class="history-refs__tags">
            <em class="history-refs__src">{{ item.sourceLabel }}</em>
            <em
              class="history-refs__similarity"
              :class="{ 'history-refs__similarity--recent': item.similarity === null || item.similarity === undefined }"
              :title="similarityHint(item.similarity)"
            >{{ similarityLabel(item.similarity) }}</em>
            <em v-for="s in item.matchedSections.slice(0, 3)" :key="s">{{ SIMILARITY_LABELS[s] ?? s }}</em>
          </span>
          <p class="history-refs__preview">{{ item.summaryPreview }}</p>
          <small v-if="item.completedAt" class="history-refs__meta">完成于 {{ new Date(item.completedAt).toLocaleDateString('zh-CN') }}</small>
        </div>
        <div class="history-refs__actions">
          <button class="button button--ghost button--sm" type="button" @click="openDetail = item">
            <ChevronRight :size="14" /> 详情
          </button>
          <button class="button button--secondary button--sm" type="button" @click="openTask(item)">
            <ExternalLink :size="14" /> 打开
          </button>
        </div>
      </li>
    </ul>

    <!-- 历史任务详情（只读）：明确标注历史参考、不会自动应用旧 Diff -->
    <Transition name="fade-up">
      <div v-if="openDetail" class="history-refs__detail">
        <header>
          <div>
            <h3>{{ openDetail.title }}</h3>
            <span class="history-refs__src">{{ openDetail.sourceLabel }} · {{ similarityLabel(openDetail.similarity) }}</span>
          </div>
          <button class="button button--ghost button--sm" type="button" @click="openDetail = null">收起</button>
        </header>
        <div class="history-refs__notice">这是历史任务，仅供参考，不会自动应用旧 Diff 或代码。</div>
        <p class="history-refs__detail-preview">{{ openDetail.summaryPreview }}</p>
        <div class="history-refs__detail-meta">
          <span v-for="s in openDetail.matchedSections" :key="s">命中：{{ SIMILARITY_LABELS[s] ?? s }}</span>
        </div>
        <footer>
          <button class="button button--primary button--sm" type="button" @click="openTask(openDetail); openDetail = null">查看完整历史任务</button>
        </footer>
      </div>
    </Transition>
  </section>
</template>

<style scoped>
.inline-icon {
  vertical-align: -2px;
}
.history-refs {
  --history-item-bg: var(--task-surface-subtle);
  --history-item-hover-bg: var(--theme-surface-hover);
  --history-border: var(--task-border-soft);
  --history-border-hover: var(--theme-border-hover);
  --history-text: var(--task-text);
  --history-muted: var(--task-muted);
  --history-faint: var(--task-faint);
  --history-tag-bg: var(--theme-tag-surface);
  --history-tag-text: var(--task-muted);
  --history-source-bg: var(--theme-source-surface);
  --history-source-text: var(--theme-source-text);
  --history-similarity: var(--task-accent);
  --history-notice-bg: var(--theme-warning-surface);
  --history-notice-border: var(--theme-warning-border);
  --history-notice-text: var(--theme-warning-text);
  container-type: inline-size;
  overflow: hidden;
}
.spin {
  animation: hist-spin 0.9s linear infinite;
}
@keyframes hist-spin {
  to {
    transform: rotate(360deg);
  }
}
.history-refs__state {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0 18px 18px;
  padding: 18px 0 0;
  color: var(--history-muted);
  font-size: 13px;
}
.history-refs__state--error {
  color: var(--theme-danger-text);
}
.history-refs__list {
  list-style: none;
  margin: 0 18px 18px;
  padding: 0;
  display: grid;
  gap: 10px;
}
.history-refs__item {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: start;
  min-width: 0;
  gap: 14px;
  padding: 14px;
  border: 1px solid var(--history-border);
  border-radius: 8px;
  background: var(--history-item-bg);
  color: var(--history-text);
  transition: border-color 0.16s ease, background 0.16s ease, box-shadow 0.16s ease;
}
.history-refs__item:hover {
  border-color: var(--history-border-hover);
  background: var(--history-item-hover-bg);
  box-shadow: 0 6px 16px rgba(30, 48, 73, 0.08);
}
.history-refs__main {
  min-width: 0;
  display: grid;
  gap: 7px;
}
.history-refs__head {
  min-width: 0;
}
.history-refs__head strong {
  min-width: 0;
  font-size: 13px;
  line-height: 19px;
  overflow-wrap: anywhere;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.history-refs__similarity {
  color: var(--history-similarity) !important;
}
.history-refs__similarity--recent {
  font-weight: 600;
  color: var(--history-muted) !important;
}
.history-refs__tags {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}
.history-refs__tags em {
  display: inline-flex;
  align-items: center;
  min-height: 21px;
  font-style: normal;
  font-size: 11px;
  padding: 2px 7px;
  border-radius: 999px;
  background: var(--history-tag-bg);
  color: var(--history-tag-text);
  line-height: 15px;
}
.history-refs__src {
  background: var(--history-source-bg) !important;
  color: var(--history-source-text) !important;
}
.history-refs__preview {
  max-width: 72ch;
  font-size: 12px;
  color: var(--history-muted);
  margin: 0;
  line-height: 18px;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.history-refs__meta {
  display: block;
  font-size: 11px;
  color: var(--history-faint);
  line-height: 16px;
}
.history-refs__actions {
  display: flex;
  align-items: center;
  gap: 7px;
  flex: none;
  align-self: end;
}
.history-refs__actions .button {
  min-height: 30px;
  height: 30px;
  padding: 0 9px;
  font-size: 12px;
}
.history-refs__detail {
  margin: 0 18px 18px;
  padding: 14px;
  border: 1px solid var(--history-border);
  border-radius: 8px;
  background: var(--history-item-bg);
  color: var(--history-text);
}
.history-refs__detail header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}
.history-refs__detail header > div {
  min-width: 0;
}
.history-refs__detail h3 {
  font-size: 14px;
  line-height: 20px;
}
.history-refs__detail .history-refs__src {
  display: inline-flex;
  margin-top: 7px;
}
.history-refs__notice {
  margin-top: 8px;
  padding: 8px 10px;
  border: 1px solid var(--history-notice-border);
  border-radius: 8px;
  background: var(--history-notice-bg);
  color: var(--history-notice-text);
  font-size: 12px;
  line-height: 18px;
}
.history-refs__detail-preview {
  margin: 8px 0 0;
  font-size: 13px;
  line-height: 20px;
  white-space: pre-wrap;
}
.history-refs__detail-meta {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin-top: 8px;
  font-size: 12px;
  color: var(--history-muted);
}
.history-refs__detail footer {
  margin-top: 10px;
  display: flex;
  justify-content: flex-end;
}

.fade-up-enter-active,
.fade-up-leave-active {
  transition: opacity 0.18s ease, transform 0.18s ease;
}
.fade-up-enter-from,
.fade-up-leave-to {
  opacity: 0;
  transform: translateY(6px);
}

@container (max-width: 560px) {
  .history-refs__state,
  .history-refs__list,
  .history-refs__detail {
    margin-right: 14px;
    margin-left: 14px;
  }
  .history-refs__state,
  .history-refs__list,
  .history-refs__detail {
    margin-bottom: 14px;
  }
  .history-refs__item {
    grid-template-columns: 1fr;
    gap: 12px;
    padding: 13px;
  }
  .history-refs__actions {
    width: 100%;
    padding-top: 10px;
    border-top: 1px solid var(--history-border);
    justify-content: stretch;
  }
  .history-refs__actions .button {
    flex: 1;
  }
  .history-refs__head {
    align-items: flex-start;
  }
  .history-refs__detail header {
    gap: 8px;
  }
  .history-refs__detail footer .button {
    width: 100%;
  }
}
</style>
