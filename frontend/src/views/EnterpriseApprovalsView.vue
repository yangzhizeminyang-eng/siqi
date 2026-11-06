<script setup lang="ts">
// 企业版 E2-01 审批中心：前端只展示和发起操作，最终权限、租户隔离和状态转换均由后端完成。
import {
  AlertTriangle,
  Check,
  ChevronRight,
  ClipboardCheck,
  Clock3,
  FileClock,
  RefreshCw,
  ShieldCheck,
  X,
} from '@lucide/vue'
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useAuthStore } from '../stores/auth'
import { useApprovalsStore } from '../stores/approvals'
import { useTenancyStore } from '../stores/tenancy'
import TaskFilterSelect from '../components/TaskFilterSelect.vue'
import type { TaskFilterOption } from '../components/TaskFilterSelect.vue'
import {
  APPROVAL_STATUS_LABELS,
  type ApprovalApplication,
  type ApprovalStatus,
} from '../types/approvals'

const store = useApprovalsStore()
const tenancy = useTenancyStore()
const auth = useAuthStore()

const statusFilter = ref<'' | ApprovalStatus>('')
const riskFilter = ref('')
const scopeFilter = ref<'all' | 'mine' | 'submitted'>('all')
const selectedApproval = ref<ApprovalApplication | null>(null)
const approvalComment = ref('')
const rejectReason = ref('')
const actionInFlight = ref('')

const statusOptions: TaskFilterOption[] = [
  { value: '', label: '全部状态' },
  ...Object.entries(APPROVAL_STATUS_LABELS).map(([value, label]) => ({ value, label })),
]
const riskOptions: TaskFilterOption[] = [
  { value: '', label: '全部风险' },
  { value: 'L2', label: 'L2 · 项目审批' },
  { value: 'L3', label: 'L3 · 双人审批' },
  { value: 'L4', label: 'L4 · 安全审批' },
]
const scopeOptions: TaskFilterOption[] = [
  { value: 'all', label: '全部申请' },
  { value: 'mine', label: '待我处理' },
  { value: 'submitted', label: '我提交的' },
]

const items = computed(() => store.items)
const pendingCount = computed(() => items.value.filter((item) => item.approval_status === 'pending').length)
const highRiskCount = computed(() => items.value.filter((item) => ['L3', 'L4'].includes(item.risk_level)).length)
const executingCount = computed(() => items.value.filter((item) => item.approval_status === 'executing').length)
const canManage = computed(() => tenancy.permissions.includes('approval:approve'))
const canCancel = (item: ApprovalApplication) =>
  item.actor_id === auth.currentUser?.id || tenancy.permissions.includes('approval:manage')

function statusLabel(status: ApprovalStatus) {
  return APPROVAL_STATUS_LABELS[status] ?? status
}

function operationLabel(operation: string) {
  const labels: Record<string, string> = {
    'repository.index.rebuild': '重建检索索引',
    'team_request.sync_gitee': '同步 Gitee 需求',
    'repository.archive': '归档仓库',
    'repository.delete': '删除仓库',
    'audit.export': '导出审计记录',
    'tenant.member.role_change': '调整成员角色',
  }
  return labels[operation] ?? operation
}

function formatDate(value: string | null) {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date)
}

function statusTone(status: ApprovalStatus) {
  if (status === 'pending') return 'warning'
  if (status === 'approved' || status === 'executed') return 'success'
  if (status === 'executing') return 'info'
  return 'neutral'
}

function riskTone(level: string) {
  if (level === 'L4') return 'critical'
  if (level === 'L3') return 'high'
  return 'standard'
}

async function load() {
  try {
    await store.load({
      status: statusFilter.value || undefined,
      risk: riskFilter.value || undefined,
      scope: scopeFilter.value,
    })
  } catch {
    ElMessage.error('加载审批队列失败，请检查服务状态后重试。')
  }
}

