<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RotateCcw } from '@lucide/vue'
import { apiRequest } from '../services/api'
import { useAuthStore } from '../stores/auth'
import { useTenancyStore } from '../stores/tenancy'
import { hasAccessRequirement } from '../services/accessControl'
import PageHeader from '../components/enterprise/PageHeader.vue'
import AsyncState from '../components/enterprise/AsyncState.vue'
import StatusBadge from '../components/enterprise/StatusBadge.vue'
import DetailDrawer from '../components/enterprise/DetailDrawer.vue'
import ConfirmDialog from '../components/enterprise/ConfirmDialog.vue'

interface RuntimeTaskDto {
  id: string
  tenant_id: string
  title?: string
  status: string
  current_step?: string | null
  completed_steps?: string[]
  checkpoint_version?: number
  retry_count?: number
  wait_human_reason?: string | null
  approval_state?: string
  idempotency_key?: string
  steps?: { name: string; status: string; retry_count?: number; failure_reason?: string | null }[]
}

const auth = useAuthStore()
const tenancy = useTenancyStore()
const canAccess = computed(() => hasAccessRequirement(auth, tenancy, ['rag:search']))
const canTakeover = computed(() =>
  ['tenant_owner', 'tenant_admin', 'security_admin', 'project_owner'].includes(tenancy.role ?? ''))

const state = ref<'loading' | 'error' | 'ready'>('loading')
const errorMsg = ref('')
const tasks = ref<RuntimeTaskDto[]>([])
const selected = ref<RuntimeTaskDto | null>(null)
const confirmAction = ref<{ id: string; action: 'resume' | 'retry' | 'cancel' } | null>(null)
const acting = ref(false)

async function load() {
  state.value = 'loading'
  errorMsg.value = ''
  try {
    const data = await apiRequest<RuntimeTaskDto[]>('/api/enterprise/runtime/tasks')
    tasks.value = data ?? []
    state.value = 'ready'
  } catch (err) {
    state.value = 'error'
    errorMsg.value = err instanceof Error ? err.message : '加载失败'
  }
}

async function doTakeover(id: string, action: 'resume' | 'retry' | 'cancel') {
  acting.value = true
  try {
    await apiRequest(`/api/enterprise/runtime/tasks/${id}/takeover/complete`, {
      method: 'POST', body: JSON.stringify({ action }),
    })
    confirmAction.value = null
    await load()
  } catch (err) {
    errorMsg.value = err instanceof Error ? err.message : '操作失败'
  } finally {
    acting.value = false
  }
}

const counts = computed(() => {
  const c: Record<string, number> = {}
  for (const t of tasks.value) c[t.status] = (c[t.status] ?? 0) + 1
  return c
})

onMounted(() => {
  if (canAccess.value) void load()
})
</script>

