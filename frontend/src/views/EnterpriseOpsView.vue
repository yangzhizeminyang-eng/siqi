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

const auth = useAuthStore()
const tenancy = useTenancyStore()
const canAccess = computed(() => hasAccessRequirement(auth, tenancy, ['project:manage', 'org:manage']))

interface OpsStatus {
  tenant_id: string; app_env: string; migration: string
  rag: Record<string, unknown>; milvus: { use_milvus?: boolean; collection_prefix?: string; host?: string }
  counts: { repositories?: number; indexes?: number }
  dependencies: Record<string, { status?: string }>
}
interface Repo { id: string; name: string; url?: string; risk_threshold?: string }

const state = ref<'loading' | 'error' | 'ready'>('loading')
const errorMsg = ref('')
const status = ref<OpsStatus | null>(null)
const repos = ref<Repo[]>([])

const healthyDependencies = computed(() => {
  const dependencies = Object.values(status.value?.dependencies ?? {})
  return dependencies.filter(dep => ['ok', 'healthy', 'ready', 'active'].includes(String(dep.status).toLowerCase())).length
})

function dependencyLabel(value?: string): string {
  const labels: Record<string, string> = {
    ok: '正常',
    healthy: '健康',
    ready: '就绪',
    active: '运行中',
    degraded: '降级',
    failed: '异常',
    unknown: '未知',
  }
  return labels[String(value ?? 'unknown').toLowerCase()] || value || '未知'
}

async function load() {
  state.value = 'loading'; errorMsg.value = ''
  try {
    const [st, rp] = await Promise.all([
      apiRequest<OpsStatus>('/api/enterprise/ops/status'),
      apiRequest<Repo[]>('/api/repositories').catch(() => [] as Repo[]),
    ])
    status.value = st
    repos.value = rp ?? []
    state.value = 'ready'
  } catch (err) {
    state.value = 'error'; errorMsg.value = err instanceof Error ? err.message : '加载失败'
  }
}

onMounted(() => { if (canAccess.value) void load() })
</script>

<template>
  <section class="page-content enterprise-ops">
    <PageHeader title="企业设置 / 运维" subtitle="服务健康、索引与配置摘要（非敏感，数据来自真实接口）" :tenant-name="tenancy.displayActiveName">
      <template #actions>
        <button class="btn ebtn" type="button" :disabled="state === 'loading'" @click="load()"><RotateCcw :size="14" /> 刷新</button>
      </template>
    </PageHeader>

    <AsyncState v-if="!canAccess" state="forbidden" message="无权限访问运维（需要 project:manage 或 org:manage；后端权威 403）" />
    <template v-else>
      <AsyncState v-if="state === 'loading'" state="loading" />
      <AsyncState v-else-if="state === 'error'" state="error" :message="errorMsg" />
      <template v-else-if="status">
        <div class="ogrid">
          <section class="panel ocard">
            <header class="panel__header">
              <div>
                <h2 class="ocard__title">服务健康</h2>
                <p class="ocard__subtitle">依赖服务的实时状态摘要</p>
              </div>
              <span class="ocard__count">{{ healthyDependencies }}/{{ Object.keys(status.dependencies).length || 0 }}</span>
            </header>
            <ul v-if="Object.keys(status.dependencies).length" class="odeps">
              <li v-for="(dep, k) in status.dependencies" :key="k">
                <span class="odeps__name">{{ k }}</span>
                <StatusBadge :status="dep.status ?? 'unknown'" :label="dependencyLabel(dep.status)" size="sm" />
              </li>
            </ul>
            <div v-else class="oempty">暂无依赖信息</div>
          </section>

          <section class="panel ocard">
            <header class="panel__header">
              <div>
                <h2 class="ocard__title">配置摘要</h2>
                <p class="ocard__subtitle">当前环境与检索链路配置</p>
              </div>
            </header>
            <div class="oconfig-grid">
              <div class="oconfig-item"><span>环境</span><strong>{{ status.app_env }}</strong></div>
              <div class="oconfig-item"><span>迁移</span><strong class="oconfig-item__mono">{{ status.migration }}</strong></div>
              <div class="oconfig-item"><span>检索模式</span><strong>{{ String(status.rag.retrieval_mode) }}</strong></div>
              <div class="oconfig-item"><span>符号 boost</span><strong>{{ String(status.rag.symbol_boost) }}</strong></div>
              <div class="oconfig-item"><span>Rerank</span><strong>{{ String(status.rag.rerank_provider) }} · {{ status.rag.rerank_enabled ? '已启用' : '已关闭' }}</strong></div>
              <div class="oconfig-item"><span>Cross-Encoder</span><strong>{{ status.rag.cross_encoder_enabled ? '已启用' : '已关闭' }}</strong></div>
              <div class="oconfig-item oconfig-item--wide"><span>Milvus</span><strong>{{ status.milvus.use_milvus ? `${status.milvus.collection_prefix} @ ${status.milvus.host}` : '未启用' }}</strong></div>
              <div class="oconfig-item"><span>仓库 / 索引</span><strong>{{ status.counts.repositories ?? 0 }} / {{ status.counts.indexes ?? 0 }}</strong></div>
            </div>
          </section>

          <section class="panel ocard">
            <header class="panel__header">
              <div>
                <h2 class="ocard__title">仓库索引</h2>
                <p class="ocard__subtitle">最多展示当前租户的 12 个仓库</p>
              </div>
              <span class="ocard__count">{{ repos.length }}</span>
            </header>
            <div v-if="!repos.length" class="oempty">暂无仓库</div>
            <div v-else class="table-wrap enterprise-ops__table-wrap">
              <table class="data-table enterprise-ops__table">
                <thead><tr><th>仓库</th><th>风险门槛</th></tr></thead>
                <tbody>
                  <tr v-for="r in repos.slice(0, 12)" :key="r.id">
                    <td><strong>{{ r.name || r.id }}</strong><small>{{ r.id }}</small></td>
                    <td><StatusBadge :status="String(r.risk_threshold || 'L2')" size="sm" /></td>
                  </tr>
                </tbody>
              </table>
            </div>
          </section>
        </div>
      </template>
    </template>
  </section>