function changeStatus(value: string) {
  statusFilter.value = value as '' | ApprovalStatus
  void load()
}

function changeRisk(value: string) {
  riskFilter.value = value
  void load()
}

function changeScope(value: string) {
  scopeFilter.value = value as 'all' | 'mine' | 'submitted'
  void load()
}

function openApproval(item: ApprovalApplication) {
  selectedApproval.value = item
  void store.loadDetail(item.id)
    .then((detail) => {
      if (selectedApproval.value?.id === item.id) selectedApproval.value = detail
    })
    .catch(() => undefined)
}

function closeApproval() {
  selectedApproval.value = null
  approvalComment.value = ''
  rejectReason.value = ''
}

async function act(action: 'approve' | 'reject' | 'cancel' | 'execute', item: ApprovalApplication) {
  if (action === 'reject' && !rejectReason.value.trim()) {
    ElMessage.warning('请填写拒绝原因。')
    return
  }

  const actionKey = `${item.id}:${action}`
  actionInFlight.value = actionKey
  try {
    let record: ApprovalApplication
    if (action === 'approve') {
      record = await store.approve(item.id, approvalComment.value.trim())
      ElMessage.success('审批已通过。')
    } else if (action === 'reject') {
      record = await store.reject(item.id, rejectReason.value.trim())
      ElMessage.success('审批已拒绝。')
    } else if (action === 'cancel') {
      record = await store.cancel(item.id, '')
      ElMessage.success('审批已取消。')
    } else {
      if (item.approval_mode === 'security') {
        await ElMessageBox.confirm(
          '该操作属于 L4 高风险操作。请确认已核对资源范围、审批依据和回滚方案。',
          '确认执行',
          { confirmButtonText: '确认执行', cancelButtonText: '暂不执行', type: 'warning' },
        )
      }
      record = await store.execute(item.id, true)
      ElMessage.success('审批操作已进入执行流程。')
    }
    selectedApproval.value = record
    approvalComment.value = ''
    rejectReason.value = ''
    await load()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '操作未完成，请刷新后确认当前状态。')
    await load()
  } finally {
    actionInFlight.value = ''
  }
}

onMounted(() => {
  void load()
})
</script>

