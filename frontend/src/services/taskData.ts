// 统一任务数据访问层：视图与 store 读取任务的唯一入口。
// real 模式走后端 API；mock 模式只读前端演示数据，不发任何请求。
import type { AgentTask, Task } from '../types/domain'
import { ApiError, apiRequest } from './api'
import { getDataSourceMode } from './dataSourceMode'
import { demoTasks } from './mockTaskSource'
import { mapAgentTask, registerTaskArtifact } from './taskMapper'

export interface TaskListQuery {
  projectId?: string
  includeArchived?: boolean
  status?: string
  risk?: string
  keyword?: string
  manualOnly?: boolean
  timeRange?: string
}

export class TaskNotFoundError extends ApiError {
  constructor(message = '任务不存在') {
    super(message, 404)
    this.name = 'TaskNotFoundError'
  }
}

export function errorStatus(error: unknown): number | undefined {
  return error instanceof ApiError ? error.status : undefined
}

function buildListPath(query: TaskListQuery): string {
  const params = new URLSearchParams()
  if (query.projectId) params.set('project_id', query.projectId)
  if (query.includeArchived) params.set('include_archived', 'true')
  if (query.status) params.set('status', query.status)
  if (query.risk) params.set('risk', query.risk)
  if (query.keyword?.trim()) params.set('keyword', query.keyword.trim())
  if (query.manualOnly) params.set('manual_only', 'true')
  if (query.timeRange) params.set('time_range', query.timeRange)
  return `/api/tasks${params.size ? `?${params.toString()}` : ''}`
}

export async function listTasks(query: TaskListQuery = {}): Promise<Task[]> {
  if (getDataSourceMode() === 'mock') {
    return demoTasks.map((task) => ({ ...task }))
  }
  const agentTasks = await apiRequest<AgentTask[]>(buildListPath(query))
  return agentTasks.map((agentTask) => {
    const task = mapAgentTask(agentTask)
    registerTaskArtifact(agentTask)
    return task
  })
}

export async function getTask(id: string): Promise<Task> {
  if (getDataSourceMode() === 'mock') {
    const demoTask = demoTasks.find((task) => task.id === id)
    if (!demoTask) throw new TaskNotFoundError(`演示数据中不存在任务：${id}`)
    return { ...demoTask }
  }
  const agentTask = await apiRequest<AgentTask>(`/api/tasks/${id}`)
  registerTaskArtifact(agentTask)
  return mapAgentTask(agentTask)
}
