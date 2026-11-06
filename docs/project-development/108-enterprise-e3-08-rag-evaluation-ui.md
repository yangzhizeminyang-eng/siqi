# E3-08 企业 RAG 检索与评测前端控制台

> 阶段：E3（企业级 RAG）
> 前置：E3-01..E3-07（Enterprise RAG 检索 + 离线评测 + 真实 Milvus verified 均已落地）

## 1. 目标、范围与前缀依赖

目标：把已完成的 enterprise RAG 检索与离线评测能力接到**真实可用的企业前端控制台**（非 demo 页），让企业用户在页面完成：
企业 RAG 检索、四模式评测报告查看、summary / metrics_by_tag / mode_comparison / failed_cases 追踪，
以及权限、加载、空态、错误态的完整闭环。

前缀依赖：`enterprise_rag_search`（tenant/RBAC/metadata 强过滤）、E3-04 评测、E3-06 真实 health-middle + 企业 Milvus verified、
E3-07 precision 调优（production-default hybrid `needs_improvement=false`）。

**硬约束（全程遵守）**：
- 前端**不决定最终权限**，仅做展示/隐藏；后端 `require_tenant_permission(ctx, RAG_SEARCH)` 仍是权威。
- auditor 无 `rag:search` 时不能进入/调用检索与报告能力（路由守卫 + 后端 403 双保险）。
- 不把 `local_only/fallback` 报告展示成 verified；`needs_improvement=true` 显示「需要继续优化」而非达标。
- 不写死 health-middle：页面全部基于 `/api/enterprise/rag` 接口响应渲染。
- 不暴露代码正文、token、cookie、password、api key、JWT、恢复码。
- 不破坏团队版前端 / 审计中心 / 审批中心 / 工作台现有行为。

## 2. 主链路（Mermaid）

```mermaid
flowchart LR
    U[企业用户<br>rag:search 角色] --> N[AppShell 侧栏入口<br>/enterprise/rag · 仅 rag:search]
    N --> R[路由守卫 hasAccessRequirement]
    R -->|无 rag:search| D[重定向 /dashboard + 提示]
    R -->|有 rag:search| C[RagConsoleView]
    C --> S[检索面板<br>query/project/repo/commit/ref/source_type/模式]
    C --> P[评测报告面板<br>报告列表/详情]
    S --> B[POST /api/enterprise/rag/search<br>后端 require_tenant_permission RAG_SEARCH]
    P --> L[GET /api/enterprise/rag/reports]
    P --> D2[GET /api/enterprise/rag/reports/{name}<br>路径穿越防护 + 脱敏投影]
    B -->|results/query_variants/retrieval_trace/rerank_trace| S
    L -->|meta| P
    D2 -->|summary/mode_comparison/metrics_by_tag/failed_cases/status| P
```

## 3. 关键代码说明

- **后端**：
  - `backend/app/enterprise_rag/routes.py`：新增 `/reports`（列表）与 `/reports/{name}`（详情）。两者都先
    `require_tenant_permission(ctx, RAG_SEARCH)`；报告目录限定 `backend/evaluation/enterprise_rag/reports`，
    用 `_SAFE_NAME`（`^[A-Za-z0-9_.\-]+\.json$`）正则 + `resolve()` 包含性校验做路径穿越防护；响应经
    `_report_meta`/projection 脱敏（不含 per_case 全量/代码正文）。既有 `/search` 保持不变。
  - 复用：`service.enterprise_rag_search`、`tenancy.permissions.RAG_SEARCH`、`tenancy.context.context_for_current_user`。
- **前端**：
  - `frontend/src/types/rag.ts`：RAG 检索/结果/报告类型；`types/tenancy.ts` 的 `EnterprisePermission` 增补 `'rag:search'`。
  - `frontend/src/services/rag.ts`：`ragSearch / listRagReports / getRagReport`（走 `services/api.apiRequest`）。
  - `frontend/src/stores/rag.ts`：保留 search/report 状态与 actions（loading/error/empty）。
  - `frontend/src/views/RagConsoleView.vue`：双面板（检索 + 评测报告）；状态徽章 verified/local_only/blocked、
    needs_improvement 提示、summary/metrics_by_tag/mode_comparison/failed_cases 表格、loading/empty/error/403、响应式栅格、键盘 focus。
  - `frontend/src/router/index.ts`：`/enterprise/rag` 路由 `meta.permission=['rag:search']`。
  - `frontend/src/components/AppShell.vue`：侧栏入口 `企业 RAG`，`permission: ['rag:search']`，仅 rag:search 角色可见。

## 4. API 契约

| 方法 | 路径 | 门禁 | 响应（data） |
| --- | --- | --- | --- |
| POST | `/api/enterprise/rag/search` | rag:search | results[]、query_variants、variant_count、retrieval_trace、rerank_enabled/provider/trace |
| GET | `/api/enterprise/rag/reports` | rag:search | `{reports:[{name,retrieval_mode,validation_status,vector_backend,milvus_verified,fallback_used,needs_improvement,dataset_size,commit_sha,index_id,generated_at}]}` |
| GET | `/api/enterprise/rag/reports/{name}` | rag:search | 详情投影：meta + summary + recommended_thresholds + mode_comparison + mode_validation + metrics_by_tag + failed_cases + config_snapshot + repository_url |

