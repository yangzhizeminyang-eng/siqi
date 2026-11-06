<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { apiRequest } from '../services/api'
import { useAuthStore } from '../stores/auth'
import { useTenancyStore } from '../stores/tenancy'
import { hasAccessRequirement } from '../services/accessControl'
import PageHeader from '../components/enterprise/PageHeader.vue'
import AsyncState from '../components/enterprise/AsyncState.vue'
import StatusBadge from '../components/enterprise/StatusBadge.vue'
import PermissionGate from '../components/enterprise/PermissionGate.vue'

const auth = useAuthStore()
const tenancy = useTenancyStore()

interface ApprovalDto { id: string; title?: string; operation?: string; risk_level?: string; approval_status?: string }
interface AuditDto { id: string; event_type?: string; summary?: string; actor_name?: string; created_at?: string }
interface RuntimeDto { id: string; title?: string; status: string }

const loading = ref(true)
const errorMsg = ref('')
const approvals = ref<ApprovalDto[]>([])
const audits = ref<AuditDto[]>([])
const runtimeTasks = ref<RuntimeDto[]>([])

const pendingApprovals = computed(() => approvals.value.filter(a => a.approval_status === 'pending'))
const runtimeCounts = computed(() => {
  const c: Record<string, number> = {}
  for (const t of runtimeTasks.value) c[t.status] = (c[t.status] ?? 0) + 1
  return c
})

const canAudit = computed(() => hasAccessRequirement(auth, tenancy, ['audit:view']))
const canRag = computed(() => hasAccessRequirement(auth, tenancy, ['rag:search']))

const permissionLabels: Record<string, string> = {
  'approval:view': '查看审批',
  'approval:approve': '审批操作',
  'approval:manage': '审批管理',
  'audit:view': '查看审计',
  'member:view': '查看成员',
  'member:manage': '成员管理',
  'org:manage': '组织管理',
  'project:manage': '项目管理',
  'rag:search': '企业检索',
  'runtime:manage': 'Runtime 管理',
  'tenant:switch': '切换租户',
  'tenant:context': '租户上下文',
}

function permissionLabel(permission: string): string {
  return permissionLabels[permission] || permission
}

function auditTypeLabel(eventType?: string): string {
  if (!eventType) return '审计事件'
  const labels: Record<string, string> = {
    PROJECT_CREATED: '创建项目',
    PROJECT_UPDATED: '更新项目',
    TENANT_SWITCHED: '切换租户',
    MEMBER_ROLE_CHANGED: '调整成员角色',
    APPROVAL_APPROVED: '审批通过',
    APPROVAL_REJECTED: '审批拒绝',
  }
  return labels[eventType] || eventType.replaceAll('_', ' ').toLowerCase()
}

