<script setup lang="ts">
import { AlertTriangle, Check, ChevronRight, Clock3, RotateCcw, UserCheck, X } from '@lucide/vue'
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useAuthStore } from '../stores/auth'
import { useWorkspaceStore } from '../stores/workspace'
import { apiRequest } from '../services/api'
import { getDataSourceMode } from '../services/dataSourceMode'
import type { AuthUser } from '../types/auth'
import type { PlanChangeType, PlanStatus, RiskThreshold } from '../types/domain'
import { connectRealtime, subscribeRealtime } from '../services/realtime'
import TaskFilterSelect from './TaskFilterSelect.vue'
import type { TaskFilterOption } from './TaskFilterSelect.vue'

const props = defineProps<{ taskId: string }>()

const store = useWorkspaceStore()
const auth = useAuthStore()

const OVERDUE_MS = 24 * 3600 * 1000

const isReal = computed(() => getDataSourceMode() === 'real')
const task = computed(() => store.taskById(props.taskId) ?? null)
const artifact = computed(() => store.artifactFor(props.taskId))
const plans = computed(() => store.plansByTask[props.taskId] ?? [])
const latestPlan = computed(() => store.latestPlanOf(props.taskId))
const canOperate = computed(() => auth.hasPermission('task:operate'))
const canManage = computed(() => auth.hasPermission('approval:manage'))
const canAssign = computed(() => auth.hasPermission('approval:assign'))
const busy = ref('')
const errorText = ref('')
const selectedVersion = ref<number | null>(null)
const actionMode = ref<'none' | 'rewrite' | 'reject'>('none')
const actionReason = ref('')
const comment = ref('')
const riskDraft = ref<RiskThreshold | null>(null)
const riskAdjustReason = ref('')
const plansLoadedFor = ref('')
let unsubscribeRealtime: (() => void) | undefined

const roleLabelMap: Record<AuthUser['role'], string> = {
  admin: '管理员',
  approver: '审批人',
  developer: '开发者',
  lead: '研发负责人',
  observer: '观察者',
}
const needsApprover = computed(() => task.value !== null && task.value.approver === '未指定')
// 指派/改派窗口：ANALYZED（可先指派再生成计划）与 WAITING_APPROVAL（改派）两个阶段
const assignWindow = computed<'assign' | 'reassign' | null>(() => {
  const status = task.value?.status
  if (!isReal.value || !canAssign.value || !status) return null
  if (status === 'ANALYZED' && needsApprover.value) return 'assign'
  if (status === 'ANALYZED' || status === 'WAITING_APPROVAL') return 'reassign'
  return null
})
const assignFormOpen = ref(false)
const assignTargetId = ref('')
const assignBusy = ref(false)
const assignError = ref('')
const assignCandidates = ref<AuthUser[]>([])
const planOverdue = computed(() => {
  const plan = currentPlan.value
  if (!plan || plan.status !== 'WAITING_APPROVAL') return false
  if (task.value?.status !== 'WAITING_APPROVAL') return false
  const created = new Date(plan.created_at).getTime()
  return Number.isFinite(created) && Date.now() - created > OVERDUE_MS
})

watch(
  assignWindow,
  async (window) => {
    if (window !== 'assign') return
    assignFormOpen.value = true
    await loadAssignCandidates()
  },
  { immediate: true },
)

function currentApproverLabel() {
  return task.value?.approver === '未指定' ? '未指派' : (task.value?.approver ?? '未指派')
}

async function loadAssignCandidates() {
  if (assignCandidates.value.length || !isReal.value) return
  try {
    const users = await apiRequest<AuthUser[]>('/api/approvals/candidates')
    assignCandidates.value = users.filter((user) => user.role === 'admin' || user.role === 'approver')
  } catch (unknown) {
    assignError.value = requestError(unknown)
  }
}

async function toggleAssignForm() {
  if (assignFormOpen.value) {
    assignFormOpen.value = false
    return
  }
  assignError.value = ''
  await loadAssignCandidates()
  assignFormOpen.value = true
}

async function submitAssign() {
  if (!assignTargetId.value || assignBusy.value || !task.value) return
  assignBusy.value = true
  assignError.value = ''
  try {
    await store.assignApprovalOwner(props.taskId, assignTargetId.value)
    assignFormOpen.value = false
    assignTargetId.value = ''
  } catch (unknown) {
    assignError.value = requestError(unknown)
  } finally {
    assignBusy.value = false
  }
}

const currentPlan = computed(
  () => plans.value.find((plan) => plan.version === selectedVersion.value) ?? latestPlan.value ?? null,
)
const sortedPlans = computed(() => [...plans.value].sort((left, right) => right.version - left.version))
const isHistorical = computed(() => Boolean(currentPlan.value && selectedVersion.value !== latestPlan.value?.version))
const riskChanged = computed(() => Boolean(currentPlan.value && riskDraft.value && riskDraft.value !== currentPlan.value.risk_level))
const waitingForApproval = computed(() => currentPlan.value?.status === 'WAITING_APPROVAL' && task.value?.status === 'WAITING_APPROVAL')
const approvedResult = computed(() => currentPlan.value?.status === 'APPROVED' && task.value?.status === 'APPROVED')
const planDetailOpen = ref(false)
const planDetailTab = ref<'files' | 'steps' | 'risk' | 'references'>('files')

const planChangeStats = computed(() => {
  const plan = currentPlan.value
  const stats: Record<PlanChangeType, number> = { add: 0, modify: 0, delete: 0, unknown: 0 }
  for (const file of plan?.file_scope ?? []) stats[file.change_type] += 1
  return stats
})

const planReferenceCount = computed(() => {
  const plan = currentPlan.value
  if (!plan) return 0
  return plan.assumptions.length + plan.rollback_plan.length + (plan.referenced_historical_tasks?.length ?? 0)
})

const planHeadline = computed(() => {
  const goal = currentPlan.value?.goal?.replace(/\s+/g, ' ').trim() ?? ''
  if (!goal) return ''
  if (/代码上下文|RAG|召回率|BM25|retrieval_strategy/i.test(goal)) return '提升代码上下文 RAG 召回质量'
  const firstClause = goal.split(/[，,。；;]/)[0] ?? goal
  return firstClause.length > 32 ? `${firstClause.slice(0, 32)}…` : firstClause
})