- 报告列表/详情仅限 `evaluation/enterprise_rag/reports/*.json`；名字含 `/`、`..`、非 `.json` → 400；不存在 → 404（穿越类请求在 URL 层也被归一化为 404）。
- 详情为脱敏投影（`_non_sensitive` 语义）：不含代码正文 / token / 凭据 / per_case 全量。

## 5. 权限矩阵（服务端权威）

| 角色 | rag:search | 检索/报告 |
| --- | --- | --- |
| tenant_owner / tenant_admin / security_admin / project_owner / developer / reviewer | ✅ | 检索 + 报告查看 |
| auditor | ❌ | 路由不显示入口；直达路由重定向；后端 403 |

前端 `hasAccessRequirement(auth, tenancy, ['rag:search'])` 只决定展示/路由；越权请求仍被后端 403 拦截。

## 6. 联调注意点

- 报告目录若部署到不同根，需确保 `routes.REPORTS_DIR` 指向真实 `evaluation/enterprise_rag/reports`。
- `/search` 返回向量腿需真实 Milvus，否则 vector/hybrid/rerank 会降级/空（页面按接口如实展示，不伪造）。
- 报告状态投影直接来自报告文件字段（verified/local_only/blocked + milvus_verified + fallback_used + needs_improvement），页面据此渲染徽章与达标提示。
- 前端权限仅展示；审计人员即便篡改前端也无法通过后端 rag:search 门禁。

## 7. 源码文件位置

| 职责 | 文件 |
| --- | --- |
| 报告列表/详情 + search 路由 | `backend/app/enterprise_rag/routes.py` |
| 检索服务 | `backend/app/enterprise_rag/service.py` |
| 评测/指标/状态 | `backend/app/enterprise_rag/evaluation.py` |
| 前端类型 | `frontend/src/types/rag.ts`、`frontend/src/types/tenancy.ts` |
| 前端服务/状态 | `frontend/src/services/rag.ts`、`frontend/src/stores/rag.ts` |
| 前端页面 | `frontend/src/views/RagConsoleView.vue` |
| 路由/入口 | `frontend/src/router/index.ts`、`frontend/src/components/AppShell.vue` |
| 后端专项测试 | `backend/tests/test_enterprise_e3_08_rag_console.py` |
| 前端专项测试 | `frontend/src/stores/__tests__/rag.spec.ts`、`frontend/src/views/__tests__/RagConsoleView.spec.ts` |

## 8. 测试证据（全部本会话真实执行）

- 后端专项 `pytest tests/test_enterprise_e3_08_rag_console.py` → **8 passed**：有权限读列表/详情、auditor 403、
  路径穿越被拒（HTTP 4xx + 单元级正则/包含校验）、verified/local_only/blocked 状态不混淆、`needs_improvement` 不伪翻、
  详情投影脱敏、search 仍 rag:search 门禁。
- 前端专项 `vitest rag.spec.ts + RagConsoleView.spec.ts` → **10 passed**（store 成功/失败/空、路由权限、hasAccessRequirement、
  verified/local_only 区分、loading/empty/error、响应式容器断言）。
- **前端全量 vitest → 30 文件 250 tests passed**（原有 240 + E3-08 10，无回归）。
- **后端全量 pytest → 741 passed, 16 skipped**（原 733 + E3-08 8，无回归）。
- `vue-tsc -b` → 0 错误；`vite build` → 成功（`BUILD_EXIT=0`）。
- `git diff --check` → 无输出。

## 9. 真实运行截图（真实浏览器 Edge，对起 dev 后端 8002 + vite 5173，登录 admin）

- `docs/project-development/assets/e308/e308_01_console.png`：登录后侧栏出现「企业 RAG」入口；控制台双面板；
  报告列表 12 份（verified/local_only 徽章正确）。
- `docs/project-development/assets/e308/e308_02_report_detail.png`：点开 `e3_07_health_middle_quality_tuned_hybrid_gate.json`
  （verified）→「真实 Milvus 已验证（milvus_verified=true / fallback=false）」、summary（precision@K 0.3396 / recall 0.8955 /
  mrr 0.8172 / ndcg 0.7981）、metrics_by_tag / failed_cases 渲染。
- `docs/project-development/assets/e308/e308_03_search.png`：检索面板（查询/project/repo/commit/ref/模式/来源类型）交互状态。
- 文本参照：`assets/e308/e308_body.txt`。

> 截图来自真实 headless Chromium 对本地 dev 栈的渲染；模型无法内联显示 PNG，正文摘录取证。页面数据全部来自接口，未硬编码。

## 10. 残余风险

- 检索面板的向量/hybrid/rerank 需真实索引/Milvus 才有结果；无索引时页面如实显示空结果/降级状态，联调需先建索引。
- 报告目录路径硬编码于 `routes.REPORTS_DIR`；不同部署根需确认指向预期的 reports 目录（含穿越防护）。
- 未做「在线触发评测/运行」入口（仅查看既有 E3-06/E3-07 报告）；在线评测触发建议另开接口（超时/异步）。
- 前端权限仅展示层；被捕获的越权一律以后端为准。
- 不宣称「企业 RAG 已彻底完成」：仅交付检索 + 报告查看控制台。