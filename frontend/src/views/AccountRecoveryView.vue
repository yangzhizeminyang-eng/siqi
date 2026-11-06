<script setup lang="ts">
import { CheckCircle2, KeyRound, RefreshCw, ShieldAlert } from '@lucide/vue'
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { ApiError, apiRequest } from '../services/api'
import type { PasswordResetRequest } from '../types/auth'

const requests = ref<PasswordResetRequest[]>([])
const loading = ref(true)
const error = ref('')
const passwords = reactive<Record<string, string>>({})
const resetting = ref<string | null>(null)
const pendingRequests = computed(() => requests.value.filter((item) => item.status === 'pending'))

function formatTime(value: string) {
  return new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value))
}

async function loadRequests() {
  loading.value = true
  error.value = ''
  try {
    requests.value = await apiRequest<PasswordResetRequest[]>('/api/auth/password/requests')
  } catch (err) {
    error.value = err instanceof ApiError ? err.message : '暂时无法加载找回申请'
  } finally {
    loading.value = false
  }
}

async function resetPassword(item: PasswordResetRequest) {
  const password = passwords[item.id]?.trim() ?? ''
  if (password.length < 8) {
    ElMessage.warning('临时密码至少需要 8 位')
    return
  }
  resetting.value = item.id
  try {
    await apiRequest(`/api/auth/password/requests/${item.id}/reset`, {
      method: 'POST',
      body: JSON.stringify({ new_password: password }),
    })
    passwords[item.id] = ''
    ElMessage.success(`已重置 ${item.user_name} 的密码，请通过企业内部渠道告知临时密码`)
    await loadRequests()
  } catch (err) {
    ElMessage.error(err instanceof ApiError ? err.message : '密码重置失败')
  } finally {
    resetting.value = null
  }
}

onMounted(() => {
  void loadRequests()
})
</script>

<template>
  <section class="page-content account-recovery-page">
    <div class="page-notice page-notice--info">
      <span class="page-notice__icon"><ShieldAlert :size="17" /></span>
      <div class="page-notice__body">
        <strong>管理员负责普通账号恢复</strong>
        <p>员工提交申请后，由管理员在这里设置临时密码，再通过企业内部渠道告知员工。管理员忘记密码请回到登录页使用应急恢复码。</p>
      </div>
    </div>

    <div class="page-toolbar">
      <div>
        <p class="section-kicker">账号与权限</p>
        <h2 class="account-recovery-title">账号恢复</h2>
      </div>
      <button class="button button--secondary" type="button" :disabled="loading" @click="loadRequests"><RefreshCw :size="16" /> 刷新申请</button>
    </div>

    <div class="account-recovery-layout">
      <main class="panel recovery-request-panel">
        <header class="panel__header">
          <div><h2>找回申请</h2><p>{{ pendingRequests.length }} 项待处理，处理后仍保留记录。</p></div>
          <span class="outline-tag">{{ requests.length }} 项记录</span>
        </header>

        <div v-if="loading" class="recovery-empty">正在加载找回申请...</div>
        <div v-else-if="error" class="recovery-empty recovery-empty--error">{{ error }}</div>
        <div v-else-if="!requests.length" class="recovery-empty">
          <KeyRound :size="22" />
          <strong>暂时没有找回申请</strong>
          <p>员工从登录页提交申请后，会出现在这里。</p>
        </div>
        <div v-else class="recovery-request-list">
          <article v-for="item in requests" :key="item.id" class="recovery-request-item" :class="{ 'is-completed': item.status === 'completed' }">
            <div class="recovery-request-item__head">
              <div>
                <strong>{{ item.user_name }}</strong>
                <span>{{ item.email }} · {{ item.role_label }}</span>
              </div>
              <span class="status-tag" :class="item.status === 'pending' ? 'warning' : 'success'">
                <CheckCircle2 v-if="item.status === 'completed'" :size="13" />
                {{ item.status === 'pending' ? '待处理' : '已完成' }}
              </span>
            </div>
            <p class="recovery-request-item__time">申请时间：{{ formatTime(item.requested_at) }}</p>
            <div v-if="item.status === 'pending'" class="recovery-request-item__actions">
              <input v-model="passwords[item.id]" type="password" autocomplete="new-password" placeholder="设置临时密码（至少 8 位）" :disabled="resetting === item.id" />
              <button class="button button--primary" type="button" :disabled="resetting === item.id" @click="resetPassword(item)">
                <KeyRound :size="15" /> {{ resetting === item.id ? '处理中...' : '重置密码' }}
              </button>
            </div>
            <p v-else class="recovery-request-item__handled">处理人：{{ item.handled_by ?? '管理员' }} · {{ item.handled_at ? formatTime(item.handled_at) : '已完成' }}</p>
          </article>
        </div>
      </main>

      <aside class="panel recovery-responsibility-panel">
        <header class="panel__header"><div><h2>责任边界</h2><p>按账号类型选择恢复路径。</p></div></header>
        <div class="recovery-responsibility-list">
          <div><span class="recovery-responsibility-list__icon"><KeyRound :size="16" /></span><div><strong>普通员工</strong><p>员工提交申请，管理员设置临时密码并通知。</p></div></div>
          <div><span class="recovery-responsibility-list__icon"><ShieldAlert :size="16" /></span><div><strong>管理员</strong><p>管理员无法登录时，由部署负责人提供应急恢复码。</p></div></div>
        </div>
      </aside>
    </div>
  </section>
</template>
