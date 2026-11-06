<script setup lang="ts">
import { ArrowLeft, CircleAlert, Info, Save } from '@lucide/vue'
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { useRouter } from 'vue-router'
import { ApiError, apiRequest } from '../services/api'
import { createTeamRequest } from '../services/teamRequests'
import { useAuthStore } from '../stores/auth'
import TaskFilterSelect from '../components/TaskFilterSelect.vue'
import type { TaskFilterOption } from '../components/TaskFilterSelect.vue'
import { requestPriorityText, requestTypeText } from '../types/domain'
import type { Project, RepositoryConfig, RequestPriority, RequestType } from '../types/domain'

const router = useRouter()
const auth = useAuthStore()
const loading = ref(false)
const saving = ref(false)
const errorText = ref('')
const projects = ref<Project[]>([])
const repositories = ref<RepositoryConfig[]>([])
const form = reactive({
  project_id: '',
  repository_id: '',
  type: 'demand' as RequestType,
  title: '',
  body: '',
  reproduction_steps: '',
  acceptance_criteria_text: '',
  priority: 'medium' as RequestPriority,
  labels_text: '',
  target_branch: '',
  test_command: '',
})

const selectedProject = computed(() => projects.value.find((item) => item.id === form.project_id) ?? null)
const selectedRepository = computed(() => repositories.value.find((item) => item.id === form.repository_id) ?? null)
const projectOptions = computed<TaskFilterOption[]>(() => projects.value.map((project) => ({ value: project.id, label: project.name })))
const repositoryOptions = computed<TaskFilterOption[]>(() => repositories.value.map((repository) => ({ value: repository.id, label: repository.name })))
const typeOptions: TaskFilterOption[] = [
  { value: 'demand', label: '需求' },
  { value: 'bug', label: 'Bug' },
  { value: 'refactor', label: '重构' },
  { value: 'test', label: '测试' },
  { value: 'doc', label: '文档' },
]
const priorityOptions: TaskFilterOption[] = [
  { value: 'high', label: '高' },
  { value: 'medium', label: '中' },
  { value: 'low', label: '低' },
]
const canSubmit = computed(() => auth.hasPermission('request:create') && !saving.value)
const requestFacts = computed(() => [
  { label: '项目', value: selectedProject.value?.name || '等待加载' },
  { label: '仓库', value: selectedRepository.value?.name || '等待加载' },
  { label: '类型', value: requestTypeText[form.type] },
  { label: '优先级', value: requestPriorityText[form.priority] },
])

watch(() => form.project_id, () => {
  void loadRepositories()
})

onMounted(() => {
  void loadProjects()
})

function apiMessage(error: unknown, fallback: string) {
  return error instanceof ApiError ? error.message : fallback
}

function splitLines(value: string) {
  return value.split(/\r?\n/).map((item) => item.trim()).filter(Boolean)
}

async function loadProjects() {
  loading.value = true
  try {
    projects.value = await apiRequest<Project[]>('/api/projects')
    form.project_id = projects.value[0]?.id ?? ''
  } catch (error) {
    errorText.value = apiMessage(error, '项目列表加载失败')
  } finally {
    loading.value = false
  }
}

async function loadRepositories() {
  repositories.value = []
  form.repository_id = ''
  if (!form.project_id) return
  try {
    repositories.value = await apiRequest<RepositoryConfig[]>(`/api/repositories?project_id=${encodeURIComponent(form.project_id)}`)
    form.repository_id = repositories.value[0]?.id ?? ''
  } catch (error) {
    errorText.value = apiMessage(error, '仓库列表加载失败')
  }
}

