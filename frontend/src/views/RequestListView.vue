<script setup lang="ts">
import { FilePlus2, RefreshCw, Search } from '@lucide/vue'
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { useRouter } from 'vue-router'
import { ApiError } from '../services/api'
import { listTeamRequests } from '../services/teamRequests'
import { useAuthStore } from '../stores/auth'
import TaskFilterSelect from '../components/TaskFilterSelect.vue'
import type { TaskFilterOption } from '../components/TaskFilterSelect.vue'
import {
  requestStatusText,
  requestTypeText,
  type RequestSyncStatus,
  type RequestType,
  type TeamRequest,
} from '../types/domain'
import { connectRealtime, subscribeRealtime } from '../services/realtime'

const router = useRouter()
const auth = useAuthStore()
const loading = ref(false)
const requests = ref<TeamRequest[]>([])
const keyword = ref('')
const status = ref<'all' | RequestSyncStatus>('all')
const type = ref<'all' | RequestType>('all')
const errorText = ref('')

const statusOptions: TaskFilterOption[] = [
  { value: 'all', label: '全部状态' },
  { value: 'DRAFT', label: '草稿' },
  { value: 'SYNCING', label: '同步中' },
  { value: 'SYNCED', label: '已同步 Gitee' },
  { value: 'SYNC_FAILED', label: '同步失败' },
  { value: 'TASK_CREATED', label: '已创建任务' },
  { value: 'CLOSED', label: '已关闭' },
]
const typeOptions: TaskFilterOption[] = [
  { value: 'all', label: '全部类型' },
  { value: 'demand', label: '需求' },
  { value: 'bug', label: 'Bug' },
  { value: 'refactor', label: '重构' },
  { value: 'test', label: '测试' },
  { value: 'doc', label: '文档' },
]

const filteredRequests = computed(() => requests.value.filter((item) => {
  const text = `${item.request_no}${item.title}${item.body}${item.project_name}${item.repository_name}${item.labels.join(' ')}`.toLowerCase()
  const matchesKeyword = !keyword.value.trim() || text.includes(keyword.value.trim().toLowerCase())
  const matchesStatus = status.value === 'all' || item.sync_status === status.value
  const matchesType = type.value === 'all' || item.type === type.value
  return matchesKeyword && matchesStatus && matchesType
}))
const draftCount = computed(() => requests.value.filter((item) => item.sync_status === 'DRAFT').length)
const failedCount = computed(() => requests.value.filter((item) => item.sync_status === 'SYNC_FAILED').length)
const taskCreatedCount = computed(() => requests.value.filter((item) => item.sync_status === 'TASK_CREATED').length)
let unsubscribeRealtime: (() => void) | undefined

onMounted(() => {
  void loadRequests()
  connectRealtime()
  unsubscribeRealtime = subscribeRealtime((event) => {
    if (event.resource_type === 'team_request' || event.type === 'team_request.updated') {
      void loadRequests()
    }
  })
})

onBeforeUnmount(() => {
  unsubscribeRealtime?.()
  unsubscribeRealtime = undefined
})

function apiMessage(error: unknown, fallback: string) {
  return error instanceof ApiError ? error.message : fallback
}

function formatTime(value: string) {
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}

