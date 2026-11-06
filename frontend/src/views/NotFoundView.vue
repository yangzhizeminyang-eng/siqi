<script setup lang="ts">
import { ArrowLeft } from '@lucide/vue'
</script>

<template>
  <main class="login-page not-found-page">
    <section class="login-aside">
      <div class="login-brand"><span class="brand__mark">404</span> 司契 Siqi</div>
      <div class="login-aside__content">
        <p class="login-aside__eyebrow">页面不存在</p>
        <h1>这个地址还没有接到页面。</h1>
        <p>可能是路由写错了，或者这个页面还没被接上。先回工作台继续干活。</p>
      </div>
      <p class="login-aside__footnote">你可以直接返回工作台或任务列表。</p>
    </section>

    <section class="login-form-wrap">
      <div class="login-form">
        <p class="section-kicker">Not Found</p>
        <h2>找不到当前页面</h2>
        <p class="form-subtitle">确认路径后再继续访问。</p>
        <div class="form-row">
          <RouterLink to="/dashboard" class="button button--primary button--wide"><ArrowLeft :size="16" /> 返回工作台</RouterLink>
        </div>
        <RouterLink to="/tasks" class="text-button">去任务列表</RouterLink>
      </div>
    </section>
  </main>
</template>
