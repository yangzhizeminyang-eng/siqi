import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import type { RepositoryIndex } from '../../types/domain'
import IndexView from '../IndexView.vue'

const { apiRequestMock, hasPermissionMock, routeState } = vi.hoisted(() => ({
  apiRequestMock: vi.fn(),
  hasPermissionMock: vi.fn(),
  routeState: { query: {} as Record<string, string | string[]> },
}))

vi.mock('vue-router', () => ({
  useRoute: () => routeState,
}))

vi.mock('../../stores/auth', () => ({
  useAuthStore: () => ({ hasPermission: hasPermissionMock }),
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

const SHA = 'a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2'

function makeIndex(overrides: Partial<RepositoryIndex> = {}): RepositoryIndex {
  return {
    id: 'idx_1',
    repository_id: 'repo_payment',
    project_id: 'proj_payment',
    base_branch: 'main',
    commit_sha: SHA,
    status: 'COMPLETED',
    mode: 'full',
    total_files: 12,
    processed_files: 12,
    chunk_count: 48,
    milvus_collection: null,
    error_message: null,
    error_stage: null,
    requested_by: 'user_admin',
    requested_by_name: '小泽',
    retry_count: 0,
    requested_at: '2026-09-04T09:00:00Z',
    created_at: '2026-09-04T09:00:00Z',
    updated_at: '2026-09-04T09:01:00Z',
    started_at: '2026-09-04T09:00:05Z',
    finished_at: '2026-09-04T09:00:50Z',
    ...overrides,
  }
}

function seedApi(indexes: RepositoryIndex[]) {
  apiRequestMock.mockImplementation((url: string, options?: { method?: string }) => {
    if (url === '/api/projects') return Promise.resolve([{ id: 'proj_payment', name: '支付中台' }])
    if (url.startsWith('/api/repositories?')) {
      return Promise.resolve([{ id: 'repo_payment', name: 'payment-service', default_branch: 'main', project_id: 'proj_payment' }])
    }
    if (url.endsWith('/indexes')) return Promise.resolve(indexes.map((item) => ({ ...item })))
    if (url.endsWith('/retry')) {
      const failed = indexes.find((item) => item.status === 'FAILED')
      return Promise.resolve(failed ? { ...failed, status: 'PENDING', retry_count: (failed.retry_count ?? 0) + 1 } : null)
    }
    if (options?.method === 'POST') {
      return Promise.resolve({ index_id: 'idx_new', repository_id: 'repo_payment', repository_name: 'payment-service', status: 'PENDING', commit_sha: SHA })
    }
    return Promise.resolve(null)
  })
}

let wrapper: VueWrapper | null = null

function mountPage() {
  wrapper = mount(IndexView)
  return wrapper
}

describe('IndexView：仓库索引管理页', () => {
  beforeEach(() => {
    hasPermissionMock.mockReset()
    hasPermissionMock.mockReturnValue(true)
    routeState.query = {}
  })

  afterEach(() => {
    wrapper?.unmount()
    wrapper = null
  })

  it('渲染索引历史各状态行与失败原因，详情弹窗展示统计', async () => {
    seedApi([
      makeIndex({ id: 'idx_1', status: 'COMPLETED', chunk_count: 48 }),
      makeIndex({
        id: 'idx_2',
        status: 'FAILED',
        error_stage: 'SYNCING',
        error_message: '模拟仓库同步超时',
        retry_count: 1,
        total_files: 0,
        processed_files: 0,
        chunk_count: 0,
      }),
      makeIndex({ id: 'idx_3', status: 'PENDING', processed_files: 0, total_files: 0, chunk_count: 0 }),
      makeIndex({ id: 'idx_4', status: 'STORING', processed_files: 8, total_files: 12, chunk_count: 40 }),
    ])
    const view = mountPage()
    await flushPromises()

    expect(view.text()).toContain('仓库索引')
    expect(view.text()).toContain('已完成')
    expect(view.text()).toContain('失败（SYNCING 阶段）')
    expect(view.text()).toContain('排队中')
    expect(view.text()).toContain('存储入库')
    expect(view.text()).toContain('已重试 1 次')
    expect(view.text()).toContain('模拟仓库同步超时')
    expect(view.text()).toContain('8 / 12')
    expect(view.text()).toContain('48')
    expect(view.text()).toContain('处理中 2 个 · 3s 自动刷新')

    const failRow = view.findAll('tbody tr')[1]
    const detailButton = failRow.findAll('button.text-button').find((button) => button.text().includes('详情'))
    await detailButton!.trigger('click')
    await flushPromises()
    const detail = view.find('.index-detail-modal')
    expect(detail.exists()).toBe(true)
    expect(detail.text()).toContain('idx_2')
    expect(detail.text()).toContain('失败阶段：SYNCING')
    expect(detail.text()).toContain('模拟仓库同步超时')
    expect(detail.text()).toContain('重试索引')
  })

  it('关键字筛选历史行，无匹配时显示空结果提示', async () => {
    seedApi([
      makeIndex({ id: 'idx_1', status: 'COMPLETED' }),
      makeIndex({ id: 'idx_2', status: 'FAILED', error_stage: 'EMBEDDING', error_message: '向量写入失败', retry_count: 0 }),
    ])
    const view = mountPage()
    await flushPromises()
    expect(view.findAll('tbody tr')).toHaveLength(2)

    await view.find('.search-field input').setValue('向量写入失败')
    expect(view.findAll('tbody tr')).toHaveLength(1)
    expect(view.text()).toContain('失败（EMBEDDING 阶段）')

    await view.find('.search-field input').setValue('no-such-commit')
    expect(view.findAll('tbody tr')).toHaveLength(1)
    expect(view.text()).toContain('没有匹配的索引记录')
  })

  it('触发索引走异步说明弹窗并调用创建接口，无停留等待打断', async () => {
    seedApi([])
    const view = mountPage()
    await flushPromises()

    await view.findAll('button').find((button) => button.text().includes('触发索引'))!.trigger('click')
    expect(view.find('.index-create-modal').exists()).toBe(true)
    expect(view.text()).toContain('为仓库生成新的索引任务')
    expect(view.text()).toContain('异步排队执行')

    await view.findAll('button').find((button) => button.text().includes('确认触发'))!.trigger('click')
    await flushPromises()

    expect(apiRequestMock).toHaveBeenCalledWith('/api/repositories/repo_payment/index', {
      method: 'POST',
      body: JSON.stringify({ base_branch: 'main', force_rebuild: false }),
    })
    expect(view.text()).toContain('payment-service · 索引任务已创建')
    expect(view.find('.index-create-modal').exists()).toBe(false)
  })

  it('重建索引弹窗带高影响说明并调用 rebuild 接口', async () => {
    seedApi([makeIndex({ id: 'idx_1', status: 'COMPLETED' })])
    const view = mountPage()
    await flushPromises()

    await view.findAll('button').find((button) => button.text().includes('重建索引'))!.trigger('click')
    expect(view.find('.index-create-modal').exists()).toBe(true)
    expect(view.text()).toContain('确认重建索引？')
    expect(view.text()).toContain('重建会生成新的检索版本')
    expect(view.text()).toContain('重建模式：full')

    await view.findAll('button').find((button) => button.text().includes('确认重建'))!.trigger('click')
    await flushPromises()

    expect(apiRequestMock).toHaveBeenCalledWith('/api/repositories/repo_payment/index/rebuild', {
      method: 'POST',
      body: JSON.stringify({ base_branch: 'main' }),
    })
    expect(view.text()).toContain('索引重建任务已创建')
  })

  it('FAILED 行重试调用 retry 接口并回到排队状态', async () => {
    seedApi([
      makeIndex({ id: 'idx_2', status: 'FAILED', error_stage: 'PARSING', error_message: '切片解析失败', retry_count: 0 }),
    ])
    const view = mountPage()
    await flushPromises()

    await view.findAll('button').find((button) => button.text().trim() === '重试')!.trigger('click')
    await flushPromises()

    expect(apiRequestMock).toHaveBeenCalledWith('/api/repositories/repo_payment/indexes/idx_2/retry', { method: 'POST' })
    expect(view.text()).toContain('已重新排队')
  })

  it('无 index:manage 权限时隐藏触发/重建/重试入口', async () => {
    hasPermissionMock.mockReturnValue(false)
    seedApi([makeIndex({ id: 'idx_2', status: 'FAILED', error_stage: 'PARSING', error_message: '切片解析失败', retry_count: 0 })])
    const view = mountPage()
    await flushPromises()

    const buttonTexts = view.findAll('button').map((button) => button.text())
    expect(buttonTexts).not.toContain('触发索引')
    expect(buttonTexts).not.toContain('重建索引')
    expect(buttonTexts).not.toContain('重试')
    expect(buttonTexts).toContain('详情')
  })

  it('运行中任务启动 3 秒轮询，结束后自动停止', async () => {
    let setIntervalSpy: MockInstance | null = null
    setIntervalSpy = vi.spyOn(window, 'setInterval')
    const clearSpy = vi.spyOn(window, 'clearInterval')

    const indexes = [makeIndex({ id: 'idx_3', status: 'PENDING', processed_files: 0, total_files: 0, chunk_count: 0 })]
    seedApi(indexes)
    const view = mountPage()
    await flushPromises()

    expect(setIntervalSpy).toHaveBeenCalledWith(expect.any(Function), 3000)

    indexes[0] = makeIndex({ id: 'idx_3', status: 'COMPLETED' })
    await view.findAll('button').find((button) => button.text().includes('刷新'))!.trigger('click')
    await flushPromises()

    expect(clearSpy).toHaveBeenCalled()
    expect(view.text()).not.toContain('3s 自动刷新')

    setIntervalSpy.mockRestore()
    clearSpy.mockRestore()
  })
})
