import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { makeAgentTask } from '../../test/fixtures'
import { demoTasks } from '../mockTaskSource'
import { getDataSourceMode, setDataSourceMode } from '../dataSourceMode'
import { getTask, listTasks, TaskNotFoundError } from '../taskData'
import { getTaskArtifact } from '../taskMapper'

const { apiRequestMock, ApiErrorMock } = vi.hoisted(() => {
  class ApiErrorMock extends Error {
    status: number
    constructor(message: string, status = 0) {
      super(message)
      this.status = status
    }
  }
  return { apiRequestMock: vi.fn(), ApiErrorMock }
})

vi.mock('../api', () => ({
  apiRequest: apiRequestMock,
  ApiError: ApiErrorMock,
}))

describe('taskData：统一任务数据访问层（DEV-009）', () => {
  beforeEach(() => {
    setDataSourceMode('real')
    apiRequestMock.mockReset()
  })

  afterEach(() => {
    setDataSourceMode('real')
  })

  it('模式开关生效：setDataSourceMode 覆盖默认 real', () => {
    expect(getDataSourceMode()).toBe('real')
    setDataSourceMode('mock')
    expect(getDataSourceMode()).toBe('mock')
    setDataSourceMode('real')
    expect(getDataSourceMode()).toBe('real')
  })

  it('real listTasks：组装查询参数、逐条映射并注册产物', async () => {
    apiRequestMock.mockResolvedValue([makeAgentTask()])

    const tasks = await listTasks({ projectId: 'proj_1', status: 'FAILED', risk: 'high', keyword: '  token  ', manualOnly: true, timeRange: '7d' })

    expect(apiRequestMock).toHaveBeenCalledTimes(1)
    expect(apiRequestMock).toHaveBeenCalledWith('/api/tasks?project_id=proj_1&status=FAILED&risk=high&keyword=token&manual_only=true&time_range=7d')
    expect(tasks).toHaveLength(1)
    expect(tasks[0].id).toBe('TASK-9001')
    expect(getTaskArtifact('TASK-9001').isPlaceholder).toBeFalsy()
  })

  it('real listTasks：无查询条件请求裸路径，ApiError 原样上抛', async () => {
    apiRequestMock.mockResolvedValue([])
    await expect(listTasks()).resolves.toEqual([])
    expect(apiRequestMock).toHaveBeenCalledWith('/api/tasks')

    apiRequestMock.mockReset()
    apiRequestMock.mockRejectedValueOnce(new ApiErrorMock('后端错误', 500))
    await expect(listTasks()).rejects.toMatchObject({ status: 500 })
  })

  it('real getTask：请求任务详情并注册产物；404 原样上抛', async () => {
    apiRequestMock.mockResolvedValue(makeAgentTask())
    const task = await getTask('TASK-9001')
    expect(apiRequestMock).toHaveBeenCalledWith('/api/tasks/TASK-9001')
    expect(task.id).toBe('TASK-9001')
    expect(getTaskArtifact('TASK-9001').isPlaceholder).toBeFalsy()

    apiRequestMock.mockReset()
    apiRequestMock.mockRejectedValueOnce(new ApiErrorMock('任务不存在', 404))
    await expect(getTask('TASK-9001')).rejects.toMatchObject({ status: 404 })
  })

  it('mock listTasks：返回 4 条 demo 拷贝且不发请求', async () => {
    setDataSourceMode('mock')
    const tasks = await listTasks({ status: 'FAILED', keyword: '任意' })

    expect(apiRequestMock).not.toHaveBeenCalled()
    expect(tasks.map((task) => task.id)).toEqual(demoTasks.map((task) => task.id))
    expect(tasks).toHaveLength(4)
  })

  it('mock getTask：命中返回 demo 拷贝，未命中抛 TaskNotFoundError(404)', async () => {
    setDataSourceMode('mock')
    const demo = await getTask('TASK-8824')
    expect(apiRequestMock).not.toHaveBeenCalled()
    expect(demo.id).toBe('TASK-8824')
    expect(demo.isDemo).toBe(true)

    await expect(getTask('TASK-9999')).rejects.toBeInstanceOf(TaskNotFoundError)
    await expect(getTask('TASK-9999')).rejects.toMatchObject({ status: 404 })
  })
})
