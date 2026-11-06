<script setup lang="ts">
import { ArrowLeft, Bot, Check, CircleAlert, ExternalLink, RefreshCw, Save } from '@lucide/vue'
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { useRoute, useRouter } from 'vue-router'
import { ApiError } from '../services/api'
import { createTaskFromTeamRequest, getTeamRequest, syncTeamRequestGitee, updateTeamRequest } from '../services/teamRequests'
import { useAuthStore } from '../stores/auth'
import TaskFilterSelect from '../components/TaskFilterSelect.vue'
import type { TaskFilterOption } from '../components/TaskFilterSelect.vue'
import {
  requestPriorityText,
  requestStatusText,
  requestTypeText,
  type RequestPriority,
  type RequestType,
  type TeamRequest,
} from '../types/domain'
import { connectRealtime, subscribeRealtime } from '../services/realtime'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const loading = ref(false)
const saving = ref(false)
const syncing = ref(false)
const creatingTask = ref(false)
const errorText = ref('')
const request = ref<TeamRequest | null>(null)
const form = reactive({
  type: 'demand' as RequestType,
  title: '',
  body: '',
  reproduction_steps: '',
  acceptance_criteria_text: '',
  priority: 'medium' as RequestPriority,
  labels_text: '',
  target_branch: '',
  test_command: '',
})

const requestId = computed(() => {
  const raw = route.params.id
  return Array.isArray(raw) ? raw[0] : raw
})
const editable = computed(() => Boolean(auth.hasPermission('request:create') && request.value && ['DRAFT', 'SYNC_FAILED'].includes(request.value.sync_status)))
// DEV-016-B：可同步 = 有创建权限 + DRAFT/SYNC_FAILED + 开发者只能同步本人草稿（与后端一致）
const canSync = computed(() => {
  const record = request.value
  if (!record || !auth.hasPermission('request:create')) return false
  if (!['DRAFT', 'SYNC_FAILED'].includes(record.sync_status)) return false
  if (auth.hasRole('developer') && record.created_by_id !== auth.currentUser?.id) return false
  return true
})
const isAuthExpired = computed(() => request.value?.sync_status === 'SYNC_FAILED' && request.value?.manual_reason === 'auth_expired')
const canReauthorize = computed(() => auth.hasPermission('project:manage'))
const synced = computed(() => Boolean(request.value && ['SYNCED', 'TASK_CREATED'].includes(request.value.sync_status)))
// DEV-016-C：创建任务权限与后端一致——request:create + 仅已同步状态 + 开发者只能操作本人需求
const canCreateTask = computed(() => {
  const record = request.value
  if (!record || !auth.hasPermission('request:create')) return false
  if (!['SYNCED', 'TASK_CREATED'].includes(record.sync_status)) return false
  if (auth.hasRole('developer') && record.created_by_id !== auth.currentUser?.id) return false
  return true
})
const linkedTaskId = computed(() => request.value?.task_id ?? '')
let unsubscribeRealtime: (() => void) | undefined
const typeOptions: TaskFilterOption[] = [
  { value: 'demand', label: '需求' },
  { value: 'bug', label: 'Bug' },
  { value: 'refactor', label: '重构' },
  { value: 'test', label: '测试' },
  { value: 'doc', label: '文档' },
]
const priorityOptions: TaskFilterOption[] = [
  { value: 'high', label: '高' },
  { value: 'medium', label: '中' },
  { value: 'low', label: '低' },
]

