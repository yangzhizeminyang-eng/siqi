<script setup lang="ts">
import { AlertTriangle, CheckCircle2, ClipboardList, FileClock, RefreshCw, Search, ShieldCheck, X } from '@lucide/vue'
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ApiError, apiRequest } from '../services/api'
import { hasAccessRequirement } from '../services/accessControl'
import { useAuthStore } from '../stores/auth'
import { useTenancyStore } from '../stores/tenancy'
import type { AuditEvent } from '../types/domain'

type ResourceFilter = 'all' | 'task' | 'repository' | 'project' | 'index'

const router = useRouter()
const auth = useAuthStore()
const tenancy = useTenancyStore()
const events = ref<AuditEvent[]>([])
const loading = ref(false)
const errorMessage = ref('')
const keyword = ref('')
const resourceFilter = ref<ResourceFilter>('all')
const selectedEvent = ref<AuditEvent | null>(null)
const canViewAudit = computed(() => hasAccessRequirement(auth, tenancy, ['project:manage', 'audit:view']))

const resourceOptions: Array<{ value: ResourceFilter; label: string }> = [
  { value: 'all', label: '全部资源' },
  { value: 'task', label: '任务' },
  { value: 'repository', label: '仓库' },
  { value: 'project', label: '项目' },
  { value: 'index', label: '索引' },
]

const filteredEvents = computed(() => {
  const query = keyword.value.trim().toLowerCase()
  return events.value.filter((event) => {
    const matchesResource = resourceFilter.value === 'all' || event.resource_type === resourceFilter.value
    const searchable = [
      event.event_type,
      event.resource_type,
      event.resource_id,
      event.actor_name,
      event.summary,
      ...event.diff_summary,
    ].join(' ').toLowerCase()
    return matchesResource && (!query || searchable.includes(query))
  })
})

const riskEvents = computed(() => events.value.filter((event) => isRiskEvent(event)).length)
const taskEvents = computed(() => events.value.filter((event) => event.resource_type === 'task').length)
const latestEventTime = computed(() => (events.value[0] ? formatDate(events.value[0].created_at) : '暂无记录'))
const resourceLabel = computed(() => new Map(resourceOptions.map((option) => [option.value, option.label])))

function isRiskEvent(event: AuditEvent) {
  return /RISK|BLOCK|FAIL|REJECT|DENY|WARNING|SECURITY|INJECTION|权限|失败|拒绝|阻断|风险/i.test(
    `${event.event_type} ${event.summary} ${event.diff_summary.join(' ')}`,
  )
}

function eventTone(event: AuditEvent) {
  if (isRiskEvent(event)) return 'risk'
  if (/APPROV|COMPLET|SUCC|UPDATED|CREATED|GENERATED/i.test(event.event_type)) return 'success'
  return 'info'
}

function eventToneLabel(event: AuditEvent) {
  if (isRiskEvent(event)) return '风险事件'
  if (eventTone(event) === 'success') return '已记录'
  return '系统事件'
}

function formatDate(value: string) {
  const timestamp = new Date(value)
  if (Number.isNaN(timestamp.getTime())) return value
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(timestamp)
}

function apiMessage(error: unknown) {
  if (error instanceof ApiError && error.message) return error.message
  return '请检查服务状态后重新加载。'
}

async function loadEvents() {
  loading.value = true
  errorMessage.value = ''
  try {
    events.value = await apiRequest<AuditEvent[]>('/api/audit-events')
  } catch (error) {
    events.value = []
    errorMessage.value = apiMessage(error)
  } finally {
    loading.value = false
  }
}

function openEvent(event: AuditEvent) {
  selectedEvent.value = event
}

function closeEvent() {
  selectedEvent.value = null
}

function goToResource(event: AuditEvent) {
  if (event.resource_type === 'task' && event.resource_id) {
    closeEvent()
    void router.push(`/tasks/${encodeURIComponent(event.resource_id)}`)
  }
}

onMounted(() => {
  if (canViewAudit.value) void loadEvents()
})
</script>

