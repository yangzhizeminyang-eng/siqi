import type { HistoricalTaskReference, HistoricalTaskSearchInput } from '../types/domain'
import { apiRequest } from './api'

/** DEV-021 历史任务复用检索（项目隔离 + 权限过滤均由后端完成）。 */
export async function searchHistoricalTasks(input: HistoricalTaskSearchInput): Promise<HistoricalTaskReference[]> {
  return apiRequest<HistoricalTaskReference[]>('/api/history/search', {
    method: 'POST',
    body: JSON.stringify(input),
  })
}