</template>

<style scoped>
.ebtn { border: 1px solid var(--theme-border, #dbe3ec); background: var(--panel-bg, #fff); color: var(--theme-text, #1f2f46); }
.ebtn:focus-visible { outline: 2px solid var(--brand, #2249d6); outline-offset: 1px; }
.ogrid { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(320px, 100%), 1fr)); gap: 16px; }
.ocard { min-width: 0; padding: 0 16px 16px; overflow: hidden; }
.ocard .panel__header { margin: 0 -16px 14px; padding: 14px 16px 12px; }
.ocard__title { margin: 0; font-size: 14px; font-weight: 800; }
.ocard__subtitle { margin-top: 4px; color: var(--text-soft, #758496); font-size: 11px; line-height: 16px; }
.ocard__count { display: inline-flex; align-items: center; justify-content: center; min-width: 38px; height: 24px; padding: 0 7px; border: 1px solid var(--theme-border, #dbe3ec); border-radius: 6px; color: var(--theme-text-muted, #65758a); background: var(--theme-surface-muted, #f1f5f9); font-size: 11px; font-weight: 800; font-variant-numeric: tabular-nums; }
.odeps { list-style: none; margin: 0; padding: 0; display: grid; gap: 8px; }
.odeps li { display: flex; justify-content: space-between; align-items: center; gap: 12px; min-height: 30px; padding-bottom: 7px; border-bottom: 1px solid var(--theme-border-subtle, #e9eef4); font-size: 13px; }
.odeps li:last-child { padding-bottom: 0; border-bottom: 0; }
.odeps__name { min-width: 0; overflow: hidden; color: var(--theme-text, #1f2f46); text-overflow: ellipsis; white-space: nowrap; }
.oempty { display: grid; min-height: 92px; align-content: center; color: var(--text-soft, #758496); font-size: 12px; }
.oconfig-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }
.oconfig-item { display: grid; gap: 4px; min-width: 0; padding: 9px 10px; border: 1px solid var(--theme-border-subtle, #e9eef4); border-radius: 7px; background: var(--theme-surface-subtle, #f7f9fc); }
.oconfig-item--wide { grid-column: 1 / -1; }
.oconfig-item span { color: var(--text-soft, #758496); font-size: 10px; font-weight: 700; }
.oconfig-item strong { min-width: 0; overflow-wrap: anywhere; color: var(--theme-text, #1f2f46); font-size: 12px; line-height: 17px; }
.oconfig-item__mono { font-family: ui-monospace, monospace; font-size: 11px !important; }
.emuted { color: var(--text-soft, #758496); font-size: 12px; }
.enterprise-ops__table-wrap { overflow-x: auto; border: 1px solid var(--theme-border-subtle, #e9eef4); border-radius: 7px; }
.enterprise-ops__table { min-width: 420px; table-layout: fixed; }
.enterprise-ops__table th:first-child { width: 70%; }
.enterprise-ops__table td { overflow: hidden; text-overflow: ellipsis; }
.enterprise-ops__table td strong, .enterprise-ops__table td small { display: block; }
.enterprise-ops__table td strong { color: var(--theme-text, #1f2f46); }
.enterprise-ops__table td small { margin-top: 3px; color: var(--text-soft, #758496); font-family: ui-monospace, monospace; font-size: 10px; overflow: hidden; text-overflow: ellipsis; }
@media (max-width: 680px) {
  .enterprise-ops .eheader__side {
    width: 100%;
  }
  .enterprise-ops .ebtn {
    width: 100%;
  }
  .odl {
    grid-template-columns: minmax(88px, 35%) minmax(0, 1fr);
  }
  .oconfig-grid {
    grid-template-columns: 1fr;
  }
  .oconfig-item--wide {
    grid-column: auto;
  }
}
</style>
