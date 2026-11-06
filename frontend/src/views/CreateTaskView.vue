<script setup lang="ts">
import { ArrowLeft, CircleAlert, GitBranch, Info, LoaderCircle, Save, ShieldCheck } from '@lucide/vue'
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { useRouter } from 'vue-router'
import { ApiError, apiRequest } from '../services/api'
import { useAuthStore } from '../stores/auth'
import { useWorkspaceStore } from '../stores/workspace'
import TaskFilterSelect from '../components/TaskFilterSelect.vue'
import HistoricalTasksPanel from '../components/HistoricalTasksPanel.vue'
import type { TaskFilterOption } from '../components/TaskFilterSelect.vue'
import type { AgentTask, Project, RepositoryConfig } from '../types/domain'

const router = useRouter()
const auth = useAuthStore()
const store = useWorkspaceStore()
const loading = ref(false)
const creating = ref(false)
const submitted = ref(false)
const projects = ref<Project[]>([])
const repositories = ref<RepositoryConfig[]>([])
const form = reactive({
  projectId: '',
  repositoryId: '',
  issueId: '',
})
const duplicateTask = ref<AgentTask | null>(null)
const createError = ref('')

const selectedProject = computed(() => projects.value.find((project) => project.id === form.projectId) ?? null)
const selectedRepository = computed(() => repositories.value.find((repository) => repository.id === form.repositoryId) ?? null)
const projectOptions = computed<TaskFilterOption[]>(() => projects.value.map((project) => ({ value: project.id, label: project.name })))
const repositoryOptions = computed<TaskFilterOption[]>(() => repositories.value.map((repository) => ({ value: repository.id, label: repository.name })))
const branchPart = computed(() => (selectedRepository.value?.default_branch ?? 'main').replace(/[^A-Za-z0-9._-]+/g, '-').replace(/^-+|-+$/g, '') || 'main')
const issuePart = computed(() => form.issueId.trim().replace(/^#/, '').replace(/[^A-Za-z0-9._-]+/g, '-') || 'xxxx')
const previewVersion = computed(() => {
  const issueKey = form.issueId.trim().replace(/^#/, '')
  if (!issueKey || !selectedRepository.value) return 1
  let maxVersion = 0
  for (const task of store.tasks) {
    if (task.repository !== selectedRepository.value.name) continue
    if (task.issue.replace(/^#/, '') !== issueKey) continue
    const match = /-v(\d+)$/.exec(task.branch)
    if (match) maxVersion = Math.max(maxVersion, Number(match[1]))
  }
  return maxVersion + 1
})
const workBranch = computed(() => `agent/${branchPart.value}/issue-${issuePart.value}-v${previewVersion.value}`)
const canSubmit = computed(() => Boolean(auth.hasPermission('task:create') && form.projectId && form.repositoryId && form.issueId.trim() && !creating.value))

onMounted(() => {
  void loadProjects()
})

function apiMessage(error: unknown, fallback: string) {
  return error instanceof ApiError ? error.message : fallback
}

async function loadProjects() {
  loading.value = true
  createError.value = ''
  try {
    projects.value = await apiRequest<Project[]>('/api/projects')
    form.projectId = projects.value[0]?.id ?? ''
    await loadRepositories()
  } catch (error) {
    createError.value = apiMessage(error, '项目配置加载失败')
    ElMessage.error(createError.value)
  } finally {
    loading.value = false
  }
}

async function loadRepositories() {
  repositories.value = []
  form.repositoryId = ''
  duplicateTask.value = null
  if (!form.projectId) return
  try {
    repositories.value = await apiRequest<RepositoryConfig[]>(`/api/repositories?project_id=${encodeURIComponent(form.projectId)}`)
    form.repositoryId = repositories.value[0]?.id ?? ''
  } catch (error) {
    createError.value = apiMessage(error, '仓库配置加载失败')
    ElMessage.error(createError.value)
  }
}

function duplicateFrom(error: ApiError) {
  const data = error.data as { existing_task?: AgentTask } | null | undefined
  return data?.existing_task ?? null
}

async function createTask() {
  if (!auth.hasPermission('task:create')) {
    ElMessage.warning('您暂无权限创建任务，请联系开发者处理')
    return
  }
  submitted.value = true
  duplicateTask.value = null
  createError.value = ''
  if (!form.projectId || !form.repositoryId || !form.issueId.trim()) return

  creating.value = true
  try {
    const agentTask = await apiRequest<AgentTask>('/api/tasks', {
      method: 'POST',
      body: JSON.stringify({
        project_id: form.projectId,
        repository_id: form.repositoryId,
        issue_id: form.issueId.trim().replace(/^#/, ''),
      }),
    })
    const task = store.createTaskFromApi(agentTask)
    ElMessage.success('Issue 已拉取，任务已进入分析队列')
    router.push(`/tasks/${task.id}`)
  } catch (error) {
    if (error instanceof ApiError && error.status === 409) {
      duplicateTask.value = duplicateFrom(error)
    }
    createError.value = apiMessage(error, '任务创建失败')
    ElMessage.error(createError.value)
  } finally {
    creating.value = false
  }
}

function goDuplicateTask() {
  if (!duplicateTask.value) return
  const task = store.createTaskFromApi(duplicateTask.value)
  router.push(`/tasks/${task.id}`)
}
</script>

<template>
  <section class="page-content create-task">
    <div v-if="!auth.hasPermission('task:create')" class="page-notice page-notice--info">
      <span class="page-notice__icon"><Info :size="16" /></span>
      <div class="page-notice__body">
        <strong>您当前仅可查看任务</strong>
        <p>创建 Agent 任务需要开发者权限。</p>
      </div>
    </div>

    <div class="breadcrumb"><RouterLink to="/tasks"><ArrowLeft :size="15" /> 返回任务列表</RouterLink><span>/</span><b>创建任务</b></div>

    <div class="create-task__layout">
      <form class="panel form-panel" @submit.prevent="createTask">
        <header class="panel__header">
          <div>
            <h2>从 Gitee Issue 创建任务</h2>
            <p>选择项目和仓库，填写 Issue ID，其余参数由项目配置自动带出。</p>
          </div>
        </header>

        <div v-if="createError" class="form-alert create-task__error">
          <CircleAlert :size="17" />
          <span>{{ createError }}</span>
        </div>

        <div v-if="duplicateTask" class="page-notice page-notice--info create-task__duplicate">
          <span class="page-notice__icon"><CircleAlert :size="16" /></span>
          <div class="page-notice__body">
            <strong>该 Issue 已有未结束任务</strong>
            <p>{{ duplicateTask.id }} · {{ duplicateTask.title }}，系统建议继续处理已有任务。</p>
          </div>
          <button class="button button--primary" type="button" @click="goDuplicateTask">跳转已有任务</button>
        </div>

        <div class="form-grid create-task__minimal">
          <label>
            <span>项目 <b>*</b></span>
            <div v-if="projects.length <= 1" class="create-task__locked-field">
              <strong>{{ selectedProject?.name ?? '暂无项目' }}</strong>
              <small>{{ selectedProject ? '当前只有一个项目，系统已自动选中' : '请先在项目配置中创建项目' }}</small>
            </div>
            <TaskFilterSelect v-else v-model="form.projectId" :options="projectOptions" ariaLabel="项目" :disabled="loading || creating" class="create-task-select" />
          </label>
          <label>
            <span>仓库 <b>*</b></span>
            <div v-if="repositories.length <= 1" class="create-task__locked-field">
              <strong>{{ selectedRepository?.name ?? '暂无仓库配置' }}</strong>
              <small>{{ selectedRepository ? '当前项目只有一个仓库，系统已自动选中' : '请先在项目配置中添加 Gitee 仓库' }}</small>
            </div>
            <TaskFilterSelect v-else v-model="form.repositoryId" :options="repositoryOptions" ariaLabel="仓库" :disabled="loading || creating || !repositories.length" class="create-task-select" />
          </label>
          <label>
            <span>Issue ID <b>*</b></span>
            <div class="input-prefix"><i>#</i><input v-model="form.issueId" :disabled="creating" inputmode="text" placeholder="1842 或 I8ABC1" /></div>
            <small v-if="submitted && !form.issueId.trim()" class="field-error">请填写 Gitee Issue ID</small>
          </label>
        </div>

        <div class="create-task__auto">
          <div class="create-task__auto-item"><span>基线分支</span><strong class="mono">{{ selectedRepository?.default_branch ?? '-' }}</strong></div>
          <div class="create-task__auto-item"><span>测试命令</span><strong class="mono">{{ selectedRepository?.test_command ?? '-' }}</strong></div>
          <div class="create-task__auto-item"><span>默认审批人</span><strong>{{ selectedRepository?.approval_owner_name ?? '-' }}</strong></div>
          <div class="create-task__auto-item"><span>工作分支</span><strong class="mono"><GitBranch :size="15" /> {{ workBranch }}</strong></div>
        </div>

        <div class="form-alert">
          <ShieldCheck :size="17" />
          <span>风险等级不会在创建时填写；系统会在 AI 分析和修改计划阶段结合安全策略计算。</span>
        </div>

        <footer class="form-footer">
          <button type="button" class="button button--secondary" :disabled="creating" @click="router.back()">取消</button>
          <button type="submit" class="button button--primary" :disabled="!canSubmit">
            <LoaderCircle v-if="creating" class="button__spinner" :size="16" />
            <Save v-else :size="16" />
            {{ creating ? '正在拉取 Issue' : '创建任务' }}
          </button>
        </footer>
      </form>

      <HistoricalTasksPanel v-if="form.projectId" :project-id="form.projectId" :query="form.issueId" />

      <aside class="create-task__aside">
        <section class="aside-note">
          <span class="aside-note__icon"><Info :size="18" /></span>
          <h2>自动带出</h2>
          <ul>
            <li>项目和仓库来自已配置列表，只有多个可选项时才需要切换。</li>
            <li>基线分支、测试命令、审批人来自项目仓库配置。</li>
            <li>创建成功后自动保存 Issue 快照并进入分析队列。</li>
            <li>重复未结束任务会提示跳转，不重复创建。</li>
          </ul>
        </section>
        <section class="aside-note aside-note--quiet">
          <h2>Issue 快照</h2>
          <p>标题、正文、评论、标签和 Gitee 原始响应会在创建时保存，后续回放不会受外部 Issue 更新影响。</p>
        </section>
      </aside>
    </div>
  </section>
</template>