function auditTime(value?: string): string {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  return new Intl.DateTimeFormat('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }).format(date)
}

async function load() {
  loading.value = true
  errorMsg.value = ''
  try {
    const [appr, aud, rt] = await Promise.all([
      apiRequest<ApprovalDto[]>('/api/enterprise/approvals').catch(() => [] as ApprovalDto[]),
      canAudit.value ? apiRequest<AuditDto[]>('/api/audit-events').catch(() => [] as AuditDto[]) : Promise.resolve([] as AuditDto[]),
      canRag.value ? apiRequest<RuntimeDto[]>('/api/enterprise/runtime/tasks').catch(() => [] as RuntimeDto[]) : Promise.resolve([] as RuntimeDto[]),
    ])
    approvals.value = appr ?? []
    audits.value = (aud ?? []).slice(0, 6)
    runtimeTasks.value = rt ?? []
  } catch (err) {
    errorMsg.value = err instanceof Error ? err.message : '加载失败'
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <section class="page-content enterprise-dashboard">
    <PageHeader title="企业工作台" subtitle="待办、审计、检索与 Runtime 运行摘要（全部来自真实接口，无虚构统计）"
                :tenant-name="tenancy.displayActiveName">
      <template #actions>
        <span class="echip">{{ tenancy.roleLabel || tenancy.role }}</span>
      </template>
    </PageHeader>

    <AsyncState v-if="loading" state="loading" />
    <AsyncState v-else-if="errorMsg" state="error" :message="errorMsg" />
    <template v-else>
      <div class="egrid">
        <section class="panel ecard">
          <header class="panel__header">
            <div>
              <h2 class="ecard__title">审批待办</h2>
              <p class="ecard__subtitle">需要人工确认的高风险操作</p>
            </div>
            <span class="ecard__count">{{ pendingApprovals.length }}</span>
          </header>
          <PermissionGate :permission="['approval:view']">
            <div v-if="!pendingApprovals.length" class="eempty eempty--compact">
              <strong>暂无待审批</strong>
              <span>新的审批请求会出现在这里。</span>
            </div>
            <ul v-else class="elist">
              <li v-for="a in pendingApprovals.slice(0, 6)" :key="a.id">
                <span class="elist__main">{{ a.title || a.operation || a.id }}</span>
                <StatusBadge :status="a.risk_level" :label="a.risk_level || '待评估'" size="sm" />
              </li>
            </ul>
            <div v-if="pendingApprovals.length > 6" class="emuted">…共 {{ pendingApprovals.length }} 条待审批</div>
          </PermissionGate>
        </section>

        <section class="panel ecard">
          <header class="panel__header">
            <div>
              <h2 class="ecard__title">最近审计</h2>
              <p class="ecard__subtitle">当前租户的最新操作留痕</p>
            </div>
            <span class="ecard__count">{{ audits.length }}</span>
          </header>
          <PermissionGate :permission="['audit:view']">
            <div v-if="!audits.length" class="eempty eempty--compact">
              <strong>暂无审计事件</strong>
              <span>完成组织、审批或检索操作后会留下记录。</span>
            </div>
            <ul v-else class="elist elist--audit">
              <li v-for="e in audits" :key="e.id">
                <span class="audit-dot" aria-hidden="true"></span>
                <span class="elist__main">
                  <strong>{{ e.summary || auditTypeLabel(e.event_type) }}</strong>
                  <small>{{ auditTypeLabel(e.event_type) }} · {{ e.actor_name || '系统' }}</small>
                </span>
                <time v-if="auditTime(e.created_at)" class="elist__time">{{ auditTime(e.created_at) }}</time>
              </li>
            </ul>
          </PermissionGate>
        </section>

        <section class="panel ecard">
          <header class="panel__header">
            <div>
              <h2 class="ecard__title">Runtime 摘要</h2>
              <p class="ecard__subtitle">任务编排与人工接管状态</p>
            </div>
            <span class="ecard__count">{{ runtimeTasks.length }}</span>
          </header>
          <PermissionGate :permission="['rag:search']">
            <div v-if="!runtimeTasks.length" class="eempty eempty--compact">
              <strong>暂无 Runtime 任务</strong>
              <span>任务开始执行后，状态会在这里汇总。</span>
            </div>
            <template v-else>
              <div class="esummary" aria-label="Runtime 状态统计">
                <span v-for="(n, k) in runtimeCounts" :key="k"><StatusBadge :status="k" :label="`${k} ${n}`" /></span>
              </div>
              <div class="ecard__footnote">共 {{ runtimeTasks.length }} 个任务，数据按当前租户隔离。</div>
            </template>
          </PermissionGate>
        </section>

        <section class="panel ecard">
          <header class="panel__header">
            <div>
              <h2 class="ecard__title">当前租户</h2>
              <p class="ecard__subtitle">本次会话的授权边界</p>
            </div>
            <span class="ecard__count">{{ tenancy.permissions.length }}</span>
          </header>
          <dl class="edl">
            <div><dt>租户</dt><dd>{{ tenancy.activeTenant?.name || '默认租户' }}</dd></div>
            <div><dt>角色</dt><dd>{{ tenancy.roleLabel || tenancy.role }}</dd></div>
            <div class="edl__permissions">
              <dt>权限</dt>
              <dd class="etags">
                <span v-for="p in tenancy.permissions" :key="p" class="etag" :title="p">{{ permissionLabel(p) }}</span>
                <span v-if="!tenancy.permissions.length" class="emuted">暂无可用权限</span>
              </dd>
            </div>
          </dl>
        </section>
      </div>
    </template>
  </section>
</template>

<style scoped>
.echip { display: inline-flex; align-items: center; min-height: 28px; padding: 0 10px; border: 1px solid var(--theme-border, #dbe3ec); border-radius: 7px; font-size: 12px; font-weight: 700; color: var(--theme-text, #1f2f46); background: var(--theme-surface-subtle, #f7f9fc); }
.egrid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }
.ecard { min-width: 0; padding: 0 16px 16px; overflow: hidden; }
.ecard .panel__header { margin: 0 -16px 14px; padding: 14px 16px 12px; }
.ecard__title { margin: 0; font-size: 14px; font-weight: 800; }
.ecard__subtitle { margin-top: 4px; color: var(--text-soft, #758496); font-size: 11px; line-height: 16px; }
.ecard__count { display: inline-flex; align-items: center; justify-content: center; min-width: 26px; height: 24px; padding: 0 7px; border: 1px solid var(--theme-border, #dbe3ec); border-radius: 6px; color: var(--theme-text-muted, #65758a); background: var(--theme-surface-muted, #f1f5f9); font-size: 11px; font-weight: 800; font-variant-numeric: tabular-nums; }
.elist { list-style: none; margin: 0; padding: 0; display: grid; gap: 8px; }
.elist li { display: flex; align-items: center; gap: 9px; min-width: 0; padding: 8px 0; border-bottom: 1px solid var(--theme-border-subtle, #e9eef4); }
.elist li:last-child { border-bottom: 0; }
.elist__main { min-width: 0; overflow-wrap: anywhere; font-size: 13px; }
.elist__main strong, .elist__main small { display: block; }
.elist__main strong { color: var(--theme-text, #1f2f46); font-size: 13px; line-height: 18px; }
.elist__main small { margin-top: 2px; color: var(--text-soft, #758496); font-size: 11px; line-height: 15px; }
.elist__time { flex: none; color: var(--text-soft, #758496); font-size: 10px; white-space: nowrap; }
.elist--audit .elist__main { flex: 1; }
.audit-dot { width: 7px; height: 7px; flex: none; border-radius: 50%; background: var(--brand, #2249d6); box-shadow: 0 0 0 4px var(--brand-soft, #eef3ff); }
.emuted { color: var(--text-soft, #758496); font-size: 12px; }
.eempty { display: grid; gap: 4px; min-height: 108px; align-content: center; color: var(--text-soft, #758496); }
.eempty strong { color: var(--theme-text, #1f2f46); font-size: 13px; }
.eempty span { font-size: 11px; line-height: 16px; }
.eempty--compact { min-height: 96px; }
.esummary { display: flex; flex-wrap: wrap; gap: 6px; }
.ecard__footnote { margin-top: 10px; color: var(--text-soft, #758496); font-size: 11px; line-height: 16px; }
.edl { display: grid; grid-template-columns: 64px minmax(0, 1fr); gap: 10px 12px; margin: 0; }
.edl dt { color: var(--text-soft, #758496); font-size: 12px; font-weight: 700; }
.edl dd { margin: 0; overflow-wrap: anywhere; color: var(--theme-text, #1f2f46); font-size: 13px; line-height: 18px; }
.edl__permissions { display: contents; }
.etags { display: flex; flex-wrap: wrap; gap: 6px; align-items: flex-start; }
.etag { display: inline-flex; align-items: center; min-height: 24px; max-width: 100%; padding: 2px 7px; border: 1px solid var(--theme-border-subtle, #e9eef4); border-radius: 5px; background: var(--theme-tag-surface, #f1f5f9); color: var(--theme-text, #1f2f46); font-size: 11px; font-weight: 700; line-height: 16px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
@media (max-width: 760px) {
  .egrid { grid-template-columns: 1fr; }
}
</style>
