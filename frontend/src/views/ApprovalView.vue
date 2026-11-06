<script setup lang="ts">
import { AlertTriangle, Check, ClipboardCheck, RotateCcw, X } from '@lucide/vue'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useAuthStore } from '../stores/auth'
import { useWorkspaceStore } from '../stores/workspace'
import { getDataSourceMode } from '../services/dataSourceMode'
import type { ModificationPlan, PlanChangeType, RiskThreshold, Task } from '../types/domain'
import StatusTag from '../components/StatusTag.vue'
import EmptyPanel from '../components/EmptyPanel.vue'
import TaskFilterSelect from '../components/TaskFilterSelect.vue'
import type { TaskFilterOption } from '../components/TaskFilterSelect.vue'
import { connectRealtime, subscribeRealtime } from '../services/realtime'

const store = useWorkspaceStore()
const auth = useAuthStore()
const comment = ref('')
const notice = ref<{ tone: 'success' | 'warning'; title: string; detail: string } | null>(null)
const focusedTaskId = ref(store.selectedTaskId)
const submitting = ref(false)
const riskDraft = ref<RiskThreshold | null>(null)
const riskAdjustReason = ref('')
const riskOrder: Record<Task['risk'], number> = { high: 0, medium: 1, low: 2, pending: 3 }
const OVERDUE_MS = 24 * 3600 * 1000
const isReal = computed(() => getDataSourceMode() === 'real')
function taskOverdue(task: Task): boolean {
  const plan = store.latestPlanOf(task.id)
  if (!plan || plan.status !== 'WAITING_APPROVAL') return false
  const created = new Date(plan.created_at).getTime()
  return Number.isFinite(created) && Date.now() - created > OVERDUE_MS
}
// 真实模式队列以 /api/approvals/pending 为准（approver 只见指派给自己的，admin 全量）；
// mock 模式沿用本地待审批演示任务。
const approvalTasks = computed(() =>
  [...(isReal.value ? store.approvalQueue : store.pendingTasks)].sort((left, right) => {
    const overdueDelta = Number(taskOverdue(left)) - Number(taskOverdue(right))
    if (overdueDelta !== 0) return -overdueDelta
    const riskDelta = riskOrder[left.risk] - riskOrder[right.risk]
    if (riskDelta !== 0) return riskDelta
    return right.updatedAt.localeCompare(left.updatedAt)
  }),
)
const focusedOverdue = computed(() => Boolean(focusedTask.value && taskOverdue(focusedTask.value)))
const emptyState = computed(() => {
  if (!isReal.value) {
    return {
      title: '暂无待审批任务',
      description: '当前没有进入待审批的计划。任务生成修改计划并提交审批后，会出现在这里。',
    }
  }
  if (auth.hasRole('lead')) {
    return {
      title: '暂无指派给你的待审批任务',
      description: '研发负责人不直接审批：可进入任务详情查看计划并指派或改派审批人，指派后由对应审批人处理。',
    }
  }
  return {
    title: '暂无待你处理的审批任务',
    description: '指派给你审批的计划提交后，会出现在这里；管理员可查看并处理全部待审批任务。',
  }
})
function planVersionOf(task: Task): number | null {
  return store.latestPlanOf(task.id)?.version ?? null
}
const selectedTasks = computed(() =>
  store.selectedTaskIds
    .map((taskId) => store.taskById(taskId))
    .filter((task): task is Task => task !== undefined && task.status === 'WAITING_APPROVAL'),
)
const selectedCount = computed(() => selectedTasks.value.length)
const ignoredCount = computed(() => store.selectedTaskIds.length - selectedCount.value)
const hasExplicitSelection = computed(() => store.selectedTaskIds.length > 0)
// 审批队列为空即整体空态：已处理完的任务不再作为聚焦对象残留展示
const focusedTask = computed((): Task | null =>
  approvalTasks.value.length ? (store.taskById(focusedTaskId.value) ?? approvalTasks.value[0] ?? null) : null,
)
const artifact = computed(() => (focusedTask.value ? store.artifactFor(focusedTask.value.id) : store.artifactFor('')))
const focusedPlan = computed((): ModificationPlan | null => (focusedTask.value ? store.latestPlanOf(focusedTask.value.id) : null))
const actionTargets = computed(() => (hasExplicitSelection.value ? selectedTasks.value : focusedTask.value ? [focusedTask.value] : []))
const highRiskTargeted = computed(() => actionTargets.value.some((task) => task.risk === 'high'))
// 摘要计数以 owner 范围队列为准（real=approvalQueue / mock=pendingTasks）
const pendingHighRisk = computed(() => approvalTasks.value.filter((task) => task.risk === 'high').length)
const pendingAuto = computed(() => approvalTasks.value.filter((task) => task.risk !== 'high').length)
const lowRiskCount = computed(() => approvalTasks.value.filter((task) => task.risk === 'low').length)
const canManageApproval = computed(() => auth.hasPermission('approval:manage'))
const canAdjustRisk = computed(
  () =>
    isReal.value &&
    canManageApproval.value &&
    !hasExplicitSelection.value &&
    focusedPlan.value?.status === 'WAITING_APPROVAL' &&
    focusedTask.value?.status === 'WAITING_APPROVAL',
)
const riskChanged = computed(() => Boolean(focusedPlan.value && riskDraft.value && riskDraft.value !== focusedPlan.value.risk_level))
const planKicker = computed(() => (focusedPlan.value ? `修改计划 v${focusedPlan.value.version}` : artifact.value.planVersion))
const planHeading = computed(() => focusedPlan.value?.goal || artifact.value.planTitle)
const systemVerdict = computed(() => (highRiskTargeted.value ? '系统建议人工审批' : '系统建议自动通过'))
const batchTitle = computed(() => {
  if (!hasExplicitSelection.value) return systemVerdict.value
  if (!selectedCount.value) return '所选任务不可审批'
  return `批量审批 ${selectedCount.value} 项`
})
const selectionNote = computed(() => {
  if (!hasExplicitSelection.value) return ''
  if (!selectedCount.value) return `${store.selectedTaskIds.length} 项已选，但都不是待审批任务`
  if (ignoredCount.value) return `${ignoredCount.value} 项不可审批已忽略`
  return ''
})
const planProviderText = computed(() => (focusedPlan.value?.provider === 'deepseek' ? 'DeepSeek 生成' : '规则基座生成'))