<template>
  <section class="page-content approval-governance">
    <header class="page-toolbar page-toolbar--stacked approval-governance__heading">
      <div>
        <h2>审批治理</h2>
        <p>处理高风险操作的审批申请，并保留执行前后的完整状态证据。</p>
      </div>
      <button
        class="button button--secondary"
        type="button"
        :disabled="store.loading"
        title="重新加载审批队列"
        @click="load"
      >
        <RefreshCw :size="16" :class="{ 'is-spinning': store.loading }" />
        {{ store.loading ? '同步中' : '重新加载' }}
      </button>
    </header>

    <section class="panel approval-governance__console" aria-label="企业审批队列">
      <div class="approval-governance__summary">
        <div class="approval-governance__summary-title">
          <span class="approval-governance__summary-icon"><ClipboardCheck :size="18" /></span>
          <span>
            <strong>审批队列</strong>
            <small>{{ tenancy.displayActiveName }} · {{ items.length }} 项申请</small>
          </span>
        </div>
        <dl class="approval-governance__signals">
          <div>
            <dt>待审批</dt>
            <dd>{{ pendingCount }}</dd>
          </div>
          <div>
            <dt>高风险</dt>
            <dd class="is-critical">{{ highRiskCount }}</dd>
          </div>
          <div>
            <dt>执行中</dt>
            <dd class="is-info">{{ executingCount }}</dd>
          </div>
        </dl>
      </div>

      <div class="approval-governance__filters" aria-label="审批队列筛选">
        <div class="approval-governance__filter">
          <span>状态</span>
          <TaskFilterSelect
            :model-value="statusFilter"
            :options="statusOptions"
            ariaLabel="审批状态"
            :disabled="store.loading"
            @update:model-value="changeStatus"
          />
        </div>
        <div class="approval-governance__filter">
          <span>风险</span>
          <TaskFilterSelect
            :model-value="riskFilter"
            :options="riskOptions"
            ariaLabel="风险等级"
            :disabled="store.loading"
            @update:model-value="changeRisk"
          />
        </div>
        <div class="approval-governance__filter">
          <span>范围</span>
          <TaskFilterSelect
            :model-value="scopeFilter"
            :options="scopeOptions"
            ariaLabel="审批范围"
            :disabled="store.loading"
            @update:model-value="changeScope"
          />
        </div>
        <span class="approval-governance__filter-result">{{ items.length }} 项结果</span>
      </div>

      <div v-if="store.error" class="page-notice page-notice--danger approval-governance__error" role="alert">
        <span class="page-notice__icon"><AlertTriangle :size="17" /></span>
        <div class="page-notice__body">
          <strong>审批队列加载失败</strong>
          <p>{{ store.error }}</p>
        </div>
        <button class="button button--secondary" type="button" @click="load">重试</button>
      </div>

      <div v-else-if="store.loading && !items.length" class="approval-governance__loading" aria-live="polite">
        <span v-for="index in 5" :key="index" class="approval-governance__loading-row" />
        <span class="sr-only">正在读取审批队列</span>
      </div>

      <div v-else-if="!items.length" class="approval-governance__empty">
        <span class="approval-governance__empty-icon"><FileClock :size="23" /></span>
        <strong>当前筛选条件下没有审批项</strong>
        <p>调整筛选条件，或等待受治理操作提交审批后再处理。</p>
      </div>

      <div v-else class="table-wrap approval-governance__table-wrap">
        <table class="data-table approval-governance__table">
          <thead>
            <tr>
              <th>审批事项</th>
              <th>对象</th>
              <th>风险</th>
              <th>提交人</th>
              <th>提交时间</th>
              <th>状态</th>
              <th><span class="sr-only">操作</span></th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="item in items"
              :key="item.id"
              class="approval-governance__row"
              :data-approval-id="item.id"
              tabindex="0"
              @click="openApproval(item)"
              @keydown.enter.prevent="openApproval(item)"
              @keydown.space.prevent="openApproval(item)"
            >
              <td class="approval-governance__item-cell">
                <strong>{{ item.title }}</strong>
                <small>{{ operationLabel(item.operation) }}</small>
              </td>
              <td>
                <span class="approval-governance__resource">{{ item.resource_type }}</span>
                <small class="mono">{{ item.resource_id }}</small>
              </td>
              <td>
                <span class="approval-governance__risk" :class="`is-${riskTone(item.risk_level)}`">{{ item.risk_level }}</span>
              </td>
              <td>
                <strong>{{ item.actor_name }}</strong>
                <small class="mono">{{ item.actor_id }}</small>
              </td>
              <td class="approval-governance__time">{{ formatDate(item.created_at) }}</td>
              <td>
                <span class="approval-governance__status" :class="`is-${statusTone(item.approval_status)}`">
                  {{ statusLabel(item.approval_status) }}
                </span>
              </td>
              <td><ChevronRight :size="17" class="approval-governance__chevron" aria-hidden="true" /></td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <Transition name="approval-governance-drawer">
      <div v-if="selectedApproval" class="approval-governance__drawer-backdrop" @click.self="closeApproval">
        <aside
          class="approval-governance__drawer"
          role="dialog"
          aria-modal="true"
          aria-labelledby="approval-detail-title"
          tabindex="-1"
          @keydown.esc="closeApproval"
        >
          <header class="approval-governance__drawer-head">
            <div>
              <span class="approval-governance__drawer-kicker">{{ operationLabel(selectedApproval.operation) }}</span>
              <h2 id="approval-detail-title">审批详情</h2>
            </div>
            <button class="icon-button" type="button" aria-label="关闭审批详情" title="关闭审批详情" @click="closeApproval">
              <X :size="18" />
            </button>
          </header>

          <div class="approval-governance__drawer-body">
            <section class="approval-governance__decision">
              <div>
                <span class="approval-governance__risk" :class="`is-${riskTone(selectedApproval.risk_level)}`">
                  {{ selectedApproval.risk_level }}
                </span>
                <span class="approval-governance__status" :class="`is-${statusTone(selectedApproval.approval_status)}`">
                  {{ statusLabel(selectedApproval.approval_status) }}
                </span>
              </div>
              <strong>{{ selectedApproval.title }}</strong>
              <p>{{ selectedApproval.reason || '该申请未提供额外风险说明。' }}</p>
            </section>

            <dl class="approval-governance__detail-list">
              <div>
                <dt>审批编号</dt>
                <dd class="mono">{{ selectedApproval.id }}</dd>
              </div>
              <div>
                <dt>资源对象</dt>
                <dd><span>{{ selectedApproval.resource_type }}</span><small class="mono">{{ selectedApproval.resource_id }}</small></dd>
              </div>
              <div>
                <dt>提交人</dt>
                <dd><span>{{ selectedApproval.actor_name }}</span><small class="mono">{{ selectedApproval.actor_id }}</small></dd>
              </div>
              <div>
                <dt>审批模式</dt>
                <dd>{{ selectedApproval.approval_mode }}</dd>
              </div>
              <div>
                <dt>提交时间</dt>
                <dd>{{ formatDate(selectedApproval.created_at) }}</dd>
              </div>
              <div>
                <dt>到期时间</dt>
                <dd>{{ formatDate(selectedApproval.expires_at) }}</dd>
              </div>
              <div v-if="selectedApproval.approved_by_name">
                <dt>审批记录</dt>
                <dd>{{ selectedApproval.approved_by_name }} · {{ formatDate(selectedApproval.approved_at) }}</dd>
              </div>
              <div v-if="selectedApproval.started_at || selectedApproval.executed_at">
                <dt>执行记录</dt>
                <dd>{{ selectedApproval.started_at ? `开始 ${formatDate(selectedApproval.started_at)}` : '' }}{{ selectedApproval.executed_at ? `完成 ${formatDate(selectedApproval.executed_at)}` : '' }}</dd>
              </div>
              <div v-if="selectedApproval.execution_error" class="is-error">
                <dt>上次执行失败</dt>
                <dd>{{ selectedApproval.execution_error }}</dd>
              </div>
            </dl>

            <section v-if="selectedApproval.approval_status === 'pending'" class="approval-governance__action-panel">
              <template v-if="canManage">
                <label>
                  <span>审批意见</span>
                  <textarea v-model="approvalComment" placeholder="记录审批结论、边界或回滚要求（可选）。" />
                </label>
                <label>
                  <span>拒绝原因</span>
                  <textarea v-model="rejectReason" placeholder="拒绝时必须填写原因。" />
                </label>
                <div class="approval-governance__action-row">
                  <button class="button button--success" type="button" :disabled="Boolean(actionInFlight)" @click="act('approve', selectedApproval)">
                    <Check :size="16" /> {{ actionInFlight.endsWith(':approve') ? '处理中' : '通过' }}
                  </button>
                  <button class="button button--danger" type="button" :disabled="Boolean(actionInFlight)" @click="act('reject', selectedApproval)">
                    <X :size="16" /> {{ actionInFlight.endsWith(':reject') ? '处理中' : '拒绝' }}
                  </button>
                  <button v-if="canCancel(selectedApproval)" class="button button--ghost" type="button" :disabled="Boolean(actionInFlight)" @click="act('cancel', selectedApproval)">
                    取消申请
                  </button>
                </div>
              </template>
              <div v-else class="approval-governance__read-only">
                <ShieldCheck :size="17" />
                <span>您可以查看审批证据，但没有处理该申请的权限。</span>
              </div>
            </section>

            <section v-if="selectedApproval.approval_status === 'approved' && canManage" class="approval-governance__action-panel">
              <div class="approval-governance__read-only">
                <Clock3 :size="17" />
                <span>{{ selectedApproval.approval_mode === 'security' ? '这是 L4 安全操作，执行前会要求二次确认。' : '审批已通过，执行前仍会由服务端重新校验资源和权限。' }}</span>
              </div>
              <button class="button button--primary button--wide" type="button" :disabled="Boolean(actionInFlight)" @click="act('execute', selectedApproval)">
                {{ actionInFlight.endsWith(':execute') ? '正在提交执行' : (selectedApproval.approval_mode === 'security' ? '二次确认并执行' : '执行操作') }}
              </button>
            </section>
          </div>
        </aside>
      </div>
    </Transition>
  </section>
