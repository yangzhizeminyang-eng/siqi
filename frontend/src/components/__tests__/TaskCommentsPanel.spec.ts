import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import TaskCommentsPanel from '../TaskCommentsPanel.vue'
import { setDataSourceMode } from '../../services/dataSourceMode'
import type { TaskComment, TaskNotification } from '../../types/domain'

const { apiRequestMock, displayUserMock } = vi.hoisted(() => ({
  apiRequestMock: vi.fn(),
  displayUserMock: vi.fn(),
}))

vi.mock('element-plus', () => ({
  ElMessage: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
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
  useAuthStore: () => ({ displayUser: displayUserMock() }),
}))

const comment: TaskComment = {
  id: 'cmt_1',
  task_id: 'TASK-9001',
  project_id: 'p',
  repository_id: 'r',
  author_id: 'user_developer',
  author_name: '张伟',
  body: '这段样式建议 @王琳 复核',
  mentioned_user_ids: ['user_approver'],
  reference_type: 'task',
  reference_id: 'TASK-9001',
  created_at: '2026-09-08T08:00:00Z',
  deleted_at: null,
}

const notif: TaskNotification = {
  id: 'notif_1',
  recipient_id: 'user_developer',
  task_id: 'TASK-9001',
  type: 'MENTION',
  title: '任务中 @了你',
  body: '请确认',
  actor_id: 'user_approver',
  actor_name: '王琳',
  priority: 'normal',
  read_at: null,
  created_at: '2026-09-08T09:00:00Z',
}

function mountPanel(status = 'WAITING_APPROVAL') {
  return mount(TaskCommentsPanel, { props: { taskId: 'TASK-9001', taskStatus: status } })
}

describe('DEV-019 TaskCommentsPanel 评论/通知/催办', () => {
  beforeEach(() => {
    setDataSourceMode('real')
    apiRequestMock.mockReset()
    displayUserMock.mockReset()
    displayUserMock.mockReturnValue({ id: 'user_developer', name: '张伟', role: 'developer' })
    apiRequestMock.mockImplementation((url: string) => {
      if (url === '/api/tasks/TASK-9001/comments') return Promise.resolve([comment])
      if (url === '/api/tasks/TASK-9001/notifications') return Promise.resolve([notif])
      return Promise.reject(new Error(`unexpected ${url}`))
    })
  })

  it('加载并展示评论与通知', async () => {
    const wrapper = mountPanel()
    await flushPromises()
    expect(wrapper.text()).toContain('这段样式建议')
    expect(wrapper.text()).toContain('任务中 @了你')
  })

  it('发表评论调用 POST', async () => {
    const wrapper = mountPanel()
    await flushPromises()
    await wrapper.find('textarea').setValue('请 @王琳 确认')
    await wrapper.findAll('button').filter((n) => n.text().includes('发表评论'))[0]!.trigger('click')
    await flushPromises()
    const calls = apiRequestMock.mock.calls.filter((c) => c[0] === '/api/tasks/TASK-9001/comments' && c[1]?.method === 'POST')
    expect(calls.length).toBe(1)
    expect(JSON.parse(calls[0][1].body).body).toBe('请 @王琳 确认')
  })

  it('作者可删除自己的评论（软删除）', async () => {
    const wrapper = mountPanel()
    await flushPromises()
    await wrapper.findAll('button').filter((n) => n.text().includes('删除'))[0]!.trigger('click')
    const del = apiRequestMock.mock.calls.find((c) => c[0] === '/api/tasks/TASK-9001/comments/cmt_1' && c[1]?.method === 'DELETE')
    expect(del).toBeTruthy()
  })

  it('标记通知已读', async () => {
    const wrapper = mountPanel()
    await flushPromises()
    await wrapper.findAll('button').filter((n) => n.text().includes('标记已读'))[0]!.trigger('click')
    const read = apiRequestMock.mock.calls.find((c) => c[0] === '/api/notifications/notif_1/read' && c[1]?.method === 'POST')
    expect(read).toBeTruthy()
  })

  it('待审批时显示催办入口并调用 POST /nudge', async () => {
    const wrapper = mountPanel('WAITING_APPROVAL')
    await flushPromises()
    await wrapper.findAll('button').filter((n) => n.text().includes('发送催办通知'))[0]!.trigger('click')
    const nudge = apiRequestMock.mock.calls.find((c) => c[0] === '/api/tasks/TASK-9001/nudge' && c[1]?.method === 'POST')
    expect(nudge).toBeTruthy()
  })

  it('非待审批不显示催办入口', async () => {
    const wrapper = mountPanel('ANALYZED')
    await flushPromises()
    expect(wrapper.text()).not.toContain('发送催办通知')
  })
})