<template>
  <section class="page-content audit-center">
    <div v-if="!canViewAudit" class="page-notice page-notice--warning">
      <span class="page-notice__icon"><ShieldCheck :size="17" /></span>
      <div class="page-notice__body">
        <strong>当前账号没有审计中心权限</strong>
        <p>审计记录对项目管理员或企业审计角色开放，任务详情中的关联事件仍按原有权限控制。</p>
      </div>
    </div>

    <div class="page-toolbar page-toolbar--stacked audit-center__toolbar">
      <div>
        <p class="section-kicker">治理与追踪</p>
        <h2>审计中心</h2>
        <p>集中查看项目配置、任务流转和风险门禁留下的操作证据。</p>
      </div>
      <button class="button button--secondary" :disabled="loading || !canViewAudit" @click="loadEvents">
        <RefreshCw :size="16" :class="{ 'is-spinning': loading }" /> {{ loading ? '同步中' : '重新加载' }}
      </button>
    </div>

    <div v-if="errorMessage" class="audit-center__error page-notice page-notice--danger">
      <span class="page-notice__icon"><AlertTriangle :size="17" /></span>
      <div class="page-notice__body">
        <strong>审计事件加载失败</strong>
        <p>{{ errorMessage }}</p>
      </div>
      <button class="button button--secondary" @click="loadEvents">重新加载</button>
    </div>

    <section v-else class="audit-center__summary" aria-label="审计摘要">
      <article class="audit-metric">
        <span class="audit-metric__icon"><FileClock :size="17" /></span>
        <div><strong>{{ events.length }}</strong><span>条审计事件</span></div>
      </article>
      <article class="audit-metric">
        <span class="audit-metric__icon audit-metric__icon--blue"><ClipboardList :size="17" /></span>
        <div><strong>{{ taskEvents }}</strong><span>条任务追踪</span></div>
      </article>
      <article class="audit-metric">
        <span class="audit-metric__icon audit-metric__icon--danger"><AlertTriangle :size="17" /></span>
        <div><strong>{{ riskEvents }}</strong><span>条风险事件</span></div>
      </article>
      <article class="audit-metric audit-metric--latest">
        <span class="audit-metric__icon audit-metric__icon--success"><CheckCircle2 :size="17" /></span>
        <div><strong>最近活动</strong><span>{{ latestEventTime }}</span></div>
      </article>
    </section>

    <section v-if="!errorMessage" class="panel audit-center__panel">
      <header class="panel__header audit-center__header">
        <div>
          <h2>事件记录</h2>
          <p>默认按最新时间排序，事件详情保留操作者、资源、变更摘要和执行步骤关联。</p>
        </div>
        <span class="outline-tag">{{ filteredEvents.length }} / {{ events.length }} 条</span>
      </header>

      <div class="audit-center__filters">
        <label class="search-field audit-center__search">
          <Search :size="15" />
          <input v-model="keyword" placeholder="搜索事件、资源、操作者或摘要" />
        </label>
        <div class="audit-center__filter-group" role="listbox" aria-label="资源类型">
          <button
            v-for="option in resourceOptions"
            :key="option.value"
            type="button"
            class="audit-center__filter-option"
            :class="{ 'is-active': resourceFilter === option.value }"
            :aria-selected="resourceFilter === option.value"
            @click="resourceFilter = option.value"
          >
            {{ option.label }}
          </button>
        </div>
      </div>

      <div v-if="loading" class="audit-center__loading" aria-live="polite">正在读取审计事件</div>
      <div v-else-if="!filteredEvents.length" class="audit-center__empty">
        <FileClock :size="24" />
        <strong>{{ events.length ? '没有匹配的审计事件' : '暂无审计事件' }}</strong>
        <p>{{ events.length ? '调整关键词或资源筛选后再试。' : '完成一次项目配置、任务流转或审批操作后，记录会出现在这里。' }}</p>
      </div>
      <div v-else class="table-wrap">
        <table class="data-table audit-center__table">
          <thead>
            <tr>
              <th>事件</th>
              <th>资源</th>
              <th>操作者</th>
              <th>时间</th>
              <th>追踪</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="event in filteredEvents" :key="event.id" class="audit-event-row" tabindex="0" @click="openEvent(event)" @keydown.enter="openEvent(event)">
              <td>
                <span class="audit-event__title">
                  <i :class="`audit-event__dot audit-event__dot--${eventTone(event)}`" aria-hidden="true" />
                  <strong>{{ event.summary }}</strong>
                </span>
                <small class="mono">{{ event.event_type }}</small>
              </td>
              <td>
                <span class="status-tag" :class="eventTone(event) === 'risk' ? 'danger' : 'info'">{{ resourceLabel.get(event.resource_type as ResourceFilter) ?? event.resource_type }}</span>
                <small class="mono">{{ event.resource_id }}</small>
              </td>
              <td><strong>{{ event.actor_name }}</strong><small class="mono">{{ event.actor_id }}</small></td>
              <td class="audit-event__time">{{ formatDate(event.created_at) }}</td>
              <td><span class="text-link">查看详情</span></td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <div v-if="selectedEvent" class="modal-backdrop" @click.self="closeEvent">
      <section class="confirm-modal audit-event-detail" role="dialog" aria-modal="true" aria-labelledby="audit-event-detail-title">
        <header>
          <div>
            <p class="section-kicker">{{ eventToneLabel(selectedEvent) }} · {{ selectedEvent.event_type }}</p>
            <h2 id="audit-event-detail-title">{{ selectedEvent.summary }}</h2>
          </div>
          <button class="icon-button" aria-label="关闭事件详情" @click="closeEvent"><X :size="18" /></button>
        </header>
        <div class="confirm-modal__body">
          <dl class="compact-dl audit-event-detail__meta">
            <div><dt>事件 ID</dt><dd class="mono">{{ selectedEvent.id }}</dd></div>
            <div><dt>操作者</dt><dd>{{ selectedEvent.actor_name }} · {{ selectedEvent.actor_id }}</dd></div>
            <div><dt>资源</dt><dd class="mono">{{ selectedEvent.resource_type }} · {{ selectedEvent.resource_id }}</dd></div>
            <div><dt>发生时间</dt><dd>{{ formatDate(selectedEvent.created_at) }}</dd></div>
          </dl>
          <section class="audit-event-detail__section">
            <h3>变更摘要</h3>
            <ul v-if="selectedEvent.diff_summary.length">
              <li v-for="item in selectedEvent.diff_summary" :key="item">{{ item }}</li>
            </ul>
            <p v-else class="muted">该事件没有附加变更摘要。</p>
          </section>
          <section v-if="selectedEvent.execution_step_ids?.length" class="audit-event-detail__section">
            <h3>关联执行步骤</h3>
            <div class="tag-row">
              <span v-for="stepId in selectedEvent.execution_step_ids" :key="stepId" class="outline-tag mono">{{ stepId }}</span>
            </div>
          </section>
        </div>
        <footer>
          <button class="button button--secondary" @click="closeEvent">关闭</button>
          <button v-if="selectedEvent.resource_type === 'task'" class="button button--primary audit-event-detail__task-link" @click="goToResource(selectedEvent)">
            查看关联任务
          </button>
        </footer>
      </section>
    </div>
  </section>
