<script setup lang="ts">
import { computed } from 'vue'
import { ElConfigProvider } from 'element-plus'
import { useRoute } from 'vue-router'
import { RouterView } from 'vue-router'
import AppShell from './components/AppShell.vue'
import { useAuthStore } from './stores/auth'

// 文件级注释：路由根容器。
// 决定是否使用 AppShell 包裹业务视图。/login 等 public 路由走 BlankShell（裸渲染）；
// 其它业务页在已确认登录或还有本地会话快照时走 AppShell。
// 这里**同时**用路由 meta 和 auth 状态作为判断依据：
//   - 登录页刷新时 layout 稳定为 BlankShell，绝不渲染 AppShell。
//   - 业务页刷新时，currentUser 还没回来之前只要 cachedUser 还在（之前登录过），
//     仍走 AppShell，避免"工作台内容"在 ready 之前被空白替代。
//     （侧栏 navItems 在 cachedUser 时也用乐观授权，菜单稳定。）
//   - 真正的鉴权与权限拦截完全交给 router.beforeEach 兜底。
//   - displayUser 也走 cachedUser 兜底，顶部用户区不会突然变"未登录"。
const route = useRoute()
const auth = useAuthStore()
const messageConfig = {
  grouping: true,
  duration: 2400,
  offset: 76,
  placement: 'top',
}
const useShell = computed(() => {
  if (route.meta.public || route.meta.layout === 'blank') return false
  return Boolean(auth.isAuthenticated || auth.cachedUser)
})
</script>

<template>
  <ElConfigProvider :message="messageConfig">
    <RouterView v-slot="{ Component }">
      <Transition name="view" mode="out-in" :appear="false">
        <component :is="Component" v-if="!useShell" key="blank" />
        <AppShell v-else key="app">
          <component :is="Component" />
        </AppShell>
      </Transition>
    </RouterView>
  </ElConfigProvider>
</template>

<style>
.view-enter-active {
  transition: opacity .22s ease-out, transform .22s ease-out;
}

.view-leave-active {
  transition: opacity .14s ease-in;
}

.view-enter-from {
  opacity: 0;
  transform: translateY(12px);
}

.view-leave-to {
  opacity: 0;
}

@media (prefers-reduced-motion: reduce) {
  .view-enter-active,
  .view-leave-active {
    transition: none;
  }
}
</style>
