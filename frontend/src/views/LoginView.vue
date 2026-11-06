<script setup lang="ts">
import { ArrowRight, Eye, EyeOff } from '@lucide/vue'
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { useRoute, useRouter } from 'vue-router'
import ThemeSwitcher from '../components/ThemeSwitcher.vue'
import { useAuthStore } from '../stores/auth'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const account = ref('')
const password = ref('')
const rememberMe = ref(false)
const showPassword = ref(false)
const submitting = computed(() => auth.loading)
const error = computed(() => auth.error)
const primaryAction = computed(() => (submitting.value ? '登录中...' : '进入工作台'))
const canSubmit = computed(() => Boolean(account.value.trim() && password.value && !submitting.value))

async function login() {
  if (!canSubmit.value) return
  try {
    const data = await auth.login(account.value.trim(), password.value, rememberMe.value)
    const redirect = typeof route.query.redirect === 'string' ? route.query.redirect : '/dashboard'
    await router.push(redirect)
    ElMessage.success(`欢迎回来，${data.user.name}`)
  } catch {
    return
  }
}

function togglePasswordVisibility() {
  showPassword.value = !showPassword.value
}
</script>

<template>
  <main class="login-page">
    <section class="login-aside">
      <div class="login-brand">
        <span class="brand__mark brand__mark--logo"><img src="/favicon.svg" alt="" aria-hidden="true" /></span>
        <span>司契 Siqi</span>
      </div>

      <div class="login-aside__content">
        <p class="login-aside__eyebrow">AI Agent 研发控制台</p>
        <h2>让每次变更，都先过审，再落地。</h2>
        <p class="login-aside__lead">从 Issue 到回放，一条可审计的流水线。</p>

        <ol class="login-aside__pipeline" aria-label="产品流程">
          <li class="pipeline-node is-done"><i></i><span>Issue</span></li>
          <li class="pipeline-node is-done"><i></i><span>分析</span></li>
          <li class="pipeline-node is-active"><i></i><span>审批</span></li>
          <li class="pipeline-node"><i></i><span>Diff</span></li>
          <li class="pipeline-node"><i></i><span>回放</span></li>
        </ol>
      </div>
    </section>

    <section class="login-form-wrap">
      <form class="login-form login-card" @submit.prevent="login">
        <header class="login-form__header">
          <div>
            <h2>登录工作台</h2>
          </div>
          <div class="login-form__header-tools">
            <ThemeSwitcher />
          </div>
        </header>
        <label>
          <span>账号</span>
          <input v-model="account" type="email" autocomplete="email" placeholder="name@company.com" autofocus :disabled="submitting" />
          <small class="login-account-hint">使用企业邮箱登录，例如 admin@company.com</small>
        </label>
        <label class="login-password-field">
          <span>
            <span>密码</span>
            <button
              class="password-toggle"
              type="button"
              :aria-pressed="showPassword"
              :aria-label="showPassword ? '隐藏密码' : '显示密码'"
              @click="togglePasswordVisibility"
            >
              <component :is="showPassword ? EyeOff : Eye" :size="15" />
              <span>{{ showPassword ? '隐藏' : '显示' }}</span>
            </button>
          </span>
          <input
            v-model="password"
            :type="showPassword ? 'text' : 'password'"
            autocomplete="current-password"
            placeholder="请输入密码"
            :disabled="submitting"
          />
        </label>

        <div class="form-row">
          <label class="checkbox"><input v-model="rememberMe" type="checkbox" :disabled="submitting" /> <span>保持登录</span></label>
          <RouterLink class="text-link" to="/forgot-password">忘记密码</RouterLink>
        </div>

        <p v-if="error" class="field-error" role="alert" aria-live="assertive">
          {{ error }}
        </p>

        <button class="button button--primary button--wide login-submit" :class="{ 'is-loading': submitting }" type="submit" :disabled="!canSubmit" :aria-busy="submitting">
          <span v-if="submitting" class="button__spinner" aria-hidden="true" />
          <span>{{ primaryAction }}</span>
          <ArrowRight v-if="!submitting" :size="17" />
        </button>

        <footer class="login-legal">
          <RouterLink to="/terms">服务协议</RouterLink>
          <RouterLink to="/privacy">隐私政策</RouterLink>
        </footer>
      </form>
    </section>
  </main>
</template>