const planOverviewItems = computed(() => {
  const plan = currentPlan.value
  if (!plan) return []
  const changeText = [
    planChangeStats.value.modify ? `${planChangeStats.value.modify} 修改` : '',
    planChangeStats.value.add ? `${planChangeStats.value.add} 新增` : '',
    planChangeStats.value.delete ? `${planChangeStats.value.delete} 删除` : '',
    planChangeStats.value.unknown ? `${planChangeStats.value.unknown} 待定` : '',
  ].filter(Boolean).join(' / ') || '待确认'
  return [
    { label: '文件范围', value: `${plan.file_scope.length} 个位置`, detail: changeText, tab: 'files' as const },
    { label: '实施步骤', value: `${plan.steps.length} 步`, detail: plan.steps[0]?.title ?? '待补充步骤', tab: 'steps' as const },
    { label: '风险与测试', value: riskMap[plan.risk_level].label, detail: `${plan.test_suggestions.length} 项测试建议`, tab: 'risk' as const },
    { label: '参考说明', value: `${planReferenceCount.value} 项`, detail: providerLabel.value, tab: 'references' as const },
  ]
})

const firstFileScope = computed(() => currentPlan.value?.file_scope[0] ?? null)

function openPlanDetail(tab: 'files' | 'steps' | 'risk' | 'references' = 'files') {
  planDetailTab.value = tab
  planDetailOpen.value = true
}

function closePlanDetail() {
  planDetailOpen.value = false
}

function displayTime(value: string) {
  return value.replace('T', ' ').slice(0, 16)
}

const planStatusMap: Record<PlanStatus, { label: string; tone: string }> = {
  DRAFT: { label: '草稿', tone: 'info' },
  WAITING_APPROVAL: { label: '待审批', tone: 'warning' },
  APPROVED: { label: '已通过', tone: 'success' },
  REJECTED: { label: '已驳回', tone: 'danger' },
  SUPERSEDED: { label: '已取代', tone: 'info' },
}

const changeTypeMap: Record<PlanChangeType, { label: string; tone: string }> = {
  modify: { label: '修改', tone: 'warning' },
  add: { label: '新增', tone: 'success' },
  delete: { label: '删除', tone: 'danger' },
  unknown: { label: '待定', tone: 'info' },
}

const riskMap: Record<RiskThreshold, { label: string; tone: string }> = {
  high: { label: '高风险', tone: 'risk-high' },
  medium: { label: '中风险', tone: 'risk-medium' },
  low: { label: '低风险', tone: 'risk-low' },
}
const riskOptions: TaskFilterOption[] = (['high', 'medium', 'low'] as RiskThreshold[]).map((level) => ({
  value: level,
  label: riskMap[level].label,
}))

function resetActionState() {
  actionMode.value = 'none'
  actionReason.value = ''
  errorText.value = ''
  const plan = currentPlan.value
  if (plan) {
    riskDraft.value = plan.risk_level
    comment.value = ''
    riskAdjustReason.value = ''
  }
}

function onRiskDraftChange(value: string) {
  riskDraft.value = value as RiskThreshold
}

watch(selectedVersion, resetActionState)

watch(
  plans,
  (list) => {
    // 计划列表每次刷新都回到最高版本（生成/重写/审批后均跟随最新版本）
    selectedVersion.value = list.length ? Math.max(...list.map((plan) => plan.version)) : null
    resetActionState()
  },
  { immediate: true },
)

watch(
  task,
  (current) => {
    const id = current?.id ?? ''
    if (!isReal.value || !id || plansLoadedFor.value === id) return
    plansLoadedFor.value = id
    store
      .loadPlans(id)
      .catch(() => {
        errorText.value = '修改计划加载失败，请稍后重试。'
      })
  },
  { immediate: true },
)

watch(
  assignWindow,
  async (window) => {
    if (window !== 'assign') return
    assignFormOpen.value = true
    await loadAssignCandidates()
  },
  { immediate: true },
)

function requestError(unknown: unknown) {
  if (unknown instanceof Error && unknown.message) return unknown.message
  return '操作失败，请稍后重试。'
}

async function generatePlan() {
  if (busy.value) return
  busy.value = 'generate'
  errorText.value = ''
  try {
    await store.generatePlan(props.taskId)
    await store.loadPlans(props.taskId)
  } catch (unknown) {
    errorText.value = requestError(unknown)
  } finally {
    busy.value = ''
  }
}

async function submitApprove() {
  const plan = currentPlan.value
  if (busy.value || !plan) return
  if (riskChanged.value && !riskAdjustReason.value.trim()) return
  busy.value = 'approve'
  errorText.value = ''
  try {
    const payload: { comment?: string; risk_level?: RiskThreshold; risk_adjust_reason?: string } = {}
    if (comment.value.trim()) payload.comment = comment.value.trim()
    if (riskChanged.value) {
      payload.risk_level = riskDraft.value!
      payload.risk_adjust_reason = riskAdjustReason.value.trim()
    }
    await store.approvePlan(props.taskId, plan.id, payload)
    await store.loadPlans(props.taskId)
  } catch (unknown) {
    errorText.value = requestError(unknown)
  } finally {
    busy.value = ''
  }
}

function openAction(mode: 'rewrite' | 'reject') {
  actionMode.value = mode
  actionReason.value = ''
  errorText.value = ''
}

async function confirmAction() {
  const plan = currentPlan.value
  if (busy.value || !plan || !actionReason.value.trim()) return
  busy.value = actionMode.value
  errorText.value = ''
  try {
    if (actionMode.value === 'rewrite') {
      await store.rewritePlan(props.taskId, plan.id, actionReason.value.trim())
    } else {
      await store.rejectPlan(props.taskId, plan.id, actionReason.value.trim())
    }
    await store.loadPlans(props.taskId)
    actionMode.value = 'none'
    actionReason.value = ''
  } catch (unknown) {
    errorText.value = requestError(unknown)
  } finally {
    busy.value = ''
  }
}

const providerLabel = computed(() => (currentPlan.value?.provider === 'deepseek' ? 'DeepSeek 生成' : '规则基座生成'))