async function loadRequests(showToast = false) {
  loading.value = true
  errorText.value = ''
  try {
    requests.value = await listTeamRequests()
    if (showToast) {
      ElMessage.success({
        message: '平台需求列表已刷新',
        grouping: true,
      })
    }
  } catch (error) {
    errorText.value = apiMessage(error, '平台需求列表加载失败')
    ElMessage.error(errorText.value)
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <section class="page-content request-page">
    <header class="request-hero">
      <div class="request-hero__top">
        <div class="request-hero__copy">
          <p class="section-kicker">Team Requests</p>
          <div class="request-hero__title-row">
            <h1>平台需求</h1>
            <span class="request-hero__context">团队工作区</span>
          </div>
          <p>团队版草稿、Gitee 同步、Agent Task 创建按阶段接入，先把需求沉淀在一条清晰可追踪的链路里。</p>
        </div>
        <div class="request-hero__actions">
          <button v-if="auth.hasPermission('request:create')" class="button button--primary" type="button" @click="router.push('/requests/new')">
            <FilePlus2 :size="16" />
            创建需求
          </button>
          <button class="button button--ghost" type="button" :disabled="loading" @click="loadRequests(true)">
            <RefreshCw :class="{ 'button__spinner': loading }" :size="16" />
            刷新列表
          </button>
        </div>
      </div>
      <div class="request-hero__summary">
        <span class="request-hero__summary-label"><i aria-hidden="true" />队列概览</span>
        <dl class="request-hero__stats" aria-label="平台需求概览">
          <div>
            <dt>当前草稿</dt>
            <dd>{{ draftCount }}</dd>
          </div>
          <div>
            <dt>同步失败</dt>
            <dd>{{ failedCount }}</dd>
          </div>
          <div>
            <dt>已创建任务</dt>
            <dd>{{ taskCreatedCount }}</dd>
          </div>
        </dl>
      </div>
    </header>

    <div v-if="errorText" class="form-alert request-alert">{{ errorText }}</div>

    <section class="request-toolbar">
      <label class="request-search">
        <Search :size="16" />
        <input v-model="keyword" placeholder="搜索编号、标题、项目或仓库" />
      </label>
      <div class="request-toolbar__filters">
        <TaskFilterSelect v-model="type" :options="typeOptions" ariaLabel="需求类型" class="request-filter-select" />
        <TaskFilterSelect v-model="status" :options="statusOptions" ariaLabel="同步状态" class="request-filter-select" />
      </div>
    </section>

    <section class="panel request-table">
      <header class="request-table__header">
        <div>
          <p class="section-kicker">Request Queue</p>
          <h2>需求列表</h2>
        </div>
        <p>{{ filteredRequests.length }} 条记录</p>
      </header>
      <table>
        <thead>
          <tr>
            <th>编号</th>
            <th>标题</th>
            <th>类型</th>
            <th>状态</th>
            <th>项目 / 仓库</th>
            <th>创建人</th>
            <th>更新时间</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="!loading && filteredRequests.length === 0">
            <td colspan="8" class="request-empty">
              <div class="request-empty__state">
                <strong>暂无平台需求草稿</strong>
                <p>先创建一条需求草稿，再继续 Gitee 同步或任务创建。</p>
              </div>
            </td>
          </tr>
          <tr v-for="item in filteredRequests" :key="item.id">
            <td class="mono">{{ item.request_no }}</td>
            <td>
              <strong>{{ item.title }}</strong>
              <p v-if="!item.content_sufficient" class="request-warning">需要补充正文后再同步 Gitee 或创建 Agent 任务</p>
            </td>
            <td>{{ requestTypeText[item.type] }}</td>
            <td><span class="request-status" :class="`request-status--${item.sync_status.toLowerCase()}`">{{ requestStatusText[item.sync_status] }}</span></td>
            <td>{{ item.project_name }} / {{ item.repository_name }}</td>
            <td>{{ item.created_by_name }}</td>
            <td>{{ formatTime(item.updated_at) }}</td>
            <td>
              <button class="text-button" type="button" @click="router.push(`/requests/${item.id}`)">查看</button>
            </td>
          </tr>
        </tbody>
      </table>
    </section>
  </section>
</template>

<style scoped>
.request-page {
  max-width: 1540px;
}

.request-hero {
  display: grid;
  gap: 16px;
  margin-bottom: 16px;
  padding: 5px 2px 15px;
  border-bottom: 1px solid var(--panel-border-soft);
}

.request-hero__top {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 28px;
}

.request-hero__copy {
  min-width: 0;
  max-width: 760px;
}

.request-hero__title-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
}

.request-hero__copy h1 {
  margin: 6px 0 0;
  color: var(--text-strong);
  font-size: clamp(26px, 3vw, 36px);
  line-height: 1.12;
  letter-spacing: -0.03em;
  text-wrap: balance;
}

.request-hero__context {
  margin-top: 7px;
  padding: 3px 7px;
  border: 1px solid var(--panel-border);
  border-radius: 5px;
  color: var(--text-soft);
  background: var(--surface-subtle);
  font-size: 10px;
  font-weight: 700;
  line-height: 15px;
}

.request-hero__copy > p:last-of-type {
  max-width: 680px;
  margin-top: 10px;
  color: var(--text-soft);
  font-size: 13px;
  line-height: 20px;
}

.request-hero__summary {
  display: flex;
  align-items: center;
  justify-content: flex-start;
  gap: 20px;
  padding-top: 13px;
  border-top: 1px solid var(--panel-border-soft);
}

.request-hero__summary-label {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  flex: none;
  color: var(--text-soft);
  font-size: 10px;
  font-weight: 700;
  letter-spacing: .05em;
  text-transform: uppercase;
}

.request-hero__summary-label i {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--brand);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--brand) 12%, transparent);
}

.request-hero__stats {
  display: flex;
  align-items: stretch;
  gap: 0;
  margin: 0;
}