const riskLabelMap: Record<RiskThreshold, string> = { high: '高风险', medium: '中风险', low: '低风险' }
const riskOptions: TaskFilterOption[] = (['high', 'medium', 'low'] as RiskThreshold[]).map((level) => ({
  value: level,
  label: riskLabelMap[level],
}))
const changeTypeMap: Record<PlanChangeType, { label: string; tone: string }> = {
  modify: { label: '修改', tone: 'warning' },
  add: { label: '新增', tone: 'success' },
  delete: { label: '删除', tone: 'danger' },
  unknown: { label: '待定', tone: 'info' },
}

const planLoadsInFlight = new Set<string>()
let unsubscribeRealtime: (() => void) | undefined

watch(
  approvalTasks,
  (tasks) => {
    if (!tasks.some((task) => task.id === focusedTaskId.value)) {
      focusedTaskId.value = tasks[0]?.id ?? ''
    }
    if (isReal.value) {
      // 队列响应已内嵌 plans，只对未带计划的任务补充加载
      const todo = tasks.filter((task) => !(store.plansByTask[task.id]?.length) && !planLoadsInFlight.has(task.id)).map((task) => task.id)
      if (todo.length) {
        todo.forEach((id) => planLoadsInFlight.add(id))
        void Promise.allSettled(todo.map((id) => store.loadPlans(id))).finally(() => {
          todo.forEach((id) => planLoadsInFlight.delete(id))
        })
      }
    }
  },
  { immediate: true },
)

watch(
  focusedPlan,
  (plan) => {
    riskDraft.value = plan?.risk_level ?? null
    riskAdjustReason.value = ''
  },
  { immediate: true },
)

onMounted(() => {
  if (isReal.value) {
    void store.loadApprovalQueue().catch(() => {
      notice.value = {
        tone: 'warning',
        title: '待审批队列加载失败',
        detail: '网络异常，当前展示的是上次加载的审批队列。',
      }
    })
  }
  connectRealtime()
  unsubscribeRealtime = subscribeRealtime((event) => {
    if (event.resource_type === 'task' || event.type === 'task.updated' || event.type === 'notification.created') {
      reloadQueueAfterAction()
    }
  })
})

onBeforeUnmount(() => {
  unsubscribeRealtime?.()
  unsubscribeRealtime = undefined
})

function displayTime(value: string) {
  return value.replace('T', ' ').slice(0, 16)
}

function requestError(unknown: unknown) {
  if (unknown instanceof Error && unknown.message) return unknown.message
  return '操作失败，请稍后重试。'
}

function permissionNotice(action: string) {
  notice.value = {
    tone: 'warning',
    title: '您暂无权限执行审批操作',
    detail: `${action}仅由审批人处理；您可以查看计划、风险和审批历史。`,
  }
}