connectRealtime()
unsubscribeRealtime = subscribeRealtime((event) => {
  if (event.task_id !== props.taskId && !(event.resource_type === 'task' && event.resource_id === props.taskId)) return
  if (isReal.value) void store.loadPlans(props.taskId)
})

onBeforeUnmount(() => {
  unsubscribeRealtime?.()
  unsubscribeRealtime = undefined
})
</script>

<template>
  <section class="detail-section plan-panel">
    <header class="plan-panel__header">
      <div class="plan-panel__heading">
        <h3>{{ currentPlan ? planHeadline : (isReal ? '计划待生成' : artifact.planTitle) }}</h3>
        <div v-if="currentPlan" class="plan-panel__goal-row">
          <p class="plan-panel__goal">{{ currentPlan.goal }}</p>
          <button type="button" class="plan-panel__goal-action" @click="openPlanDetail('references')">
            查看完整目标 <ChevronRight :size="13" />
          </button>
        </div>
      </div>
      <div class="plan-panel__tags">
        <span v-if="currentPlan" class="plan-panel__version">V{{ currentPlan.version }}</span>
        <span v-if="currentPlan" class="status-tag" :class="planStatusMap[currentPlan.status].tone">{{ planStatusMap[currentPlan.status].label }}</span>
        <span v-else-if="task?.status === 'APPROVED'" class="status-tag success">审批已通过</span>
      </div>
    </header>

    <p v-if="errorText" class="field-error plan-panel__error" role="alert">{{ errorText }}</p>
    <p v-if="!isReal" class="form-subtitle">演示数据没有计划接口，下方为演示占位内容，真实计划由 AI 生成并进入审批队列。</p>
    <p v-if="planOverdue" class="form-alert plan-overdue"><AlertTriangle :size="15" /> 该计划等待审批已超过 24 小时，请尽快处理或改派审批人。</p>

    <template v-if="currentPlan">
      <div v-if="sortedPlans.length > 1" class="plan-versions" aria-label="历史版本">
        <button
          v-for="plan in sortedPlans"
          :key="plan.version"
          type="button"
          class="plan-versions__item"
          :class="{ 'is-active': plan.version === selectedVersion, 'is-dead': plan.status === 'SUPERSEDED' || plan.status === 'REJECTED' }"
          @click="selectedVersion = plan.version"
        >
          v{{ plan.version }}<span class="plan-versions__dot" :class="planStatusMap[plan.status].tone" />
        </button>
      </div>
      <p v-if="isHistorical" class="form-subtitle">正在查看历史版本 v{{ currentPlan.version }}，内容仅供回顾，不参与当前流转。</p>

      <section class="plan-overview" aria-label="修改计划概览">
        <div v-for="item in planOverviewItems" :key="item.label" class="plan-overview__item">
          <span>{{ item.label }}</span>
          <strong>{{ item.value }}</strong>
          <small>{{ item.detail }}</small>
          <button
            type="button"
            class="plan-overview__open"
            :aria-label="`查看${item.label}`"
            :title="`查看${item.label}`"
            @click="openPlanDetail(item.tab)"
          >
            <ChevronRight :size="14" />
            <span class="visually-hidden">查看{{ item.label }}</span>
          </button>
        </div>
      </section>

      <section v-if="firstFileScope" class="plan-inline-snapshot">
        <div>
          <span class="status-tag" :class="changeTypeMap[firstFileScope.change_type].tone">{{ changeTypeMap[firstFileScope.change_type].label }}</span>
          <code>{{ firstFileScope.file_path }}</code>
        </div>
        <p>{{ firstFileScope.reason }}</p>
        <button type="button" @click="openPlanDetail('files')">查看完整文件范围</button>
      </section>

      <div v-if="planDetailOpen" class="plan-detail-modal" role="dialog" aria-modal="true" aria-label="修改计划详情" @click.self="closePlanDetail">
        <section class="plan-detail-modal__panel">
          <header class="plan-detail-modal__header">
            <div>
              <span class="section-kicker">修改计划详情 · v{{ currentPlan.version }}</span>
              <h3>{{ planHeadline }}</h3>
              <p class="plan-detail-modal__goal">{{ currentPlan.goal }}</p>
            </div>
            <button type="button" class="plan-detail-modal__close" aria-label="关闭修改计划详情" @click="closePlanDetail"><X :size="18" /></button>
          </header>
          <nav class="plan-detail-tabs" aria-label="计划详情分类">
            <button type="button" :class="{ 'is-active': planDetailTab === 'files' }" @click="planDetailTab = 'files'">文件范围</button>
            <button type="button" :class="{ 'is-active': planDetailTab === 'steps' }" @click="planDetailTab = 'steps'">实施步骤</button>
            <button type="button" :class="{ 'is-active': planDetailTab === 'risk' }" @click="planDetailTab = 'risk'">风险测试</button>
            <button type="button" :class="{ 'is-active': planDetailTab === 'references' }" @click="planDetailTab = 'references'">参考说明</button>
          </nav>
          <div class="plan-detail-modal__body">
            <section v-if="planDetailTab === 'files'" class="plan-detail-pane">
              <div class="plan-block__head">
                <h4>文件范围</h4>
                <span>{{ currentPlan.file_scope.length }} 个位置</span>
              </div>
              <ul class="plan-files">
                <li v-for="file in currentPlan.file_scope" :key="file.file_path">
                  <div class="plan-file-row">
                    <code>{{ file.file_path }}</code>
                    <span class="status-tag" :class="changeTypeMap[file.change_type].tone">{{ changeTypeMap[file.change_type].label }}</span>
                  </div>
                  <p>{{ file.reason }}</p>
                  <div class="plan-file-meta">
                    <span v-if="file.risk_hint" class="outline-tag">{{ file.risk_hint }}</span>
                  </div>
                </li>
              </ul>
            </section>

            <section v-else-if="planDetailTab === 'steps'" class="plan-detail-pane">
              <div class="plan-block__head">
                <h4>实施步骤</h4>
                <span>{{ currentPlan.steps.length }} 步</span>
              </div>
              <ol class="plan-list plan-steps">
                <li v-for="step in currentPlan.steps" :key="step.order">
                  <b>{{ step.order }}</b>
                  <span>
                    <strong>{{ step.title }}</strong>
                    <small>{{ step.description }}</small>
                    <small v-if="step.target_files.length" class="mono">涉及：{{ step.target_files.join('、') }}</small>
                    <small v-if="step.risk_hint">风险：{{ step.risk_hint }}</small>
                  </span>
                </li>
              </ol>
            </section>

            <section v-else-if="planDetailTab === 'risk'" class="plan-detail-pane">
              <section class="plan-block plan-risk">
                <h4>风险判定</h4>
                <p>
                  <span class="status-tag" :class="riskMap[currentPlan.risk_level].tone">{{ riskMap[currentPlan.risk_level].label }}</span>
                  <span>{{ currentPlan.risk_reason }}</span>
                </p>
                <p
                  v-if="currentPlan.analysis_risk_level && currentPlan.analysis_risk_level !== currentPlan.risk_level"
                  class="plan-risk__initial"
                >
                  <AlertTriangle :size="14" />
                  <span>分析阶段初判为{{ riskMap[currentPlan.analysis_risk_level].label }}，计划阶段结合文件范围与步骤重算为{{ riskMap[currentPlan.risk_level].label }}。</span>
                </p>
                <p v-if="currentPlan.risk_adjust_reason" class="plan-risk__adjust">
                  <AlertTriangle :size="14" />
                  <span v-if="currentPlan.risk_adjust_from">
                    审批人已调整风险等级：{{ riskMap[currentPlan.risk_adjust_from].label }} → {{ riskMap[currentPlan.risk_level].label }}，原因：{{ currentPlan.risk_adjust_reason }}
                  </span>
                  <span v-else>审批人已调整风险等级并补充原因：{{ currentPlan.risk_adjust_reason }}</span>
                </p>
              </section>
              <section v-if="currentPlan.test_suggestions.length" class="plan-block">
                <h4>测试建议</h4>
                <ul class="plan-notes">
                  <li v-for="(item, index) in currentPlan.test_suggestions" :key="`t-${index}`">{{ item }}</li>
                </ul>
              </section>
            </section>

            <section v-else class="plan-detail-pane">
              <section v-if="currentPlan.assumptions.length" class="plan-block">
                <h4>假设与说明</h4>
                <ul class="plan-notes">
                  <li v-for="(item, index) in currentPlan.assumptions" :key="`a-${index}`">{{ item }}</li>
                </ul>
              </section>
              <section v-if="currentPlan.rollback_plan.length" class="plan-block">
                <h4>回滚计划</h4>
                <ul class="plan-notes">
                  <li v-for="(item, index) in currentPlan.rollback_plan" :key="`r-${index}`">{{ item }}</li>
                </ul>
              </section>
              <section v-if="currentPlan.referenced_historical_tasks?.length" class="plan-block plan-history">
                <h4>历史相似任务参考</h4>
                <p class="plan-history__hint">仅作经验参考，不会自动套用旧 Diff，也不替代当前判断。</p>
                <ul class="plan-history__list">
                  <li v-for="ref in currentPlan.referenced_historical_tasks" :key="`${ref.task_id}-${ref.version}`">
                    <div class="plan-history__head">
                      <RouterLink class="plan-history__link" :to="`/tasks/${ref.task_id}`">{{ ref.task_id }} · v{{ ref.version }}</RouterLink>
                      <span class="outline-tag">相似度 {{ (ref.score * 100).toFixed(0) }}%</span>
                    </div>
                    <strong>{{ ref.title }}</strong>
                    <small v-if="ref.summary">{{ ref.summary }}</small>
                  </li>
                </ul>
              </section>
            </section>
          </div>
        </section>
      </div>

      <p class="plan-panel__meta">
        <span>{{ providerLabel }}</span><i />基于分析第 {{ currentPlan.generated_from_analysis_revision }} 版<template v-if="currentPlan.generated_from_context_ids.length"> · {{ currentPlan.generated_from_context_ids.length }} 项代码上下文</template><i />由 {{ currentPlan.created_by_name }} 于 {{ displayTime(currentPlan.created_at) }} 提交
      </p>

      <footer v-if="waitingForApproval && canManage" class="plan-actions">
        <label class="plan-action-field"><span>审批意见</span><textarea v-model="comment" :disabled="busy !== ''" placeholder="填写审批结论或风险要求（选填）。" /></label>
        <label class="plan-action-field plan-action-field--select"><span>风险等级</span>
          <TaskFilterSelect :model-value="riskDraft ?? ''" :options="riskOptions" ariaLabel="风险等级" :disabled="busy !== ''" @update:model-value="onRiskDraftChange" />
        </label>
        <label v-if="riskChanged" class="plan-action-field"><span>调整风险原因 <b>*</b></span><textarea v-model="riskAdjustReason" :disabled="busy !== ''" placeholder="调整风险等级时必须填写原因。" /><small>审批通过后，任务风险等级会同步为调整值。</small></label>
        <div class="plan-actions__buttons">
          <button class="button button--success" :disabled="busy !== '' || (riskChanged && !riskAdjustReason.trim())" @click="submitApprove"><Check :size="15" /> 通过</button>
          <button class="button button--secondary" :disabled="busy !== ''" @click="openAction('rewrite')"><RotateCcw :size="15" /> 要求重写</button>
          <button class="button button--danger" :disabled="busy !== ''" @click="openAction('reject')"><X :size="15" /> 拒绝</button>
        </div>
      </footer>

      <footer v-if="waitingForApproval && !canManage" class="plan-actions plan-actions--readonly">
        <p v-if="task?.approver && task?.approver !== '未指定'"><Clock3 :size="15" /> 计划已提交审批，等待 {{ task.approver }} 处理。开发者可查看计划内容；审批通过后任务会自动进入 Diff 生成阶段。</p>
        <p v-else><Clock3 :size="15" /> 计划已提交审批，等待指派审批人。开发者可查看计划内容；指派并通过后任务会自动进入 Diff 生成阶段。</p>
        <RouterLink v-if="auth.hasPermission('approval:view')" to="/approvals" class="button button--primary">前往审批</RouterLink>
      </footer>

      <footer v-if="approvedResult" class="plan-actions plan-actions--result">
        <p><Check :size="15" /> 计划已通过，任务进入 Diff 生成阶段。可在任务头部进入 Diff 审核。</p>
        <p v-if="currentPlan.approved_by_name" class="plan-result__meta">
          <span>审批人：{{ currentPlan.approved_by_name }}</span><i />{{ displayTime(currentPlan.approved_at ?? currentPlan.created_at) }}
        </p>
        <p v-if="currentPlan.approval_comment" class="plan-result__meta">审批意见：{{ currentPlan.approval_comment }}</p>
        <p v-if="currentPlan.risk_adjust_from && currentPlan.risk_adjust_from !== currentPlan.risk_level" class="plan-result__meta plan-result__adjust">
          <AlertTriangle :size="13" /> 审批人调整风险：{{ riskMap[currentPlan.risk_adjust_from].label }} → {{ riskMap[currentPlan.risk_level].label }}<template v-if="currentPlan.risk_adjust_reason">，原因：{{ currentPlan.risk_adjust_reason }}</template>
        </p>
        <p v-else-if="currentPlan.risk_adjust_reason" class="plan-result__meta plan-result__adjust">
          <AlertTriangle :size="13" /> 审批人已调整风险等级并补充原因：{{ currentPlan.risk_adjust_reason }}
        </p>
      </footer>

      <footer v-if="currentPlan.status === 'REJECTED'" class="plan-actions plan-actions--readonly">
        <p><X :size="15" /> 该版本计划已被驳回，任务已标记失败。可通过任务失败提示重试，重建任务版本后重新生成计划。</p>
      </footer>
    </template>

    <template v-else>
      <ol v-if="artifact.planItems?.length" class="plan-list plan-steps">
        <li v-for="(item, index) in artifact.planItems" :key="item.title">
          <b>{{ index + 1 }}</b>
          <span><strong>{{ item.title }}</strong><small>{{ item.detail }}</small></span>
        </li>
      </ol>
      <div class="plan-footer">
        <span><AlertTriangle :size="16" /> {{ isReal ? (task?.status === 'ANALYZED' ? '分析已完成。生成计划后，计划与风险等级会提交审批。' : artifact.planFooter) : artifact.planFooter }}</span>
        <button
          v-if="isReal && task?.status === 'ANALYZED' && canOperate && !needsApprover"
          class="button button--primary"
          :disabled="busy !== ''"
          @click="generatePlan"
        >
          {{ busy === 'generate' ? '生成中…' : '生成修改计划' }}
        </button>
        <button v-else-if="isReal && task?.status === 'ANALYZED' && needsApprover && !canAssign" class="button" disabled title="请先指派审批人">生成修改计划（待指派）</button>
        <RouterLink v-else-if="task?.status === 'WAITING_APPROVAL' && auth.hasPermission('approval:view')" to="/approvals" class="button button--primary">前往审批</RouterLink>
      </div>
    </template>

    <div v-if="assignWindow" class="plan-assign">
      <div class="plan-assign__line">
        <span class="plan-assign__owner"><UserCheck :size="14" /> 审批人：{{ currentApproverLabel() }}</span>
        <button v-if="!assignFormOpen" class="button button--ghost" @click="toggleAssignForm">{{ assignWindow === 'assign' ? '指派审批人' : '改派审批人' }}</button>
      </div>
      <p v-if="assignWindow === 'assign' && !assignFormOpen" class="plan-assign__hint">仓库未配置默认审批人：指派后即可生成修改计划。</p>
      <div v-if="assignFormOpen" class="plan-assign__form">
        <TaskFilterSelect v-model="assignTargetId" :options="[
          { value: '', label: '选择审批人…' },
          ...assignCandidates.map((user) => ({ value: user.id, label: `${user.name}（${roleLabelMap[user.role]}）` })),
        ]" ariaLabel="选择审批人" :disabled="assignBusy" class="plan-assign__select" />
        <button class="button button--primary" :disabled="assignBusy || !assignTargetId" @click="submitAssign">{{ assignBusy ? '指派中…' : '确认指派' }}</button>
        <button class="button button--ghost" :disabled="assignBusy" @click="assignFormOpen = false">取消</button>
        <p v-if="assignError" class="field-error plan-assign__error" role="alert">{{ assignError }}</p>
      </div>
    </div>

    <footer v-if="actionMode !== 'none' && waitingForApproval" class="plan-action-mode">
      <label class="plan-action-field"><span>{{ actionMode === 'rewrite' ? '重写原因' : '拒绝原因' }} <b>*</b></span><textarea v-model="actionReason" :disabled="busy !== ''" :placeholder="actionMode === 'rewrite' ? '说明需要调整的内容与原因，系统将生成新版本计划。' : '填写驳回理由，任务将标记失败并可重试重建。'" /></label>
      <div class="plan-actions__buttons">
        <button class="button button--primary" :disabled="busy !== '' || !actionReason.trim()" @click="confirmAction">{{ busy === actionMode ? '提交中…' : '确认提交' }}</button>
        <button class="button button--ghost" :disabled="busy !== ''" @click="actionMode = 'none'">取消</button>
      </div>
    </footer>
  </section>
