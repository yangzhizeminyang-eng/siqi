import type {
  TeamRequest,
  TeamRequestCreatePayload,
  TeamRequestEventsResponse,
  TeamRequestListQuery,
  TeamRequestTaskResponse,
  TeamRequestUpdatePayload,
} from '../types/domain'
import { apiRequest } from './api'

function listPath(query: TeamRequestListQuery = {}) {
  const params = new URLSearchParams()
  if (query.project_id) params.set('project_id', query.project_id)
  if (query.repository_id) params.set('repository_id', query.repository_id)
  if (query.sync_status) params.set('sync_status', query.sync_status)
  if (query.type) params.set('type', query.type)
  if (query.created_by) params.set('created_by', query.created_by)
  return `/api/team/requests${params.size ? `?${params.toString()}` : ''}`
}

export function listTeamRequests(query: TeamRequestListQuery = {}) {
  return apiRequest<TeamRequest[]>(listPath(query))
}

export function getTeamRequest(id: string) {
  return apiRequest<TeamRequest>(`/api/team/requests/${encodeURIComponent(id)}`)
}

export function createTeamRequest(payload: TeamRequestCreatePayload) {
  return apiRequest<TeamRequest>('/api/team/requests', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function updateTeamRequest(id: string, payload: TeamRequestUpdatePayload) {
  return apiRequest<TeamRequest>(`/api/team/requests/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  })
}

/** DEV-016-B：平台需求同步创建 Gitee Issue；失败时返回 SYNC_FAILED 草稿，由页面按 manual_reason 分流展示。 */
export function syncTeamRequestGitee(id: string) {
  return apiRequest<TeamRequest>(`/api/team/requests/${encodeURIComponent(id)}/sync-gitee`, {
    method: 'POST',
  })
}

/** DEV-016-B：平台需求同步事件时间线（回放）。 */
export function getTeamRequestEvents(id: string) {
  return apiRequest<TeamRequestEventsResponse>(`/api/team/requests/${encodeURIComponent(id)}/events`)
}

/** DEV-016-C：从已同步需求创建/复用 Agent 任务；同 Issue 未结束任务时后端返回 reused + jump_url。 */
export function createTaskFromTeamRequest(id: string) {
  return apiRequest<TeamRequestTaskResponse>(`/api/team/requests/${encodeURIComponent(id)}/create-task`, {
    method: 'POST',
  })
}