/** 真实模式操作后重载 owner 范围队列：同步角标并移除已处理项；mock 模式无队列，空转。 */
function reloadQueueAfterAction() {
  if (!isReal.value) return
  void store.loadApprovalQueue().catch(() => {
    notice.value = {
      tone: 'warning',
      title: '审批队列刷新失败',
      detail: '操作已提交，但队列未能同步，请稍后刷新页面查看最新状态。',
    }
  })
}

async function approveWithRiskAdjust(task: Task, plan: ModificationPlan) {
  const payload: { comment?: string; risk_level?: RiskThreshold; risk_adjust_reason?: string } = {}
  if (comment.value.trim()) payload.comment = comment.value.trim()
  if (canAdjustRisk.value && riskDraft.value && riskDraft.value !== plan.risk_level) {
    if (!riskAdjustReason.value.trim()) throw new Error('调整风险等级时必须填写原因')
    payload.risk_level = riskDraft.value
    payload.risk_adjust_reason = riskAdjustReason.value.trim()
  }
  await store.approvePlan(task.id, plan.id, payload)
}

async function decide(action: 'APPROVED' | 'FAILED' | 'PLAN_GENERATED') {
  if (!canManageApproval.value) {
    permissionNotice('通过、拒绝和要求重写')
    return
  }
  if (submitting.value) return
  if (hasExplicitSelection.value && !selectedCount.value) {
    notice.value = {
      tone: 'warning',
      title: '所选任务不可审批',
      detail: '已选任务里没有待审批项，请重新选择高风险或等待审批的任务。',
    }
    return
  }
  if (action !== 'APPROVED' && !comment.value.trim()) {
    notice.value = {
      tone: 'warning',
      title: action === 'FAILED' ? '拒绝计划需要填写原因' : '要求重写需要填写原因',
      detail: '请补充驳回理由或需要调整的内容，该原因会随操作写入计划记录。',
    }
    return
  }
  if (!isReal.value) {
    const targets = [...actionTargets.value]
    targets.forEach((task) => store.setTaskStatus(task.id, action))
    store.clearTaskSelection()
    comment.value = ''
    notice.value = {
      tone: 'success',
      title:
        targets.length > 1
          ? `${targets.length} 项任务已完成处理`
          : action === 'APPROVED'
            ? '计划已通过，可生成 Diff'
            : action === 'FAILED'
              ? '计划已拒绝'
              : '已要求重新生成计划',
      detail: `${targets.map((task) => task.id).join(' · ')}${ignoredCount.value ? ` · ${ignoredCount.value} 项不可审批已忽略` : ''}`,
    }
    return
  }
  const targets = [...actionTargets.value]
  const done: string[] = []
  const skipped: string[] = []
  const errors: string[] = []
  submitting.value = true
  try {
    await Promise.all(
      targets.map(async (task) => {
        const plan = store.latestPlanOf(task.id)
        if (!plan || plan.status !== 'WAITING_APPROVAL') {
          skipped.push(task.id)
          return
        }
        try {
          if (action === 'APPROVED') {
            await approveWithRiskAdjust(task, plan)
          } else if (action === 'FAILED') {
            await store.rejectPlan(task.id, plan.id, comment.value.trim())
          } else {
            await store.rewritePlan(task.id, plan.id, comment.value.trim())
          }
          done.push(task.id)
        } catch (error) {
          errors.push(`${task.id}：${requestError(error)}`)
        }
      }),
    )
  } finally {
    submitting.value = false
  }
  store.clearTaskSelection()
  comment.value = ''
  riskAdjustReason.value = ''
  if (errors.length === 0) {
    const skippedNote = skipped.length ? ` · ${skipped.length} 项无待审批计划已跳过` : ''
    notice.value = {
      tone: 'success',
      title:
        action === 'APPROVED'
          ? done.length > 1
            ? `${done.length} 项计划已通过`
            : '计划已通过，任务进入 Diff 生成阶段'
          : action === 'FAILED'
            ? done.length > 1
              ? `${done.length} 项计划已拒绝`
              : '计划已拒绝，任务可重试重建'
            : done.length > 1
              ? `${done.length} 项已要求重写`
              : '已要求重写，新版本计划已生成',
      detail: `${done.join(' · ')}${skippedNote}${ignoredCount.value ? ` · ${ignoredCount.value} 项不可审批已忽略` : ''}`,
    }
  } else {
    notice.value = {
      tone: 'warning',
      title: '部分审批操作未完成',
      detail: `${errors.join('；')}${done.length ? `；已完成：${done.join(' · ')}` : ''}`,
    }
  }
  reloadQueueAfterAction()
}

function toggleSelection(taskId: string) {
  if (!canManageApproval.value) {
    permissionNotice('批量选择')
    return
  }
  store.toggleTaskSelection(taskId)
}