</template>

<style scoped>
.plan-panel {
  --plan-surface: var(--theme-surface-base);
  --plan-surface-soft: var(--theme-surface-subtle);
  --plan-surface-raised: var(--theme-surface-raised);
  --plan-header: var(--theme-surface-header);
  --plan-border: var(--theme-border);
  --plan-border-strong: var(--theme-border-strong);
  --plan-text: var(--theme-text);
  --plan-heading: var(--theme-text-heading);
  --plan-muted: var(--theme-text-muted);
  --plan-link: var(--theme-link);
  --plan-link-hover: var(--theme-link-hover);
  --plan-tab-active: var(--theme-accent-soft);
  --plan-tab-active-border: var(--theme-accent-border);
  --plan-shadow: var(--theme-modal-shadow);
}

.plan-panel__header {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: start;
  gap: 16px;
}
.plan-panel__heading {
  min-width: 0;
}
.plan-panel__version {
  display: inline-flex;
  min-height: 22px;
  align-items: center;
  padding: 1px 7px;
  border: 1px solid var(--plan-border);
  border-radius: 5px;
  color: var(--plan-muted);
  background: var(--plan-surface-soft);
  font-size: 10px;
  font-weight: 800;
  letter-spacing: .04em;
}
.plan-panel.plan-panel .plan-panel__header h3 {
  max-width: 42ch;
  margin: 0;
  color: var(--plan-heading);
  font-size: 18px;
  font-weight: 750;
  line-height: 25px;
  letter-spacing: 0;
}
.plan-panel__goal-row {
  display: flex;
  align-items: baseline;
  gap: 10px;
  min-width: 0;
  margin-top: 5px;
}
.plan-panel__goal {
  min-width: 0;
  max-width: 68ch;
  margin: 0;
  overflow: hidden;
  color: var(--plan-muted);
  font-size: 12px;
  line-height: 18px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.plan-panel__goal-action {
  display: inline-flex;
  flex: none;
  align-items: center;
  gap: 2px;
  padding: 0;
  border: 0;
  color: var(--plan-link);
  background: transparent;
  font-size: 11px;
  font-weight: 750;
  white-space: nowrap;
}
.plan-panel__goal-action:hover {
  color: var(--plan-link-hover);
}
.plan-panel__tags {
  display: flex;
  justify-content: flex-end;
  gap: 6px;
  align-items: center;
  padding-top: 1px;
}
.plan-panel__error {
  margin-bottom: 10px;
}
.plan-versions {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin: 4px 0 12px;
}
.plan-versions__item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  border: 1px solid var(--plan-border);
  border-radius: 999px;
  background: var(--plan-surface);
  color: var(--plan-text);
  font-size: 12px;
  cursor: pointer;
}
.plan-versions__item.is-active {
  border-color: var(--plan-link);
  color: var(--plan-link);
}
.plan-versions__item.is-dead {
  opacity: 0.62;
}
.plan-versions__dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
}
.plan-versions__dot.success { background: var(--theme-status-success); }
.plan-versions__dot.warning { background: var(--theme-status-warning); }
.plan-versions__dot.danger { background: var(--theme-status-danger); }
.plan-versions__dot.info { background: var(--plan-muted); }
.plan-block {
  margin-top: 14px;
}
.plan-overview {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 0;
  margin-top: 14px;
  overflow: hidden;
  border: 1px solid var(--plan-border);
  border-radius: 8px;
  background: var(--plan-surface-soft);
}
.plan-overview__item {
  position: relative;
  display: grid;
  gap: 4px;
  min-width: 0;
  padding: 11px 36px 11px 12px;
  border-right: 1px solid var(--plan-border);
}
.plan-overview__item:last-child {
  border-right: 0;
}
.plan-overview__item > span {
  color: var(--plan-muted);
  font-size: 11px;
  font-weight: 700;
}
.plan-overview__item strong {
  color: var(--plan-text);
  font-size: 15px;
  line-height: 20px;
}
.plan-overview__item small {
  display: -webkit-box;
  min-height: 32px;
  overflow: hidden;
  color: var(--plan-muted);
  font-size: 11px;
  line-height: 16px;
  text-overflow: ellipsis;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}
