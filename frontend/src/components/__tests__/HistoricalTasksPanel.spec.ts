import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import HistoricalTasksPanel from '../HistoricalTasksPanel.vue'
import { setDataSourceMode } from '../../services/dataSourceMode'
import { ApiError } from '../../services/api'
import type { HistoricalTaskReference } from '../../types/domain'

const { apiRequestMock } = vi.hoisted(() => ({ apiRequestMock: vi.fn() }))
const { pushMock } = vi.hoisted(() => ({ pushMock: vi.fn() }))

vi.mock('element-plus', () => ({
  ElMessage: { error: vi.fn(), success: vi.fn(), warning: vi.fn() },
  ElLoading: { service: vi.fn() },
}))

vi.mock('../../services/api', () => ({
  apiRequest: apiRequestMock,
  ApiError: class ApiError extends Error {
    status: number
    constructor(message: string, status = 0) {
      super(message)
      this.status = status
    }
  },
}))

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: pushMock }),
}))

const refs: HistoricalTaskReference[] = [
  {
    taskId: 'TASK-HIST-1',
    summaryId: 'hist_sum_TASK-HIST-1',
    title: '支付幂等修复',
    project: 'proj_payment',
    similarity: 0.86,
    completedAt: '2026-09-01T00:00:00Z',
    matchedSections: ['issue', 'analysis'],
    sourceLabel: '历史任务参考',
    summaryPreview: '处理支付回调重复入账',
    canOpenDetail: true,
  },
]

function mountPanel() {
  return mount(HistoricalTasksPanel, {
    props: { taskId: 'TASK-CUR', query: '支付幂等' },
  })
}

function mountCreatePanel() {
  return mount(HistoricalTasksPanel, {
    props: { projectId: 'proj_payment', query: 'TICKET-42 支付回调' },
  })
}

beforeEach(() => {
  setDataSourceMode('real')
  apiRequestMock.mockReset()
  pushMock.mockReset()
})

describe('DEV-021 HistoricalTasksPanel 历史相似任务', () => {
  it('real 模式请求 /api/history/search 并渲染结果', async () => {
    apiRequestMock.mockImplementation((path: string) => {
      if (path === '/api/history/search') return Promise.resolve(refs)
      return Promise.reject(new Error('unexpected'))
    })
    const wrapper = mountPanel()
    await flushPromises()
    expect(apiRequestMock.mock.calls.some((c) => c[0] === '/api/history/search')).toBe(true)
    expect(wrapper.text()).toContain('支付幂等修复')
    expect(wrapper.text()).toContain('历史任务参考')
    expect(wrapper.text()).toContain('相似度 86%')
    expect(wrapper.find('.history-refs__similarity').attributes('title')).toContain('不代表任务进度')
  })

  it('空结果显示空态', async () => {
    apiRequestMock.mockResolvedValue([])
    const wrapper = mountPanel()
    await flushPromises()
    expect(wrapper.text()).toContain('暂无相似历史任务')
  })

  it('检索失败显示轻量错误且不阻断', async () => {
    apiRequestMock.mockRejectedValue(new ApiError('检索失败', 500))
    const wrapper = mountPanel()
    await flushPromises()
    expect(wrapper.text()).toContain('检索失败')
  })

  it('点击详情只读展示且标注不自动套用旧 Diff', async () => {
    apiRequestMock.mockResolvedValue(refs)
    const wrapper = mountPanel()
    await flushPromises()
    const detailBtn = wrapper.findAll('button').find((b) => b.text().includes('详情'))
    await detailBtn!.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('不会自动应用旧 Diff')
    expect(wrapper.text()).toContain('处理支付回调重复入账')
  })

  it('打开历史任务跳转到该任务详情（不复制内容到当前任务）', async () => {
    apiRequestMock.mockResolvedValue(refs)
    const wrapper = mountPanel()
    await flushPromises()
    const openBtn = wrapper.findAll('button').find((b) => b.text().includes('打开'))
    await openBtn!.trigger('click')
    expect(pushMock).toHaveBeenCalledWith('/tasks/TASK-HIST-1')
  })

  it('创建任务上下文（无 taskId、仅 projectId）也能检索', async () => {
    apiRequestMock.mockImplementation((path: string) => {
      if (path === '/api/history/search') return Promise.resolve(refs)
      return Promise.reject(new Error('unexpected'))
    })
    const wrapper = mountCreatePanel()
    await flushPromises()
    const calls = apiRequestMock.mock.calls.filter((c) => c[0] === '/api/history/search')
    expect(calls.length).toBe(1)
    const body = JSON.parse(calls[0][1].body)
    expect(body.projectId).toBe('proj_payment')
    expect(body.taskId).toBeUndefined()
    expect(wrapper.text()).toContain('支付幂等修复')
  })
})
