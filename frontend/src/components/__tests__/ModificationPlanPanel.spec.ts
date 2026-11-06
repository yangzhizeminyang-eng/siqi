import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { RouterLinkStub, flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import type { AgentTask, ModificationPlan } from '../../types/domain'
import { makeAgentTask, makeModificationPlan } from '../../test/fixtures'
import { setDataSourceMode } from '../../services/dataSourceMode'
import { useWorkspaceStore } from '../../stores/workspace'
import { ApiError } from '../../services/api'
import ModificationPlanPanel from '../ModificationPlanPanel.vue'

const { hasPermissionMock } = vi.hoisted(() => ({ hasPermissionMock: vi.fn() }))
const { apiRequestMock } = vi.hoisted(() => ({ apiRequestMock: vi.fn() }))

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

let activePinia: Pinia

function grantPermissions(...permissions: string[]) {
  hasPermissionMock.mockReset()
  hasPermissionMock.mockImplementation((permission: string) => permissions.includes(permission))
}

function waitingTask(plans: ModificationPlan[]): AgentTask {
  return makeAgentTask({
    status: 'WAITING_APPROVAL',
    automation_state: 'waiting_approval',
    manual_required: true,
    manual_reason: 'approval_required',
    next_action: 'review_plan',
    risk_level: plans[0].risk_level,
    risk_source: 'plan',
    plans,
  })
}

function routeByUrlMock(handler: (url: string, init?: { method?: string; body?: string }) => unknown) {
  apiRequestMock.mockReset()
  apiRequestMock.mockImplementation((url: string, init?: { method?: string; body?: string }) => {
    const result = handler(url, init)
    return Promise.resolve(result)
  })
}

function mountPanel(taskId = 'TASK-9001') {
  return mount(ModificationPlanPanel, {
    props: { taskId },
    global: {
      plugins: [activePinia],
      stubs: { RouterLink: RouterLinkStub },
    },
  })
}

async function approvePosts() {
  return apiRequestMock.mock.calls.filter(
    (call) => call[1]?.method === 'POST' && String(call[0]).endsWith('/approve'),
  )
}

describe('ModificationPlanPanel', () => {
  beforeEach(() => {
    setDataSourceMode('real')
    localStorage.clear()
    activePinia = createPinia()
    setActivePinia(activePinia)
    apiRequestMock.mockReset()
    hasPermissionMock.mockReset()
  })

  afterEach(() => {
    setDataSourceMode('real')
  })

  it('ANALYZED 且具备 task:operate：展示生成按钮，点击后 POST 并渲染新计划', async () => {
    grantPermissions('task:operate')
    const store = useWorkspaceStore()
    store.createTaskFromApi(makeAgentTask({ status: 'ANALYZED', plans: null }))
    const plan = makeModificationPlan()
    let plans: ModificationPlan[] = []
    routeByUrlMock((url, init) => {
      if (init?.method === 'POST' && url === '/api/tasks/TASK-9001/plans') {
        plans = [plan]
        return waitingTask(plans)
      }
      if (url === '/api/tasks/TASK-9001/plans') return plans
      throw new ApiError(`未预期请求 ${url}`, 500)
    })

    const wrapper = mountPanel()
    await flushPromises()
    const generateButton = wrapper.findAll('button').find((button) => button.text().includes('生成修改计划'))
    expect(generateButton).toBeDefined()

    generateButton!.trigger('click')
    await flushPromises()

    const createCall = apiRequestMock.mock.calls.find(
      (call) => call[1]?.method === 'POST' && call[0] === '/api/tasks/TASK-9001/plans',
    )
    expect(createCall).toBeDefined()
    expect(wrapper.text()).toContain('增加幂等键')
    expect(wrapper.text()).toContain('待审批')
    expect(wrapper.findAll('button').some((button) => button.text().includes('生成修改计划'))).toBe(false)
  })

  it('待审批计划 + approval:manage：三操作可用，不调风险直接通过时请求体为空', async () => {
    grantPermissions('approval:manage')
    const store = useWorkspaceStore()
    const plan = makeModificationPlan()
    store.createTaskFromApi(waitingTask([plan]))
    let plans = [plan]
    routeByUrlMock((url, init) => {
      if (url === '/api/tasks/TASK-9001/plans' && init?.method !== 'POST') return plans
      if (init?.method === 'POST' && url.endsWith('/approve')) {
        plans = [{ ...plan, status: 'APPROVED' as const }]
        return makeAgentTask({
          status: 'APPROVED',
          automation_state: 'blocked',
          manual_required: false,
          manual_reason: 'none',
          next_action: 'auto_generate_diff',
          risk_level: 'high',
          risk_source: 'plan',
          plans,
        })
      }
      throw new ApiError(`未预期请求 ${url}`, 500)
    })

    const wrapper = mountPanel()
    await flushPromises()

    expect(wrapper.text()).toContain('回调入口增加幂等校验。')
    const buttons = wrapper.findAll('button').map((button) => button.text())
    expect(buttons).toContain('通过')
    expect(buttons).toContain('要求重写')
    expect(buttons).toContain('拒绝')

    await wrapper.find('.plan-actions__buttons .button--success').trigger('click')
    await flushPromises()

    const posts = await approvePosts()
    expect(posts).toHaveLength(1)
    expect(JSON.parse(String(posts[0][1]?.body))).toEqual({})
    expect(wrapper.text()).toContain('计划已通过')
    expect(wrapper.text()).toContain('已通过')
  })

  it('审批人调整风险等级：必填调整原因，否则禁用提交；提交携带调整值并展示结果', async () => {
    grantPermissions('approval:manage')
    const store = useWorkspaceStore()
    const plan = makeModificationPlan()
    store.createTaskFromApi(waitingTask([plan]))
    let plans = [plan]
    routeByUrlMock((url, init) => {
      if (url === '/api/tasks/TASK-9001/plans' && init?.method !== 'POST') return plans
      if (init?.method === 'POST' && url.endsWith('/approve')) {
        plans = [
          {
            ...plan,
            risk_level: 'medium' as const,
            status: 'APPROVED' as const,
            risk_adjust_reason: '仅回调入口新增幂等校验，无资金链路改动。',
          },
        ]
        return makeAgentTask({
          status: 'APPROVED',
          automation_state: 'blocked',
          manual_required: false,
          manual_reason: 'none',
          next_action: 'auto_generate_diff',
          risk_level: 'medium',
          risk_source: 'plan_approval',
          plans,
        })
      }
      throw new ApiError(`未预期请求 ${url}`, 500)
    })

    const wrapper = mountPanel()
    await flushPromises()

    await wrapper.find('.plan-action-field--select .task-filter-select__trigger').trigger('click')
    await wrapper.findAll('.plan-action-field--select .task-filter-select__option').find((option) => option.text() === '中风险')!.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('调整风险原因')
    expect(wrapper.find('.plan-actions__buttons .button--success').attributes('disabled')).toBeDefined()

    await wrapper.findAll('.plan-action-field textarea')[1].setValue('仅回调入口新增幂等校验，无资金链路改动。')
    await flushPromises()
    expect(wrapper.find('.plan-actions__buttons .button--success').attributes('disabled')).toBeUndefined()

    await wrapper.find('.plan-actions__buttons .button--success').trigger('click')
    await flushPromises()

    const posts = await approvePosts()
    expect(posts).toHaveLength(1)
    expect(JSON.parse(String(posts[0][1]?.body))).toEqual({
      risk_level: 'medium',
      risk_adjust_reason: '仅回调入口新增幂等校验，无资金链路改动。',
    })
    expect(wrapper.text()).toContain('中风险')
    expect(wrapper.text()).toContain('审批人已调整风险等级并补充原因')
  })

  it('要求重写：原因必填，提交后新版本取代旧版并渲染版本切换器', async () => {
    grantPermissions('approval:manage')
    const store = useWorkspaceStore()
    const plan = makeModificationPlan()
    store.createTaskFromApi(waitingTask([plan]))
    let plans = [plan]
    routeByUrlMock((url, init) => {
      if (url === '/api/tasks/TASK-9001/plans' && init?.method !== 'POST') return plans
      if (init?.method === 'POST' && url.endsWith('/rewrite')) {
        const next = makeModificationPlan({
          id: 'PLAN-9001-2',
          version: 2,
          goal: '按幂等键跳过重复回调，并补充监控告警。',
          created_by: 'user_approver',
          created_by_name: '陈晓',
        })
        plans = [{ ...plan, status: 'SUPERSEDED' as const }, next]
        return waitingTask(plans)
      }
      throw new ApiError(`未预期请求 ${url}`, 500)
    })

    const wrapper = mountPanel()
    await flushPromises()

    await wrapper.findAll('button').find((button) => button.text().includes('要求重写'))!.trigger('click')
    await flushPromises()
    const confirmButton = wrapper.findAll('.plan-action-mode button').find((button) => button.text().includes('确认提交'))
    expect(confirmButton!.attributes('disabled')).toBeDefined()

    await wrapper.find('.plan-action-mode textarea').setValue('文件范围需覆盖测试目录与告警配置。')
    await flushPromises()
    await confirmButton!.trigger('click')
    await flushPromises()

    const rewriteCall = apiRequestMock.mock.calls.find(
      (call) => call[1]?.method === 'POST' && String(call[0]).endsWith('/rewrite'),
    )
    expect(rewriteCall).toBeDefined()
    expect(JSON.parse(String(rewriteCall![1]?.body))).toEqual({ reason: '文件范围需覆盖测试目录与告警配置。' })
    expect(wrapper.text()).toContain('按幂等键跳过重复回调，并补充监控告警。')
    const chips = wrapper.findAll('.plan-versions__item')
    expect(chips.map((chip) => chip.text())).toEqual(['v2', 'v1'])
    expect(chips[0].classes()).toContain('is-active')
    expect(chips[1].classes()).toContain('is-dead')
    expect(wrapper.find('.plan-action-mode').exists()).toBe(false)
  })

  it('拒绝：原因必填，提交后计划 REJECTED 并展示驳回说明', async () => {
    grantPermissions('approval:manage')
    const store = useWorkspaceStore()
    const plan = makeModificationPlan()
    store.createTaskFromApi(waitingTask([plan]))
    let plans = [plan]
    routeByUrlMock((url, init) => {
      if (url === '/api/tasks/TASK-9001/plans' && init?.method !== 'POST') return plans
      if (init?.method === 'POST' && url.endsWith('/reject')) {
        plans = [{ ...plan, status: 'REJECTED' as const }]
        return makeAgentTask({
          status: 'FAILED',
          automation_state: 'blocked',
          manual_required: true,
          manual_reason: 'plan_rejected',
          next_action: 'retry_or_cancel',
          failure_node: '审批驳回',
          failure_reason: '范围不清，需要补充支付幂等细节。',
          risk_level: null,
          risk_source: 'pending_ai_analysis',
          plans,
        })
      }
      throw new ApiError(`未预期请求 ${url}`, 500)
    })

    const wrapper = mountPanel()
    await flushPromises()

    await wrapper.findAll('button').find((button) => button.text().includes('拒绝'))!.trigger('click')
    await flushPromises()
    const confirmButton = wrapper.findAll('.plan-action-mode button').find((button) => button.text().includes('确认提交'))
    expect(confirmButton!.attributes('disabled')).toBeDefined()

    await wrapper.find('.plan-action-mode textarea').setValue('范围不清，需要补充支付幂等细节。')
    await flushPromises()
    await confirmButton!.trigger('click')
    await flushPromises()

    const rejectCall = apiRequestMock.mock.calls.find(
      (call) => call[1]?.method === 'POST' && String(call[0]).endsWith('/reject'),
    )
    expect(rejectCall).toBeDefined()
    expect(JSON.parse(String(rejectCall![1]?.body))).toEqual({ reason: '范围不清，需要补充支付幂等细节。' })
    expect(wrapper.text()).toContain('已驳回')
    expect(wrapper.text()).toContain('该版本计划已被驳回')
  })

  it('多版本：默认展示最新版，切换历史版本为只读回顾且无操作区', async () => {
    grantPermissions('approval:manage')
    const store = useWorkspaceStore()
    const first = makeModificationPlan({
      id: 'PLAN-9001-1',
      version: 1,
      goal: '初版：回调去重。',
      status: 'SUPERSEDED',
    })
    const second = makeModificationPlan({
      id: 'PLAN-9001-2',
      version: 2,
      goal: '第二版：回调去重并补测试。',
    })
    const plans = [first, second]
    store.createTaskFromApi(waitingTask(plans))
    routeByUrlMock((url, init) => {
      if (url === '/api/tasks/TASK-9001/plans' && init?.method !== 'POST') return plans
      throw new ApiError(`未预期请求 ${url}`, 500)
    })

    const wrapper = mountPanel()
    await flushPromises()

    expect(wrapper.text()).toContain('第二版：回调去重并补测试。')
    expect(wrapper.find('.plan-actions__buttons .button--success').exists()).toBe(true)

    const versionChips = wrapper.findAll('.plan-versions__item')
    await versionChips.find((chip) => chip.text().startsWith('v1'))!.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('正在查看历史版本 v1')
    expect(wrapper.text()).toContain('初版：回调去重。')
    expect(wrapper.find('.plan-actions__buttons .button--success').exists()).toBe(false)
    expect(wrapper.find('.plan-action-mode').exists()).toBe(false)
  })

  it('非审批人（仅 task:operate）：只读提示，无操作按钮与跳转', async () => {
    grantPermissions('task:operate')
    const store = useWorkspaceStore()
    const plan = makeModificationPlan()
    store.createTaskFromApi(waitingTask([plan]))
    routeByUrlMock((url, init) => {
      if (url === '/api/tasks/TASK-9001/plans' && init?.method !== 'POST') return [plan]
      throw new ApiError(`未预期请求 ${url}`, 500)
    })

    const wrapper = mountPanel()
    await flushPromises()

    expect(wrapper.text()).toContain('等待 陈晓 处理')
    expect(wrapper.text()).toContain('回调入口增加幂等校验。')
    const buttons = wrapper.findAll('button').map((button) => button.text())
    expect(buttons).not.toContain('通过')
    expect(buttons).not.toContain('要求重写')
    expect(buttons).not.toContain('拒绝')
    expect(buttons).not.toContain('前往审批')
  })

  it('ANALYZED 且未指派审批人 + approval:assign：自动展开指派控件，确认后 POST 指派接口', async () => {
    grantPermissions('approval:assign', 'task:operate')
    const store = useWorkspaceStore()
    const ownerless = makeAgentTask({
      status: 'ANALYZED',
      plans: null,
      approval_owner_id: null,
      approval_owner_name: null,
      manual_required: false,
      manual_reason: 'none',
      next_action: 'generate_plan',
    })
    store.createTaskFromApi(ownerless)
    const candidates = [
      { id: 'user_approver', name: '陈晓', email: 'chenxiao@example.com', role: 'approver', role_label: '审批人', permissions: [] },
      { id: 'user_admin', name: '小泽', email: 'xiaoze@example.com', role: 'admin', role_label: '管理员', permissions: [] },
    ]
    routeByUrlMock((url, init) => {
      if (url === '/api/tasks/TASK-9001/plans' && init?.method !== 'POST') return []
      if (url === '/api/approvals/candidates') return candidates
      if (init?.method === 'POST' && url === '/api/tasks/TASK-9001/approval-owner') {
        return { ...ownerless, approval_owner_id: 'user_approver', approval_owner_name: '陈晓' }
      }
      throw new ApiError(`未预期请求 ${url}`, 500)
    })

    const wrapper = mountPanel()
    await flushPromises()

    expect(wrapper.find('.plan-assign').exists()).toBe(true)
    expect(wrapper.find('.plan-assign__form .task-filter-select__trigger').exists()).toBe(true)
    expect(wrapper.text()).toContain('审批人：未指派')
    expect(apiRequestMock).toHaveBeenCalledWith('/api/approvals/candidates')

    await wrapper.find('.plan-assign__form .task-filter-select__trigger').trigger('click')
    await wrapper.findAll('.plan-assign__form .task-filter-select__option').find((option) => option.text().includes('陈晓'))!.trigger('click')
    await wrapper.findAll('.plan-assign__form button').find((button) => button.text().includes('确认指派'))!.trigger('click')
    await flushPromises()

    const assignCall = apiRequestMock.mock.calls.find(
      (call) => call[1]?.method === 'POST' && call[0] === '/api/tasks/TASK-9001/approval-owner',
    )
    expect(assignCall).toBeDefined()
    expect(JSON.parse(String(assignCall![1]?.body))).toEqual({ user_id: 'user_approver' })
    expect(wrapper.text()).toContain('审批人：陈晓')
    expect(wrapper.text()).toContain('改派审批人')
    const generateButton = wrapper.findAll('button').find((button) => button.text().includes('生成修改计划'))
    expect(generateButton).toBeDefined()
  })

  it('ANALYZED 未指派审批人 + 无指派权限：无指派控件，生成按钮置为待指派', async () => {
    grantPermissions('task:operate')
    const store = useWorkspaceStore()
    store.createTaskFromApi(
      makeAgentTask({
        status: 'ANALYZED',
        plans: null,
        approval_owner_id: null,
        approval_owner_name: null,
        manual_required: false,
        manual_reason: 'none',
        next_action: 'generate_plan',
      }),
    )
    routeByUrlMock((url, init) => {
      if (url === '/api/tasks/TASK-9001/plans' && init?.method !== 'POST') return []
      throw new ApiError(`未预期请求 ${url}`, 500)
    })

    const wrapper = mountPanel()
    await flushPromises()

    expect(wrapper.find('.plan-assign').exists()).toBe(false)
    expect(apiRequestMock).not.toHaveBeenCalledWith('/api/approvals/candidates')
    const disabledButton = wrapper.findAll('button').find((button) => button.text().includes('生成修改计划（待指派）'))
    expect(disabledButton).toBeDefined()
    expect(disabledButton!.attributes('disabled')).toBeDefined()
  })

  it('待审批计划等待超 24 小时：展示逾期提示；改派入口可重新指派并留痕', async () => {
    grantPermissions('task:operate', 'approval:assign')
    const store = useWorkspaceStore()
    const stalePlan = makeModificationPlan({
      created_at: new Date(Date.now() - 26 * 3600 * 1000).toISOString(),
    })
    const ownerlessTask = makeAgentTask({
      status: 'WAITING_APPROVAL',
      automation_state: 'waiting_approval',
      manual_required: true,
      manual_reason: 'approval_required',
      next_action: 'review_plan',
      risk_level: 'high',
      risk_source: 'plan',
      approval_owner_id: null,
      approval_owner_name: null,
      plans: [stalePlan],
    })
    store.createTaskFromApi(ownerlessTask)
    const candidates = [
      { id: 'user_approver', name: '陈晓', email: 'chenxiao@example.com', role: 'approver', role_label: '审批人', permissions: [] },
      { id: 'user_admin', name: '小泽', email: 'xiaoze@example.com', role: 'admin', role_label: '管理员', permissions: [] },
    ]
    routeByUrlMock((url, init) => {
      if (url === '/api/tasks/TASK-9001/plans' && init?.method !== 'POST') return [stalePlan]
      if (url === '/api/approvals/candidates') return candidates
      if (init?.method === 'POST' && url === '/api/tasks/TASK-9001/approval-owner') {
        return { ...ownerlessTask, approval_owner_id: 'user_admin', approval_owner_name: '小泽' }
      }
      throw new ApiError(`未预期请求 ${url}`, 500)
    })

    const wrapper = mountPanel()
    await flushPromises()

    expect(wrapper.text()).toContain('该计划等待审批已超过 24 小时')
    expect(wrapper.find('.plan-overdue').exists()).toBe(true)
    expect(wrapper.text()).toContain('等待指派审批人')

    await wrapper.findAll('.plan-assign__line button').find((button) => button.text().includes('改派审批人'))!.trigger('click')
    await flushPromises()
    expect(apiRequestMock).toHaveBeenCalledWith('/api/approvals/candidates')

    await wrapper.find('.plan-assign__form .task-filter-select__trigger').trigger('click')
    await wrapper.findAll('.plan-assign__form .task-filter-select__option').find((option) => option.text().includes('管理员'))!.trigger('click')
    await wrapper.findAll('.plan-assign__form button').find((button) => button.text().includes('确认指派'))!.trigger('click')
    await flushPromises()

    const assignCall = apiRequestMock.mock.calls.find(
      (call) => call[1]?.method === 'POST' && call[0] === '/api/tasks/TASK-9001/approval-owner',
    )
    expect(assignCall).toBeDefined()
    expect(JSON.parse(String(assignCall![1]?.body))).toEqual({ user_id: 'user_admin' })
    expect(wrapper.text()).toContain('审批人：小泽')
  })

  it('审批通过结果区展示审批记录（审批人/时间/意见/风险调整原→新）', async () => {
    grantPermissions('approval:manage')
    const store = useWorkspaceStore()
    const approvedPlan = makeModificationPlan({
      status: 'APPROVED',
      risk_level: 'medium',
      risk_adjust_from: 'high',
      risk_adjust_reason: '仅回调入口新增幂等校验，无资金链路改动。',
      approved_by_id: 'user_approver',
      approved_by_name: '陈晓',
      approved_at: '2026-09-04T10:05:00Z',
      approval_comment: '同意方案，上线后关注重复推送监控。',
    })
    store.createTaskFromApi(
      makeAgentTask({
        status: 'APPROVED',
        automation_state: 'blocked',
        manual_required: false,
        manual_reason: 'none',
        next_action: 'auto_generate_diff',
        risk_level: 'medium',
        risk_source: 'plan_approval',
        plans: [approvedPlan],
      }),
    )
    routeByUrlMock((url, init) => {
      if (url === '/api/tasks/TASK-9001/plans' && init?.method !== 'POST') return [approvedPlan]
      throw new ApiError(`未预期请求 ${url}`, 500)
    })

    const wrapper = mountPanel()
    await flushPromises()

    expect(wrapper.text()).toContain('计划已通过，任务进入 Diff 生成阶段')
    expect(wrapper.text()).toContain('审批人：陈晓')
    expect(wrapper.text()).toContain('审批意见：同意方案，上线后关注重复推送监控。')
    expect(wrapper.text()).toContain('审批人调整风险：高风险 → 中风险')
    expect(wrapper.text()).toContain('仅回调入口新增幂等校验，无资金链路改动。')
  })

  it('团队版 7.5：展示历史相似任务参考区与分析初判→计划重算风险变化', async () => {
    grantPermissions('approval:manage')
    const store = useWorkspaceStore()
    const plan = makeModificationPlan({
      risk_level: 'medium',
      analysis_risk_level: 'high',
      referenced_historical_tasks: [
        {
          task_id: 'TASK-HIST-001',
          version: 2,
          title: '历史：修复列表页布局错位',
          issue_id: 'IHIST001',
          status: 'COMPLETED' as const,
          risk_level: 'low',
          score: 0.71,
          summary: '历史任务通过弹性布局修复同类窄屏问题。',
        },
      ],
    })
    store.createTaskFromApi(waitingTask([plan]))
    routeByUrlMock((url, init) => {
      if (url === '/api/tasks/TASK-9001/plans' && init?.method !== 'POST') return [plan]
      throw new ApiError(`未预期请求 ${url}`, 500)
    })

    const wrapper = mountPanel()
    await flushPromises()

    const riskOverview = wrapper.findAll('.plan-overview__item').find((item) => item.text().includes('风险与测试'))
    await riskOverview!.find('button').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('分析阶段初判为高风险')
    expect(wrapper.text()).toContain('重算为中风险')

    await wrapper.findAll('.plan-detail-tabs button').find((button) => button.text() === '参考说明')!.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('历史相似任务参考')
    expect(wrapper.text()).toContain('仅作经验参考，不会自动套用旧 Diff')
    expect(wrapper.text()).toContain('历史：修复列表页布局错位')
    expect(wrapper.text()).toContain('相似度 71%')
  })

  it('团队版 7.5：分析初判与计划风险一致时不展示重算变化行', async () => {
    grantPermissions('approval:manage')
    const store = useWorkspaceStore()
    const plan = makeModificationPlan({ risk_level: 'low', analysis_risk_level: 'low' })
    store.createTaskFromApi(waitingTask([plan]))
    routeByUrlMock((url, init) => {
      if (url === '/api/tasks/TASK-9001/plans' && init?.method !== 'POST') return [plan]
      throw new ApiError(`未预期请求 ${url}`, 500)
    })

    const wrapper = mountPanel()
    await flushPromises()
    expect(wrapper.find('.plan-risk__initial').exists()).toBe(false)
    expect(wrapper.find('.plan-history').exists()).toBe(false)
  })

  it('mock 模式：不请求后端、不展示生成按钮，渲染演示说明', async () => {
    setDataSourceMode('mock')
    grantPermissions('task:operate', 'approval:manage')
    const store = useWorkspaceStore()
    store.createTaskFromApi(makeAgentTask({ status: 'ANALYZED', plans: null }))

    const wrapper = mountPanel()
    await flushPromises()

    expect(apiRequestMock).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('演示数据没有计划接口')
    expect(wrapper.findAll('button').some((button) => button.text().includes('生成修改计划'))).toBe(false)
  })
})
