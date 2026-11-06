<script setup lang="ts">
import { ArrowLeft, FileText, ShieldCheck } from '@lucide/vue'
import { computed } from 'vue'
import { RouterLink } from 'vue-router'
import ThemeSwitcher from '../components/ThemeSwitcher.vue'

const props = defineProps<{ kind: 'terms' | 'privacy' }>()
const isTerms = computed(() => props.kind === 'terms')
const title = computed(() => (isTerms.value ? '服务协议' : '隐私政策'))
const updatedAt = '2026 年 9 月 3 日'
</script>

<template>
  <main class="login-page legal-page">
    <section class="login-aside">
      <div class="login-brand">
        <span class="brand__mark brand__mark--logo"><img src="/favicon.svg" alt="" aria-hidden="true" /></span>
        <span>司契 Siqi</span>
      </div>
      <div class="login-aside__content">
        <p class="login-aside__eyebrow">登录前须知</p>
        <h1>{{ title }}</h1>
        <p class="login-aside__lead">这份说明用于明确平台的使用边界、账号责任和数据处理方式。</p>
        <div class="legal-aside-note"><ShieldCheck :size="18" /><span>企业研发场景 · 仅限授权成员使用</span></div>
      </div>
    </section>

    <section class="login-form-wrap legal-form-wrap">
      <article class="login-card legal-card">
        <header class="legal-card__header">
          <div>
            <RouterLink to="/login" class="text-link recovery-back"><ArrowLeft :size="15" /> 返回登录</RouterLink>
            <p class="section-kicker"><FileText :size="14" /> 司契 Siqi</p>
            <h2>{{ title }}</h2>
            <p class="form-subtitle">生效日期：{{ updatedAt }}</p>
          </div>
          <ThemeSwitcher />
        </header>

        <div v-if="isTerms" class="legal-content">
          <h3>一、协议范围</h3>
          <p>本协议适用于企业研发团队使用司契（Siqi）进行 Issue 接入、Agent 任务协作、审批、代码变更审阅、测试记录和过程审计。</p>
          <h3>二、账号与权限</h3>
          <p>账号仅限被企业授权的成员使用。用户应妥善保管账号、密码和应急恢复信息，并对账号下的操作负责。不同角色只能访问和操作授权范围内的项目与任务。</p>
          <h3>三、AI 任务使用边界</h3>
          <p>平台生成的分析、修改计划和代码变更建议需要结合真实项目上下文复核。高风险操作必须经过授权审批，用户不得利用平台绕过审批、扩大文件范围或执行未授权命令。</p>
          <h3>四、审计与服务变更</h3>
          <p>平台会记录登录、任务流转、审批、配置变更和执行结果等必要事件。平台能力、测试账号和演示数据可能随版本更新而调整。</p>
          <h3>五、联系与终止</h3>
          <p>账号、权限和密码问题由企业管理员或平台安全联系人处理。违反企业安全规则或本协议的账号，企业可暂停或终止其访问权限。</p>
        </div>

        <div v-else class="legal-content">
          <h3>一、我们处理哪些信息</h3>
          <p>平台会处理登录账号、姓名、角色、项目任务、审批意见、代码上下文引用、测试结果和审计事件等开展研发协作所必需的信息。</p>
          <h3>二、信息如何使用</h3>
          <p>这些信息用于身份认证、权限控制、任务流转、风险审批、任务回放、故障定位和安全审计。平台不会把密码明文保存到前端，也不会把密码写入浏览器本地存储。</p>
          <h3>三、代码与敏感信息</h3>
          <p>企业应按项目安全策略配置仓库和敏感路径。Token、密码、私钥等敏感信息不应出现在 Issue、评论、任务描述或提交内容中。</p>
          <h3>四、保存与访问</h3>
          <p>信息只在企业授权范围内提供访问。管理员可依据企业制度处理成员权限和账号恢复申请；审计记录用于追责和复盘，不应被普通用户修改或删除。</p>
          <h3>五、你的联系渠道</h3>
          <p>如需查询、修改或删除与账号相关的信息，请联系企业管理员或平台安全联系人。涉及企业代码和任务数据的请求，还需遵守所在组织的保密与数据治理制度。</p>
        </div>

        <footer class="legal-card__footer">
          <RouterLink to="/login" class="button button--secondary">返回登录</RouterLink>
          <RouterLink v-if="isTerms" to="/privacy" class="text-link">查看隐私政策</RouterLink>
          <RouterLink v-else to="/terms" class="text-link">查看服务协议</RouterLink>
        </footer>
      </article>
    </section>
  </main>
</template>