.plan-inline-snapshot button {
  width: fit-content;
  padding: 0;
  border: 0;
  color: var(--plan-link);
  background: transparent;
  cursor: pointer;
  font-size: 12px;
  font-weight: 800;
}
.plan-inline-snapshot button:hover {
  color: var(--plan-link-hover);
  text-decoration: underline;
  text-underline-offset: 3px;
}
.plan-panel__goal-action {
  white-space: nowrap;
}
.plan-overview__open {
  position: absolute;
  top: 10px;
  right: 9px;
  display: inline-flex;
  width: 24px;
  height: 24px;
  align-items: center;
  justify-content: center;
  padding: 0;
  border: 1px solid transparent;
  border-radius: 5px;
  color: var(--plan-muted);
  background: transparent;
  cursor: pointer;
}
.plan-overview__open:hover,
.plan-overview__open:focus-visible {
  border-color: var(--plan-border-strong);
  color: var(--plan-link);
  background: var(--plan-surface);
}
.plan-overview__open svg {
  display: block;
}
.plan-inline-snapshot {
  display: grid;
  gap: 7px;
  margin-top: 12px;
  padding: 10px 12px;
  border: 1px solid var(--plan-border);
  border-radius: 8px;
  background: var(--plan-surface);
}
.plan-inline-snapshot > div {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.plan-inline-snapshot code {
  min-width: 0;
  overflow: hidden;
  color: var(--plan-text);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.plan-inline-snapshot p {
  display: -webkit-box;
  margin: 0;
  overflow: hidden;
  color: var(--plan-muted);
  font-size: 12px;
  line-height: 19px;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 1;
}
.plan-block__head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 10px;
  margin: 0 0 8px;
}
.plan-block h4 {
  margin: 0;
  font-size: 12px;
  color: var(--plan-muted);
  text-transform: uppercase;
  letter-spacing: 0.03em;
}
.plan-block__head > span {
  color: var(--plan-muted);
  font-size: 11px;
}
.plan-files {
  display: grid;
  gap: 8px;
  margin: 0;
  padding: 0;
  list-style: none;
}
.plan-files li {
  display: grid;
  gap: 7px;
  padding: 10px 12px;
  border: 1px solid var(--plan-border);
  border-radius: 7px;
  background: var(--plan-surface-soft);
}
.plan-file-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  min-width: 0;
}
.plan-files code {
  min-width: 0;
  overflow: hidden;
  color: var(--plan-text);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.plan-files p {
  display: -webkit-box;
  margin: 0;
  overflow: hidden;
  color: var(--plan-muted);
  font-size: 12px;
  line-height: 19px;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 1;
}
.plan-file-meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}
.plan-file-meta details {
  min-width: 0;
}
.plan-file-meta summary {
  color: var(--plan-link);
  cursor: pointer;
  font-size: 12px;
  font-weight: 700;
  list-style: none;
}
.plan-file-meta summary::-webkit-details-marker {
  display: none;
}
.plan-file-meta summary::after {
  display: inline-block;
  width: 6px;
  height: 6px;
  margin-left: 6px;
  border-right: 1.5px solid currentColor;
  border-bottom: 1.5px solid currentColor;
  transform: translateY(-1px) rotate(45deg);
  transition: transform .16s ease;
  content: "";
}
.plan-file-meta details[open] summary::after {
  transform: translateY(2px) rotate(225deg);
}
.plan-file-meta details > div {
  display: grid;
  gap: 5px;
  margin-top: 8px;
  padding: 9px 10px;
  border: 1px solid var(--plan-border);
  border-radius: 7px;
  background: var(--plan-surface);
}
.plan-file-meta strong {
  color: var(--plan-text);
  font-size: 11px;
}
.plan-file-meta details p {
  display: block;
  color: var(--plan-muted);
  -webkit-line-clamp: unset;
}
.plan-risk > p {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  margin: 0 0 8px;
  line-height: 1.6;
  color: var(--plan-text);
}
.plan-risk__adjust {
  align-items: center !important;
  padding: 8px 10px;
  border-radius: 6px;
  background: var(--theme-warning-surface);
  color: var(--theme-warning-text) !important;
  font-size: 12px;
}
.plan-risk__initial {
  align-items: center !important;
  gap: 6px;
  color: var(--plan-muted) !important;
  font-size: 12px;
}
.plan-risk__initial svg {
  flex: none;
  color: var(--theme-status-warning);
}
.plan-history__hint {
  margin: 0 0 8px;
  color: var(--theme-warning-text);
  font-size: 12px;
}
.plan-history__list {
  display: grid;
  gap: 6px;
  margin: 0;
  padding: 0;
  list-style: none;
}
.plan-history__list li {
  display: grid;
  gap: 3px;
  padding: 8px 10px;
  border: 1px solid var(--plan-border);
  border-radius: 6px;
  background: var(--plan-surface-soft);
}
.plan-history__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.plan-history__link {
  font-size: 12px;
  color: var(--plan-link);
  text-decoration: none;
}
.plan-history__list strong {
  font-size: 13px;
  color: var(--plan-text);
}
.plan-history__list small {
  color: var(--plan-muted);
  line-height: 1.5;
}
.plan-notes {
  display: grid;
  gap: 5px;
  margin: 0;
  padding: 0 0 0 18px;
  color: var(--plan-text);
  line-height: 1.6;
  font-size: 13px;
}
.plan-panel__meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin: 14px 0 0;
  color: var(--plan-muted);
  font-size: 11px;
}
.plan-panel__meta i {
  width: 3px;
  height: 3px;
  border-radius: 50%;
  background: var(--plan-border-strong);
}
.plan-actions {
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px dashed var(--plan-border);
  display: grid;
  gap: 10px;
}
.plan-actions--readonly p,
.plan-actions--result p {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  margin: 0;
  color: var(--plan-text);
  font-size: 13px;
  line-height: 1.6;
}
.plan-actions--result p {
  color: var(--theme-status-success);
}
.plan-action-field {
  display: grid;
  gap: 4px;
}
.plan-action-field > span {
  color: var(--plan-muted);
  font-size: 12px;
  font-weight: 600;
}
.plan-action-field b {
  color: var(--theme-status-danger);
}
.plan-action-field textarea {
  min-height: 52px;
}
.plan-action-field--select .task-filter-select {
  max-width: 200px;
}
.plan-action-field small {
  color: var(--plan-muted);
  font-size: 11px;
}
.plan-actions__buttons {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}
.plan-action-mode {
  margin-top: 12px;
  padding: 12px;
  border: 1px solid var(--plan-border);
  border-radius: 8px;
  background: var(--plan-surface-soft);
  display: grid;
  gap: 10px;
}
.plan-steps li > span small:last-child {
  margin-top: 2px;
}
.plan-overdue {
  margin: 0 0 4px;
}
.plan-footer > span {
  display: inline-flex;
  align-items: flex-start;
  gap: 8px;
  min-width: 0;
  padding: 8px 10px;
  border: 1px solid var(--theme-warning-border);
  border-radius: 7px;
  color: var(--theme-warning-text);
  background: var(--theme-warning-surface);
  line-height: 18px;
}
.plan-footer > span svg {
  flex: none;
  margin-top: 1px;
  color: var(--theme-status-warning);
}
.plan-assign {
  --assign-bg: var(--plan-surface-soft);
  --assign-border: var(--plan-border);
  --assign-text: var(--plan-text);
  --assign-icon: var(--plan-link);
  --assign-hint: var(--theme-warning-text);
  margin-top: 14px;
  padding: 11px 13px;
  border: 1px solid var(--assign-border);
  border-radius: 8px;
  background: var(--assign-bg);
  display: grid;
  gap: 8px;
}
.plan-assign__line {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
}
.plan-assign__owner {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--assign-text);
  font-size: 12px;
  font-weight: 600;
}
.plan-assign__owner svg {
  color: var(--assign-icon);
}
.plan-assign__hint {
  margin: 0;
  color: var(--assign-hint);
  font-size: 12px;
  line-height: 1.6;
}
.plan-assign__form {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}
.plan-assign__form .plan-assign__select {
  flex: 1 1 220px;
  min-width: 0;
}
.plan-assign__error {
  flex-basis: 100%;
  margin: 0;
}

