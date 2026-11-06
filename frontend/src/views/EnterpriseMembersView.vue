<script setup lang="ts">
// 企业版 E1-01 成员管理：展示当前租户成员，企业管理员可改启用态与角色。
// 仅展示权限；最终授权由后端 require_tenant_permission 完成，前端不伪造授权。
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { useTenancyStore } from '../stores/tenancy'
import { ENTERPRISE_ROLE_LABELS, type EnterpriseMember, type EnterpriseRole } from '../types/tenancy'
import PageHeader from '../components/enterprise/PageHeader.vue'
import AsyncState from '../components/enterprise/AsyncState.vue'
import StatusBadge from '../components/enterprise/StatusBadge.vue'
import { CircleAlert, ChevronDown, RefreshCw, ShieldCheck, UserCheck, UsersRound } from '@lucide/vue'

const tenancy = useTenancyStore()
const loading = ref(false)
const error = ref('')

const canManage = computed(() => tenancy.canManageMembers)
const members = computed(() => tenancy.members)
const activeMemberCount = computed(() => members.value.filter((member) => member.status === 'active').length)
const inactiveMemberCount = computed(() => members.value.length - activeMemberCount.value)

const roleOptions = Object.entries(ENTERPRISE_ROLE_LABELS) as Array<[EnterpriseRole, string]>

async function load() {
  loading.value = true
  error.value = ''
  try {
    await tenancy.loadContext()
    await tenancy.loadMembers()
  } catch (err) {
    error.value = err instanceof Error ? err.message : '加载成员失败'
  } finally {
    loading.value = false
  }
}

async function toggleEnabled(member: EnterpriseMember, enabled: boolean) {
  try {
    const updated = await tenancy.updateMember(member.id, { enabled })
    ElMessage.success(updated.status === 'active' ? `已启用 ${updated.user_name}` : `已禁用 ${updated.user_name}`)
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '更新启用状态失败')
    void load()
  }
}

async function changeRole(member: EnterpriseMember, role: EnterpriseRole) {
  if (role === member.role) return
  try {
    await tenancy.updateMember(member.id, { role })
    ElMessage.success(`已更新 ${member.user_name} 角色`)
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '更新角色失败')
    void load()
  }
}

function formatStatus(status: string) {
  return { active: '已启用', suspended: '已禁用', invited: '邀请中', removed: '已移除' }[status] ?? status
}

function formatStatusHint(status: string) {
  return {
    active: '可访问工作台',
    suspended: '暂不可访问',
    invited: '等待接受邀请',
    removed: '已从租户移除',
  }[status] ?? '状态待确认'
}

onMounted(() => {
  void load()
})
</script>

