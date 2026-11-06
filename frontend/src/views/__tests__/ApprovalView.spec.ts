import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { makeAgentTask, makeModificationPlan } from '../../test/fixtures'
import { setDataSourceMode } from '../../services/dataSourceMode'
import { ApiError } from '../../services/api'
import type { ModificationPlan } from '../../types/domain'
import ApprovalView from '../ApprovalView.vue'

const { hasPermissionMock, hasRoleMock, apiRequestMock } = vi.hoisted(() => ({
  hasPermissionMock: vi.fn(),
  hasRoleMock: vi.fn(),
  apiRequestMock: vi.fn(),
}))

vi.mock('../../stores/auth', () => ({
  useAuthStore: () => ({ hasPermission: hasPermissionMock, hasRole: hasRoleMock }),
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

function mountApproval() {
  return mount(ApprovalView, {
    global: {
      stubs: { StatusTag: true },
    },
  })
}

function routeByUrl(handler: (url: string, init?: { method?: string; body?: string }) => unknown) {
  apiRequestMock.mockReset()
  apiRequestMock.mockImplementation((url: string, init?: { method?: string; body?: string }) => {
    const result = handler(url, init)
    return Promise.resolve(result)
  })
}

function approveCalls() {
  return apiRequestMock.mock.calls.filter(
    (call) => call[1]?.method === 'POST' && String(call[0]).includes('/approve'),
  )
}

function pendingCalls() {
  return apiRequestMock.mock.calls.filter((call) => call[0] === '/api/approvals/pending')
}

describe('ApprovalView：空任务空态', () => {
  beforeEach(() => {
    setDataSourceMode('real')
    localStorage.clear()
    setActivePinia(createPinia())
    hasPermissionMock.mockReset()
    hasPermissionMock.mockReturnValue(true)
    hasRoleMock.mockReset()
    hasRoleMock.mockReturnValue(false)
    apiRequestMock.mockReset()
    apiRequestMock.mockResolvedValue([])
  })

  afterEach(() => {
    setDataSourceMode('real')
  })

  it('无待审批任务时展示 EmptyPanel 不渲染审批网格', async () => {
    const wrapper = mountApproval()
    await flushPromises()

    expect(pendingCalls()).toHaveLength(1)
    expect(wrapper.find('.approval-grid').exists()).toBe(false)
    expect(wrapper.find('.empty-panel').exists()).toBe(true)
    expect(wrapper.text()).toContain('暂无待你处理的审批任务')
    expect(wrapper.text()).toContain('指派给你审批的计划提交后')
  })

  it('lead 空态：研发负责人专属说明（只读 + 指派入口引导）', async () => {
    hasRoleMock.mockReturnValue(true)
    const wrapper = mountApproval()
    await flushPromises()

    expect(wrapper.find('.empty-panel').exists()).toBe(true)
    expect(wrapper.text()).toContain('暂无指派给你的待审批任务')
    expect(wrapper.text()).toContain('研发负责人不直接审批')
  })
})

describe('ApprovalView：有待审批任务渲染（mock 演示）', () => {
  beforeEach(() => {
    setDataSourceMode('mock')
    localStorage.clear()
    setActivePinia(createPinia())
    hasPermissionMock.mockReset()
    hasRoleMock.mockReset()
  })

  afterEach(() => {
    setDataSourceMode('real')
  })

  it('审批人视角展示任务列表、计划面板与审批操作', async () => {
    hasPermissionMock.mockReturnValue(true)
    const wrapper = mountApproval()
    await flushPromises()

    expect(wrapper.find('.approval-grid').exists()).toBe(true)
    expect(wrapper.findAll('.approval-list__item')).toHaveLength(1)
    expect(wrapper.text()).toContain('修复支付回调重复消费问题')
    expect(wrapper.find('.approval-main').exists()).toBe(true)
    for (const text of ['通过', '拒绝', '要求重写']) {
      expect(wrapper.text()).toContain(text)
    }
  })

  it('非审批人只读：无操作按钮并显示查看说明', async () => {
    hasPermissionMock.mockReturnValue(false)
    const wrapper = mountApproval()
    await flushPromises()

    expect(wrapper.find('.approval-actions').exists()).toBe(false)
    expect(wrapper.text()).toContain('您当前可查看审批内容')
    expect(wrapper.text()).not.toContain('通过所选')
    expect(wrapper.text()).not.toContain('拒绝所选')
  })
})

describe('ApprovalView：真实 API 审批流转（DEV-009/DEV-010）', () => {
  beforeEach(() => {
    setDataSourceMode('real')
    localStorage.clear()
    setActivePinia(createPinia())
    hasPermissionMock.mockReset()
    hasPermissionMock.mockReturnValue(true)
    hasRoleMock.mockReset()
    hasRoleMock.mockReturnValue(false)
  })

  afterEach(() => {
    setDataSourceMode('real')
  })

  function waitingPayload(overrides: Parameters<typeof makeAgentTask>[0] = {}) {
    return makeAgentTask({
      status: 'WAITING_APPROVAL',
      automation_state: 'waiting_approval',
      manual_required: true,
      manual_reason: 'approval_required',
      next_action: 'review_plan',
      ...overrides,
    })
  }

  function approvedPayload(plan: ModificationPlan) {
    return makeAgentTask({
      status: 'APPROVED',
      automation_state: 'blocked',
      manual_required: false,
      manual_reason: 'none',
      next_action: 'auto_generate_diff',
      risk_level: plan.risk_level,
      risk_source: plan.risk_level === 'high' ? 'plan' : 'plan_approval',
      plans: [plan],
    })
  }

  function waitingTask(id: string, title: string, risk: 'low' | 'medium' | 'high') {
    const seq = id.split('-')[1]
    const plan = makeModificationPlan({
      id: `PLAN-${seq}-1`,
      task_id: id,
      risk_level: risk,
      risk_reason:
        risk === 'low'
          ? '纯新增回归测试。'
          : risk === 'medium'
            ? '改动集中在独立模块。'
            : '变更涉及支付回调核心链路，存在资金一致性风险。',
      created_at: new Date().toISOString(),
    })
    return waitingPayload({
      id,
      issue_id: `I${seq}`,
      title,
      risk_level: risk,
      risk_source: 'plan',
      plans: [plan],
    })
  }

  function approvedTask(id: string, plan: ModificationPlan) {
    return makeAgentTask({
      id,
      status: 'APPROVED',
      automation_state: 'blocked',
      manual_required: false,
      manual_reason: 'none',
      next_action: 'auto_generate_diff',
      risk_level: plan.risk_level,
      risk_source: plan.risk_level === 'high' ? 'plan' : 'plan_approval',
      plans: [{ ...plan, status: 'APPROVED' as const }],
    })
  }

  it('渲染后端待审批任务与计划内容，通过后调用审批接口并移出队列', async () => {
    const plan = makeModificationPlan()
    const payload = waitingPayload({ risk_level: 'high', risk_source: 'plan', plans: [plan] })
    let approved = false
    routeByUrl((url, init) => {
      if (url === '/api/approvals/pending' && !init?.method) return approved ? [] : [payload]
      if (init?.method === 'POST' && url.endsWith('/plans/PLAN-9001-1/approve')) {
        approved = true
        return approvedPayload({ ...plan, status: 'APPROVED' as const })
      }
      throw new Error(`未预期请求 ${url}`)
    })

    const wrapper = mountApproval()
    await flushPromises()

    expect(wrapper.findAll('.approval-list__item')).toHaveLength(1)
    expect(wrapper.text()).toContain('为支付回调增加幂等键')
    expect(wrapper.text()).toContain('回调入口增加幂等校验。')
    expect(wrapper.text()).toContain('实施步骤')
    expect(wrapper.text()).toContain('计划 v1')
    expect(wrapper.text()).toContain('等待 陈晓 审批')

    const approveButton = wrapper.findAll('button').find((button) => button.text().trim() === '通过')
    expect(approveButton).toBeDefined()
    await approveButton!.trigger('click')
    await flushPromises()

    const posts = approveCalls()
    expect(posts).toHaveLength(1)
    expect(posts[0][0]).toBe('/api/tasks/TASK-9001/plans/PLAN-9001-1/approve')
    expect(JSON.parse(String(posts[0][1]?.body))).toEqual({})
    expect(wrapper.text()).toContain('计划已通过，任务进入 Diff 生成阶段')
    expect(wrapper.find('.approval-list__item').exists()).toBe(false)
    expect(wrapper.text()).toContain('暂无待你处理的审批任务')
    expect(pendingCalls().length).toBeGreaterThanOrEqual(2)
  })

  it('审批通过后详情区展示审批记录行（审批人/意见/风险调整原→新）', async () => {
    const plan = makeModificationPlan()
    const payload = waitingPayload({ risk_level: 'high', risk_source: 'plan', plans: [plan] })
    let queueCount = 0
    routeByUrl((url, init) => {
      if (url === '/api/approvals/pending' && !init?.method) {
        queueCount += 1
        // 第二次（操作后重载）失败：队列保留已通过任务，展示审批记录行与刷新失败提示
        if (queueCount > 1) throw new Error('网络异常')
        return [payload]
      }
      if (init?.method === 'POST' && url.endsWith('/plans/PLAN-9001-1/approve')) {
        return approvedPayload({
          ...plan,
          status: 'APPROVED' as const,
          risk_level: 'medium' as const,
          risk_adjust_from: 'high' as const,
          risk_adjust_reason: '回调链路已由幂等表承接，风险可控。',
          approved_by_id: 'user_approver',
          approved_by_name: '陈晓',
          approved_at: '2026-09-04T10:05:00Z',
          approval_comment: '同意方案，上线后关注重复推送监控。',
        })
      }
      throw new Error(`未预期请求 ${url}`)
    })

    const wrapper = mountApproval()
    await flushPromises()

    const approveButton = wrapper.findAll('button').find((button) => button.text().trim() === '通过')
    await approveButton!.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('陈晓 审批通过计划 v1')
    expect(wrapper.text()).toContain('意见：同意方案，上线后关注重复推送监控。')
    expect(wrapper.text()).toContain('高风险 → 中风险，原因：回调链路已由幂等表承接，风险可控。')
    expect(wrapper.text()).toContain('审批队列刷新失败')
  })

  it('拒绝不填写原因：前端拦截提示，不发请求', async () => {
    const plan = makeModificationPlan()
    const payload = waitingPayload({ risk_level: 'high', risk_source: 'plan', plans: [plan] })
    routeByUrl((url) => {
      if (url === '/api/approvals/pending') return [payload]
      throw new Error(`未预期请求 ${url}`)
    })

    const wrapper = mountApproval()
    await flushPromises()

    const rejectButton = wrapper.findAll('button').find((button) => button.text().trim() === '拒绝')
    await rejectButton!.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('拒绝计划需要填写原因')
    expect(apiRequestMock.mock.calls.some((call) => String(call[0]).includes('/reject'))).toBe(false)
  })

  it('单任务聚焦可调整风险：不填原因被汇总提示，填写后请求携带调整值', async () => {
    const plan = makeModificationPlan()
    const payload = waitingPayload({ risk_level: 'high', risk_source: 'plan', plans: [plan] })
    let approved = false
    routeByUrl((url, init) => {
      if (url === '/api/approvals/pending' && !init?.method) return approved ? [] : [payload]
      if (init?.method === 'POST' && url.endsWith('/plans/PLAN-9001-1/approve')) {
        approved = true
        return approvedPayload({
          ...plan,
          status: 'APPROVED' as const,
          risk_level: 'medium' as const,
          risk_adjust_from: 'high' as const,
          risk_adjust_reason: '回调链路已由幂等表承接，风险可控。',
          approved_by_id: 'user_approver',
          approved_by_name: '陈晓',
          approved_at: '2026-09-04T10:05:00Z',
        })
      }
      throw new Error(`未预期请求 ${url}`)
    })

    const wrapper = mountApproval()
    await flushPromises()

    await wrapper.find('.approval-risk-adjust .task-filter-select__trigger').trigger('click')
    await wrapper.findAll('.approval-risk-adjust .task-filter-select__option').find((option) => option.text() === '中风险')!.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('调整风险原因')

    const approveButton = wrapper.findAll('button').find((button) => button.text().trim() === '通过')
    await approveButton!.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('调整风险等级时必须填写原因')
    expect(approveCalls()).toHaveLength(0)

    await wrapper.findAll('.approval-risk-adjust textarea')[0].setValue('回调链路已由幂等表承接，风险可控。')
    await flushPromises()
    await approveButton!.trigger('click')
    await flushPromises()

    const posts = approveCalls()
    expect(posts).toHaveLength(1)
    expect(JSON.parse(String(posts[0][1]?.body))).toEqual({
      risk_level: 'medium',
      risk_adjust_reason: '回调链路已由幂等表承接，风险可控。',
    })
  })

  it('一键通过低风险：对低风险任务逐项调用通过接口', async () => {
    const plan = makeModificationPlan({ risk_level: 'low', risk_reason: '纯新增回归测试。' })
    const payload = waitingPayload({ risk_level: 'low', risk_source: 'plan', plans: [plan] })
    let approved = false
    routeByUrl((url, init) => {
      if (url === '/api/approvals/pending' && !init?.method) return approved ? [] : [payload]
      if (init?.method === 'POST' && url.endsWith('/plans/PLAN-9001-1/approve')) {
        approved = true
        return approvedPayload({ ...plan, status: 'APPROVED' as const })
      }
      throw new Error(`未预期请求 ${url}`)
    })

    const wrapper = mountApproval()
    await flushPromises()

    const quickButton = wrapper.findAll('button').find((button) => button.text().includes('一键通过低风险 1 项'))
    expect(quickButton).toBeDefined()
    await quickButton!.trigger('click')
    await flushPromises()

    const posts = approveCalls()
    expect(posts).toHaveLength(1)
    expect(posts[0][0]).toBe('/api/tasks/TASK-9001/plans/PLAN-9001-1/approve')
    expect(wrapper.text()).toContain('1 项低风险任务已自动通过')
    expect(wrapper.find('.approval-list__item').exists()).toBe(false)
  })

  it('逾期任务：列表角标与聚焦强提示（等待超过 24 小时）', async () => {
    const stalePlan = makeModificationPlan({
      created_at: new Date(Date.now() - 26 * 3600 * 1000).toISOString(),
    })
    const payload = waitingPayload({ risk_level: 'high', risk_source: 'plan', plans: [stalePlan] })
    routeByUrl((url) => {
      if (url === '/api/approvals/pending') return [payload]
      throw new Error(`未预期请求 ${url}`)
    })

    const wrapper = mountApproval()
    await flushPromises()

    expect(wrapper.text()).toContain('逾期 >24h')
    expect(wrapper.find('.approval-overdue-chip').exists()).toBe(true)
    expect(wrapper.text()).toContain('审批等待已超过 24 小时')
    expect(wrapper.text()).toContain('逾期任务在工作台也有催办提示')
  })

  it('一键通过低风险：混合队列按钮按 low 计数并逐项调用通过接口', async () => {
    const low1 = waitingTask('TASK-9001', '修复支付回调重复记账', 'low')
    const low2 = waitingTask('TASK-9002', '优化对账任务超时问题', 'low')
    const medium = waitingTask('TASK-9003', '日志采集增加采样开关', 'medium')
    const high = waitingTask('TASK-9004', '升级支付网关 SDK', 'high')
    let queue = [low1, low2, medium, high]
    routeByUrl((url, init) => {
      if (url === '/api/approvals/pending' && !init?.method) return queue
      const approveMatch =
        init?.method === 'POST' ? /\/tasks\/(TASK-\d+)\/plans\/(PLAN-\d+-\d+)\/approve$/.exec(url) : null
      if (approveMatch) {
        const task = queue.find((item) => item.id === approveMatch[1])
        if (!task || task.risk_level !== 'low') throw new Error(`不应审批非 low 任务 ${url}`)
        queue = queue.filter((item) => item.id !== task.id)
        return approvedTask(task.id, task.plans![0])
      }
      throw new Error(`未预期请求 ${url}`)
    })

    const wrapper = mountApproval()
    await flushPromises()

    const quickButton = wrapper.findAll('button').find((button) => button.text().includes('一键通过低风险 2 项'))
    expect(quickButton).toBeDefined()
    expect(quickButton!.attributes('disabled')).toBeUndefined()
    await quickButton!.trigger('click')
    await flushPromises()

    const posts = approveCalls()
    expect(posts).toHaveLength(2)
    expect(posts.map((call) => String(call[0])).sort()).toEqual([
      '/api/tasks/TASK-9001/plans/PLAN-9001-1/approve',
      '/api/tasks/TASK-9002/plans/PLAN-9002-1/approve',
    ])
    expect(wrapper.text()).toContain('2 项低风险任务已自动通过')
    expect(wrapper.findAll('.approval-list__item')).toHaveLength(2)
    expect(wrapper.text()).not.toContain('一键通过低风险 2 项')
  })

  it('一键通过低风险：无 low 项时入口隐藏且不可点击', async () => {
    const medium = waitingTask('TASK-9003', '日志采集增加采样开关', 'medium')
    const high = waitingTask('TASK-9004', '升级支付网关 SDK', 'high')
    routeByUrl((url) => {
      if (url === '/api/approvals/pending') return [medium, high]
      throw new Error(`未预期请求 ${url}`)
    })

    const wrapper = mountApproval()
    await flushPromises()

    const quickButton = wrapper.findAll('button').find((button) => button.text().includes('一键通过低风险'))
    expect(quickButton).toBeDefined()
    expect(quickButton!.classes()).toContain('is-hidden')
    expect(quickButton!.attributes('disabled')).toBeDefined()
  })

  it('一键通过低风险：部分失败逐项透出失败原因，不吞失败项', async () => {
    const low1 = waitingTask('TASK-9001', '修复支付回调重复记账', 'low')
    const low2 = waitingTask('TASK-9002', '优化对账任务超时问题', 'low')
    let queue = [low1, low2]
    routeByUrl((url, init) => {
      if (url === '/api/approvals/pending' && !init?.method) return queue
      if (init?.method === 'POST' && url.endsWith('/plans/PLAN-9001-1/approve')) {
        queue = queue.filter((item) => item.id !== 'TASK-9001')
        return approvedTask('TASK-9001', low1.plans![0])
      }
      if (init?.method === 'POST' && url.endsWith('/plans/PLAN-9002-1/approve')) {
        throw new ApiError('审批服务繁忙，请稍后重试', 500)
      }
      throw new Error(`未预期请求 ${url}`)
    })

    const wrapper = mountApproval()
    await flushPromises()

    const quickButton = wrapper.findAll('button').find((button) => button.text().includes('一键通过低风险 2 项'))
    expect(quickButton).toBeDefined()
    await quickButton!.trigger('click')
    await flushPromises()

    expect(approveCalls()).toHaveLength(2)
    expect(wrapper.text()).toContain('部分低风险任务未通过')
    expect(wrapper.text()).toContain('TASK-9002：审批服务繁忙，请稍后重试')
    expect(wrapper.text()).toContain('已完成：TASK-9001')
    // 失败项不静默消失：仍在待处理队列
    expect(wrapper.findAll('.approval-list__item')).toHaveLength(1)
    expect(wrapper.text()).toContain('优化对账任务超时问题')
  })

  it('要求重写收到 409 超限：服务端提示透传展示且任务留在队列', async () => {
    const plan = makeModificationPlan({ created_at: new Date().toISOString() })
    const payload = waitingPayload({ risk_level: 'high', risk_source: 'plan', plans: [plan] })
    routeByUrl((url, init) => {
      if (url === '/api/approvals/pending' && !init?.method) return [payload]
      if (init?.method === 'POST' && url.endsWith('/rewrite')) {
        throw new ApiError('计划已重写 2 次达上限；可审批当前版本或拒绝终止，也可重试任务开启新轮次', 409)
      }
      throw new Error(`未预期请求 ${url}`)
    })

    const wrapper = mountApproval()
    await flushPromises()

    await wrapper.find('.approval-comment textarea').setValue('需要再收敛文件范围')
    await flushPromises()
    const rewriteButton = wrapper.findAll('button').find((button) => button.text().trim() === '要求重写')
    expect(rewriteButton).toBeDefined()
    await rewriteButton!.trigger('click')
    await flushPromises()

    const rewrites = apiRequestMock.mock.calls.filter(
      (call) => call[1]?.method === 'POST' && String(call[0]).includes('/rewrite'),
    )
    expect(rewrites).toHaveLength(1)
    expect(wrapper.text()).toContain('部分审批操作未完成')
    expect(wrapper.text()).toContain('计划已重写 2 次达上限')
    expect(wrapper.text()).toContain('可审批当前版本或拒绝终止')
    expect(wrapper.findAll('.approval-list__item')).toHaveLength(1)
  })

  it('一键通过低风险：无审批权限时不渲染入口', async () => {
    hasPermissionMock.mockReturnValue(false)
    const low = waitingTask('TASK-9001', '修复支付回调重复记账', 'low')
    routeByUrl((url) => {
      if (url === '/api/approvals/pending') return [low]
      throw new Error(`未预期请求 ${url}`)
    })

    const wrapper = mountApproval()
    await flushPromises()

    expect(wrapper.text()).toContain('您当前可查看审批内容')
    const quickButton = wrapper.findAll('button').find((button) => button.text().includes('一键通过低风险'))
    expect(quickButton).toBeUndefined()
  })
})