function approveLowRiskTasks() {
  if (!canManageApproval.value) {
    permissionNotice('一键通过低风险')
    return
  }
  if (submitting.value) return
  const lowRiskTargets = approvalTasks.value.filter((task) => task.risk === 'low')
  if (!lowRiskTargets.length) {
    notice.value = {
      tone: 'warning',
      title: '当前没有可自动通过的低风险任务',
      detail: '先处理高风险任务，或稍后再看是否有低风险任务进入队列。',
    }
    return
  }
  if (!isReal.value) {
    lowRiskTargets.forEach((task) => store.setTaskStatus(task.id, 'APPROVED'))
    store.clearTaskSelection()
    notice.value = {
      tone: 'success',
      title: `${lowRiskTargets.length} 项低风险任务已自动通过`,
      detail: lowRiskTargets.map((task) => task.id).join(' · '),
    }
    return
  }
  const done: string[] = []
  const skipped: string[] = []
  const errors: string[] = []
  submitting.value = true
  void Promise.all(
    lowRiskTargets.map(async (task) => {
      const plan = store.latestPlanOf(task.id)
      if (!plan || plan.status !== 'WAITING_APPROVAL') {
        skipped.push(task.id)
        return
      }
      try {
        await store.approvePlan(task.id, plan.id)
        done.push(task.id)
      } catch (error) {
        errors.push(`${task.id}：${requestError(error)}`)
      }
    }),
  ).finally(() => {
    submitting.value = false
    if (errors.length === 0) {
      notice.value = {
        tone: 'success',
        title: `${done.length} 项低风险任务已自动通过`,
        detail: `${done.join(' · ')}${skipped.length ? ` · ${skipped.length} 项无待审批计划已跳过` : ''}`,
      }
    } else {
      notice.value = {
        tone: 'warning',
        title: '部分低风险任务未通过',
        detail: `${errors.join('；')}${done.length ? `；已完成：${done.join(' · ')}` : ''}`,
      }
    }
    reloadQueueAfterAction()
  })
}

function onRiskDraftChange(value: string) {
  riskDraft.value = value as RiskThreshold
}
</script>