</template>

<style scoped>
.approval-governance {
  --approval-surface: var(--theme-surface-base);
  --approval-subtle: var(--theme-surface-subtle);
  --approval-muted: var(--theme-surface-muted);
  --approval-border: var(--theme-border);
  --approval-border-soft: var(--theme-border-subtle);
  --approval-text: var(--theme-text);
  --approval-heading: var(--theme-text-heading);
  --approval-muted-text: var(--theme-text-muted);
  --approval-faint: var(--theme-text-faint);
  --approval-link: var(--theme-link);
}

.approval-governance__heading {
  align-items: center;
  margin-bottom: 16px;
}

.approval-governance__heading h2 {
  color: var(--approval-heading);
  font-size: 22px;
  line-height: 29px;
}

.approval-governance__heading p {
  margin-top: 5px;
  color: var(--approval-muted-text);
  font-size: 12px;
  line-height: 18px;
}

.approval-governance__console {
  overflow: visible;
  border-color: var(--approval-border);
  background: var(--approval-surface);
}

.approval-governance__summary {
  display: flex;
  align-items: stretch;
  justify-content: space-between;
  gap: 20px;
  min-height: 82px;
  padding: 16px 18px;
  border-bottom: 1px solid var(--approval-border-soft);
}

.approval-governance__summary-title {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}

