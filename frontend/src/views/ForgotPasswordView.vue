<script setup lang="ts">
import { ArrowLeft, CheckCircle2, KeyRound, LifeBuoy, ShieldCheck } from '@lucide/vue'
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { RouterLink, useRouter } from 'vue-router'
import { ApiError, apiRequest } from '../services/api'
import ThemeSwitcher from '../components/ThemeSwitcher.vue'

type RecoveryMode = 'employee' | 'admin'

const router = useRouter()
const mode = ref<RecoveryMode>('employee')
const email = ref('')
const account = ref('')
const recoveryCode = ref('')
const newPassword = ref('')
const submitted = ref(false)
const submitting = ref(false)
const error = ref('')

const title = computed(() => (mode.value === 'employee' ? '提交密码找回申请' : '管理员应急恢复'))
const subtitle = computed(() =>
  mode.value === 'employee'
    ? '员工本人提交企业邮箱后，由企业管理员在账号恢复页处理。'
    : '管理员无法登录时，由部署负责人或平台安全联系人提供应急恢复码。',
)
const canSubmit = computed(() => {
  if (submitting.value) return false
  return mode.value === 'employee'
    ? Boolean(email.value.trim())
    : Boolean(account.value.trim() && recoveryCode.value && newPassword.value.length >= 8)
})

function switchMode(nextMode: RecoveryMode) {
  mode.value = nextMode
  error.value = ''
  submitted.value = false
}

async function submit() {
  if (!canSubmit.value) return
  submitting.value = true
  error.value = ''
  try {
    if (mode.value === 'employee') {
      await apiRequest('/api/auth/password/forgot', {
        method: 'POST',
        body: JSON.stringify({ email: email.value.trim() }),
      })
      submitted.value = true
      return
    }
    await apiRequest('/api/auth/password/emergency-reset', {
      method: 'POST',
      body: JSON.stringify({
        account: account.value.trim(),
        recovery_code: recoveryCode.value,
        new_password: newPassword.value,
      }),
    })
    submitted.value = true
    ElMessage.success('管理员密码已恢复')
  } catch (err) {
    error.value = err instanceof ApiError ? err.message : '请求失败，请稍后重试'
  } finally {
    submitting.value = false
  }
}

function returnToLogin() {
  void router.push('/login')
}
</script>

<template>
  <main class="login-page recovery-page">
    <section class="login-aside">
      <div class="login-brand">
        <span class="brand__mark brand__mark--logo"><img src="/favicon.svg" alt="" aria-hidden="true" /></span>
        <span>司契 Siqi</span>
      </div>
      <div class="login-aside__content">
        <p class="login-aside__eyebrow">账号恢复</p>
        <h2>每个账号，都有明确的恢复路径。</h2>
        <div class="recovery-route-list">
          <div><span class="recovery-route-list__icon"><LifeBuoy :size="17" /></span><span><strong>普通账号</strong><small>提交邮箱，等待管理员处理</small></span></div>
          <div><span class="recovery-route-list__icon"><ShieldCheck :size="17" /></span><span><strong>管理员账号</strong><small>使用部署时配置的应急恢复码</small></span></div>
        </div>
      </div>
    </section>

    <section class="login-form-wrap">
      <form class="login-form login-card recovery-card" @submit.prevent="submit">
        <header class="login-form__header">
          <div>
            <RouterLink to="/login" class="text-link recovery-back"><ArrowLeft :size="15" /> 返回登录</RouterLink>
            <h2>{{ title }}</h2>
            <p class="form-subtitle">{{ subtitle }}</p>
          </div>
          <ThemeSwitcher />
        </header>

        <div class="recovery-tabs" role="tablist" aria-label="密码恢复方式">
          <button type="button" :class="{ 'is-active': mode === 'employee' }" @click="switchMode('employee')">普通账号</button>
          <button type="button" :class="{ 'is-active': mode === 'admin' }" @click="switchMode('admin')">管理员应急恢复</button>
        </div>

        <div v-if="submitted" class="page-notice page-notice--success">
          <span class="page-notice__icon"><CheckCircle2 :size="17" /></span>
          <div class="page-notice__body">
            <strong>{{ mode === 'employee' ? '申请已提交' : '密码已重置' }}</strong>
            <p>{{ mode === 'employee' ? '请联系企业管理员确认处理进度。管理员账号请切换到应急恢复流程。' : '请返回登录页使用新密码登录。' }}</p>
          </div>
        </div>

        <template v-else-if="mode === 'employee'">
          <label>
            <span>企业邮箱</span>
            <input v-model="email" type="email" autocomplete="email" placeholder="name@company.com" :disabled="submitting" />
          </label>
          <div class="recovery-explainer">
            <KeyRound :size="17" />
            <p>系统不会自动发送邮件。申请会进入管理员的“账号恢复”待办，由管理员重置后通过企业内部渠道告知临时密码。</p>
          </div>
        </template>

        <template v-else>
          <label>
            <span>管理员账号</span>
            <input v-model="account" type="email" autocomplete="username" placeholder="admin@company.com" :disabled="submitting" />
          </label>
          <label>
            <span>应急恢复码</span>
            <input v-model="recoveryCode" type="password" autocomplete="one-time-code" placeholder="请输入部署负责人提供的恢复码" :disabled="submitting" />
          </label>
          <label>
            <span>设置新密码</span>
            <input v-model="newPassword" type="password" autocomplete="new-password" placeholder="至少 8 位字符" :disabled="submitting" />
          </label>
          <div class="recovery-explainer">
            <ShieldCheck :size="17" />
            <p>应急恢复码只应由部署负责人或平台安全联系人保管。生产环境请通过环境变量 `AUTH_RECOVERY_CODE` 配置并定期轮换。</p>
          </div>
        </template>

        <p v-if="error" class="field-error" role="alert">{{ error }}</p>
        <button v-if="!submitted" class="button button--primary button--wide" type="submit" :disabled="!canSubmit">
          {{ submitting ? '处理中...' : mode === 'employee' ? '提交找回申请' : '重置管理员密码' }}
        </button>
        <button v-else class="button button--primary button--wide" type="button" @click="returnToLogin">返回登录</button>
      </form>
    </section>
  </main>
</template>