<template>
  <section class="page-content enterprise-members">
    <PageHeader
      title="成员管理"
      :subtitle="canManage ? '维护当前租户的成员角色与启用状态。变更由服务端校验并记录审计。' : '当前账号可查看成员，但没有修改成员的权限。'"
      :tenant-name="tenancy.displayActiveName"
    >
      <template #actions>
        <button
          class="button button--secondary enterprise-members__refresh"
          type="button"
          :disabled="loading"
          aria-label="重新加载成员"
          @click="load"
        >
          <RefreshCw :size="15" :class="{ 'is-spinning': loading }" />
          {{ loading ? '同步中' : '重新加载' }}
        </button>
      </template>
    </PageHeader>

    <AsyncState v-if="loading" state="loading" message="正在读取成员目录" />
    <AsyncState v-else-if="error" state="error" :message="error" />
    <AsyncState
      v-else-if="!members.length"
      state="empty"
      :message="`当前租户 ${tenancy.displayActiveName} 暂无成员`"
    />
    <template v-else>
      <section class="member-overview" aria-label="成员统计">
        <article class="member-stat">
          <span class="member-stat__icon member-stat__icon--blue" aria-hidden="true"><UsersRound :size="16" /></span>
          <span class="member-stat__copy">
            <strong>{{ members.length }}</strong>
            <small>成员总数</small>
          </span>
          <span class="member-stat__note">当前租户</span>
        </article>
        <article class="member-stat">
          <span class="member-stat__icon member-stat__icon--green" aria-hidden="true"><UserCheck :size="16" /></span>
          <span class="member-stat__copy">
            <strong>{{ activeMemberCount }}</strong>
            <small>已启用</small>
          </span>
          <span class="member-stat__note">可访问</span>
        </article>
        <article class="member-stat">
          <span class="member-stat__icon member-stat__icon--amber" aria-hidden="true"><CircleAlert :size="16" /></span>
          <span class="member-stat__copy">
            <strong>{{ inactiveMemberCount }}</strong>
            <small>非活跃成员</small>
          </span>
          <span class="member-stat__note">需关注</span>
        </article>
      </section>

      <section class="panel enterprise-members__panel" aria-label="租户成员目录">
      <header class="panel__header enterprise-members__panel-head">
        <div class="enterprise-members__panel-copy">
          <span class="enterprise-members__section-label"><UsersRound :size="14" />租户成员目录</span>
          <h2>成员目录</h2>
          <p>{{ members.length }} 位成员 · {{ canManage ? '可编辑角色与启用状态' : '当前为只读查看' }}</p>
        </div>
        <span class="outline-tag enterprise-members__permission">
          <ShieldCheck :size="13" />
          {{ canManage ? '管理权限' : '只读权限' }}
        </span>
      </header>
      <div class="table-wrap enterprise-members__table-wrap">
        <table class="data-table member-table">
          <caption class="visually-hidden">当前租户成员、角色、状态和启用设置</caption>
          <colgroup>
            <col class="member-table__col-member" />
            <col class="member-table__col-role" />
            <col class="member-table__col-status" />
            <col class="member-table__col-enabled" />
          </colgroup>
          <thead>
            <tr>
              <th scope="col">成员身份</th>
              <th scope="col">角色</th>
              <th scope="col">访问状态</th>
              <th scope="col">启用</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="member in members"
              :key="member.id"
              :class="{ 'is-inactive': member.status !== 'active' }"
            >
              <td class="member-table__user">
                <span class="member-table__avatar" aria-hidden="true">{{ member.user_name?.slice(0, 2) || '--' }}</span>
                <span class="member-table__identity">
                  <strong>{{ member.user_name || member.user_id }}</strong>
                  <span class="member-table__identity-meta">
                    <small>账号 ID</small>
                    <small class="mono">{{ member.user_id }}</small>
                  </span>
                </span>
              </td>
              <td>
                <div class="member-role-field">
                  <select
                    :value="member.role"
                    :disabled="!canManage || member.is_active === false"
                    aria-label="角色"
                    @change="changeRole(member, ($event.target as HTMLSelectElement).value as EnterpriseRole)"
                  >
                    <option v-for="[value, label] in roleOptions" :key="value" :value="value">{{ label }}</option>
                  </select>
                  <ChevronDown :size="14" aria-hidden="true" />
                </div>
              </td>
              <td>
                <div class="member-table__status">
                  <StatusBadge :status="member.status" :label="formatStatus(member.status)" size="sm" />
                  <small>{{ formatStatusHint(member.status) }}</small>
                </div>
              </td>
              <td class="member-table__toggle-cell">
                <button
                  type="button"
                  role="switch"
                  :aria-checked="member.status === 'active'"
                  :aria-label="`${member.user_name || member.user_id}启用状态`"
                  :disabled="!canManage || (member.status === 'active' && tenancy.tenants.length <= 1)"
                  class="member-switch"
                  :class="{ 'is-on': member.status === 'active' }"
                  @click="toggleEnabled(member, member.status !== 'active')"
                >
                  <span aria-hidden="true" />
                </button>
                <small class="member-table__toggle-label">{{ member.status === 'active' ? '已启用' : '未启用' }}</small>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <footer class="member-table__footer">
        <span><ShieldCheck :size="14" aria-hidden="true" />角色与启用状态变更会由服务端校验并记录审计。</span>
        <span v-if="!canManage">当前账号仅具备成员查看权限。</span>
      </footer>
      </section>
    </template>
  </section>
