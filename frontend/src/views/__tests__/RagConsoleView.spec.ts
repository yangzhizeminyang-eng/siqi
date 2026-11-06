import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import RagConsoleView from '../RagConsoleView.vue'
import { useRagStore } from '../../stores/rag'
import { useTenancyStore } from '../../stores/tenancy'
import type { EnterpriseContext } from '../../types/tenancy'
import { hasAccessRequirement } from '../../services/accessControl'

const { apiRequestMock, hasPermissionMock, listReportsMock, getReportMock } = vi.hoisted(() => ({
  apiRequestMock: vi.fn(),
  hasPermissionMock: vi.fn(),
  listReportsMock: vi.fn(),
  getReportMock: vi.fn(),
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
  useAuthStore: () => ({ hasPermission: hasPermissionMock }),
}))

vi.mock('../../services/rag', () => ({
  ragSearch: vi.fn(),
  listRagReports: listReportsMock,
  getRagReport: getReportMock,
}))

function makeCtx(permissions: string[]): EnterpriseContext {
  return {
    active_tenant: { id: 'tenant_default', name: '默认租户', role: 'tenant_owner', role_label: '租户所有者' },
    tenants: [{ id: 'tenant_default', name: '默认租户', role: 'tenant_owner', status: 'active' }],
    role: 'tenant_owner',
    role_label: '租户所有者',
    permissions,
  }
}

const verified = {
  name: 'e3_07_health_middle_quality_tuned_hybrid_gate.json',
  validation_status: 'verified',
  retrieval_mode: 'hybrid',
  dataset_size: 67,
  milvus_verified: true,
  fallback_used: false,
  needs_improvement: false,
}
const local = { ...verified, name: 'r_local.json', validation_status: 'local_only', milvus_verified: false, fallback_used: true, needs_improvement: true }

const detail = {
  ...verified,
  summary: { total_cases: 67, precision_at_k: 0.3396, mrr: 0.8172 },
  recommended_thresholds: { precision_at_k: 0.3 },
  mode_comparison: { bm25: { precision_at_k: 0.2 }, hybrid: { precision_at_k: 0.3396 } },
  metrics_by_tag: { symbol: { count: 50, precision_at_k: 0.3 } },
  failed_cases: [{ case_id: 'hm001', query: 'build_x', tags: ['symbol'], expected_status: 200, actual_status: 200, status_passed: true, retrieval_passed: false }],
}

async function mountRag(permissions: string[]) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const tenancy = useTenancyStore()
  tenancy.context = makeCtx(permissions)
  tenancy.activeTenant = tenancy.context.active_tenant
  tenancy.ready = true
  const rag = useRagStore()
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: RagConsoleView }] })
  await router.push('/')
  const wrapper = mount(RagConsoleView, { global: { plugins: [pinia, router] } })
  await flushPromises()
  return { wrapper, rag }
}

describe('RagConsoleView 企业 RAG 控制台', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    apiRequestMock.mockReset()
    hasPermissionMock.mockReset()
    hasPermissionMock.mockReturnValue(false)
    listReportsMock.mockReset()
    getReportMock.mockReset()
  })

  it('hasAccessRequirement：有 rag:search 可访问，无 rag:search 拒绝（含 auditor 语义）', () => {
    const auth = { hasPermission: () => false }
    const okTenancy = { hasPermission: (p: string) => ['rag:search'].includes(p) }
    const noTenancy = { hasPermission: () => false }
    expect(hasAccessRequirement(auth, okTenancy, ['rag:search'])).toBe(true)
    expect(hasAccessRequirement(auth, noTenancy, ['rag:search'])).toBe(false)
  })

  it('无权限角色看不到搜索表单，仅显示无权限提示', async () => {
    listReportsMock.mockResolvedValue({ reports: [], reports_dir: '' })
    const { wrapper } = await mountRag([]) // auditor 语义：无 rag:search
    expect(wrapper.text()).toContain('无权限')
    expect(wrapper.text()).toContain('rag:search')
    expect(wrapper.find('input[placeholder*="build_login_response"]').exists()).toBe(false)
  })

  it('有权限角色能看搜索表单与报告列表，并渲染 verified 摘要/mode/metrics/failed', async () => {
    listReportsMock.mockResolvedValue({ reports: [verified, local], reports_dir: '/r' })
    getReportMock.mockResolvedValue(detail)
    const { wrapper } = await mountRag(['rag:search'])
    expect(wrapper.find('input[placeholder*="build_login_response"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('e3_07_health_middle_quality_tuned_hybrid_gate.json')

    // 点击 verified 报告 → 详情渲染
    await wrapper.find('.rag-console__report-list button').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('precision_at_k')
    expect(wrapper.text()).toContain('0.3396')
    expect(wrapper.text()).toContain('mode_comparison')
    expect(wrapper.text()).toContain('metrics_by_tag')
    expect(wrapper.text()).toContain('failed_cases')
    expect(wrapper.text()).toContain('真实 Milvus 已验证')
  })

  it('verified 与 local_only 状态区分：未验证报告提示不能当达标', async () => {
    listReportsMock.mockResolvedValue({ reports: [local], reports_dir: '/r' })
    getReportMock.mockResolvedValue(local)
    const { wrapper } = await mountRag(['rag:search'])
    await wrapper.find('.rag-console__report-list button').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('local_only')
    expect(wrapper.text()).toContain('未验证为真实 Milvus')
    expect(wrapper.text()).toContain('needs_improvement')
  })

  it('报告列表为空时显示空态；加载错误时显示错误态', async () => {
    listReportsMock.mockResolvedValue({ reports: [], reports_dir: '' })
    const { wrapper } = await mountRag(['rag:search'])
    expect(wrapper.text()).toContain('暂无评测报告')

    listReportsMock.mockRejectedValueOnce(new Error('加载报告列表失败'))
    const { wrapper: w2 } = await mountRag(['rag:search'])
    expect(w2.text()).toContain('加载报告列表失败')
  })

  it('包含响应式栅格容器（基础响应式断言）', async () => {
    listReportsMock.mockResolvedValue({ reports: [], reports_dir: '' })
    const { wrapper } = await mountRag(['rag:search'])
    expect(wrapper.find('.rag-console__grid').exists()).toBe(true)
  })

  it('使用项目级自定义检索模式菜单，不渲染原生 select', async () => {
    listReportsMock.mockResolvedValue({ reports: [], reports_dir: '' })
    const { wrapper } = await mountRag(['rag:search'])

    expect(wrapper.find('select').exists()).toBe(false)
    const trigger = wrapper.find('.rag-console__mode-select .task-filter-select__trigger')
    expect(trigger.exists()).toBe(true)
    expect(trigger.text()).toContain('Hybrid（默认）')

    await trigger.trigger('click')
    expect(wrapper.find('.rag-console__mode-select .task-filter-select__menu').exists()).toBe(true)
    const bm25Option = wrapper.findAll('.rag-console__mode-select .task-filter-select__option').find((option) => option.text() === 'BM25')
    expect(bm25Option).toBeDefined()
    await bm25Option!.trigger('click')
    expect(wrapper.find('.rag-console__mode-select .task-filter-select__trigger').text()).toContain('BM25')
  })
})