<template>
  <section class="page-content approval-page">
    <div class="approval-summary">
      <span><ClipboardCheck :size="19" /></span>
      <div>
        <strong>{{ approvalTasks.length }} 项待处理 · {{ pendingHighRisk }} 项高风险 · {{ pendingAuto }} 项可自动流转</strong>
        <p>{{ systemVerdict }}。低风险可自动流转，高风险只保留人审项。</p>
      </div>
      <div class="approval-summary__actions">
        <button v-if="canManageApproval" class="button button--ghost" :class="{ 'is-hidden': !lowRiskCount }" :disabled="!lowRiskCount || submitting" @click="approveLowRiskTasks">一键通过低风险 {{ lowRiskCount }} 项</button>
        <button v-if="canManageApproval" class="button button--secondary" :class="{ 'is-hidden': !hasExplicitSelection }" :disabled="!hasExplicitSelection || submitting" @click="store.clearTaskSelection">清空选择</button>
      </div>
    </div>
    <div v-if="notice" class="page-notice" :class="`page-notice--${notice.tone}`">
      <span class="page-notice__icon">
        <Check v-if="notice.tone === 'success'" :size="16" />
        <AlertTriangle v-else :size="16" />
      </span>
      <div class="page-notice__body">
        <strong>{{ notice.title }}</strong>
        <p>{{ notice.detail }}</p>
      </div>
      <button class="icon-button" aria-label="关闭提示" @click="notice = null"><X :size="18" /></button>
    </div>
    <div v-if="focusedTask" class="approval-grid">
      <aside class="panel approval-list">
        <header class="panel__header"><div><h2>待处理任务</h2><p>默认优先显示需要人工介入的任务</p></div></header>
        <div
          v-for="task in approvalTasks"
          :key="task.id"
          class="approval-list__item"
          :class="{ 'is-active': task.id === focusedTask.id, 'is-auto': task.risk !== 'high', 'is-selected': store.selectedTaskIds.includes(task.id) }"
          role="button"
          tabindex="0"
          @click="focusedTaskId = task.id"
          @keydown.enter.prevent="focusedTaskId = task.id"
          @keydown.space.prevent="focusedTaskId = task.id"
        >
          <span class="approval-list__lead">
            <button v-if="canManageApproval" class="task-select-control approval-select" :class="{ 'is-checked': store.selectedTaskIds.includes(task.id) }" type="button" :aria-pressed="store.selectedTaskIds.includes(task.id)" @click.stop="toggleSelection(task.id)">
              <span class="task-select-control__dot" aria-hidden="true" />
            </button>
            <span>
              <strong>{{ task.title }}</strong>
              <small>{{ task.id }} · {{ task.repository }}<template v-if="planVersionOf(task) !== null"> · 计划 v{{ planVersionOf(task) }}</template></small>
            </span>
          </span>
          <span class="approval-list__tags">
            <span v-if="taskOverdue(task)" class="approval-overdue-chip">逾期 &gt;24h</span>
            <StatusTag :risk="task.risk" />
          </span>
        </div>
      </aside>
      <main class="panel approval-main">
        <header class="panel__header">
          <div>
            <p class="section-kicker">{{ focusedTask.id }} · {{ planKicker }}</p>
            <h2>{{ planHeading }}</h2>
          </div>
          <StatusTag :status="focusedTask.status" />
        </header>

        <div v-if="focusedOverdue" class="page-notice page-notice--warning approval-overdue-note">
          <span class="page-notice__icon"><AlertTriangle :size="16" /></span>
          <div class="page-notice__body">
            <strong>审批等待已超过 24 小时</strong>
            <p>该计划长期未处理，建议尽快给出结论；逾期任务在工作台也有催办提示。</p>
          </div>
        </div>

        <template v-if="focusedPlan">
          <section class="approval-section">
            <h3>
              <span>{{ batchTitle }}</span>
              <span class="approval-selection-count" :class="{ 'is-hidden': !selectedCount }">已选 {{ selectedCount }} 项</span>
            </h3>
            <p>{{ focusedPlan.status === 'WAITING_APPROVAL' ? '计划已提交审批，请核对文件范围、实施步骤与风险判定后给出结论。' : '计划已不在审批流程中，仅作结果展示。' }}</p>
            <p class="field-error approval-selection-note" :class="{ 'is-hidden': !selectionNote }">{{ selectionNote || '当前选择可直接审批' }}</p>
            <div class="approval-quick">
              <span><b>风险等级</b>{{ riskLabelMap[focusedPlan.risk_level] }}</span>
              <span><b>涉及文件</b>{{ focusedPlan.file_scope.length }} 个</span>
              <span><b>实施步骤</b>{{ focusedPlan.steps.length }} 步</span>
            </div>
          </section>
          <section class="approval-section">
            <h3>文件范围</h3>
            <ul class="approval-plan__files">
              <li v-for="file in focusedPlan.file_scope" :key="file.file_path">
                <span class="status-tag" :class="changeTypeMap[file.change_type].tone">{{ changeTypeMap[file.change_type].label }}</span>
                <code>{{ file.file_path }}</code>
                <small>{{ file.reason }}<template v-if="file.risk_hint">（{{ file.risk_hint }}）</template></small>
              </li>
            </ul>
          </section>
          <section class="approval-section">
            <h3>实施步骤</h3>
            <ol class="approval-plan__steps">
              <li v-for="step in focusedPlan.steps" :key="step.order">
                <b>{{ step.order }}</b>
                <span>
                  <strong>{{ step.title }}</strong>
                  <small>{{ step.description }}</small>
                  <small v-if="step.target_files.length" class="mono">涉及：{{ step.target_files.join('、') }}</small>
                </span>
              </li>
            </ol>
          </section>
          <section class="approval-section">
            <h3>风险判定</h3>
            <p class="approval-plan__risk">
              <span class="status-tag" :class="`risk-${focusedPlan.risk_level}`">{{ riskLabelMap[focusedPlan.risk_level] }}</span>
              <span>{{ focusedPlan.risk_reason }}</span>
            </p>
            <p v-if="focusedPlan.risk_adjust_reason" class="approval-plan__adjust">
              <AlertTriangle :size="14" />
              <span v-if="focusedPlan.risk_adjust_from">
                审批人已调整风险等级：{{ riskLabelMap[focusedPlan.risk_adjust_from] }} → {{ riskLabelMap[focusedPlan.risk_level] }}，原因：{{ focusedPlan.risk_adjust_reason }}
              </span>
              <span v-else>审批人已调整风险等级并补充原因：{{ focusedPlan.risk_adjust_reason }}</span>
            </p>
          </section>
          <section class="approval-section">
            <h3>测试建议</h3>
            <ul class="approval-plan__notes">
              <li v-for="(item, index) in focusedPlan.test_suggestions" :key="`t-${index}`">{{ item }}</li>
            </ul>
          </section>
          <section class="approval-section">
            <h3>假设与说明</h3>
            <ul class="approval-plan__notes">
              <li v-for="(item, index) in focusedPlan.assumptions" :key="`a-${index}`">{{ item }}</li>
            </ul>
          </section>
          <section class="approval-section">
            <h3>回滚计划</h3>
            <ul class="approval-plan__notes">
              <li v-for="(item, index) in focusedPlan.rollback_plan" :key="`r-${index}`">{{ item }}</li>
            </ul>
          </section>
          <p class="approval-plan__meta">
            <span>{{ planProviderText }}</span><i />基于分析第 {{ focusedPlan.generated_from_analysis_revision }} 版<template v-if="focusedPlan.generated_from_context_ids.length"> · {{ focusedPlan.generated_from_context_ids.length }} 项代码上下文</template><i />由 {{ focusedPlan.created_by_name }} 于 {{ displayTime(focusedPlan.created_at) }} 提交
          </p>
          <section class="approval-section">
            <h3>审批历史</h3>
            <ol class="mini-timeline">
              <li><i />{{ focusedPlan.created_by_name }} 提交计划 v{{ focusedPlan.version }} <time>{{ displayTime(focusedPlan.created_at) }}</time></li>
              <li v-if="focusedPlan.status === 'APPROVED' && focusedPlan.approved_by_name" class="is-approved">
                <i />
                <span>{{ focusedPlan.approved_by_name }} 审批通过计划 v{{ focusedPlan.version }}<small v-if="focusedPlan.approval_comment">意见：{{ focusedPlan.approval_comment }}</small></span>
                <time>{{ displayTime(focusedPlan.approved_at ?? focusedPlan.created_at) }}</time>
              </li>
              <li v-else-if="focusedPlan.status === 'APPROVED'" class="is-approved"><i /><span>计划已通过</span><time>{{ displayTime(focusedPlan.approved_at ?? focusedPlan.created_at) }}</time></li>
              <li v-else-if="focusedPlan.status === 'REJECTED'"><i /><span>计划已拒绝，任务已置为失败，可重试重建</span><time>{{ displayTime(focusedPlan.created_at) }}</time></li>
              <li v-else-if="focusedPlan.status === 'SUPERSEDED'"><i /><span>旧版本计划已被新版本取代</span><time>{{ displayTime(focusedPlan.created_at) }}</time></li>
              <li v-else class="is-active"><i /><span>等待 {{ focusedTask.approver === '未指定' ? '指派审批人' : focusedTask.approver }} 审批</span><time>进行中</time></li>
            </ol>
          </section>
        </template>

        <template v-else-if="isReal">
          <section class="approval-section approval-plan__loading">
            <p>正在加载该任务的修改计划，请稍候…</p>
            <button class="button button--ghost" :disabled="submitting" @click="store.loadPlans(focusedTask.id).catch(() => { notice = { tone: 'warning', title: '修改计划加载失败', detail: '网络异常，请稍后重试。' } })">重新加载</button>
          </section>
        </template>

        <template v-else>
          <section class="approval-section">
            <h3>
              <span>{{ batchTitle }}</span>
              <span class="approval-selection-count" :class="{ 'is-hidden': !selectedCount }">已选 {{ selectedCount }} 项</span>
            </h3>
            <p>{{ artifact.analysisBody }}</p>
            <p class="field-error approval-selection-note" :class="{ 'is-hidden': !selectionNote }">{{ selectionNote || '当前选择可直接审批' }}</p>
            <div class="approval-quick">
              <span><b>影响模块</b>{{ artifact.analysisPoints[0].value }}</span>
              <span><b>变更范围</b>{{ artifact.analysisPoints[1].value }}</span>
              <span><b>风险提示</b>{{ artifact.analysisPoints[2].value }}</span>
            </div>
          </section>
          <section class="approval-section">
            <h3>文件范围</h3>
            <ul class="file-scope">
              <li v-for="file in artifact.diffFiles" :key="file.path">
                <code>{{ file.path }}</code>
                <span>{{ file.change }}</span>
              </li>
            </ul>
          </section>
          <section class="approval-section">
            <h3>审批历史</h3>
            <ol class="mini-timeline">
              <li><i />王琳提交计划 {{ artifact.planVersion }} <time>10:42</time></li>
              <li class="is-active"><i />等待 {{ focusedTask.approver }} 审批 <time>进行中</time></li>
            </ol>
          </section>
        </template>
      </main>
      <aside class="approval-action">
        <section class="risk-callout">
          <AlertTriangle :size="19" />
          <div>
            <strong>{{ highRiskTargeted ? '高风险变更' : '可自动流转' }}</strong>
            <p>{{ highRiskTargeted ? '所选任务中存在高风险项。请人工确认计划内容与风险判定后再通过。' : '当前所选任务风险较低，可直接通过，也可先要求重写补充说明。' }}</p>
          </div>
        </section>
        <div v-if="canAdjustRisk" class="approval-risk-adjust">
          <label class="approval-risk-adjust__select"><span>通过时风险等级</span>
            <TaskFilterSelect :model-value="riskDraft ?? ''" :options="riskOptions" ariaLabel="通过时风险等级" @update:model-value="onRiskDraftChange" />
          </label>
          <label v-if="riskChanged"><span>调整风险原因 <b>*</b></span><textarea v-model="riskAdjustReason" placeholder="调整风险等级时必须填写原因。" /><small>审批通过后，任务风险等级会同步为调整值。</small></label>
        </div>
        <label class="approval-comment"><span>审批意见<template v-if="isReal">（拒绝与要求重写必填）</template></span><textarea v-model="comment" :disabled="!canManageApproval || submitting" :placeholder="canManageApproval ? '填写审批结论、风险要求或需要补充的测试。' : '您当前可以查看审批内容，不能填写审批意见。'" /><small>{{ canManageApproval ? (isReal ? '高风险通过建议填写意见；拒绝与要求重写必须填写原因。' : '高风险、拒绝和要求重写时必须填写。') : '审批意见仅审批人可以填写。' }}</small></label>
        <div v-if="canManageApproval" class="approval-actions">
          <button class="button button--success" :disabled="submitting" @click="decide('APPROVED')"><Check :size="16" /> {{ submitting ? '处理中…' : (hasExplicitSelection ? '通过所选' : '通过') }}</button>
          <button class="button button--danger" :disabled="submitting" @click="decide('FAILED')"><X :size="16" /> {{ submitting ? '处理中…' : (hasExplicitSelection ? '拒绝所选' : '拒绝') }}</button>
          <button class="button button--secondary" :disabled="submitting" @click="decide('PLAN_GENERATED')"><RotateCcw :size="16" /> {{ submitting ? '处理中…' : (hasExplicitSelection ? '批量重写' : '要求重写') }}</button>
        </div>
        <div v-else class="page-notice page-notice--info">
          <span class="page-notice__icon"><ClipboardCheck :size="16" /></span>
          <div class="page-notice__body">
            <strong>您当前可查看审批内容</strong>
            <p>通过、拒绝、要求重写和补充审批意见仅由审批人执行。</p>
          </div>
        </div>
      </aside>
    </div>
    <EmptyPanel v-else :title="emptyState.title" :description="emptyState.description" />
  </section>