.approval-governance__summary-icon {
  display: grid;
  flex: none;
  place-items: center;
  width: 34px;
  height: 34px;
  border-radius: 7px;
  color: var(--approval-link);
  background: var(--theme-accent-soft);
}

.approval-governance__summary-title span:last-child {
  display: grid;
  gap: 3px;
  min-width: 0;
}

.approval-governance__summary-title strong {
  color: var(--approval-heading);
  font-size: 14px;
  line-height: 18px;
}

.approval-governance__summary-title small {
  overflow: hidden;
  color: var(--approval-muted-text);
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.approval-governance__signals {
  display: flex;
  align-items: stretch;
  margin: 0;
}

.approval-governance__signals div {
  display: grid;
  align-content: center;
  min-width: 86px;
  padding: 0 16px;
  border-left: 1px solid var(--approval-border-soft);
}

.approval-governance__signals dt {
  color: var(--approval-faint);
  font-size: 10px;
  line-height: 14px;
}

.approval-governance__signals dd {
  margin: 4px 0 0;
  color: var(--approval-heading);
  font-size: 19px;
  font-variant-numeric: tabular-nums;
  line-height: 22px;
}

.approval-governance__signals .is-critical {
  color: var(--theme-status-danger);
}

.approval-governance__signals .is-info {
  color: var(--approval-link);
}

.approval-governance__filters {
  display: flex;
  align-items: center;
  gap: 12px;
  min-height: 64px;
  padding: 12px 16px;
  border-bottom: 1px solid var(--approval-border-soft);
  background: var(--approval-subtle);
}

.approval-governance__filter {
  display: grid;
  grid-template-columns: max-content minmax(126px, 156px);
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.approval-governance__filter > span {
  color: var(--approval-muted-text);
  font-size: 11px;
  font-weight: 650;
  white-space: nowrap;
}

.approval-governance__filter :deep(.task-filter-select) {
  min-width: 126px;
  flex: none;
}

.approval-governance__filter-result {
  margin-left: auto;
  color: var(--approval-muted-text);
  font-size: 11px;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.approval-governance__error {
  margin: 16px;
}

.approval-governance__loading {
  display: grid;
  gap: 1px;
  min-height: 296px;
  padding: 0 16px;
  background: var(--approval-surface);
}

.approval-governance__loading-row {
  display: block;
  height: 58px;
  border-bottom: 1px solid var(--approval-border-soft);
  background: linear-gradient(90deg, transparent 0%, var(--approval-muted) 42%, transparent 84%);
  background-size: 220% 100%;
  animation: approval-shimmer 1.45s ease-in-out infinite;
}

.approval-governance__loading-row:nth-child(2) { animation-delay: .08s; }
.approval-governance__loading-row:nth-child(3) { animation-delay: .16s; }
.approval-governance__loading-row:nth-child(4) { animation-delay: .24s; }
.approval-governance__loading-row:nth-child(5) { animation-delay: .32s; }

@keyframes approval-shimmer {
  to { background-position: -120% 0; }
}

.approval-governance__empty {
  display: grid;
  place-items: center;
  align-content: center;
  gap: 8px;
  min-height: 296px;
  padding: 30px;
  text-align: center;
}

.approval-governance__empty-icon {
  display: grid;
  place-items: center;
  width: 40px;
  height: 40px;
  border: 1px solid var(--approval-border);
  border-radius: 7px;
  color: var(--approval-faint);
  background: var(--approval-subtle);
}

.approval-governance__empty strong {
  margin-top: 3px;
  color: var(--approval-heading);
  font-size: 13px;
}

.approval-governance__empty p {
  max-width: 370px;
  color: var(--approval-muted-text);
  font-size: 11px;
  line-height: 18px;
}

.approval-governance__table-wrap {
  overflow-x: auto;
}

.approval-governance__table {
  min-width: 1010px;
}

.approval-governance__table th {
  height: 37px;
  color: var(--approval-faint);
  background: var(--approval-subtle);
  font-size: 10px;
  font-weight: 650;
}

.approval-governance__table td {
  height: 66px;
  color: var(--approval-text);
  vertical-align: middle;
}

.approval-governance__table td:first-child {
  min-width: 258px;
}

.approval-governance__table td:nth-child(2) {
  min-width: 164px;
}

.approval-governance__table td:nth-child(4) {
  min-width: 128px;
}

.approval-governance__row {
  cursor: pointer;
  outline: 0;
  transition: background .16s ease;
}

.approval-governance__row:hover,
.approval-governance__row:focus-visible {
  background: var(--theme-surface-hover);
}

.approval-governance__row:focus-visible {
  box-shadow: inset 0 0 0 2px var(--theme-accent-border);
}

.approval-governance__item-cell strong,
.approval-governance__table td > strong {
  display: block;
  overflow: hidden;
  color: var(--approval-heading);
  font-size: 12px;
  line-height: 17px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.approval-governance__item-cell small,
.approval-governance__table td > small,
.approval-governance__resource + small {
  display: block;
  max-width: 190px;
  margin-top: 3px;
  overflow: hidden;
  color: var(--approval-muted-text);
  font-size: 10px;
  line-height: 14px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.approval-governance__resource {
  display: block;
  color: var(--approval-text);
  font-size: 11px;
  line-height: 15px;
}

.approval-governance__risk,
.approval-governance__status {
  display: inline-flex;
  align-items: center;
  min-height: 23px;
  padding: 2px 7px;
  border: 1px solid transparent;
  border-radius: 999px;
  font-size: 10px;
  font-weight: 700;
  line-height: 16px;
  white-space: nowrap;
}

.approval-governance__risk.is-standard {
  border-color: var(--theme-accent-border);
  color: var(--approval-link);
  background: var(--theme-accent-soft);
}

.approval-governance__risk.is-high {
  border-color: var(--theme-warning-border);
  color: var(--theme-warning-text);
  background: var(--theme-warning-surface);
}

.approval-governance__risk.is-critical {
  border-color: color-mix(in srgb, var(--theme-status-danger) 28%, transparent);
  color: var(--theme-status-danger);
  background: color-mix(in srgb, var(--theme-status-danger) 9%, transparent);
}

.approval-governance__status.is-warning {
  border-color: var(--theme-warning-border);
  color: var(--theme-warning-text);
  background: var(--theme-warning-surface);
}

.approval-governance__status.is-success {
  border-color: color-mix(in srgb, var(--theme-status-success) 25%, transparent);
  color: var(--theme-status-success);
  background: var(--theme-success-surface);
}

.approval-governance__status.is-info {
  border-color: var(--theme-accent-border);
  color: var(--approval-link);
  background: var(--theme-accent-soft);
}

.approval-governance__status.is-neutral {
  border-color: var(--approval-border);
  color: var(--approval-muted-text);
  background: var(--approval-subtle);
}

.approval-governance__time {
  color: var(--approval-muted-text) !important;
  font-size: 10px;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.approval-governance__chevron {
  color: var(--approval-faint);
  transition: color .16s ease, transform .16s ease;
}

.approval-governance__row:hover .approval-governance__chevron,
.approval-governance__row:focus-visible .approval-governance__chevron {
  color: var(--approval-link);
  transform: translateX(2px);
}

.approval-governance__drawer-backdrop {
  position: fixed;
  z-index: 150;
  inset: 0;
  display: flex;
  justify-content: flex-end;
  background: var(--theme-overlay);
}

.approval-governance__drawer {
  display: grid;
  grid-template-rows: auto minmax(0, 1fr);
  width: min(560px, 100vw);
  height: 100%;
  overflow: hidden;
  border-left: 1px solid var(--approval-border);
  color: var(--approval-text);
  background: var(--approval-surface);
  box-shadow: -18px 0 42px rgba(15, 23, 42, .18);
  outline: 0;
}

.approval-governance__drawer-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 14px;
  padding: 18px;
  border-bottom: 1px solid var(--approval-border-soft);
}

.approval-governance__drawer-kicker {
  display: block;
  margin-bottom: 4px;
  color: var(--approval-muted-text);
  font-size: 11px;
  line-height: 15px;
}

.approval-governance__drawer-head h2 {
  color: var(--approval-heading);
  font-size: 18px;
  line-height: 24px;
}

.approval-governance__drawer-body {
  min-height: 0;
  overflow-y: auto;
  padding: 18px;
}

.approval-governance__decision {
  padding: 14px;
  border: 1px solid var(--approval-border);
  border-radius: 8px;
  background: var(--approval-subtle);
}

.approval-governance__decision > div {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.approval-governance__decision strong {
  display: block;
  margin-top: 11px;
  color: var(--approval-heading);
  font-size: 15px;
  line-height: 21px;
}

.approval-governance__decision p {
  margin-top: 6px;
  color: var(--approval-muted-text);
  font-size: 12px;
  line-height: 19px;
}

.approval-governance__detail-list {
  display: grid;
  gap: 0;
  margin: 16px 0 0;
  border-top: 1px solid var(--approval-border-soft);
}

.approval-governance__detail-list div {
  display: grid;
  grid-template-columns: 116px minmax(0, 1fr);
  gap: 12px;
  min-height: 45px;
  padding: 11px 0;
  border-bottom: 1px solid var(--approval-border-soft);
}

.approval-governance__detail-list dt {
  color: var(--approval-faint);
  font-size: 11px;
  line-height: 17px;
}

.approval-governance__detail-list dd {
  min-width: 0;
  margin: 0;
  overflow-wrap: anywhere;
  color: var(--approval-text);
  font-size: 11px;
  line-height: 17px;
}

.approval-governance__detail-list dd span,
.approval-governance__detail-list dd small {
  display: block;
}

.approval-governance__detail-list dd small {
  margin-top: 2px;
  color: var(--approval-muted-text);
}

.approval-governance__detail-list .is-error dd {
  color: var(--theme-status-danger);
}

.approval-governance__action-panel {
  display: grid;
  gap: 10px;
  margin-top: 18px;
  padding: 14px;
  border: 1px solid var(--approval-border);
  border-radius: 8px;
  background: var(--approval-subtle);
}

.approval-governance__action-panel label {
  display: grid;
  gap: 5px;
}

.approval-governance__action-panel label > span {
  color: var(--approval-muted-text);
  font-size: 11px;
  font-weight: 650;
}

.approval-governance__action-panel textarea {
  min-height: 62px;
  border-color: var(--approval-border);
  color: var(--approval-text);
  background: var(--approval-surface);
  font-size: 12px;
  line-height: 18px;
}

.approval-governance__action-panel textarea::placeholder {
  color: var(--approval-faint);
}

.approval-governance__action-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 2px;
}

.approval-governance__read-only {
  display: grid;
  grid-template-columns: 18px minmax(0, 1fr);
  gap: 8px;
  color: var(--approval-muted-text);
  font-size: 11px;
  line-height: 18px;
}

.approval-governance__read-only svg {
  margin-top: 1px;
  color: var(--approval-link);
}

.approval-governance-drawer-enter-active,
.approval-governance-drawer-leave-active {
  transition: opacity .18s ease;
}

.approval-governance-drawer-enter-active .approval-governance__drawer,
.approval-governance-drawer-leave-active .approval-governance__drawer {
  transition: transform .22s cubic-bezier(.2, .8, .2, 1);
}

.approval-governance-drawer-enter-from,
.approval-governance-drawer-leave-to {
  opacity: 0;
}

.approval-governance-drawer-enter-from .approval-governance__drawer,
.approval-governance-drawer-leave-to .approval-governance__drawer {
  transform: translateX(100%);
}

@media (max-width: 900px) {
  .approval-governance__summary {
    align-items: flex-start;
    flex-direction: column;
    gap: 14px;
  }

  .approval-governance__signals {
    width: 100%;
  }

  .approval-governance__signals div {
    flex: 1;
  }

  .approval-governance__filters {
    align-items: stretch;
    flex-wrap: wrap;
  }

  .approval-governance__filter {
    flex: 1 1 220px;
  }

  .approval-governance__filter-result {
    width: 100%;
    margin-left: 0;
  }
}

@media (max-width: 580px) {
  .approval-governance__heading {
    align-items: flex-start;
    flex-direction: column;
  }

  .approval-governance__heading .button {
    width: 100%;
  }

  .approval-governance__summary,
  .approval-governance__filters {
    padding-right: 14px;
    padding-left: 14px;
  }

  .approval-governance__signals div {
    min-width: 0;
    padding: 0 10px;
  }

  .approval-governance__filter {
    grid-template-columns: 44px minmax(0, 1fr);
    width: 100%;
  }

  .approval-governance__filter :deep(.task-filter-select) {
    width: 100%;
  }

  .approval-governance__drawer {
    width: 100vw;
  }

  .approval-governance__drawer-head,
  .approval-governance__drawer-body {
    padding-right: 14px;
    padding-left: 14px;
  }

  .approval-governance__detail-list div {
    grid-template-columns: 1fr;
    gap: 3px;
  }
}

@media (prefers-reduced-motion: reduce) {
  .approval-governance__loading-row,
  .approval-governance-drawer-enter-active,
  .approval-governance-drawer-leave-active,
  .approval-governance-drawer-enter-active .approval-governance__drawer,
  .approval-governance-drawer-leave-active .approval-governance__drawer {
    animation: none;
    transition-duration: .01ms;
  }
}
</style>