.request-hero__stats div {
  display: grid;
  gap: 3px;
  min-width: 112px;
  padding: 0 22px;
  border-left: 1px solid var(--panel-border-soft);
}

.request-hero__stats div:first-child {
  padding-left: 0;
  border-left: 0;
}

.request-hero__stats dt {
  color: var(--text-faint);
  font-size: 10px;
  font-weight: 700;
  letter-spacing: .06em;
}

.request-hero__stats dd {
  margin: 0;
  color: var(--text-strong);
  font-size: 19px;
  font-weight: 700;
  line-height: 24px;
  font-variant-numeric: tabular-nums;
}

.request-hero__actions {
  display: flex;
  flex: none;
  align-items: center;
  gap: 8px;
  padding-top: 20px;
}

.request-hero__actions .button {
  min-width: 112px;
}

.request-toolbar {
  display: grid;
  grid-template-columns: minmax(260px, 1fr) auto;
  gap: 14px;
  align-items: center;
  margin-bottom: 14px;
  padding: 10px;
  border: 1px solid var(--panel-border);
  border-radius: 10px;
  background: color-mix(in srgb, var(--panel-bg) 92%, var(--surface-subtle));
  box-shadow: var(--shadow-sm);
}

.request-search {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  height: 40px;
  padding: 0 12px;
  border: 1px solid var(--panel-border);
  border-radius: 11px;
  color: var(--text-faint);
  background: var(--control-bg);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, .06), 0 2px 10px rgba(4, 18, 31, .08);
  transition: border-color .16s ease, box-shadow .16s ease, background .16s ease;
}

.request-search:focus-within {
  border-color: #1cc4bd;
  box-shadow:
    0 0 0 2px rgba(19, 184, 178, .14),
    0 0 16px rgba(19, 184, 178, .12),
    inset 0 1px 0 rgba(255, 255, 255, .05);
}

.request-search input {
  min-width: 0;
  height: 38px;
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--text-strong);
  caret-color: #6ee7df;
  box-shadow: none !important;
}

:global([data-theme="dark"]) .request-search {
  border-color: #24384f;
  background: #101b2c;
  color: #9eafc4;
}

:global([data-theme="dark"]) .request-search input {
  border: 0;
  background: transparent;
  color: #eff7ff;
  box-shadow: none !important;
}

:global([data-theme="dark"]) .request-search input::placeholder {
  color: #9aaac0;
}

:global([data-theme="dark"]) .request-search:focus-within {
  border-color: #1cc4bd;
  background: #111d2f;
}

.request-toolbar__filters {
  display: flex;
  gap: 8px;
}

.request-toolbar__filters .request-filter-select {
  width: 150px;
}

.request-table {
  overflow: hidden;
  border-color: var(--panel-border);
  border-radius: 10px;
  background: var(--panel-bg);
  box-shadow: var(--shadow-sm);
}

.request-table__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  min-height: 72px;
  padding: 16px 18px 14px;
  border-bottom: 1px solid var(--panel-border-soft);
  background: var(--surface-subtle);
}

.request-table__header h2 {
  margin-top: 4px;
  color: var(--text-strong);
  font-size: 16px;
  line-height: 21px;
}

.request-table__header > p {
  color: var(--text-faint);
  font-size: 11px;
  font-variant-numeric: tabular-nums;
}

.request-table table {
  width: 100%;
  min-width: 980px;
  border-collapse: collapse;
}

.request-table th,
.request-table td {
  padding: 13px 16px;
  border-bottom: 1px solid var(--panel-border-soft);
  text-align: left;
  vertical-align: top;
}

.request-table th {
  height: 42px;
  color: var(--text-faint);
  background: var(--surface-subtle);
  font-size: 10px;
  font-weight: 700;
  letter-spacing: .06em;
  white-space: nowrap;
}

.request-table td {
  color: var(--text-base);
  font-size: 12px;
  line-height: 18px;
  white-space: nowrap;
}

.request-table tbody tr {
  transition: background .16s ease;
}

.request-table tbody tr:hover {
  background: var(--surface-hover);
}

.request-table td:first-child {
  color: var(--brand-strong);
  font-size: 11px;
  font-weight: 700;
}

.request-table td:nth-child(2) {
  min-width: 260px;
  max-width: 390px;
  white-space: normal;
}

.request-table td:nth-child(2) strong {
  display: block;
  color: var(--text-strong);
  font-size: 13px;
  line-height: 19px;
}