.plan-actions--result p.plan-result__meta {
  color: #5f7185;
}
.plan-result__meta {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 6px;
  margin: 0;
  font-size: 12px;
  line-height: 1.6;
}
.plan-result__meta i {
  width: 3px;
  height: 3px;
  border-radius: 50%;
  background: var(--plan-border-strong);
}
.plan-result__adjust {
  color: var(--theme-warning-text) !important;
}
.plan-result__adjust svg {
  flex: none;
  align-self: center;
}
.plan-detail-modal {
  position: fixed;
  inset: 0;
  z-index: 80;
  display: grid;
  place-items: center;
  padding: 24px;
  background: var(--theme-overlay);
  backdrop-filter: blur(8px);
}
.plan-detail-modal__panel {
  display: grid;
  grid-template-rows: auto auto minmax(0, 1fr);
  width: min(980px, 100%);
  max-height: min(760px, calc(100dvh - 48px));
  overflow: hidden;
  border: 1px solid var(--plan-border);
  border-radius: 12px;
  background: var(--plan-surface);
  box-shadow: var(--plan-shadow);
}
.plan-detail-modal__header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 14px;
  padding: 20px 22px 16px;
  border-bottom: 1px solid var(--plan-border);
  background: var(--plan-header);
}
.plan-panel.plan-panel .plan-detail-modal__header h3 {
  margin: 4px 0 0;
  color: var(--plan-heading);
  font-size: 18px;
  line-height: 25px;
}
.plan-detail-modal__goal {
  max-width: 78ch;
  margin: 6px 0 0;
  color: var(--plan-muted);
  font-size: 12px;
  line-height: 19px;
}
.plan-detail-modal__close {
  display: grid;
  width: 38px;
  height: 38px;
  flex: none;
  place-items: center;
  border: 1px solid var(--plan-border);
  border-radius: 8px;
  color: var(--plan-muted);
  background: var(--plan-surface-raised);
  cursor: pointer;
}
.plan-detail-modal__close:hover {
  border-color: var(--plan-border-strong);
  color: var(--plan-heading);
  background: var(--plan-surface-soft);
}
.plan-detail-tabs {
  display: flex;
  gap: 6px;
  padding: 10px 14px;
  border-bottom: 1px solid var(--plan-border);
  background: var(--plan-surface);
}
.plan-detail-tabs button {
  min-height: 32px;
  padding: 0 12px;
  border: 1px solid transparent;
  border-radius: 7px;
  color: var(--plan-muted);
  background: transparent;
  cursor: pointer;
  font-size: 12px;
  font-weight: 800;
}
.plan-detail-tabs button:hover {
  color: var(--plan-heading);
  background: var(--plan-surface-soft);
}
.plan-detail-tabs button.is-active {
  border-color: var(--plan-tab-active-border);
  color: var(--plan-heading);
  background: var(--plan-tab-active);
}
.plan-detail-modal__body {
  min-height: 0;
  overflow: auto;
  padding: 18px 22px 22px;
}
.plan-detail-pane {
  display: grid;
  gap: 14px;
}