</template>

<style scoped>
.enterprise-members {
  min-width: 0;
}
.member-overview {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
  margin-bottom: 14px;
}
.member-stat {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
  min-height: 72px;
  padding: 13px 14px;
  border: 1px solid var(--panel-border);
  border-radius: 10px;
  background: var(--panel-bg);
  box-shadow: var(--shadow-sm);
}
.member-stat__icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  flex: none;
  border-radius: 8px;
}
.member-stat__icon--blue {
  color: var(--brand-strong);
  background: var(--brand-soft);
}
.member-stat__icon--green {
  color: var(--success);
  background: color-mix(in srgb, var(--success) 12%, var(--panel-bg));
}
.member-stat__icon--amber {
  color: var(--warning);
  background: color-mix(in srgb, var(--warning) 13%, var(--panel-bg));
}
.member-stat__copy {
  display: grid;
  gap: 2px;
  min-width: 0;
}
.member-stat__copy strong {
  color: var(--text-strong);
  font-size: 20px;
  line-height: 23px;
  font-variant-numeric: tabular-nums;
}
.member-stat__copy small,
.member-stat__note {
  color: var(--text-soft);
  font-size: 11px;
  line-height: 16px;
}
.member-stat__note {
  margin-left: auto;
  align-self: flex-end;
  white-space: nowrap;
}
.enterprise-members__panel {
  overflow: hidden;
}
.enterprise-members__panel-head {
  align-items: center;
  padding: 16px 18px 15px;
}
.enterprise-members__panel-copy {
  min-width: 0;
}
.enterprise-members__section-label {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 7px;
  color: var(--brand-strong);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: .02em;
}
.enterprise-members__panel-head h2 {
  color: var(--text-strong);
  font-size: 16px;
  line-height: 21px;
}
.enterprise-members__panel-head p {
  margin-top: 4px;
  color: var(--text-soft);
  line-height: 17px;
}
.enterprise-members__permission {
  gap: 5px;
  flex: none;
  color: var(--brand-strong);
  border-color: var(--panel-border);
  background: var(--brand-soft);
}
.enterprise-members__permission svg {
  flex: none;
}
.enterprise-members__table-wrap {
  overflow-x: auto;
}
.member-table {
  min-width: 760px;
  table-layout: fixed;
}
.member-table__col-member { width: 40%; }
.member-table__col-role { width: 25%; }
.member-table__col-status { width: 21%; }
.member-table__col-enabled { width: 14%; }
.member-table th,
.member-table td {
  vertical-align: middle;
}
.member-table th {
  height: 42px;
  padding: 0 18px;
  color: var(--text-soft);
  background: var(--shell-bg);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: .02em;
}
.member-table td {
  height: 68px;
  padding: 11px 18px;
}
.member-table tbody tr {
  transition: background .16s ease, box-shadow .16s ease;
}
.member-table tbody tr:hover {
  background: var(--brand-soft);
}
.member-table tbody tr.is-inactive {
  background: color-mix(in srgb, var(--shell-bg) 72%, var(--panel-bg));
}
.member-table tbody tr.is-inactive:hover {
  background: var(--brand-soft);
}
.member-table__user {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}
.member-table__identity {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}
.member-table__identity strong {
  overflow: hidden;
  color: var(--text-strong);
  font-size: 13px;
  line-height: 18px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.member-table__identity-meta {
  display: flex;
  align-items: center;
  gap: 5px;
  min-width: 0;
  color: var(--text-soft);
}
.member-table__identity-meta small {
  overflow: hidden;
  font-size: 12px;
  line-height: 16px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.member-table__identity-meta small:first-child {
  flex: none;
  color: var(--text-faint, var(--text-soft));
  font-size: 10px;
  font-weight: 700;
  letter-spacing: .02em;
}
.member-table__avatar {
  width: 34px;
  height: 34px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: none;
  border-radius: 9px;
  color: var(--brand-strong);
  background: var(--brand-soft);
  font-size: 13px;
  font-weight: 700;
}
.member-role-field {
  position: relative;
  width: min(100%, 170px);
}
.member-role-field select {
  min-width: 0;
  padding-right: 30px;
  font-size: 13px;
  color: var(--text-base);
  background: var(--panel-bg);
  cursor: pointer;
  appearance: none;
}
.member-role-field svg {
  position: absolute;
  top: 50%;
  right: 10px;
  color: var(--text-soft);
  pointer-events: none;
  transform: translateY(-50%);
}
.member-role-field select:hover:not(:disabled) {
  border-color: var(--theme-border-hover, #cddbf0);
  background: var(--theme-surface-hover, #f4f8ff);
}
.member-role-field select:focus-visible {
  border-color: var(--brand);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--brand) 16%, transparent);
}
.member-role-field select:disabled {
  color: var(--text-soft);
  background: var(--shell-bg);
  cursor: not-allowed;
}
.member-table__status {
  display: grid;
  gap: 3px;
  justify-items: start;
}
.member-table__status small {
  color: var(--text-soft);
  font-size: 10px;
  line-height: 14px;
}
.member-table__toggle-cell {
  white-space: nowrap;
}
.member-table__toggle-label {
  margin-left: 8px;
  color: var(--text-soft);
  font-size: 11px;
  vertical-align: middle;
}
.member-switch {
  position: relative;
  display: inline-flex;
  align-items: center;
  width: 42px;
  height: 24px;
  padding: 0;
  border-radius: 999px;
  border: 1px solid var(--panel-border);
  background: var(--shell-bg);
  cursor: pointer;
  transition: background .16s ease, border-color .16s ease, box-shadow .16s ease;
}
.member-switch span {
  position: absolute;
  top: 3px;
  left: 3px;
  width: 16px;
  height: 16px;
  border-radius: 50%;
  background: var(--panel-bg);
  transition: transform .16s ease, background .16s ease;
  box-shadow: 0 1px 3px rgba(26, 43, 68, .18);
}
.member-switch.is-on {
  border-color: var(--brand);
  background: var(--brand);
  box-shadow: 0 3px 8px color-mix(in srgb, var(--brand) 22%, transparent);
}
.member-switch.is-on span {
  transform: translateX(18px);
}
.member-switch:hover:not(:disabled) {
  border-color: var(--brand);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--brand) 12%, transparent);
}
.member-switch:focus-visible {
  outline: 0;
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--brand) 20%, transparent);
}
.member-switch:disabled {
  opacity: .55;
}
.member-table__footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  min-height: 43px;
  padding: 10px 18px;
  border-top: 1px solid var(--panel-border-soft);
  color: var(--text-soft);
  background: var(--shell-bg);
  font-size: 11px;
  line-height: 16px;
}
.member-table__footer span {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}
.member-table__footer svg {
  flex: none;
  color: var(--success);
}
.enterprise-members__refresh {
  flex: none;
}
@media (max-width: 900px) {
  .member-table__footer {
    align-items: flex-start;
    flex-direction: column;
  }
}
@media (max-width: 680px) {
  .member-overview {
    grid-template-columns: 1fr;
  }
  .enterprise-members__refresh {
    width: 100%;
  }
  .enterprise-members__panel-head {
    align-items: flex-start;
    flex-direction: column;
  }
  .enterprise-members__permission {
    align-self: flex-start;
  }
  .member-stat {
    min-height: 64px;
  }
  .member-table th,
  .member-table td {
    padding-right: 14px;
    padding-left: 14px;
  }
}
</style>