</template>

<style scoped>
.approval-page {
  --approval-plan-card-bg: #f8fbfe;
  --approval-plan-card-border: #e4eaf1;
  --approval-plan-card-shadow: inset 0 1px 0 rgba(255, 255, 255, .9);
  --approval-plan-code: #33475f;
  --approval-plan-body: #4f6073;
  --approval-plan-muted: #758496;
  --approval-plan-dot: #c3cedb;
  --approval-plan-step-bg: #eaf1ff;
  --approval-plan-step-text: #214bcf;
  --approval-plan-warning-bg: #fdf3e3;
  --approval-plan-warning-border: #f2d7a7;
  --approval-plan-warning-text: #8a5a12;
  --approval-risk-adjust-bg: #f8fbfe;
  --approval-risk-adjust-border: #e2e9f0;
  --approval-danger-text: #b83d35;
  --approval-overdue-bg: #fef3f2;
  --approval-overdue-border: #f2c1bc;
  --approval-overdue-text: #b42318;
  --approval-approved-dot: #15803d;
}

[data-theme="dark"] .approval-page {
  --approval-plan-card-bg: #121d2f;
  --approval-plan-card-border: #2a3b52;
  --approval-plan-card-shadow: inset 0 1px 0 rgba(255, 255, 255, .035), 0 8px 18px rgba(0, 0, 0, .12);
  --approval-plan-code: #dce8f6;
  --approval-plan-body: #b9c8da;
  --approval-plan-muted: #8fa0b5;
  --approval-plan-dot: #506177;
  --approval-plan-step-bg: #1a2d4a;
  --approval-plan-step-text: #b8d0ff;
  --approval-plan-warning-bg: rgba(214, 160, 58, .13);
  --approval-plan-warning-border: #57431e;
  --approval-plan-warning-text: #efcc82;
  --approval-risk-adjust-bg: #111c2d;
  --approval-risk-adjust-border: #2b3f58;
  --approval-danger-text: #ffaaa3;
  --approval-overdue-bg: rgba(239, 106, 97, .12);
  --approval-overdue-border: #713f3e;
  --approval-overdue-text: #ffb4ad;
  --approval-approved-dot: #3ebd73;
}