onMounted(() => {
  void loadRequest()
  connectRealtime()
  unsubscribeRealtime = subscribeRealtime((event) => {
    if (event.resource_type === 'team_request' && event.resource_id === requestId.value) {
      void loadRequest()
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

function splitLines(value: string) {
  return value.split(/\r?\n/).map((item) => item.trim()).filter(Boolean)
}

function hydrate(record: TeamRequest) {
  request.value = record
  form.type = record.type
  form.title = record.title
  form.body = record.body
  form.reproduction_steps = record.reproduction_steps
  form.acceptance_criteria_text = record.acceptance_criteria.join('\n')
  form.priority = record.priority ?? 'medium'
  form.labels_text = record.labels.join('\n')
  form.target_branch = record.target_branch ?? ''
  form.test_command = record.test_command ?? ''
}

async function loadRequest() {
  loading.value = true
  errorText.value = ''
  try {
    hydrate(await getTeamRequest(requestId.value))
  } catch (error) {
    errorText.value = apiMessage(error, '平台需求详情加载失败')
  } finally {
    loading.value = false
  }
}

async function saveRequest() {
  if (!request.value || !editable.value) return
  saving.value = true
  errorText.value = ''
  try {
    const updated = await updateTeamRequest(request.value.id, {
      type: form.type,
      title: form.title,
      body: form.body,
      reproduction_steps: form.reproduction_steps,
      acceptance_criteria: splitLines(form.acceptance_criteria_text),
      priority: form.priority,
      labels: splitLines(form.labels_text.replaceAll(',', '\n')),
      target_branch: form.target_branch || null,
      test_command: form.test_command || null,
    })
    hydrate(updated)
    if (updated.content_sufficient) {
      ElMessage.success('平台需求草稿已保存')
    } else {
      ElMessage.warning(updated.validation_warnings[0] ?? '草稿已保存，但同步前需要补充正文')
    }
  } catch (error) {
    errorText.value = apiMessage(error, '平台需求保存失败')
    ElMessage.error(errorText.value)
  } finally {
    saving.value = false
  }
}

async function syncGitee() {
  const record = request.value
  if (!record || !canSync.value || syncing.value) return
  syncing.value = true
  errorText.value = ''
  try {
    const updated = await syncTeamRequestGitee(record.id)
    hydrate(updated)
    if (updated.sync_status === 'TASK_CREATED') {
      ElMessage.success('已同步 Gitee Issue，并自动创建了 Agent 任务')
    } else if (updated.sync_status === 'SYNCED') {
      ElMessage.success('已同步 Gitee Issue')
    } else if (updated.sync_status === 'SYNC_FAILED') {
      ElMessage.error(updated.failure_reason ?? '同步失败，请重试')
    }
  } catch (error) {
    errorText.value = apiMessage(error, '同步 Gitee 失败')
    ElMessage.error(errorText.value)
  } finally {
    syncing.value = false
  }
}

// DEV-016-C：从已同步需求一键创建/复用 Agent 任务；后端保证幂等，按钮禁用只是体验层
async function createAgentTask() {
  const record = request.value
  if (!record || !canCreateTask.value || creatingTask.value) return
  creatingTask.value = true
  errorText.value = ''
  try {
    const result = await createTaskFromTeamRequest(record.id)
    hydrate(result.request)
    if (result.reused) {
      ElMessage.info('该 Issue 已存在未结束任务，已直接关联，无需重复创建')
    } else if (result.task.manual_required) {
      ElMessage.warning('任务已创建，但 Issue 上下文不足，已转入人工补充，请在任务详情补充信息')
    } else {
      ElMessage.success('Agent 任务已创建')
    }
  } catch (error) {
    errorText.value = apiMessage(error, '创建 Agent 任务失败')
    ElMessage.error(errorText.value)
  } finally {
    creatingTask.value = false
  }
}
</script>

<template>
  <section class="page-content request-page">
    <div class="breadcrumb"><RouterLink to="/requests"><ArrowLeft :size="15" /> 返回平台需求</RouterLink><span>/</span><b>{{ request?.request_no ?? '需求详情' }}</b></div>

    <div v-if="errorText" class="form-alert request-alert">
      <CircleAlert :size="17" />
      <span>{{ errorText }}</span>
    </div>

    <section v-if="request" class="request-detail">
      <header class="panel request-detail__summary">
        <div class="request-detail__identity">
          <div class="request-detail__identity-line">
            <span class="request-detail__number">{{ request.request_no }}</span>
            <span class="request-detail__separator">/</span>
            <span class="request-detail__record-type">平台需求</span>
          </div>
          <h1>{{ request.title }}</h1>
          <p class="request-detail__source">
            <span>{{ request.project_name }}</span>
            <span aria-hidden="true">/</span>
            <span class="mono">{{ request.repository_name }}</span>
          </p>
        </div>
        <div class="request-detail__facts" aria-label="需求状态">
          <span class="request-detail__status" :data-status="request.sync_status">
            <i aria-hidden="true" />
            {{ requestStatusText[request.sync_status] }}
          </span>
          <span class="outline-tag">{{ requestTypeText[request.type] }}</span>
          <span class="outline-tag">优先级 · {{ request.priority ? requestPriorityText[request.priority] : '未设' }}</span>
        </div>
      </header>

      <div class="panel request-sync">
        <div v-if="request.sync_status === 'SYNCING'" class="request-sync__line">
          <span class="request-sync__mark request-sync__mark--loading" aria-hidden="true"><span class="request-sync__spinner" /></span>
          <div class="request-sync__hint">
            <strong>正在同步到 Gitee</strong>
            <p>正在创建 Gitee Issue，请勿重复点击。</p>
          </div>
          <button type="button" class="button button--secondary" disabled>同步中</button>
        </div>

        <div v-else-if="canSync" class="request-sync__line">
          <span class="request-sync__mark" aria-hidden="true"><RefreshCw :size="16" /></span>
          <div class="request-sync__hint">
            <strong>{{ request.sync_status === 'SYNC_FAILED' ? '上次同步失败' : '待同步到 Gitee' }}</strong>
            <p v-if="request.sync_status === 'SYNC_FAILED'">{{ request.failure_reason }}</p>
            <p v-else>同步后将在 Gitee 仓库创建对应 Issue，并记录映射。</p>
          </div>
          <button type="button" class="button button--primary" :disabled="syncing || saving" @click="syncGitee">
            <RefreshCw :size="16" />
            {{ request.sync_status === 'SYNC_FAILED' ? '重试同步' : '同步到 Gitee' }}
          </button>
        </div>

        <div v-else-if="request.sync_status === 'SYNC_FAILED'" class="request-sync__line">
          <span class="request-sync__mark request-sync__mark--danger" aria-hidden="true"><CircleAlert :size="16" /></span>
          <div class="request-sync__hint">
            <strong>同步失败</strong>
            <p>{{ request.failure_reason }}</p>
          </div>
        </div>

        <div v-else-if="synced" class="request-sync__line request-sync__line--ok">
          <span class="request-sync__mark request-sync__mark--success" aria-hidden="true"><Check :size="17" /></span>
          <div class="request-sync__hint">
            <strong>已同步 Gitee Issue</strong>
            <p>平台需求与 Gitee Issue 映射已建立。</p>
          </div>
          <a v-if="request.gitee_issue_url" :href="request.gitee_issue_url" target="_blank" rel="noopener" class="button button--secondary request-sync__link">
            <ExternalLink :size="15" />
            查看 Issue #{{ request.gitee_issue_number }}
          </a>
        </div>

        <div v-if="isAuthExpired" class="request-sync__auth">
          <strong>需要重新授权</strong>
          <p>Gitee Token 已过期或无权限，请重新授权后重试。</p>
          <button v-if="canReauthorize" type="button" class="button button--secondary" @click="router.push('/settings')">
            前往项目配置重新授权
          </button>
          <p v-else class="request-sync__muted">请联系项目管理员更新 Gitee Token 后重试。</p>
        </div>
      </div>

      <div v-if="synced" class="panel request-task">
        <div class="request-task__label">Agent 执行入口</div>
        <div class="request-task__line">
          <span class="request-task__mark" aria-hidden="true"><Bot :size="17" /></span>
          <div class="request-task__hint">
            <strong>Agent 任务</strong>
            <p v-if="linkedTaskId">该需求已关联 Agent 任务，可直接跳转处理；任务结束后也可新建版本。</p>
            <p v-else>同步已完成，可一键创建 Agent 任务，自动绑定项目、仓库、Issue 与当前 Commit。</p>
          </div>
          <div class="request-task__actions">
            <RouterLink v-if="linkedTaskId" class="button button--primary request-task__link" :to="`/tasks/${linkedTaskId}`">
              <Bot :size="15" />
              查看任务 {{ linkedTaskId }}
            </RouterLink>
            <button
              v-if="canCreateTask"
              type="button"
              class="button"
              :class="linkedTaskId ? 'button--secondary' : 'button--primary'"
              :disabled="creatingTask"
              @click="createAgentTask"
            >
              <Bot :size="15" />
              {{ creatingTask ? '创建中' : linkedTaskId ? '新建任务版本' : '创建 Agent 任务' }}
            </button>
          </div>
        </div>
      </div>

      <div v-if="request.validation_warnings.length" class="page-notice page-notice--info">
        <span class="page-notice__icon"><CircleAlert :size="16" /></span>
        <div class="page-notice__body">
          <strong>需要补充信息</strong>
          <p v-for="warning in request.validation_warnings" :key="warning">{{ warning }}</p>
        </div>
      </div>

      <form class="panel request-form" @submit.prevent="saveRequest">
        <header class="request-form__header">
          <div>
            <h2>需求内容</h2>
            <p>完善需求上下文，保存后可继续同步到 Gitee 或创建 Agent 任务。</p>
          </div>
          <span class="request-form__state" :class="{ 'request-form__state--editable': editable }">
            {{ editable ? '可编辑草稿' : '只读记录' }}
          </span>
        </header>

        <div class="request-form__body">
          <section class="request-form__section">
            <div class="request-form__section-head">
              <div>
                <h3>基本信息</h3>
                <p>先确认需求类型和处理优先级。</p>
              </div>
            </div>
            <div class="request-form__grid request-form__grid--compact">
              <label class="request-field">
                <span>类型</span>
                <TaskFilterSelect v-model="form.type" :options="typeOptions" ariaLabel="需求类型" :disabled="!editable || saving" class="request-form-select" />
              </label>
              <label class="request-field">
                <span>优先级</span>
                <TaskFilterSelect v-model="form.priority" :options="priorityOptions" ariaLabel="优先级" :disabled="!editable || saving" class="request-form-select" />
              </label>
              <label class="request-field request-field--full">
                <span>标题</span>
                <input v-model="form.title" :disabled="!editable || saving" />
              </label>
            </div>
          </section>

          <section class="request-form__section">
            <div class="request-form__section-head">
              <div>
                <h3>问题描述</h3>
                <p>把现象、影响和可复现路径写成开发者可以直接执行的上下文。</p>
              </div>
            </div>
            <div class="request-form__grid">
              <label class="request-field request-field--full">
                <span>正文</span>
                <textarea v-model="form.body" rows="5" :disabled="!editable || saving" />
              </label>
              <label class="request-field">
                <span>复现步骤</span>
                <textarea v-model="form.reproduction_steps" rows="4" :disabled="!editable || saving" />
              </label>
              <label class="request-field">
                <span>验收标准</span>
                <textarea v-model="form.acceptance_criteria_text" rows="4" :disabled="!editable || saving" />
              </label>
            </div>
          </section>

          <section class="request-form__section">
            <div class="request-form__section-head">
              <div>
                <h3>交付约束</h3>
                <p>这些信息会随需求一起传递给后续的任务执行流程。</p>
              </div>
            </div>
            <div class="request-form__grid">
              <label class="request-field">
                <span>标签</span>
                <textarea v-model="form.labels_text" rows="3" :disabled="!editable || saving" />
              </label>
              <label class="request-field">
                <span>期望分支</span>
                <input v-model="form.target_branch" :disabled="!editable || saving" />
              </label>
              <label class="request-field request-field--full">
                <span>测试命令</span>
                <input v-model="form.test_command" :disabled="!editable || saving" />
              </label>
            </div>
          </section>

          <section class="request-form__section request-form__section--meta">
            <div class="request-form__section-head">
              <div>
                <h3>系统记录</h3>
                <p>用于追踪需求来源、修改时间和外部 Issue 映射。</p>
              </div>
            </div>
            <dl class="request-meta">
              <div><dt>创建人</dt><dd>{{ request.created_by_name }}</dd></div>
              <div><dt>创建时间</dt><dd>{{ formatTime(request.created_at) }}</dd></div>
              <div><dt>更新时间</dt><dd>{{ formatTime(request.updated_at) }}</dd></div>
              <div><dt>Gitee Issue</dt><dd>
                <a v-if="synced && request.gitee_issue_url" :href="request.gitee_issue_url" target="_blank" rel="noopener">{{ request.gitee_issue_number }}</a>
                <template v-else>{{ request.gitee_issue_number || '尚未同步' }}</template>
              </dd></div>
            </dl>
          </section>
        </div>
        <footer class="form-footer">
          <button type="button" class="button button--secondary" @click="router.push('/requests')">返回列表</button>
          <button v-if="editable" type="submit" class="button button--primary" :disabled="saving">
            <Save :size="16" />
            {{ saving ? '保存中' : '保存草稿' }}
          </button>
        </footer>
      </form>
    </section>

    <section v-else-if="!loading" class="panel request-empty">暂无平台需求详情</section>
  </section>
</template>

<style scoped>
.request-detail {
  display: grid;
  width: min(100%, 1240px);
  margin: 0 auto;
  gap: 14px;
}
.request-detail__summary {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 28px;
  min-height: 132px;
  padding: 25px 26px 23px;
  overflow: hidden;
  background:
    linear-gradient(135deg, color-mix(in srgb, var(--panel-bg) 94%, #163653) 0%, var(--panel-bg) 66%),
    var(--panel-bg);
}
.request-detail__identity {
  min-width: 0;
}
.request-detail__identity-line {
  display: flex;
  align-items: center;
  gap: 9px;
  color: var(--text-soft, #758496);
  font-size: 11px;
  font-weight: 600;
  line-height: 16px;
}
.request-detail__number {
  color: var(--brand, #2249d6);
  font-family: "JetBrains Mono", Consolas, monospace;
  font-weight: 700;
}
.request-detail__separator {
  color: var(--panel-border);
}
.request-detail__record-type {
  color: var(--text-soft, #758496);
}
.request-detail__summary h1 {
  max-width: 800px;
  margin-top: 9px;
  color: var(--text-strong, #172033);
  font-size: clamp(22px, 2.4vw, 32px);
  font-weight: 750;
  letter-spacing: -.02em;
  line-height: 1.24;
  overflow-wrap: anywhere;
}
.request-detail__source {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 7px;
  margin-top: 12px;
  color: var(--text-soft, #758496);
  font-size: 12px;
  line-height: 18px;
}
.request-detail__source .mono {
  color: var(--text-base, #425166);
}
.request-detail__facts {
  display: flex;
  flex: none;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 7px;
  max-width: 310px;
  padding-top: 1px;
}
.request-detail__status,
.request-detail__facts .outline-tag {
  display: inline-flex;
  align-items: center;
  min-height: 26px;
  padding: 3px 9px;
  border: 1px solid var(--panel-border);
  border-radius: 7px;
  color: var(--text-base, #425166);
  background: color-mix(in srgb, var(--panel-bg) 76%, var(--surface-subtle, #f6f8fb));
  font-size: 11px;
  font-weight: 650;
  line-height: 16px;
  white-space: nowrap;
}
.request-detail__status {
  gap: 6px;
}
.request-detail__status i {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--text-soft, #758496);
}
.request-detail__status[data-status="DRAFT"] i,
.request-detail__status[data-status="SYNC_FAILED"] i {
  background: var(--warning, #9b5c03);
}
.request-detail__status[data-status="SYNCING"] i {
  background: var(--brand, #2249d6);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--brand, #2249d6) 14%, transparent);
}
.request-detail__status[data-status="SYNCED"] i,
.request-detail__status[data-status="TASK_CREATED"] i {
  background: var(--success, #187448);
}
.request-detail__facts .outline-tag {
  border-color: var(--panel-border-soft);
  color: var(--text-soft, #758496);
  background: transparent;
}
.request-sync,
.request-task {
  position: relative;
  padding: 17px 19px;
  overflow: hidden;
}
.request-sync__line,
.request-task__line {
  display: grid;
  grid-template-columns: 34px minmax(0, 1fr) auto;
  align-items: center;
  gap: 12px;
}
.request-sync__mark,
.request-task__mark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border: 1px solid color-mix(in srgb, var(--brand, #2249d6) 24%, var(--panel-border));
  border-radius: 8px;
  color: var(--brand, #2249d6);
  background: color-mix(in srgb, var(--brand, #2249d6) 9%, var(--panel-bg));
}
.request-sync__mark--success {
  border-color: color-mix(in srgb, var(--success, #187448) 28%, var(--panel-border));
  color: var(--success, #187448);
  background: color-mix(in srgb, var(--success, #187448) 9%, var(--panel-bg));
}
.request-sync__mark--danger {
  border-color: color-mix(in srgb, var(--danger, #b42318) 28%, var(--panel-border));
  color: var(--danger, #b42318);
  background: color-mix(in srgb, var(--danger, #b42318) 9%, var(--panel-bg));
}
.request-sync__mark--loading {
  color: var(--brand, #2249d6);
}
.request-sync__spinner {
  width: 15px;
  height: 15px;
  border: 2px solid color-mix(in srgb, currentColor 23%, transparent);
  border-top-color: currentColor;
  border-radius: 50%;
  animation: request-sync-rotate .8s linear infinite;
}
.request-sync__hint,
.request-task__hint {
  display: grid;
  min-width: 0;
  gap: 4px;
}
.request-sync__hint strong,
.request-task__hint strong {
  color: var(--text-strong, #172033);
  font-size: 13px;
  line-height: 18px;
}
.request-sync__hint p,
.request-task__hint p {
  margin: 0;
  color: var(--text-soft, #758496);
  font-size: 12px;
  line-height: 18px;
  overflow-wrap: anywhere;
}
.request-sync__link,
.request-task__link {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  white-space: nowrap;
}
.request-sync__line--ok {
  padding: 0;
}
.request-sync__auth {
  display: grid;
  gap: 4px;
  margin: 14px 0 0 46px;
  padding: 10px 12px;
  border: 1px solid color-mix(in srgb, var(--danger, #b42318) 35%, var(--panel-border));
  border-radius: 8px;
  background: color-mix(in srgb, var(--danger, #b42318) 7%, var(--panel-bg));
}
.request-sync__auth strong {
  color: var(--danger, #b42318);
  font-size: 12px;
}
.request-sync__auth p {
  margin: 0;
  color: var(--text-soft, #758496);
  font-size: 12px;
  line-height: 18px;
}
.request-sync__muted {
  color: var(--text-soft, #758496);
  font-size: 11px;
}
.request-task {
  padding-top: 14px;
}
.request-task__label {
  margin: 0 0 10px 46px;
  color: var(--text-soft, #758496);
  font-size: 10px;
  font-weight: 750;
  letter-spacing: .07em;
  text-transform: uppercase;
}
.request-task__mark {
  border-color: color-mix(in srgb, var(--success, #187448) 28%, var(--panel-border));
  color: var(--success, #187448);
  background: color-mix(in srgb, var(--success, #187448) 9%, var(--panel-bg));
}
.request-task__actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 8px;
}
.request-task__actions .button {
  min-width: 132px;
}
.request-form {
  overflow: hidden;
}
@keyframes request-sync-rotate {
  to {
    transform: rotate(360deg);
  }
}
.request-form__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 19px 21px;
  border-bottom: 1px solid var(--panel-border-soft);
}
.request-form__header h2 {
  color: var(--text-strong, #172033);
  font-size: 16px;
  font-weight: 750;
  letter-spacing: -.01em;
}
.request-form__header p {
  margin-top: 4px;
  color: var(--text-soft, #758496);
  font-size: 12px;
  line-height: 18px;
}
.request-form__state {
  flex: none;
  padding: 4px 8px;
  border: 1px solid var(--panel-border);
  border-radius: 6px;
  color: var(--text-soft, #758496);
  background: var(--surface-subtle, #f6f8fb);
  font-size: 10px;
  font-weight: 700;
  white-space: nowrap;
}
.request-form__state--editable {
  border-color: color-mix(in srgb, var(--brand, #2249d6) 25%, var(--panel-border));
  color: var(--brand, #2249d6);
  background: color-mix(in srgb, var(--brand, #2249d6) 7%, var(--panel-bg));
}
.request-form__body {
  display: grid;
  gap: 0;
  padding: 0 21px;
}
.request-form__section {
  padding: 20px 0 21px;
  border-bottom: 1px solid var(--panel-border-soft);
}
.request-form__section:last-child {
  border-bottom: 0;
}
.request-form__section-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 14px;
  margin-bottom: 14px;
}
.request-form__section-head h3 {
  color: var(--text-strong, #172033);
  font-size: 13px;
  font-weight: 750;
  line-height: 18px;
}
.request-form__section-head p {
  margin-top: 3px;
  color: var(--text-soft, #758496);
  font-size: 11px;
  line-height: 17px;
}
.request-form__grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px 16px;
}
.request-form__grid--compact {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}
.request-field {
  display: grid;
  min-width: 0;
  gap: 6px;
}
.request-field > span {
  color: var(--text-base, #425166);
  font-size: 11px;
  font-weight: 700;
  line-height: 16px;
}
.request-field--full {
  grid-column: 1 / -1;
}
.request-field input,
.request-field textarea {
  font-size: 13px;
  line-height: 20px;
}
.request-field textarea {
  min-height: 0;
}
.request-form .task-filter-select {
  width: 100%;
  min-width: 0;
}
.request-form .task-filter-select__trigger {
  min-height: 42px;
}
.request-form__section--meta {
  padding-bottom: 18px;
}
.request-meta {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 8px;
  margin: 0;
}
.request-meta div {
  min-width: 0;
  padding: 10px 11px;
  border: 1px solid var(--panel-border);
  border-radius: 7px;
  background: var(--surface-subtle, #f6f8fb);
}
.request-meta dd,
.request-meta dd a {
  overflow: hidden;
  color: var(--text-base, #425166);
  font-size: 12px;
  line-height: 17px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.request-meta dd a {
  color: var(--brand, #2249d6);
  text-decoration: underline;
  text-decoration-color: color-mix(in srgb, var(--brand, #2249d6) 35%, transparent);
  text-underline-offset: 3px;
}
.request-meta dt {
  color: var(--text-soft, #758496);
  font-size: 12px;
}
.request-meta dd {
  margin: 4px 0 0;
}
.request-form .form-footer {
  padding: 14px 21px 17px;
  background: var(--surface-subtle, #f6f8fb);
}
.request-detail .panel:hover {
  box-shadow: var(--shadow-sm);
}
@media (max-width: 900px) {
  .request-detail__summary {
    flex-direction: column;
    min-height: 0;
    padding: 21px;
  }
  .request-detail__facts {
    justify-content: flex-start;
    max-width: none;
    padding-top: 0;
  }
  .request-sync__line,
  .request-task__line {
    grid-template-columns: 34px minmax(0, 1fr);
  }
  .request-sync__line > .button,
  .request-sync__line > .request-sync__link,
  .request-task__actions {
    grid-column: 2;
    justify-content: flex-start;
  }
  .request-sync__auth {
    margin-left: 0;
  }
}
@media (max-width: 640px) {
  .request-detail__summary h1 {
    font-size: 23px;
  }
  .request-detail__source {
    margin-top: 9px;
  }
  .request-sync,
  .request-task {
    padding: 15px;
  }
  .request-form__header {
    align-items: flex-start;
    flex-direction: column;
    padding: 17px;
  }
  .request-form__body {
    padding: 0 17px;
  }
  .request-form__grid,
  .request-meta {
    grid-template-columns: 1fr;
  }
  .request-form__section {
    padding: 17px 0;
  }
  .request-field--full {
    grid-column: auto;
  }
  .request-form .form-footer {
    flex-direction: column-reverse;
    padding: 13px 17px 16px;
  }
  .request-form .form-footer .button {
    width: 100%;
  }
}
</style>
