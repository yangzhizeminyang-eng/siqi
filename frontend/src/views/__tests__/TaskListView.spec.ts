import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { RouterLinkStub, flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import { makeAgentTask } from '../../test/fixtures'
import { setDataSourceMode } from '../../services/dataSourceMode'
import { useWorkspaceStore } from '../../stores/workspace'
import TaskListView from '../TaskListView.vue'

const { apiRequestMock, messageMocks } = vi.hoisted(() => ({
  apiRequestMock: vi.fn(),
  messageMocks: { success: vi.fn(), error: vi.fn() },
}))

vi.mock('element-plus', () => ({
  ElMessage: messageMocks,
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

vi.mock('../../stores/auth', () => ({
  useAuthStore: () => ({ hasPermission: () => true }),
}))

let activePinia: Pinia
let router: Router

async function mountList() {
  await router.push('/tasks')
  return mount(TaskListView, {
    global: {
      plugins: [activePinia, router],
      stubs: { RouterLink: RouterLinkStub, StatusTag: true },
    },
  })
}

function taskCalls(): string[] {
  return apiRequestMock.mock.calls.map((call) => String(call[0])).filter((path) => path.startsWith('/api/tasks'))
}

describe('TaskListView：mock 模式展示 4 条 demo 任务且任务接口不发请求', () => {
  beforeEach(async () => {
    setDataSourceMode('mock')
    localStorage.clear()
    activePinia = createPinia()
    setActivePinia(activePinia)
    router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/', component: { template: '<div />' } },
        { path: '/tasks', component: TaskListView },
        { path: '/tasks/:id', component: { template: '<div />' } },
        { path: '/approvals', component: { template: '<div />' } },
      ],
    })
    await router.push('/')
    apiRequestMock.mockReset()
    apiRequestMock.mockResolvedValue([])
    messageMocks.success.mockClear()
    messageMocks.error.mockClear()
  })

  afterEach(() => {
    setDataSourceMode('real')
  })

  it('渲染 4 条 demo 任务，可选中并进入详情', async () => {
    const wrapper = await mountList()
    await flushPromises()

    const rows = wrapper.findAll('.data-table--task tbody tr')
    expect(rows).toHaveLength(4)
    expect(wrapper.text()).toContain('TASK-8842')
    expect(wrapper.text()).toContain('TASK-8824')

    // 只允许 /api/projects 的项目请求，任务读取不发请求
    expect(taskCalls()).toEqual([])

    const demoRow = rows.find((row) => row.text().includes('TASK-8824'))!
    await demoRow.trigger('click')
    expect(useWorkspaceStore().selectedTaskId).toBe('TASK-8824')

    const detailHrefs = wrapper.findAllComponents(RouterLinkStub).map((link) => String(link.props('to')))
    expect(detailHrefs).toContain('/tasks/TASK-8824')
  })

  it('点击刷新按钮空转并提示刷新成功', async () => {
    const wrapper = await mountList()
    await flushPromises()

    const refreshButton = wrapper.findAll('button').find((button) => button.text().includes('刷新'))!
    await refreshButton.trigger('click')
    await flushPromises()

    expect(messageMocks.success).toHaveBeenCalledWith({
      message: '任务列表已刷新',
      grouping: true,
    })
    expect(taskCalls()).toEqual([])
  })

  it('使用产品级筛选下拉并能选择状态', async () => {
    const wrapper = await mountList()
    await flushPromises()

    const statusTrigger = wrapper.findAll('.task-filter-select__trigger')[1]
    await statusTrigger.trigger('click')

    expect(wrapper.find('.task-filter-select__menu').exists()).toBe(true)
    expect(wrapper.find('.task-filter-select__menu').attributes('role')).toBe('listbox')
    expect(wrapper.findAll('.task-filter-select__option')).toHaveLength(14)

    await wrapper.findAll('.task-filter-select__option')[1].trigger('click')

    expect(wrapper.findAll('.task-filter-select__trigger')[1].text()).toContain('已创建')
    expect(wrapper.find('.task-filter-select__menu').exists()).toBe(false)
  })

})

describe('TaskListView：real 模式刷新以 API 为准并清掉 demo 残留', () => {
  beforeEach(async () => {
    setDataSourceMode('real')
    localStorage.clear()
    activePinia = createPinia()
    setActivePinia(activePinia)
    router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/', component: { template: '<div />' } },
        { path: '/tasks', component: TaskListView },
        { path: '/tasks/:id', component: { template: '<div />' } },
        { path: '/approvals', component: { template: '<div />' } },
      ],
    })
    await router.push('/')
    apiRequestMock.mockReset()
    messageMocks.success.mockClear()
    messageMocks.error.mockClear()
  })

  it('store 有 demo 残留时，real 刷新 replace 为后端任务列表', async () => {
    // 预置一条 demo 形态任务（id 与后端返回不同），模拟旧会话残留
    const store = useWorkspaceStore()
    store.createTaskFromApi(makeAgentTask({ id: 'TASK-8842' }))
    expect(store.tasks).toHaveLength(1)

    apiRequestMock.mockImplementation((path: string) => {
      if (path === '/api/projects') return Promise.resolve([])
      return Promise.resolve([makeAgentTask()])
    })

    const wrapper = await mountList()
    await flushPromises()

    expect(taskCalls()).toEqual(['/api/tasks?project_id=proj_payment'])
    const rows = wrapper.findAll('.data-table--task tbody tr')
    expect(rows).toHaveLength(1)
    expect(wrapper.text()).toContain('TASK-9001')
    expect(wrapper.text()).not.toContain('TASK-8842')
    expect(store.tasks.map((task) => task.id)).toEqual(['TASK-9001'])
  })

  it('没有任务时展示明确的空状态', async () => {
    apiRequestMock.mockImplementation((path: string) => {
      if (path === '/api/projects') return Promise.resolve([])
      return Promise.resolve([])
    })

    const wrapper = await mountList()
    await flushPromises()

    expect(wrapper.find('.task-empty-state').exists()).toBe(true)
    expect(wrapper.text()).toContain('暂时没有任务')
  })
})