<template>
  <section class="page-content enterprise-runtime">
    <PageHeader title="企业 Runtime" subtitle="任务编排、checkpoint、重试与人工接管（数据来自真实接口）"
                :tenant-name="tenancy.displayActiveName">
      <template #actions>
        <button class="btn ebtn" type="button" :disabled="state === 'loading'" @click="load()"><RotateCcw :size="14" /> 刷新</button>
      </template>
    </PageHeader>

    <AsyncState v-if="!canAccess" state="forbidden" message="无权限访问 Runtime（rag/审批/运维角色需具备 runtime 权限，后端权威 403）" />
    <template v-else>
      <AsyncState v-if="state === 'loading'" state="loading" />
      <AsyncState v-else-if="state === 'error'" state="error" :message="errorMsg" />
      <template v-else>
        <div v-if="!tasks.length" class="eempty eempty--runtime">
          <strong>暂无 Runtime 任务</strong>
          <span>任务创建后会在这里展示执行状态、checkpoint 和人工接管入口。</span>
        </div>
        <template v-else>
          <section class="runtime-summary" aria-label="任务状态统计">
            <div>
              <strong>任务状态</strong>
              <span>当前租户的编排任务</span>
            </div>
            <div class="esummary">
              <span v-for="(n, k) in counts" :key="k" class="esummary__item"><StatusBadge :status="k" :label="`${k} ${n}`" /></span>
            </div>
          </section>
          <div class="etable-wrap enterprise-runtime__table-wrap">
            <table class="data-table enterprise-runtime__table">
              <colgroup>
                <col class="runtime-col--task" />
                <col class="runtime-col--status" />
                <col class="runtime-col--step" />
                <col class="runtime-col--checkpoint" />
                <col class="runtime-col--retry" />
                <col class="runtime-col--approval" />
                <col class="runtime-col--actions" />
              </colgroup>
              <thead><tr><th>任务</th><th>状态</th><th>当前步骤</th><th>checkpoint</th><th>重试</th><th>审批</th><th>操作</th></tr></thead>
              <tbody>
                <tr v-for="t in tasks" :key="t.id">
                  <td><button class="elink" type="button" :aria-label="`查看任务 ${t.title || t.id}`" @click="selected = t">{{ t.title || t.id }}</button></td>
                  <td><StatusBadge :status="t.status" /></td>
                  <td>{{ t.current_step || '—' }}</td>
                  <td>v{{ t.checkpoint_version ?? 0 }}</td>
                  <td>{{ t.retry_count ?? 0 }}</td>
                  <td>{{ t.approval_state || '—' }}</td>
                  <td>
                    <div class="runtime-actions">
                      <template v-if="canTakeover && t.status === 'waiting_human'">
                        <button class="btn ebtn ebtn--sm" type="button" @click="confirmAction = { id: t.id, action: 'resume' }">resume</button>
                        <button class="btn ebtn ebtn--sm" type="button" @click="confirmAction = { id: t.id, action: 'retry' }">retry</button>
                        <button class="btn ebtn ebtn--sm ebtn--danger" type="button" @click="confirmAction = { id: t.id, action: 'cancel' }">cancel</button>
                      </template>
                      <span v-else class="emuted">—</span>
                    </div>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </template>
      </template>
    </template>

    <DetailDrawer :open="!!selected" title="Runtime 任务详情" @close="selected = null">
      <template v-if="selected">
        <dl class="edl">
          <div><dt>任务</dt><dd>{{ selected.id }}</dd></div>
          <div><dt>标题</dt><dd>{{ selected.title || '—' }}</dd></div>
          <div><dt>状态</dt><dd><StatusBadge :status="selected.status" /></dd></div>
          <div><dt>租户</dt><dd>{{ selected.tenant_id }}</dd></div>
          <div><dt>当前步骤</dt><dd>{{ selected.current_step || '—' }}</dd></div>
          <div><dt>已完成</dt><dd>{{ (selected.completed_steps || []).join(', ') || '—' }}</dd></div>
          <div><dt>checkpoint</dt><dd>v{{ selected.checkpoint_version ?? 0 }}</dd></div>
          <div><dt>重试</dt><dd>{{ selected.retry_count ?? 0 }}</dd></div>
          <div><dt>审批</dt><dd>{{ selected.approval_state || '—' }}</dd></div>
          <div v-if="selected.wait_human_reason"><dt>接管原因</dt><dd>{{ selected.wait_human_reason }}</dd></div>
        </dl>
        <h4 class="esec">步骤</h4>
        <table class="data-table">
          <thead><tr><th>步骤</th><th>状态</th><th>重试</th><th>失败原因</th></tr></thead>
          <tbody>
            <tr v-for="s in selected.steps || []" :key="s.name">
              <td>{{ s.name }}</td>
              <td><StatusBadge :status="s.status" size="sm" /></td>
              <td>{{ s.retry_count ?? 0 }}</td>
              <td class="emuted">{{ s.failure_reason || '—' }}</td>
            </tr>
          </tbody>
        </table>
      </template>
    </DetailDrawer>

    <ConfirmDialog :open="!!confirmAction"
                   title="人工接管"
                   :message="`对任务 ${confirmAction?.id} 执行 ${confirmAction?.action}？`"
                   :danger="confirmAction?.action === 'cancel'"
                   :loading="acting"
                   @confirm="confirmAction && doTakeover(confirmAction.id, confirmAction.action)"
                   @cancel="confirmAction = null" />
  </section>