.plan-panel .plan-overview,
.plan-panel .plan-inline-snapshot,
.plan-panel .plan-files li,
.plan-panel .plan-history__list li,
.plan-panel .plan-action-mode {
  border-color: var(--plan-border);
  background: var(--plan-surface-soft);
}

.plan-panel .plan-overview__item {
  border-color: var(--plan-border);
}

.plan-panel .plan-inline-snapshot,
.plan-panel .plan-detail-tabs,
.plan-panel .plan-detail-modal__body {
  background: var(--plan-surface);
}

.plan-panel .plan-detail-modal__panel {
  border-color: var(--plan-border);
  background: var(--plan-surface);
  box-shadow: var(--plan-shadow);
}

.plan-panel .plan-detail-modal__header {
  border-color: var(--plan-border);
  background: var(--plan-header);
}

.plan-panel .plan-detail-tabs {
  border-color: var(--plan-border);
}

.plan-panel .plan-overview__item strong,
.plan-panel .plan-inline-snapshot code,
.plan-panel .plan-files code,
.plan-panel .plan-history__list strong,
.plan-panel .plan-detail-modal__header h3 {
  color: var(--plan-heading);
}

.plan-panel .plan-overview__item > span,
.plan-panel .plan-overview__item small,
.plan-panel .plan-inline-snapshot p,
.plan-panel .plan-files p,
.plan-panel .plan-history__list small,
.plan-panel .plan-block h4,
.plan-panel .plan-block__head > span,
.plan-panel .plan-action-field > span,
.plan-panel .plan-action-field small,
.plan-panel .plan-detail-tabs button {
  color: var(--plan-muted);
}

