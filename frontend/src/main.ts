// 文件级注释：前端入口。
// 启动顺序：清理旧 session 残留 → 初始化主题 → 创建 Vue/Pinia/Router →
// 挂载应用 → 等待 router 完成首屏导航 → 最后再做 auth.bootstrap()。
// 这样可以避免刷新页面时，路由守卫在 auth.ready=false 的瞬间把用户踢回 /login。
import { createApp } from 'vue'
import { createPinia } from 'pinia'
import 'element-plus/theme-chalk/base.css'
import 'element-plus/theme-chalk/el-button.css'
import 'element-plus/theme-chalk/el-icon.css'
import 'element-plus/theme-chalk/el-message.css'
import 'element-plus/theme-chalk/el-message-box.css'
import './style.css'
import { cleanupLegacyAuthArtifacts } from './utils/auth'
import { initTheme } from './stores/theme'
import { initLanguage } from './stores/language'
import { useAuthStore } from './stores/auth'
import App from './App.vue'
import router from './router/index'

async function bootstrap() {
  // 1. 清理旧 localStorage 残留。
  cleanupLegacyAuthArtifacts()
  // 2. 初始化主题（深色/浅色）。
  initTheme()
  initLanguage()

  // 3. 创建 Vue 应用与 Pinia/Router。
  const app = createApp(App)
  const pinia = createPinia()
  app.use(pinia).use(router)

  // 4. 准备好鉴权 store，但暂不 await：先让 UI 挂载，避免白屏。
  const auth = useAuthStore(pinia)

  // 5. 先挂载应用，再让路由完成首屏导航。
  //    这样即使 /api/auth/me 慢，浏览器也已经渲染出 AppShell 占位，
  //    不会因为整页白屏而被误判为"跳到登录页"。
  app.mount('#app')
  await router.isReady()

  // 6. 路由就绪后再 bootstrap，确保守卫有正确的会话状态。
  await auth.bootstrap()
}

void bootstrap()