</template>

<style scoped>
.audit-center__toolbar h2 {
  margin-top: 3px;
  color: var(--text-strong);
  font-size: 22px;
  line-height: 29px;
}

.audit-center__toolbar > div > p:last-child {
  margin-top: 5px;
  color: var(--text-soft);
  font-size: 12px;
}

.audit-center__summary {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
  margin-bottom: 16px;
}

.audit-metric {
  display: flex;
  align-items: center;
  gap: 11px;
  min-width: 0;
  min-height: 76px;
  padding: 14px;
  border: 1px solid var(--panel-border);
  border-radius: 8px;
  background: var(--panel-bg);
  box-shadow: var(--shadow-sm);
}

.audit-metric__icon {
  display: grid;
  flex: none;
  place-items: center;
  width: 32px;
  height: 32px;
  border-radius: 8px;
  color: #8b5a00;
  background: #fff4d9;
}

.audit-metric__icon--blue {
  color: #2859d6;
  background: #eaf1ff;
}

.audit-metric__icon--danger {
  color: #b42318;
  background: #fff0ee;
}

.audit-metric__icon--success {
  color: #187448;
  background: #eaf8f0;
}

.audit-metric div {
  display: grid;
  gap: 3px;
  min-width: 0;
}

.audit-metric strong {
  color: var(--text-strong);
  font-size: 19px;
  line-height: 23px;
  font-variant-numeric: tabular-nums;
}