.request-status {
  display: inline-flex;
  align-items: center;
  min-height: 23px;
  padding: 2px 8px;
  border: 1px solid var(--panel-border);
  border-radius: 6px;
  color: var(--text-base);
  background: var(--surface-subtle);
  font-size: 11px;
  font-weight: 700;
}

.request-status--draft {
  color: #b8d0ff;
  border-color: #35527e;
  background: #172943;
}

.request-status--syncing {
  color: #edcc83;
  border-color: #665126;
  background: #302613;
}

.request-status--sync_failed {
  color: #ffb4ad;
  border-color: #693c3b;
  background: #351f22;
}

.request-status--synced,
.request-status--task_created {
  color: #91deb2;
  border-color: #2b6046;
  background: #153326;
}

.request-status--closed {
  color: var(--text-soft);
  border-color: var(--panel-border);
  background: var(--surface-subtle);
}

.request-warning {
  margin: 4px 0 0;
  color: #edcc83;
  font-size: 11px;
  line-height: 16px;
}

.request-table .text-button {
  font-size: 12px;
  font-weight: 700;
}

.request-empty {
  text-align: center;
}

.request-empty__state {
  display: grid;
  justify-items: center;
  gap: 5px;
  min-height: 180px;
  align-content: center;
}

.request-empty__state::before {
  width: 34px;
  height: 34px;
  margin-bottom: 4px;
  border: 1px solid var(--panel-border);
  border-radius: 10px;
  background:
    linear-gradient(135deg, transparent 46%, var(--brand) 47% 53%, transparent 54%),
    var(--surface-subtle);
  content: "";
  opacity: .9;
}

.request-empty__state strong {
  color: var(--text-strong);
  font-size: 13px;
}

.request-empty__state p {
  color: var(--text-soft);
  font-size: 11px;
}

@media (prefers-color-scheme: light) {
  :global(:root:not([data-theme="dark"])) .request-status--draft {
    color: #2456a0;
    border-color: #cbdcf5;
    background: #eef5ff;
  }

  :global(:root:not([data-theme="dark"])) .request-status--syncing {
    color: #8a5c0b;
    border-color: #f0d79d;
    background: #fff8e9;
  }

  :global(:root:not([data-theme="dark"])) .request-status--sync_failed {
    color: #a63831;
    border-color: #efc8c4;
    background: #fff3f1;
  }

  :global(:root:not([data-theme="dark"])) .request-status--synced,
  :global(:root:not([data-theme="dark"])) .request-status--task_created {
    color: #176845;
    border-color: #c8e6d5;
    background: #edf9f2;
  }

  :global(:root:not([data-theme="dark"])) .request-warning {
    color: #8a5c0b;
  }
}

:global([data-theme="light"]) .request-status--draft {
  color: #2456a0;
  border-color: #cbdcf5;
  background: #eef5ff;
}

:global([data-theme="light"]) .request-status--syncing {
  color: #8a5c0b;
  border-color: #f0d79d;
  background: #fff8e9;
}

:global([data-theme="light"]) .request-status--sync_failed {
  color: #a63831;
  border-color: #efc8c4;
  background: #fff3f1;
}

:global([data-theme="light"]) .request-status--synced,
:global([data-theme="light"]) .request-status--task_created {
  color: #176845;
  border-color: #c8e6d5;
  background: #edf9f2;
}

:global([data-theme="light"]) .request-warning {
  color: #8a5c0b;
}

@media (max-width: 900px) {
  .request-hero {
    align-items: flex-start;
    gap: 15px;
  }

  .request-hero__top {
    flex-direction: column;
    gap: 15px;
  }

  .request-hero__actions {
    padding-top: 0;
    width: 100%;
  }

  .request-hero__actions .button {
    flex: 1;
  }

  .request-toolbar {
    grid-template-columns: 1fr;
  }

  .request-toolbar__filters {
    display: grid;
    grid-template-columns: 1fr 1fr;
  }

  .request-toolbar__filters .request-filter-select {
    width: 100%;
  }

  .request-hero__summary {
    align-items: flex-start;
    flex-direction: column;
    gap: 11px;
  }

  .request-hero__stats {
    width: 100%;
  }

  .request-hero__stats div {
    flex: 1;
    min-width: 0;
  }
}

@media (max-width: 520px) {
  .request-hero__stats {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }

  .request-hero__stats div,
  .request-hero__stats div:first-child {
    min-width: 0;
    padding: 0 10px;
  }

  .request-hero__stats div:first-child {
    padding-left: 0;
  }

  .request-hero__stats dd {
    font-size: 18px;
  }

  .request-toolbar__filters {
    grid-template-columns: 1fr;
  }
}
</style>