async function submitRequest() {
  if (!canSubmit.value) return
  saving.value = true
  errorText.value = ''
  try {
    const record = await createTeamRequest({
      project_id: form.project_id,
      repository_id: form.repository_id,
      type: form.type,
      title: form.title,
      body: form.body,
      reproduction_steps: form.reproduction_steps,
      acceptance_criteria: splitLines(form.acceptance_criteria_text),
      priority: form.priority,
      labels: splitLines(form.labels_text.replaceAll(',', '\n')),
      target_branch: form.target_branch || null,
      test_command: form.test_command || null,
    })
    if (record.content_sufficient) {
      ElMessage.success(`平台需求草稿已创建：${record.request_no}`)
    } else {
      ElMessage.warning(record.validation_warnings[0] ?? '草稿已保存，但同步前需要补充正文')
    }
    router.push(`/requests/${record.id}`)
  } catch (error) {
    errorText.value = apiMessage(error, '平台需求创建失败')
    ElMessage.error(errorText.value)
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <section class="page-content request-page request-create-page">
    <div class="breadcrumb"><RouterLink to="/requests"><ArrowLeft :size="15" /> 返回平台需求</RouterLink><span>/</span><b>创建需求</b></div>

    <div v-if="!auth.hasPermission('request:create')" class="page-notice page-notice--info">
      <span class="page-notice__icon"><Info :size="16" /></span>
      <div class="page-notice__body">
        <strong>您当前只能查看平台需求</strong>
        <p>创建或修改草稿需要开发者、项目负责人或管理员权限。</p>
      </div>
    </div>

    <header class="request-create-hero">
      <div class="request-create-hero__copy">
        <p class="section-kicker">Request intake</p>
        <h1>创建需求</h1>
        <p>把问题背景、验收边界和交付上下文集中记录，保存后可以继续补充，再进入后续同步流程。</p>
        <dl class="request-create-hero__facts" aria-label="当前需求上下文">
          <div v-for="fact in requestFacts" :key="fact.label">
            <dt>{{ fact.label }}</dt>
            <dd>{{ fact.value }}</dd>
          </div>
        </dl>
      </div>
      <div class="request-create-hero__meta">
        <span class="request-create-hero__status"><i />草稿模式</span>
        <small>保存后仍可修改</small>
      </div>
    </header>

    <form class="request-form-shell" @submit.prevent="submitRequest">
      <div class="panel request-form">
        <header class="request-form__header">
          <div>
            <p class="section-kicker">Request brief</p>
            <h2>平台需求草稿</h2>
            <p>先完成核心信息，后续再补充复现、验收和交付配置。</p>
          </div>
          <span class="request-form__counter">01 / 04</span>
        </header>

        <div v-if="errorText" class="form-alert request-alert">
          <CircleAlert :size="17" />
          <span>{{ errorText }}</span>
        </div>

        <section class="request-form__section">
          <header class="request-form__section-head">
            <span class="request-form__step">01</span>
            <div>
              <h3>项目范围</h3>
              <p>确定这条需求最终落在哪个项目和代码仓库。</p>
            </div>
          </header>
          <div class="request-form__grid">
            <label>
              <span>项目 <b>*</b></span>
              <TaskFilterSelect v-model="form.project_id" :options="projectOptions" ariaLabel="项目" :disabled="loading || saving || !auth.hasPermission('request:create')" class="request-form-select" />
            </label>
            <label>
              <span>仓库 <b>*</b></span>
              <TaskFilterSelect v-model="form.repository_id" :options="repositoryOptions" ariaLabel="仓库" :disabled="loading || saving || !repositories.length || !auth.hasPermission('request:create')" class="request-form-select" />
            </label>
            <label>
              <span>类型 <b>*</b></span>
              <TaskFilterSelect v-model="form.type" :options="typeOptions" ariaLabel="需求类型" :disabled="saving || !auth.hasPermission('request:create')" class="request-form-select" />
            </label>
            <label>
              <span>优先级</span>
              <TaskFilterSelect v-model="form.priority" :options="priorityOptions" ariaLabel="优先级" :disabled="saving || !auth.hasPermission('request:create')" class="request-form-select" />
            </label>
          </div>
        </section>

        <section class="request-form__section">
          <header class="request-form__section-head">
            <span class="request-form__step">02</span>
            <div>
              <h3>需求说明</h3>
              <p>用一句清晰的标题说明变化，再补充完整背景和影响范围。</p>
            </div>
          </header>
          <label>
            <span>标题 <b>*</b></span>
            <input v-model="form.title" :disabled="saving || !auth.hasPermission('request:create')" placeholder="例如：登录页刷新后误跳转" />
          </label>
          <label>
            <span>正文 <b>*</b></span>
            <textarea v-model="form.body" :disabled="saving || !auth.hasPermission('request:create')" rows="6" placeholder="写清现象、期望结果和影响范围。正文过短可保存，但同步 Gitee 或创建 Agent 任务前需要补充。" />
          </label>
        </section>

        <section class="request-form__section">
          <header class="request-form__section-head">
            <span class="request-form__step">03</span>
            <div>
              <h3>验证信息</h3>
              <p>补充复现路径和验收标准，帮助后续协作减少来回确认。</p>
            </div>
          </header>
          <label>
            <span>复现步骤</span>
            <textarea v-model="form.reproduction_steps" :disabled="saving || !auth.hasPermission('request:create')" rows="3" placeholder="按实际操作顺序填写，每一步单独一行。" />
          </label>
          <div class="request-form__grid">
            <label>
              <span>验收标准</span>
              <textarea v-model="form.acceptance_criteria_text" :disabled="saving || !auth.hasPermission('request:create')" rows="4" placeholder="每行一条，可直接作为评审和测试检查项。" />
            </label>
            <label>
              <span>标签</span>
              <textarea v-model="form.labels_text" :disabled="saving || !auth.hasPermission('request:create')" rows="4" placeholder="每行一个，或用逗号分隔。" />
            </label>
          </div>
        </section>

        <section class="request-form__section request-form__section--last">
          <header class="request-form__section-head">
            <span class="request-form__step">04</span>
            <div>
              <h3>交付配置</h3>
              <p>没有特殊要求可以留空，系统会使用仓库默认配置。</p>
            </div>
          </header>
          <div class="request-form__grid request-form__grid--delivery">
            <label>
              <span>期望分支</span>
              <input v-model="form.target_branch" :disabled="saving || !auth.hasPermission('request:create')" :placeholder="selectedRepository?.default_branch ?? 'main'" />
              <small>留空时使用仓库默认分支 {{ selectedRepository?.default_branch ?? 'main' }}</small>
            </label>
            <label>
              <span>测试命令</span>
              <input v-model="form.test_command" :disabled="saving || !auth.hasPermission('request:create')" :placeholder="selectedRepository?.test_command ?? 'pytest tests/ -q'" />
              <small>命令白名单：{{ selectedRepository?.command_whitelist.join('、') || '-' }}</small>
            </label>
          </div>
        </section>

        <footer class="form-footer">
          <p class="request-form__footer-hint">保存草稿后仍可回到详情页继续补充，当前上下文会保留下来。</p>
          <div class="request-form__footer-actions">
            <button type="button" class="button button--secondary" :disabled="saving" @click="router.push('/requests')">取消</button>
            <button type="submit" class="button button--primary" :disabled="!canSubmit">
              <Save :size="16" />
              {{ saving ? '保存中' : '保存草稿' }}
            </button>
          </div>
        </footer>
      </div>

      <aside class="request-create-aside">
        <section class="request-support-panel">
          <header class="request-support-panel__header">
            <p class="section-kicker">Before submit</p>
            <h2>提交前确认</h2>
            <p>把关键条件一次性补齐，减少后面同步、评审和验收的来回确认。</p>
          </header>
          <ul class="request-support-panel__list">
            <li>
              <i />
              <div>
                <strong>标题能直接说明要解决的事情</strong>
                <p>尽量让人一眼知道这条需求的业务目标。</p>
              </div>
            </li>
            <li>
              <i />
              <div>
                <strong>正文包含现象、期望结果和影响范围</strong>
                <p>信息完整一点，后面同步和排查都会少来回。</p>
              </div>
            </li>
            <li>
              <i />
              <div>
                <strong>验收标准可以被复现和检查</strong>
                <p>按步骤验证，不要只写主观判断。</p>
              </div>
            </li>
            <li>
              <i />
              <div>
                <strong>测试命令符合仓库白名单</strong>
                <p>不在白名单里的命令，保存后也无法继续走流程。</p>
              </div>
            </li>
          </ul>
          <footer class="request-support-panel__footer">
            <p class="section-kicker">Next step</p>
            <strong>保存后可继续补充</strong>
            <p>草稿保存成功后，可以从详情页继续编辑，再进入 Gitee 同步和 Agent Task 创建流程。</p>
          </footer>
        </section>
      </aside>
    </form>
  </section>
</template>

<style scoped>
.request-create-page {
  max-width: 1480px;
}

.request-create-hero {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 176px;
  gap: 24px;
  align-items: start;
  max-width: 1240px;
  margin: 0 0 16px;
  padding: 18px 20px;
  border: 1px solid var(--panel-border);
  border-radius: 12px;
  background: linear-gradient(180deg, color-mix(in srgb, var(--panel-bg) 96%, var(--brand-soft)) 0%, var(--panel-bg) 100%);
  box-shadow: var(--shadow-sm);
}

.request-create-hero__copy {
  min-width: 0;
}

.request-create-hero h1 {
  margin-top: 6px;
  color: var(--text-strong);
  font-size: clamp(28px, 3vw, 40px);
  line-height: 1.08;
  letter-spacing: -.04em;
  text-wrap: balance;
}

.request-create-hero__copy > p:last-of-type {
  max-width: 650px;
  margin-top: 11px;
  color: var(--text-soft);
  font-size: 13px;
  line-height: 21px;
}

.request-create-hero__facts {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
  margin: 18px 0 0;
}

.request-create-hero__facts div {
  display: grid;
  gap: 3px;
  min-width: 0;
  padding: 11px 12px;
  border: 1px solid var(--panel-border-soft);
  border-radius: 9px;
  background: color-mix(in srgb, var(--panel-bg) 82%, var(--brand-soft));
}

.request-create-hero__facts dt {
  color: var(--text-faint);
  font-size: 10px;
  font-weight: 700;
  letter-spacing: .06em;
}

.request-create-hero__facts dd {
  margin: 0;
  color: var(--text-strong);
  font-size: 13px;
  font-weight: 600;
  line-height: 18px;
}

.request-create-hero__meta {
  display: grid;
  justify-items: end;
  gap: 7px;
  padding-top: 4px;
}

.request-create-hero__status {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  color: var(--text-base);
  font-size: 12px;
  font-weight: 700;
}

.request-create-hero__status i {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #d8a64a;
  box-shadow: 0 0 0 4px rgba(216, 166, 74, .12);
}

.request-create-hero__meta small {
  color: var(--text-faint);
  font-size: 11px;
}

.request-form-shell {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 286px;
  gap: 20px;
  align-items: start;
  max-width: 1240px;
}

.request-form {
  display: grid;
  gap: 0;
  overflow: hidden;
  border-color: var(--panel-border);
  border-radius: 12px;
  background: linear-gradient(180deg, color-mix(in srgb, var(--panel-bg) 98%, var(--brand-soft)) 0%, var(--panel-bg) 100%);
  box-shadow: var(--shadow-sm);
}

.request-form__header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 18px;
  padding: 19px 22px 17px;
  border-bottom: 1px solid var(--panel-border-soft);
  background: var(--surface-subtle);
}

.request-form__header h2 {
  margin-top: 5px;
  color: var(--text-strong);
  font-size: 17px;
  line-height: 22px;
}

.request-form__header p:last-child {
  margin-top: 5px;
  color: var(--text-soft);
  font-size: 11px;
  line-height: 17px;
}

.request-form__counter {
  flex: none;
  color: var(--text-faint);
  font-family: "JetBrains Mono", Consolas, monospace;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: .08em;
}

.request-form__section {
  display: grid;
  gap: 16px;
  padding: 21px 22px;
  border-bottom: 1px solid var(--panel-border-soft);
}

.request-form__section--last {
  border-bottom: 0;
}

.request-form__section-head {
  display: grid;
  grid-template-columns: 32px minmax(0, 1fr);
  gap: 11px;
  align-items: start;
  margin-bottom: 2px;
}

.request-form__step {
  display: grid;
  place-items: center;
  width: 28px;
  height: 28px;
  border: 1px solid var(--panel-border);
  border-radius: 8px;
  color: var(--brand-strong);
  background: var(--brand-soft);
  font-family: "JetBrains Mono", Consolas, monospace;
  font-size: 10px;
  font-weight: 700;
}

.request-form__section-head h3 {
  color: var(--text-strong);
  font-size: 14px;
  line-height: 19px;
}

.request-form__section-head p {
  margin-top: 3px;
  color: var(--text-soft);
  font-size: 11px;
  line-height: 17px;
}

.request-form__grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
}