.plan-panel .plan-inline-snapshot button,
.plan-panel .plan-history__link {
  color: var(--plan-link);
}

.plan-panel .plan-inline-snapshot button:hover,
.plan-panel .plan-history__link:hover {
  color: var(--plan-link-hover);
}

.plan-panel .plan-overview__open {
  color: var(--plan-muted);
}

.plan-panel .plan-overview__open:hover,
.plan-panel .plan-overview__open:focus-visible {
  color: var(--plan-link);
}

.plan-panel .plan-detail-tabs button:hover {
  color: var(--plan-heading);
  background: var(--plan-surface-soft);
}

.plan-panel .plan-detail-tabs button.is-active {
  border-color: var(--plan-tab-active-border);
  color: var(--plan-heading);
  background: var(--plan-tab-active);
}

.plan-panel .plan-detail-modal__close {
  border-color: var(--plan-border);
  color: var(--plan-muted);
  background: var(--plan-surface-raised);
}

.plan-panel .plan-detail-modal__close:hover {
  border-color: var(--plan-border-strong);
  color: var(--plan-heading);
  background: var(--plan-surface-soft);
}

@media (max-width: 820px) {
  .plan-overview {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .plan-overview__item:nth-child(2n) {
    border-right: 0;
  }
  .plan-overview__item:nth-last-child(-n + 2) {
    border-top: 1px solid var(--plan-border);
  }
  .plan-detail-modal {
    padding: 12px;
  }
}

@media (max-width: 560px) {
  .plan-panel__header {
    grid-template-columns: minmax(0, 1fr);
    gap: 10px;
  }
  .plan-panel__tags {
    justify-content: flex-start;
  }
  .plan-panel__header h3 {
    max-width: none;
  }
  .plan-panel__goal-row {
    align-items: flex-start;
    flex-direction: column;
    gap: 3px;
  }
  .plan-overview {
    grid-template-columns: minmax(0, 1fr);
  }
  .plan-overview__item,
  .plan-overview__item:nth-child(2n) {
    border-right: 0;
    border-bottom: 1px solid var(--plan-border);
  }
  .plan-overview__item:last-child {
    border-bottom: 0;
  }
  .plan-detail-tabs {
    overflow-x: auto;
  }
  .plan-detail-modal__header {
    padding: 16px;
  }
  .plan-detail-modal__body {
    padding: 14px 16px 18px;
  }
}
</style>
