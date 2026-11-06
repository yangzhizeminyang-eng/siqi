import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { RouterLinkStub, flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { setDataSourceMode } from '../../services/dataSourceMode'
import { makeAgentTask, makeModificationPlan } from '../../test/fixtures'
import { useWorkspaceStore } from '../../stores/workspace'
import DashboardView from '../DashboardView.vue'

const { displayUserState } = vi.hoisted(() => ({
  displayUserState: { name: '小泽', role: 'developer', role_label: '开发者' },
}))

vi.mock('../../stores/auth', () => ({
  useAuthStore: () => ({
    displayUser: displayUserState,
    hasPermission: () => true,
  }),
}))

function mountDashboard() {
  return mount(DashboardView, {
    global: {
      stubs: { RouterLink: RouterLinkStub },
    },
  })
}

describe('DashboardView：real 空任务空态', () => {
  beforeEach(() => {
    setDataSourceMode('real')
    localStorage.clear()
    setActivePinia(createPinia())
    displayUserState.role = 'developer'
    displayUserState.role_label = '开发者'
  })

  afterEach(() => {
    setDataSourceMode('real')
  })

  it('无任务时渲染空态工作台和最近任务空态行', async () => {
    const wrapper = mountDashboard()
    await flushPromises()

    expect(wrapper.find('.workbench-banner').exists()).toBe(true)
    expect(wrapper.text()).toContain('当前没有进行中的任务')
    expect(wrapper.text()).toContain('创建新任务')
    expect(wrapper.text()).toContain('暂无需要处理的事项')
    expect(wrapper.findAll('.dashboard-table tbody tr')).toHaveLength(1)
    expect(wrapper.find('.dashboard-table__empty').text()).toContain('暂无最近任务')
  })

  it('approver 角色空任务显示待审批空态文案', async () => {
    displayUserState.role = 'approver'
    displayUserState.role_label = '审批人'
    const wrapper = mountDashboard()
    await flushPromises()

    expect(wrapper.text()).toContain('当前没有待审批任务')
    expect(wrapper.text()).toContain('查看任务列表')
  })
})

describe('DashboardView：mock 种子任务正常渲染', () => {
  beforeEach(() => {
    setDataSourceMode('mock')
    localStorage.clear()
    setActivePinia(createPinia())
    displayUserState.role = 'developer'
    displayUserState.role_label = '开发者'
  })

  afterEach(() => {
    setDataSourceMode('real')
  })

  it('渲染 4 条 demo 任务行与工作台卡片', async () => {
    const wrapper = mountDashboard()
    await flushPromises()

    expect(wrapper.findAll('.data-table tbody tr')).toHaveLength(4)
    expect(wrapper.text()).toContain('TASK-8842')
    expect(wrapper.text()).toContain('修复支付回调重复消费问题')
    expect(wrapper.text()).toContain('工作台')
    expect(wrapper.text()).toContain('交付趋势与质量')
    expect(wrapper.find('.delivery-trend__summary').exists()).toBe(true)
    expect(wrapper.find('.delivery-trend__summary[role="group"]').attributes('aria-label')).toBe('交付趋势摘要')
    expect(wrapper.find('.delivery-trend__legend[role="group"]').attributes('aria-label')).toBe('趋势指标')
    expect(wrapper.find('.workbench-automation-strip[role="group"]').attributes('aria-label')).toBe('当前角色可完成操作')
    expect(wrapper.find('.delivery-trend__controls[role="group"]').attributes('aria-label')).toBe('趋势时间范围')
    expect(wrapper.text()).toContain('新增任务')
    expect(wrapper.text()).toContain('交付成功率')
  })

  it('将交付趋势渲染为双轴折线图，并允许关闭单个指标', async () => {
    const wrapper = mountDashboard()
    await flushPromises()

    expect(wrapper.find('[data-testid="delivery-trend-svg"]').exists()).toBe(true)
    expect(wrapper.findAll('.delivery-trend__right-axis-label')).toHaveLength(3)

    const createdToggle = wrapper.get('[data-testid="trend-series-created"]')
    expect(createdToggle.attributes('aria-pressed')).toBe('true')
    await createdToggle.trigger('click')
    expect(createdToggle.attributes('aria-pressed')).toBe('false')
  })
})

describe('DashboardView：审批逾期催办（mock 待审批口径）', () => {
  beforeEach(() => {
    setDataSourceMode('mock')
    localStorage.clear()
    setActivePinia(createPinia())
    displayUserState.role = 'approver'
    displayUserState.role_label = '审批人'
  })

  afterEach(() => {
    setDataSourceMode('real')
  })

  it('approver：逾期任务进入「需要处理」列表首位，meta 与 focus 带逾期口径', async () => {
    const store = useWorkspaceStore()
    const wrapper = mountDashboard()
    await flushPromises()

    const stalePlan = makeModificationPlan({
      created_at: new Date(Date.now() - 26 * 3600 * 1000).toISOString(),
    })
    store.createTaskFromApi(
      makeAgentTask({
        status: 'WAITING_APPROVAL',
        automation_state: 'waiting_approval',
        manual_required: true,
        manual_reason: 'approval_required',
        next_action: 'review_plan',
        risk_level: 'high',
        risk_source: 'plan',
        plans: [stalePlan],
      }),
    )
    await flushPromises()

    const attentionCards = wrapper.findAll('.attention-item--card')
    expect(attentionCards.length).toBeGreaterThan(0)
    expect(attentionCards[0].text()).toContain('审批等待超过 24 小时')
    expect(attentionCards[0].text()).toContain('TASK-9001 · 修复支付回调重复记账')

    expect(wrapper.text()).toContain('项已逾期')
    expect(wrapper.text()).toContain('另有 1 项审批等待超过 24 小时')
    const primaryAction = wrapper.findAll('.next-action-card').find((card) => card.text().includes('待处理'))
    expect(primaryAction).toBeDefined()
    expect(primaryAction!.text()).toContain('1 项已逾期')
  })
})