.request-form__grid--delivery {
  align-items: start;
}

.request-form label {
  display: grid;
  gap: 7px;
  min-width: 0;
  color: var(--text-base);
  font-size: 12px;
  font-weight: 700;
}

.request-form label > span b {
  color: #e07a70;
  font-weight: 700;
}

.request-form input,
.request-form textarea {
  border-color: var(--panel-border);
  background: var(--control-bg);
  color: var(--text-strong);
}

.request-form input {
  height: 42px;
  padding: 0 12px;
}

.request-form textarea {
  min-height: 112px;
  padding: 11px 12px;
  line-height: 19px;
}

.request-form__section:nth-of-type(3) textarea {
  min-height: 92px;
}

.request-form input::placeholder,
.request-form textarea::placeholder {
  color: var(--text-faint);
}

.request-form small {
  display: block;
  margin-top: -2px;
  color: var(--text-faint);
  font-size: 10px;
  font-weight: 500;
  line-height: 16px;
}

.request-form .form-alert {
  margin: 16px 22px 0;
}

.request-form .form-footer {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 14px;
  align-items: center;
  padding: 16px 22px 18px;
  border-top: 1px solid var(--panel-border-soft);
  background: var(--surface-subtle);
}

.request-form__footer-hint {
  min-width: 0;
  color: var(--text-soft);
  font-size: 11px;
  line-height: 17px;
}