</template>

<style scoped>
.eempty { display: grid; gap: 5px; min-height: 150px; align-content: center; padding: 24px; border: 1px dashed var(--theme-border-strong, #c8d6e6); border-radius: 8px; color: var(--text-soft, #758496); background: var(--theme-surface-subtle, #f7f9fc); }
.eempty strong { color: var(--theme-text, #1f2f46); font-size: 14px; }
.eempty span { max-width: 62ch; font-size: 12px; line-height: 18px; }
.eempty--runtime { margin-bottom: 2px; }
.runtime-summary { display: flex; align-items: center; justify-content: space-between; gap: 14px; margin-bottom: 12px; padding: 12px 14px; border: 1px solid var(--theme-border, #dbe3ec); border-radius: 8px; background: var(--theme-surface-subtle, #f7f9fc); }
.runtime-summary > div:first-child { display: grid; gap: 3px; min-width: 0; }
.runtime-summary strong { color: var(--theme-text, #1f2f46); font-size: 13px; }
.runtime-summary > div:first-child span { color: var(--text-soft, #758496); font-size: 11px; }
.esummary { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 8px; }
.etable-wrap { min-width: 0; overflow-x: auto; }
.enterprise-runtime__table-wrap { border: 1px solid var(--panel-border); border-radius: 8px; background: var(--panel-bg); }
.enterprise-runtime__table-wrap .data-table { min-width: 920px; table-layout: fixed; }
.enterprise-runtime__table th,
.enterprise-runtime__table td { overflow: hidden; text-overflow: ellipsis; }
.runtime-col--task { width: 25%; }
.runtime-col--status { width: 14%; }
.runtime-col--step { width: 15%; }
.runtime-col--checkpoint { width: 11%; }
.runtime-col--retry { width: 8%; }
.runtime-col--approval { width: 11%; }
.runtime-col--actions { width: 16%; }
.runtime-actions { display: flex; flex-wrap: wrap; gap: 4px; }
.elink { background: none; border: none; color: var(--brand, #2249d6); font: inherit; text-align: left; cursor: pointer; padding: 0; }
.elink:focus-visible { outline: 2px solid var(--brand, #2249d6); outline-offset: 2px; }
.ebtn { border: 1px solid var(--theme-border, #dbe3ec); background: var(--panel-bg, #fff); color: var(--theme-text, #1f2f46); }
.ebtn--sm { min-height: 28px; padding: 0 8px; font-size: 12px; }
.ebtn--danger { color: var(--danger, #b42318); border-color: var(--danger, #b42318); }
.ebtn:focus-visible { outline: 2px solid var(--brand, #2249d6); outline-offset: 1px; }
.emuted { color: var(--text-soft, #758496); }
.edl { display: grid; grid-template-columns: 100px 1fr; gap: 8px 12px; }
.edl dt { color: var(--text-soft, #758496); font-size: 12px; font-weight: 700; }
.edl dd { margin: 0; overflow-wrap: anywhere; }
.esec { margin: 14px 0 6px; font-size: 12px; color: var(--text-soft, #758496); text-transform: uppercase; }
@media (max-width: 680px) {
  .enterprise-runtime__table-wrap {
    margin-right: -2px;
    margin-left: -2px;
  }
  .enterprise-runtime .page-header__actions,
  .enterprise-runtime .ebtn {
    max-width: 100%;
  }
  .runtime-summary {
    align-items: flex-start;
    flex-direction: column;
  }
  .esummary {
    justify-content: flex-start;
  }
}
</style>
