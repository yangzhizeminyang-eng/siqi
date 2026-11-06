<script setup lang="ts">
import { Check, CircleAlert, EyeOff, FileClock, GitBranch, KeyRound, Minus, Plus, RefreshCw, Save, ShieldAlert, Sparkles } from '@lucide/vue'
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { ApiError, apiRequest } from '../services/api'
import { useAuthStore } from '../stores/auth'
import type { AuthUser } from '../types/auth'
import type { ApprovalMode, AuditEvent, Project, RepositoryConfig, RepositoryConfigPayload, RepositoryInspectResult, RiskThreshold } from '../types/domain'
import TaskFilterSelect from '../components/TaskFilterSelect.vue'
import type { TaskFilterOption } from '../components/TaskFilterSelect.vue'

type TabKey = 'repository' | 'model' | 'test' | 'approval' | 'security'

interface RepositoryForm {
  name: string
  url: string
  default_branch: string
  gitee_token: string
  test_command: string
  approval_owner_id: string
  approval_mode: ApprovalMode
  risk_threshold: RiskThreshold
  max_files: number
  max_retries: number
  auto_create_task_after_sync: boolean
}

const auth = useAuthStore()
const active = ref<TabKey>('repository')
const loading = ref(false)
const savingProject = ref(false)
const savingRepository = ref(false)
const inspectingRepository = ref(false)
const showProjectCreator = ref(false)
const selectedProjectId = ref('')
const selectedRepositoryId = ref('')
const projects = ref<Project[]>([])
const repositories = ref<RepositoryConfig[]>([])
const users = ref<AuthUser[]>([])
const audits = ref<AuditEvent[]>([])
const modelProvider = ref('DeepSeek')
const executionModel = ref('deepseek-chat')
const repositoryUrlPattern = /^(https:\/\/gitee\.com\/[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+(?:\.git)?|git@gitee\.com:[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+(?:\.git)?)$/

const tabs: Array<[TabKey, string]> = [
  ['repository', '仓库配置'],
  ['model', '模型配置'],
  ['test', '测试命令'],
  ['approval', '审批策略'],
  ['security', '安全策略'],
]

const projectDraft = reactive({ name: '', description: '', default_test_command: '' })
const projectCreator = reactive({ name: '', description: '', default_test_command: '' })
const repositoryForm = reactive<RepositoryForm>({
  name: '',
  url: '',
  default_branch: 'main',
  gitee_token: '',
  test_command: 'pytest tests/ -q',
  approval_owner_id: '',
  approval_mode: 'single',
  risk_threshold: 'medium',
  max_files: 12,
  max_retries: 2,
  auto_create_task_after_sync: false,
})
const commandWhitelistText = ref('pytest\nnpm test\npnpm test')
const blockedPathsText = ref('.env\ninfra/production/\nsecrets/')
const inspectionResult = ref<RepositoryInspectResult | null>(null)

const selectedProject = computed(() => projects.value.find((project) => project.id === selectedProjectId.value) ?? null)
const selectedRepository = computed(() => repositories.value.find((repository) => repository.id === selectedRepositoryId.value) ?? null)
const projectSelectOptions = computed<TaskFilterOption[]>(() => projects.value.length
  ? projects.value.map((project) => ({ value: project.id, label: project.name }))
  : [{ value: '', label: loading.value ? '加载项目中' : '暂无项目' }])
const repositorySelectOptions = computed<TaskFilterOption[]>(() => [
  { value: '', label: repositories.value.length ? '新增仓库' : '暂无仓库' },
  ...repositories.value.map((repository) => ({ value: repository.id, label: repository.name })),
])
const approvalOwnerSelectOptions = computed<TaskFilterOption[]>(() => [
  { value: '', label: '未配置（任务详情页手动指派）' },
  ...approverOptions.value.map((user) => ({ value: user.id, label: `${user.name} · ${user.role_label}` })),
])
const approvalModeSelectOptions = computed<TaskFilterOption[]>(() => [
  { value: 'single', label: approvalModeText.single },
  { value: 'all', label: approvalModeText.all },
])
const riskThresholdSelectOptions = computed<TaskFilterOption[]>(() => [
  { value: 'low', label: riskThresholdText.low },
  { value: 'medium', label: riskThresholdText.medium },
  { value: 'high', label: riskThresholdText.high },
])
const modelProviderSelectOptions = computed<TaskFilterOption[]>(() => [{ value: modelProvider.value, label: modelProvider.value }])
const executionModelSelectOptions = computed<TaskFilterOption[]>(() => [{ value: executionModel.value, label: executionModel.value }])
// 默认审批人与指派候选限定 {admin, approver}（与后端一致：lead 只负责指派、不直接审批）
const approverOptions = computed(() => users.value.filter((user) => user.role === 'admin' || user.role === 'approver'))
const whitelistPreview = computed(() => parseLines(commandWhitelistText.value))
const blockedPathPreview = computed(() => parseLines(blockedPathsText.value))
const tokenStatusText = computed(() => {
  if (repositoryForm.gitee_token.trim()) return '待更新，保存后不再显示明文'
  if (selectedRepository.value?.token_configured) return `${selectedRepository.value.token_alias ?? '已配置'} · ${formatDate(selectedRepository.value.token_updated_at)}`
  return '未配置'
})

const approvalModeText: Record<ApprovalMode, string> = {
  single: '单人审批',
  all: '全部审批人通过',
}
const riskThresholdText: Record<RiskThreshold, string> = {
  low: '低风险',
  medium: '中风险',
  high: '高风险',
}

onMounted(() => {
  void loadInitialData()
})

function formatDate(value?: string | null) {
  if (!value) return '无更新时间'
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(value))
}

function parseLines(value: string) {
  return value
    .split(/\r?\n|,/)
    .map((item) => item.trim())
    .filter(Boolean)
}

function adjustNumberField(field: 'max_files' | 'max_retries', step: number) {
  const limits = field === 'max_files' ? { min: 1, max: 200 } : { min: 0, max: 10 }
  const current = Number(repositoryForm[field])
  const next = Number.isFinite(current) ? current + step : limits.min
  repositoryForm[field] = Math.min(limits.max, Math.max(limits.min, next))
}

function isCommandAllowed(command: string, whitelist: string[]) {
  const normalized = command.trim()
  return whitelist.some((allowed) => normalized === allowed || normalized.startsWith(`${allowed} `))
}

function apiMessage(error: unknown, fallback: string) {
  return error instanceof ApiError ? error.message : fallback
}

function syncProjectDraft(project: Project | null) {
  projectDraft.name = project?.name ?? ''
  projectDraft.description = project?.description ?? ''
  projectDraft.default_test_command = project?.default_test_command ?? ''
}

function applyRepository(repository: RepositoryConfig | null) {
  selectedRepositoryId.value = repository?.id ?? ''
  inspectionResult.value = null
  repositoryForm.name = repository?.name ?? ''
  repositoryForm.url = repository?.url ?? ''
  repositoryForm.default_branch = repository?.default_branch ?? 'main'
  repositoryForm.gitee_token = ''
  repositoryForm.test_command = repository?.test_command ?? 'pytest tests/ -q'
  repositoryForm.approval_owner_id = repository?.approval_owner_id ?? ''
  repositoryForm.approval_mode = repository?.approval_mode ?? 'single'
  repositoryForm.risk_threshold = repository?.risk_threshold ?? 'medium'
  repositoryForm.max_files = repository?.max_files ?? 12
  repositoryForm.max_retries = repository?.max_retries ?? 2
  repositoryForm.auto_create_task_after_sync = repository?.auto_create_task_after_sync ?? false
  commandWhitelistText.value = (repository?.command_whitelist ?? ['pytest', 'npm test', 'pnpm test']).join('\n')
  blockedPathsText.value = (repository?.blocked_paths ?? ['.env', 'infra/production/', 'secrets/']).join('\n')
}

async function loadInitialData() {
  loading.value = true
  try {
    const [projectList, userList] = await Promise.all([
      apiRequest<Project[]>('/api/projects'),
      apiRequest<AuthUser[]>('/api/users'),
    ])
    projects.value = projectList
    users.value = userList
    selectedProjectId.value = projectList[0]?.id ?? ''
    syncProjectDraft(selectedProject.value)
    await loadRepositories()
    await loadAudits()
  } catch (error) {
    ElMessage.error(apiMessage(error, '项目配置加载失败'))
  } finally {
    loading.value = false
  }
}

async function loadRepositories() {
  if (!selectedProjectId.value) {
    repositories.value = []
    applyRepository(null)
    return
  }
  repositories.value = await apiRequest<RepositoryConfig[]>(`/api/repositories?project_id=${encodeURIComponent(selectedProjectId.value)}`)
  applyRepository(repositories.value[0] ?? null)
}

async function loadAudits() {
  audits.value = await apiRequest<AuditEvent[]>('/api/audit-events?resource_type=repository')
}

async function selectProject() {
  syncProjectDraft(selectedProject.value)
  try {
    await loadRepositories()
    await loadAudits()
  } catch (error) {
    ElMessage.error(apiMessage(error, '项目数据加载失败'))
  }
}

function startRepositoryCreate() {
  applyRepository(null)
  repositoryForm.name = ''
  repositoryForm.url = ''
  active.value = 'repository'
}

function selectRepository(repositoryId: string) {
  applyRepository(repositories.value.find((repository) => repository.id === repositoryId) ?? null)
}

function handleRepositoryChange(repositoryId: string) {
  selectRepository(repositoryId)
}

async function inspectRepository() {
  if (!repositoryUrlPattern.test(repositoryForm.url.trim())) {
    ElMessage.error('请先填写有效的 Gitee HTTPS 或 SSH 仓库地址')
    return
  }
  inspectingRepository.value = true
  inspectionResult.value = null
  try {
    const result = await apiRequest<RepositoryInspectResult>('/api/repositories/inspect', {
      method: 'POST',
      body: JSON.stringify({
        url: repositoryForm.url.trim(),
        gitee_token: repositoryForm.gitee_token.trim() || null,
      }),
    })
    const suggested = result.suggested_payload
    repositoryForm.name = String(suggested.name || repositoryForm.name || '')
    repositoryForm.url = String(suggested.url || repositoryForm.url)
    repositoryForm.default_branch = String(suggested.default_branch || repositoryForm.default_branch)
    repositoryForm.test_command = String(suggested.test_command || '')
    if (suggested.command_whitelist?.length) commandWhitelistText.value = suggested.command_whitelist.join('\n')
    if (suggested.approval_owner_id !== undefined) repositoryForm.approval_owner_id = suggested.approval_owner_id || ''
    if (suggested.approval_mode) repositoryForm.approval_mode = suggested.approval_mode
    if (suggested.risk_threshold) repositoryForm.risk_threshold = suggested.risk_threshold
    if (suggested.max_files) repositoryForm.max_files = suggested.max_files
    if (suggested.max_retries !== undefined) repositoryForm.max_retries = suggested.max_retries
    if (suggested.blocked_paths?.length) blockedPathsText.value = suggested.blocked_paths.join('\n')
    if (suggested.auto_create_task_after_sync !== undefined) {
      repositoryForm.auto_create_task_after_sync = suggested.auto_create_task_after_sync
    }
    inspectionResult.value = result
    active.value = result.suggested_payload.test_command ? 'repository' : 'test'
    ElMessage.success('已根据仓库真实内容填充配置，请确认后保存')
  } catch (error) {
    ElMessage.error(apiMessage(error, '仓库识别失败，请检查地址和授权'))
  } finally {
    inspectingRepository.value = false
  }
}

async function createProject() {
  if (!projectCreator.name.trim()) {
    ElMessage.warning('请填写项目名称')
    return
  }
  savingProject.value = true
  try {
    const project = await apiRequest<Project>('/api/projects', {
      method: 'POST',
      body: JSON.stringify({
        name: projectCreator.name.trim(),
        description: projectCreator.description.trim(),
        default_test_command: projectCreator.default_test_command.trim() || null,
      }),
    })
    projects.value = [project, ...projects.value]
    selectedProjectId.value = project.id
    projectCreator.name = ''
    projectCreator.description = ''
    projectCreator.default_test_command = ''
    showProjectCreator.value = false
    syncProjectDraft(project)
    await loadRepositories()
    await loadAudits()
    ElMessage.success('项目已创建')
  } catch (error) {
    ElMessage.error(apiMessage(error, '项目创建失败'))
  } finally {
    savingProject.value = false
  }
}

async function updateProject() {
  if (!selectedProject.value) return
  if (!projectDraft.name.trim()) {
    ElMessage.warning('请填写项目名称')
    return
  }
  savingProject.value = true
  try {
    const project = await apiRequest<Project>(`/api/projects/${selectedProject.value.id}`, {
      method: 'PUT',
      body: JSON.stringify({
        name: projectDraft.name.trim(),
        description: projectDraft.description.trim(),
        default_test_command: projectDraft.default_test_command.trim() || null,
      }),
    })
    projects.value = projects.value.map((item) => (item.id === project.id ? project : item))
    syncProjectDraft(project)
    ElMessage.success('项目已更新')
  } catch (error) {
    ElMessage.error(apiMessage(error, '项目更新失败'))
  } finally {
    savingProject.value = false
  }
}

async function saveRepository() {
  if (!auth.hasPermission('project:manage')) {
    ElMessage.warning('您暂无权限修改项目配置')
    return
  }
  if (!selectedProjectId.value) {
    ElMessage.warning('请先创建项目')
    return
  }
  if (!repositoryUrlPattern.test(repositoryForm.url.trim())) {
    active.value = 'repository'
    ElMessage.error('仓库地址格式错误，仅支持 gitee.com 的 HTTPS 或 SSH 地址')
    return
  }
  const whitelist = whitelistPreview.value
  if (!whitelist.length) {
    active.value = 'test'
    ElMessage.warning('至少配置一条命令白名单')
    return
  }
  if (!isCommandAllowed(repositoryForm.test_command, whitelist)) {
    active.value = 'test'
    ElMessage.error('默认测试命令不在命令白名单中')
    return
  }

  const payload: RepositoryConfigPayload = {
    project_id: selectedProjectId.value,
    name: repositoryForm.name.trim(),
    url: repositoryForm.url.trim(),
    default_branch: repositoryForm.default_branch.trim(),
    test_command: repositoryForm.test_command.trim(),
    command_whitelist: whitelist,
    approval_owner_id: repositoryForm.approval_owner_id,
    approval_mode: repositoryForm.approval_mode,
    risk_threshold: repositoryForm.risk_threshold,
    max_files: repositoryForm.max_files,
    max_retries: repositoryForm.max_retries,
    blocked_paths: blockedPathPreview.value,
    auto_create_task_after_sync: repositoryForm.auto_create_task_after_sync,
  }
  const token = repositoryForm.gitee_token.trim()
  if (token) {
    payload.gitee_token = token
  }

  savingRepository.value = true
  try {
    const addressChanged = Boolean(
      selectedRepository.value && selectedRepository.value.url !== payload.url,
    )
    const path = selectedRepositoryId.value ? `/api/repositories/${selectedRepositoryId.value}` : '/api/repositories'
    const repository = await apiRequest<RepositoryConfig>(path, {
      method: selectedRepositoryId.value ? 'PUT' : 'POST',
      body: JSON.stringify(payload),
    })
    await loadRepositories()
    applyRepository(repository)
    await loadAudits()
    ElMessage.success(addressChanged ? '仓库地址已切换，已创建新的仓库数据空间' : '仓库配置已保存')
  } catch (error) {
    ElMessage.error(apiMessage(error, '仓库配置保存失败'))
  } finally {
    savingRepository.value = false
  }
}
</script>

<template>
  <section class="page-content settings-page">
    <div v-if="!auth.hasPermission('project:manage')" class="page-notice page-notice--info">
      <span class="page-notice__icon"><ShieldAlert :size="16" /></span>
      <div class="page-notice__body">
        <strong>您当前仅可查看项目配置</strong>
        <p>修改仓库、审批和安全策略需要管理员权限。</p>
      </div>
    </div>

    <div class="page-toolbar settings-toolbar">
      <div class="settings-toolbar__project">
        <label>
          <span>当前项目</span>
          <TaskFilterSelect
            v-model="selectedProjectId"
            :options="projectSelectOptions"
            :ariaLabel="'当前项目'"
            :disabled="loading || !projects.length"
            class="settings-select settings-select--project"
            @update:modelValue="selectProject"
          />
        </label>
        <button class="button button--secondary" :disabled="loading" @click="showProjectCreator = !showProjectCreator">
          <Plus :size="16" /> 新建项目
        </button>
      </div>
      <div class="toolbar-actions">
        <button class="button button--ghost" :disabled="loading" @click="loadInitialData"><RefreshCw :size="16" /> 刷新</button>
      </div>
    </div>

    <section v-if="showProjectCreator" class="panel settings-project-create">
      <header class="panel__header"><div><h2>创建项目</h2><p>项目创建后即可继续绑定 Gitee 仓库。</p></div></header>
      <div class="form-grid">
        <label><span>项目名称 <b>*</b></span><input v-model="projectCreator.name" /></label>
        <label class="form-grid__full"><span>项目说明</span><textarea v-model="projectCreator.description" /></label>
      </div>
      <footer class="form-footer">
        <button class="button button--secondary" @click="showProjectCreator = false">取消</button>
        <button class="button button--primary" :disabled="savingProject" @click="createProject"><Plus :size="16" /> 创建项目</button>
      </footer>
    </section>

    <div class="settings-layout">
      <aside class="settings-nav">
        <button v-for="[key, label] in tabs" :key="key" :class="{ 'is-active': active === key }" @click="active = key">{{ label }}</button>
      </aside>

      <main class="panel settings-form" :aria-busy="loading">
        <header class="panel__header settings-form__head">
          <div>
            <p class="section-kicker">{{ selectedProject?.name ?? '尚未选择项目' }}</p>
            <h2>{{ selectedRepositoryId ? '编辑仓库配置' : '新增仓库配置' }}</h2>
            <p>{{ selectedRepository ? `${selectedRepository.name} · ${selectedRepository.url}` : '配置仓库地址、命令白名单、审批人与安全策略。' }}</p>
          </div>
          <div class="settings-form__actions">
            <TaskFilterSelect
              v-model="selectedRepositoryId"
              :options="repositorySelectOptions"
              :ariaLabel="'当前仓库'"
              :disabled="!repositories.length"
              class="settings-select settings-select--repository"
              @update:modelValue="handleRepositoryChange"
            />
            <button class="button button--secondary" @click="startRepositoryCreate"><Plus :size="16" /> 新增仓库</button>
          </div>
        </header>

        <template v-if="active === 'repository'">
          <div class="settings-section form-grid">
            <label><span>项目名称 <b>*</b></span><input v-model="projectDraft.name" /></label>
            <label><span>项目操作</span><button class="button button--secondary" :disabled="!selectedProject || savingProject" @click="updateProject"><Save :size="16" /> 保存项目信息</button></label>
            <label class="form-grid__full"><span>项目说明</span><textarea v-model="projectDraft.description" /></label>
            <label class="form-grid__full project-test-command">
              <span>项目级默认测试命令</span>
              <input v-model="projectDraft.default_test_command" class="mono" placeholder="npm test" />
              <small class="muted">仅当任务级和仓库级命令未提供时使用该命令；须命中仓库命令白名单。</small>
            </label>
            <label><span>仓库名称 <b>*</b></span><input v-model="repositoryForm.name" placeholder="payment-service" /></label>
            <label><span>默认分支 <b>*</b></span><input v-model="repositoryForm.default_branch" class="mono" placeholder="main" /></label>
            <label class="form-grid__full">
              <span>仓库地址 <b>*</b></span>
              <input v-model="repositoryForm.url" class="mono" placeholder="https://gitee.com/company/payment-service.git" />
              <small class="muted">支持 HTTPS 和 SSH：git@gitee.com:company/payment-service.git</small>
              <button class="button button--secondary settings-inspect-button" :disabled="inspectingRepository || !repositoryForm.url.trim()" @click="inspectRepository">
                <Sparkles :size="16" /> {{ inspectingRepository ? '正在读取仓库...' : '智能识别并填充' }}
              </button>
            </label>
          </div>

          <div v-if="inspectionResult" class="settings-section repository-inspection">
            <div class="repository-inspection__header">
              <div>
                <h3>已读取真实仓库内容</h3>
                <p class="muted">
                  {{ inspectionResult.detected_stack.length ? `识别到：${inspectionResult.detected_stack.join('、')}` : '暂未识别到明确技术栈' }}
                </p>
              </div>
              <span class="status-tag" :class="inspectionResult.warnings.length ? 'warning' : 'success'">
                {{ inspectionResult.warnings.length ? '部分识别' : '识别完成' }}
              </span>
            </div>
            <div v-if="inspectionResult.sources.test_command" class="repository-inspection__source">
              <strong>测试命令</strong>
              <code>{{ repositoryForm.test_command || '未识别' }}</code>
              <span>来源：{{ inspectionResult.sources.test_command }}</span>
            </div>
            <ul v-if="inspectionResult.warnings.length" class="repository-inspection__warnings">
              <li v-for="warning in inspectionResult.warnings" :key="warning">{{ warning }}</li>
            </ul>
          </div>

          <div class="settings-section">
            <h3>Gitee 授权</h3>
            <div class="secret-status">
              <span><KeyRound :size="18" /></span>
              <div>
                <strong>Gitee Token</strong>
                <p>{{ tokenStatusText }}</p>
              </div>
              <em><Check v-if="selectedRepository?.token_configured || repositoryForm.gitee_token.trim()" :size="14" /><EyeOff v-else :size="14" /> {{ selectedRepository?.token_configured || repositoryForm.gitee_token.trim() ? '已配置' : '待配置' }}</em>
            </div>
            <label class="settings-secret-input">
              <span>重新填写 Token</span>
              <input v-model="repositoryForm.gitee_token" type="password" autocomplete="off" placeholder="保存后仅记录已配置状态，不回显明文" />
            </label>
          </div>

          <div class="settings-section">
            <h3>团队自动化</h3>
            <label class="settings-checkbox" :class="{ 'is-enabled': repositoryForm.auto_create_task_after_sync }">
              <span class="settings-checkbox__control">
                <input v-model="repositoryForm.auto_create_task_after_sync" type="checkbox" />
                <span class="settings-checkbox__box" aria-hidden="true"><Check :size="13" :stroke-width="3" /></span>
              </span>
              <span class="settings-checkbox__copy">
                <strong>平台需求同步 Gitee 成功后自动创建 Agent 任务</strong>
                <small class="muted">默认关闭。开启后同步成功会自动建任务；同 Issue 已有未结束任务时自动复用，自动创建失败不影响同步结果，可在需求详情手动创建。</small>
              </span>
            </label>
          </div>
        </template>

        <template v-else-if="active === 'model'">
          <header class="panel__header settings-static-header">
            <div><h2>模型配置</h2><p>当前通过部署环境变量接入模型，后续再补平台级密钥管理。</p></div>
          </header>
          <div class="settings-section form-grid">
            <label>
              <span>默认模型 Provider</span>
              <TaskFilterSelect v-model="modelProvider" :options="modelProviderSelectOptions" ariaLabel="默认模型 Provider" disabled class="settings-select" />
            </label>
            <label>
              <span>执行模型</span>
              <TaskFilterSelect v-model="executionModel" :options="executionModelSelectOptions" ariaLabel="执行模型" disabled class="settings-select" />
            </label>
          </div>
          <div class="settings-section">
            <div class="secret-status">
              <span><EyeOff :size="18" /></span>
              <div><strong>模型 API Key</strong><p>DeepSeek 与智谱 Key 只从服务端环境变量读取，不在页面回显。</p></div>
              <em><EyeOff :size="14" /> 环境变量接入</em>
            </div>
          </div>
        </template>

        <template v-else-if="active === 'test'">
          <div class="settings-section form-grid">
            <label class="form-grid__full"><span>默认测试命令 <b>*</b></span><input v-model="repositoryForm.test_command" class="mono" /></label>
            <label class="form-grid__full"><span>命令白名单 <b>*</b></span><textarea v-model="commandWhitelistText" class="mono" placeholder="pytest&#10;npm test&#10;pnpm test" /></label>
          </div>
          <div class="settings-section">
            <h3>当前可用命令</h3>
            <div class="command-list">
              <code v-for="command in whitelistPreview" :key="command">{{ command }}</code>
              <span v-if="!whitelistPreview.length" class="field-error">请至少配置一条命令</span>
            </div>
          </div>
        </template>

        <template v-else-if="active === 'approval'">
          <div class="settings-section form-grid">
            <label class="form-grid__full">
              <span>默认审批人（可空）</span>
              <TaskFilterSelect v-model="repositoryForm.approval_owner_id" :options="approvalOwnerSelectOptions" ariaLabel="默认审批人" class="settings-select" />
              <small class="muted">未配置默认审批人时，任务需在详情页指派审批人后再生成修改计划。</small>
            </label>
            <label>
              <span>审批模式</span>
              <TaskFilterSelect v-model="repositoryForm.approval_mode" :options="approvalModeSelectOptions" ariaLabel="审批模式" class="settings-select" />
            </label>
            <label>
              <span>风险告警阈值</span>
              <TaskFilterSelect v-model="repositoryForm.risk_threshold" :options="riskThresholdSelectOptions" ariaLabel="风险告警阈值" class="settings-select" />
            </label>
          </div>
        </template>

        <template v-else-if="selectedProject">
          <div class="risk-callout">
            <ShieldAlert :size="19" />
            <div><strong>安全策略</strong><p>这些配置会影响后续 Agent 执行范围，并在保存后写入审计事件。</p></div>
          </div>
          <div class="settings-section form-grid">
            <div class="settings-number-setting">
              <span class="settings-number-setting__label">最大可修改文件数</span>
              <span class="settings-number-field">
                <button class="settings-number-field__control settings-number-field__control--decrease" type="button" aria-label="减少最大可修改文件数" :disabled="repositoryForm.max_files <= 1" @mousedown.prevent @click="adjustNumberField('max_files', -1)"><Minus :size="14" /></button>
                <input v-model.number="repositoryForm.max_files" type="number" min="1" max="200" inputmode="numeric" />
                <button class="settings-number-field__control settings-number-field__control--increase" type="button" aria-label="增加最大可修改文件数" :disabled="repositoryForm.max_files >= 200" @mousedown.prevent @click="adjustNumberField('max_files', 1)"><Plus :size="14" /></button>
              </span>
            </div>
            <div class="settings-number-setting">
              <span class="settings-number-setting__label">最大重试次数</span>
              <span class="settings-number-field">
                <button class="settings-number-field__control settings-number-field__control--decrease" type="button" aria-label="减少最大重试次数" :disabled="repositoryForm.max_retries <= 0" @mousedown.prevent @click="adjustNumberField('max_retries', -1)"><Minus :size="14" /></button>
                <input v-model.number="repositoryForm.max_retries" type="number" min="0" max="10" inputmode="numeric" />
                <button class="settings-number-field__control settings-number-field__control--increase" type="button" aria-label="增加最大重试次数" :disabled="repositoryForm.max_retries >= 10" @mousedown.prevent @click="adjustNumberField('max_retries', 1)"><Plus :size="14" /></button>
              </span>
            </div>
            <label class="form-grid__full"><span>禁止修改路径</span><textarea v-model="blockedPathsText" class="mono" /></label>
          </div>
          <div class="settings-section">
            <h3>拦截路径预览</h3>
            <div class="command-list">
              <code v-for="path in blockedPathPreview" :key="path">{{ path }}</code>
              <span v-if="!blockedPathPreview.length" class="muted">暂未配置禁止路径</span>
            </div>
          </div>
        </template>
        <div v-else class="settings-empty settings-empty--main">
          <span class="settings-empty__icon"><ShieldAlert :size="18" /></span>
          <strong>尚未选择项目</strong>
          <p>先选择已有项目，或创建一个新项目，再配置仓库、审批和安全策略。</p>
          <button class="button button--secondary" @click="showProjectCreator = true"><Plus :size="16" /> 创建项目</button>
        </div>

        <footer class="form-footer">
          <button class="button button--primary" :disabled="savingRepository || loading" @click="saveRepository">
            <Save :size="16" /> 保存仓库配置
          </button>
        </footer>
      </main>
    </div>

    <section class="panel audit-panel">
      <header class="panel__header">
        <div><h2>最近审计事件</h2><p>保存项目和仓库配置后，后端会记录操作者、资源和变更摘要。</p></div>
        <FileClock :size="18" />
      </header>
      <div v-if="audits.length" class="audit-list">
        <article v-for="event in audits.slice(0, 5)" :key="event.id" class="audit-list__item">
          <span><GitBranch :size="16" /></span>
          <div>
            <strong>{{ event.summary }}</strong>
            <p>{{ event.actor_name }} · {{ formatDate(event.created_at) }}</p>
            <small>{{ event.diff_summary.join('；') }}</small>
          </div>
        </article>
      </div>
      <div v-else class="settings-empty">
        <CircleAlert :size="17" />
        <span>暂无仓库配置审计事件。</span>
      </div>
    </section>
  </section>
</template>

<style scoped>
.settings-checkbox {
  display: grid;
  grid-template-columns: 24px minmax(0, 1fr);
  align-items: flex-start;
  gap: 12px;
  min-height: 68px;
  padding: 12px 14px;
  border: 1px solid var(--panel-border-soft);
  border-radius: 8px;
  background: var(--surface-subtle);
  cursor: pointer;
  transition: border-color .18s ease, background .18s ease, box-shadow .18s ease;
}
.settings-checkbox:hover {
  border-color: #b9c9df;
  background: var(--surface-hover);
  box-shadow: 0 4px 12px rgba(15, 23, 42, .05);
}
.settings-checkbox.is-enabled {
  border-color: rgba(37, 87, 197, .32);
  background: var(--brand-soft);
}
.settings-checkbox__control {
  position: relative;
  display: grid;
  place-items: center;
  width: 24px;
  height: 24px;
  margin-top: 1px;
}
.settings-checkbox input {
  position: absolute;
  inset: 0;
  z-index: 1;
  width: 100%;
  height: 100%;
  margin: 0;
  opacity: 0;
  cursor: pointer;
}
.settings-checkbox input:focus {
  border: 0;
  outline: 0;
  box-shadow: none;
}
.settings-checkbox__box {
  display: grid;
  place-items: center;
  width: 21px;
  height: 21px;
  border: 1px solid #a7b5c7;
  border-radius: 6px;
  color: transparent;
  background: var(--panel-bg);
  box-shadow: inset 0 1px 1px rgba(15, 23, 42, .06);
  transition: border-color .18s ease, background .18s ease, color .18s ease, box-shadow .18s ease, transform .18s ease;
}
.settings-checkbox__box svg {
  opacity: 0;
  transform: scale(.65);
  transition: opacity .16s ease, transform .18s cubic-bezier(.2, .8, .2, 1);
}
.settings-checkbox input:checked + .settings-checkbox__box {
  border-color: #2855c7;
  color: #fff;
  background: #2855c7;
  box-shadow: 0 3px 8px rgba(40, 85, 199, .24);
}
.settings-checkbox input:checked + .settings-checkbox__box svg {
  opacity: 1;
  transform: scale(1);
}
.settings-checkbox input:focus-visible + .settings-checkbox__box {
  outline: 3px solid rgba(40, 85, 199, .2);
  outline-offset: 2px;
}
.settings-checkbox__copy {
  display: grid;
  gap: 4px;
  min-width: 0;
}
.settings-checkbox strong {
  font-size: 13px;
  line-height: 18px;
}
.settings-checkbox small {
  line-height: 1.6;
}
[data-theme="dark"] .settings-checkbox {
  border-color: #263850;
  background: #0d1625;
}
[data-theme="dark"] .settings-checkbox:hover {
  border-color: #3a5273;
  background: #142238;
  box-shadow: 0 8px 18px rgba(0, 0, 0, .16);
}
[data-theme="dark"] .settings-checkbox.is-enabled {
  border-color: rgba(110, 231, 223, .34);
  background: #122b39;
}
[data-theme="dark"] .settings-checkbox__box {
  border-color: #4c6079;
  background: #111d2e;
  box-shadow: inset 0 1px 1px rgba(0, 0, 0, .24);
}
[data-theme="dark"] .settings-checkbox input:checked + .settings-checkbox__box {
  border-color: #1cc4bd;
  background: #1a9f9b;
  box-shadow: 0 3px 10px rgba(28, 196, 189, .25);
}
[data-theme="dark"] .settings-checkbox input:focus-visible + .settings-checkbox__box {
  outline-color: rgba(110, 231, 223, .28);
}
.project-test-command {
  border-left: 2px solid #2855c7;
  padding-left: 10px;
}
</style>