.request-form__footer-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 10px;
}

.request-create-aside {
  align-self: start;
}

.request-support-panel {
  display: grid;
  gap: 0;
  overflow: hidden;
  border: 1px solid var(--panel-border);
  border-radius: 12px;
  background: linear-gradient(180deg, color-mix(in srgb, var(--panel-bg) 96%, var(--brand-soft)) 0%, var(--surface-subtle) 100%);
  box-shadow: var(--shadow-sm);
}

.request-support-panel__header {
  display: grid;
  gap: 4px;
  padding: 16px 16px 14px;
  border-bottom: 1px solid var(--panel-border-soft);
}

.request-support-panel__header h2 {
  color: var(--text-strong);
  font-size: 15px;
  line-height: 21px;
}

.request-support-panel__header p:last-child {
  color: var(--text-soft);
  font-size: 11px;
  line-height: 17px;
}

.request-support-panel__list {
  display: grid;
  gap: 10px;
  margin: 0;
  padding: 14px 16px 8px;
  list-style: none;
}

.request-support-panel__list li {
  display: grid;
  grid-template-columns: 8px minmax(0, 1fr);
  gap: 10px;
  align-items: start;
  padding: 10px 0;
  border-top: 1px solid var(--panel-border-soft);
  color: var(--text-base);
}