.approval-plan__files {
  display: grid;
  gap: 6px;
  margin: 0;
  padding: 0;
  list-style: none;
}
.approval-plan__files li {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 4px 8px;
  align-items: center;
  padding: 8px 10px;
  border: 1px solid var(--approval-plan-card-border);
  border-radius: 6px;
  background: var(--approval-plan-card-bg);
  box-shadow: var(--approval-plan-card-shadow);
}
.approval-plan__files code {
  font-size: 12px;
  color: var(--approval-plan-code);
  word-break: break-all;
}
.approval-plan__files small {
  grid-column: 1 / -1;
  color: var(--approval-plan-muted);
  line-height: 1.5;
}
.approval-plan__steps {
  display: grid;
  gap: 8px;
  margin: 0;
  padding: 0;
  list-style: none;
}
.approval-plan__steps li {
  display: grid;
  grid-template-columns: 26px minmax(0, 1fr);
  gap: 10px;
  padding: 10px 12px;
  border: 1px solid var(--approval-plan-card-border);
  border-radius: 8px;
  background: var(--approval-plan-card-bg);
  box-shadow: var(--approval-plan-card-shadow);
}
.approval-plan__steps li > b {
  display: grid;
  place-items: center;
  width: 26px;
  height: 26px;
  border-radius: 50%;
  background: var(--approval-plan-step-bg);
  color: var(--approval-plan-step-text);
  font-size: 12px;
}
.approval-plan__steps li > span {
  display: grid;
  gap: 3px;
  min-width: 0;
}
.approval-plan__steps strong {
  color: var(--approval-plan-code);
  font-size: 13px;
}
.approval-plan__steps small {
  color: var(--approval-plan-muted);
  font-size: 12px;
  line-height: 1.55;
}
.approval-plan__risk {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  margin: 0;
  color: var(--approval-plan-body);
  font-size: 13px;
  line-height: 1.7;
}
.approval-plan__risk .status-tag {
  flex: none;
  margin-top: 2px;
}
.approval-plan__adjust {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  margin: 10px 0 0;
  padding: 8px 10px;
  border: 1px solid var(--approval-plan-warning-border);
  border-radius: 6px;
  background: var(--approval-plan-warning-bg);
  color: var(--approval-plan-warning-text);
  font-size: 12px;
  line-height: 1.6;
}
.approval-plan__notes {
  display: grid;
  gap: 5px;
  margin: 0;
  padding: 0 0 0 18px;
  color: var(--approval-plan-body);
  font-size: 13px;
  line-height: 1.6;
}
.approval-plan__meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin: 4px 0 12px;
  color: var(--approval-plan-muted);
  font-size: 11px;
}
.approval-plan__meta i {
  width: 3px;
  height: 3px;
  border-radius: 50%;
  background: var(--approval-plan-dot);
}
.approval-plan__loading p {
  margin: 0 0 10px;
  color: var(--approval-plan-body);
  font-size: 13px;
}
.approval-risk-adjust {
  display: grid;
  gap: 8px;
  margin-bottom: 10px;
  padding: 10px 12px;
  border: 1px solid var(--approval-risk-adjust-border);
  border-radius: 7px;
  background: var(--approval-risk-adjust-bg);
  box-shadow: var(--approval-plan-card-shadow);
}
.approval-risk-adjust > label {
  display: grid;
  gap: 4px;
}
.approval-risk-adjust label > span {
  color: var(--approval-plan-muted);
  font-size: 12px;
  font-weight: 600;
}
.approval-risk-adjust label b {
  color: var(--approval-danger-text);
}
.approval-risk-adjust select {
  max-width: 200px;
  height: 32px;
}
.approval-risk-adjust textarea {
  min-height: 48px;
}
.approval-risk-adjust small {
  color: var(--approval-plan-muted);
  font-size: 11px;
}
.approval-list__tags {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  flex: none;
}
.approval-overdue-chip {
  padding: 2px 8px;
  border: 1px solid var(--approval-overdue-border);
  border-radius: 999px;
  color: var(--approval-overdue-text);
  background: var(--approval-overdue-bg);
  font-size: 10px;
  font-weight: 700;
  line-height: 14px;
  white-space: nowrap;
}
.approval-overdue-note {
  margin: 14px 18px 0;
}
.mini-timeline li > span {
  min-width: 0;
}
.mini-timeline small {
  display: block;
  margin-top: 2px;
  color: var(--approval-plan-muted);
  line-height: 1.5;
}
.mini-timeline li.is-approved i {
  border-color: var(--approval-approved-dot);
  background: var(--approval-approved-dot);
}
</style>