.audit-metric span:not(.audit-metric__icon) {
  overflow: hidden;
  color: var(--text-soft);
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.audit-metric--latest strong {
  font-size: 13px;
}

.audit-center__panel {
  overflow: hidden;
}

.audit-center__header {
  align-items: center;
}

.audit-center__filters {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 16px;
  border-bottom: 1px solid var(--panel-border-soft);
  background: var(--theme-surface-subtle);
}

.audit-center__search {
  width: min(360px, 100%);
}

.audit-center__filter-group {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  padding: 3px;
  border: 1px solid var(--theme-border);
  border-radius: 8px;
  background: var(--theme-surface-base);
}

.audit-center__filter-option {
  min-height: 29px;
  padding: 0 10px;
  border: 0;
  border-radius: 6px;
  color: var(--theme-text-muted);
  background: transparent;
  font-size: 11px;
  transition: color .16s ease, background .16s ease, box-shadow .16s ease;
}

.audit-center__filter-option:hover {
  color: var(--theme-text-heading);
  background: var(--theme-surface-hover);
}

.audit-center__filter-option.is-active {
  color: var(--theme-link);
  background: var(--theme-accent-soft);
  box-shadow: inset 0 0 0 1px var(--theme-accent-border);
  font-weight: 700;
}

.audit-center__table td {
  height: 62px;
  vertical-align: middle;
}

.audit-center__table td:first-child {
  min-width: 280px;
  white-space: normal;
}

.audit-center__table td small {
  overflow-wrap: anywhere;
}

.audit-event-row {
  cursor: pointer;
  outline: 0;
}

.audit-event-row:focus-visible {
  background: var(--theme-surface-hover);
  box-shadow: inset 3px 0 0 var(--brand);
}

.audit-event__title {
  display: flex;
  align-items: flex-start;
  gap: 8px;
}

.audit-event__title strong {
  line-height: 18px;
}

.audit-event__dot {
  flex: none;
  width: 7px;
  height: 7px;
  margin-top: 5px;
  border-radius: 50%;
  background: #6c7f96;
}

.audit-event__dot--success {
  background: #1f8a57;
}

.audit-event__dot--risk {
  background: #c24135;
}

.audit-event__dot--info {
  background: #3970d8;
}

.audit-event__time {
  color: var(--theme-text-muted) !important;
  font-size: 11px;
  white-space: nowrap !important;
}

.audit-center__loading,
.audit-center__empty {
  display: grid;
  place-items: center;
  min-height: 230px;
  padding: 28px;
  color: var(--theme-text-muted);
  text-align: center;
}

.audit-center__empty {
  gap: 8px;
}

.audit-center__empty svg {
  color: var(--theme-text-faint);
}

.audit-center__empty strong {
  color: var(--theme-text-heading);
  font-size: 13px;
}

.audit-center__empty p {
  max-width: 360px;
  color: var(--theme-text-muted);
  font-size: 11px;
  line-height: 18px;
}

.audit-center__error {
  margin-bottom: 16px;
}

.audit-event-detail {
  width: min(620px, 100%);
}

.audit-event-detail__meta {
  gap: 10px;
  padding-bottom: 16px;
  border-bottom: 1px solid var(--panel-border-soft);
}

.audit-event-detail__meta dd {
  max-width: 70%;
  overflow-wrap: anywhere;
}

.audit-event-detail__section {
  margin-top: 18px;
}

.audit-event-detail__section h3 {
  margin-bottom: 9px;
  color: var(--theme-text-heading);
  font-size: 12px;
}

.audit-event-detail__section ul {
  display: grid;
  gap: 7px;
  margin: 0;
  padding-left: 17px;
  color: var(--theme-text);
  font-size: 12px;
  line-height: 18px;
}

@media (max-width: 920px) {
  .audit-center__summary {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .audit-center__filters {
    align-items: stretch;
    flex-direction: column;
  }

  .audit-center__search {
    width: 100%;
  }

  .audit-center__filter-group {
    overflow-x: auto;
    flex-wrap: nowrap;
  }

  .audit-center__filter-option {
    flex: none;
  }
}

@media (max-width: 560px) {
  .audit-center__summary {
    grid-template-columns: 1fr;
  }

  .audit-center__table {
    min-width: 760px;
  }
}
</style>