.request-support-panel__list li i {
  width: 6px;
  height: 6px;
  margin-top: 5px;
  border-radius: 50%;
  background: var(--brand);
}

.request-support-panel__list li:first-child {
  padding-top: 0;
  border-top: 0;
}

.request-support-panel__list li strong {
  display: block;
  color: var(--text-strong);
  font-size: 12px;
  line-height: 16px;
}

.request-support-panel__list li p {
  margin-top: 4px;
  color: var(--text-soft);
  font-size: 11px;
  line-height: 16px;
}

.request-support-panel__footer {
  display: grid;
  gap: 4px;
  padding: 14px 16px 16px;
  border-top: 1px solid var(--panel-border-soft);
  background: color-mix(in srgb, var(--brand-soft) 72%, var(--surface-subtle));
}

.request-support-panel__footer strong {
  color: var(--text-strong);
  font-size: 13px;
  line-height: 18px;
}

.request-support-panel__footer p:last-child {
  color: var(--text-soft);
  font-size: 11px;
  line-height: 18px;
}

:global([data-theme="dark"]) .request-support-panel__footer {
  background: #14233b;
}

:global([data-theme="dark"]) .request-create-hero__status i {
  background: #d7a550;
}

@media (max-width: 1100px) {
  .request-create-hero,
  .request-form-shell {
    grid-template-columns: minmax(0, 1fr);
    max-width: 900px;
  }

  .request-create-aside {
    position: static;
  }

  .request-create-hero__meta {
    justify-items: start;
  }
}

@media (max-width: 800px) {
  .request-create-hero__facts,
  .request-form__grid {
    grid-template-columns: 1fr;
  }

  .request-form .form-footer {
    grid-template-columns: 1fr;
  }

  .request-form__footer-actions {
    justify-content: flex-start;
  }

  .request-form__footer-actions .button {
    flex: 1;
  }

  .request-form__section,
  .request-form__header {
    padding-right: 16px;
    padding-left: 16px;
  }

  .request-form .form-alert {
    margin-right: 16px;
    margin-left: 16px;
  }

  .request-form .form-footer {
    padding-right: 16px;
    padding-left: 16px;
  }
}

</style>
