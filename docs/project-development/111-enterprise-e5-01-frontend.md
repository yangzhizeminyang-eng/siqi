# E5-01 企业版前端完整化（控制台打磨）

> 阶段：E5（企业前端）
> 前置：E1（租户/RBAC/审计）、E2（审批治理）、E3（RAG/控制台）、E4（Runtime）
> **状态：DONE（企业 7 模块控制台、角色走查、响应式核验、企业设置/运维页和回归证据已闭环；**
> **云端生产部署、多副本压测与长期稳定性不属于本轮验收范围）**

## 1. 目标与范围（本轮已完成）
- **设计审计**：`docs/project-development/assets/e501/audit-note.md`（重复组件/视觉不一致/Demo 感来源/可复用 token/页面边界/不触碰团队版行为）。
- **共享组件库** `frontend/src/components/enterprise/`：StatusBadge / PageHeader / AsyncState(loading/empty/error/forbidden) / DetailDrawer(Esc 关闭) / ConfirmDialog(键盘 Enter/Esc) / PermissionGate(前端展示层)。
- **企业工作台** `/enterprise/dashboard`（EnterpriseDashboardView）：真实接口数据（审批待办 `GET /api/enterprise/approvals`、最近审计 `GET /api/audit-events`、Runtime 摘要 `GET /api/enterprise/runtime/tasks`、当前租户上下文），按 `audit:view/approval:view/rag:search` 分区门控；无虚构统计。
- **Runtime 控制台** `/enterprise/runtime`（RuntimeConsoleView）：真实 E4-01 接口（任务列表/状态徽章/checkpoint/当前步骤/步骤明细/等待人工任务的 resume·retry·cancel 接管），接管按钮仅 tenant_owner/admin/security_admin/project_owner 显示，后端 403 权威；auditor 无 rag:search → 路由重定向。
- **成员与审批页面**：成员角色/启用状态真实读写，受限角色控件只读；审批列表、详情和操作状态统一企业组件。
- **审计、RAG、运维页面**：审计事件、RAG 报告状态、Milvus/迁移/索引摘要均来自真实接口；运维接口新增 `org:manage` 后端门禁，auditor 实测 403。
- **路由/侧栏**：7 个企业模块均接入权限展示与路由守卫；前端权限仅做显示/导航，后端保留最终安全边界。

## 2. 页面信息架构与 Mermaid
```mermaid
flowchart TD
    A[AppShell 侧栏] --> B[/enterprise/dashboard 工作台]
    A --> C[/enterprise/members 成员]
    A --> D[/audit 审计]
    A --> E[/enterprise/approvals 审批]
    A --> F[/enterprise/rag RAG 控制台]
    A --> G[/enterprise/runtime Runtime 控制台]
    A --> K[/enterprise/ops 企业设置与运维]
    B -->|真实接口| H[审批待办/最近审计/Runtime 摘要/租户上下文]
    G -->|真实接口| I[任务列表/状态/checkpoint/步骤/人工接管]
    F -->|真实接口| J[检索 + 报告 verified/local_only/blocked]
    K -->|真实接口| L[健康/迁移/配置/索引摘要]
```
- 前端权限仅做显示/导航；后端是最终安全边界（跨租户/越权 403）。

## 3. 角色权限矩阵（展示层；后端权威）
| 角色 | 工作台 | 成员 | 审计 | 审批 | RAG | Runtime 接管 |
| --- | --- | --- | --- | --- | --- | --- |
| tenant_owner / tenant_admin / security_admin / project_owner | ✅ | 按权限 | ✅ | ✅ | ✅ | ✅（waiting_human） |
| developer / reviewer | ✅ | 受限 | 受限 | 只读 | ✅ | 查看（无接管） |
| auditor | ✅(审计分区) | 只读 | ✅ | 只读 | ❌（无 rag:search） | ❌（路由重定向 + 后端） |

## 4. 共享组件与状态
- StatusBadge 状态→语义色（ok/warn/danger/neutral：succeeded/verified→ok，waiting_approval/waiting_human/retrying/local_only→warn，failed/blocked/rejected→danger，queued/cancelled→neutral）。
- AsyncState：loading/empty/error/forbidden 四态；DetailDrawer/ConfirmDialog 支持长标题、键盘、窄屏；PermissionGate 基于 `hasAccessRequirement`。

## 5. 真实 API 契约（前端已使用）
| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/enterprise/approvals` | 工作台审批待办 |
| GET | `/api/audit-events` | 工作台最近审计 |
| GET | `/api/enterprise/runtime/tasks` | Runtime 任务列表 / 工作台摘要 |
| POST | `/api/enterprise/runtime/tasks/{id}/takeover/complete` | 人工接管 resume/retry/cancel |
| GET | `/api/enterprise/rag/reports` + `/reports/{name}` | RAG 报告查看（E3-08 复用） |
所有请求走 `services/api.apiRequest`；响应统一 `{code,message,data}`。

## 6. 真实浏览器证据（本会话实跑，真实 Edge/Chromium）
- `assets/e501/e501_01_dashboard.png`、`e501_14_admin_dashboard_final.png`：管理员工作台真实数据渲染。
- `e501_02_runtime.png`、`e501_03_members.png`、`e501_04_audit.png`、`e501_05_rag.png`：Runtime、成员、审计、RAG 页面。
- `e501_10_admin_dashboard_390.png`、`e501_11_members_390.png`、`e501_12_runtime_390.png`：390px 关键页面。
- `e501_mobile_enterprise_dashboard.png`、`e501_mobile_enterprise_members.png`、`e501_mobile_enterprise_approvals.png`、`e501_mobile_audit.png`、`e501_mobile_enterprise_rag.png`、`e501_mobile_enterprise_runtime.png`、`e501_mobile_enterprise_ops.png`：本轮真实 CDP 390px 七页面截图。
- `e501_08_auditor_workbench.png`、`e501_09_auditor_runtime_denied.png`、`e501_13_auditor_runtime_denied.png`：auditor 真实走查；直输 `/enterprise/runtime` 重定向 `/dashboard`。
- 角色走查覆盖管理员、developer、reviewer、auditor、tenant_admin；Runtime 接管按钮按角色显示，后端 403 仍为最终约束。
- 当前真实桌面页 1103px 无整体溢出；历史 1440/768px 检查与本轮 390px 七页面检查均通过。

## 7. 测试证据（全部本会话真实执行）
- `frontend: npm run test`：**264 passed**（31 个测试文件）。
- `frontend: npm run build`：`vue-tsc -b` 通过，Vite 生产构建通过；Impeccable detector 返回 `[]`。
- `backend: .venv\Scripts\python.exe -m pytest tests/test_enterprise_e5_ops.py -q`：**4 passed**。
- 后端专项组合（Dashboard 时间边界、迁移、Worker、E5 运维）：**18 passed**。
- 后端全量（项目 `.venv`）：**772 passed, 25 skipped, 4 warnings**。
- `git diff --check`：无输出。
- 浏览器真实核验：管理员 7 页面、auditor 越权重定向、developer/reviewer/tenant_admin 角色边界、390px 七页面无整体横向溢出。

## 8. 源码位置
| 职责 | 文件 |
| --- | --- |
| 共享组件 | `frontend/src/components/enterprise/{StatusBadge,PageHeader,AsyncState,DetailDrawer,ConfirmDialog,PermissionGate}.vue` |
| 工作台 | `frontend/src/views/EnterpriseDashboardView.vue` |
| Runtime 控制台 | `frontend/src/views/RuntimeConsoleView.vue` |
| 成员/运维 | `frontend/src/views/EnterpriseMembersView.vue`、`frontend/src/views/EnterpriseOpsView.vue`、`backend/app/enterprise_ops/routes.py` |
| 审批/审计/RAG | `frontend/src/views/EnterpriseApprovalsView.vue`、`frontend/src/views/AuditCenterView.vue`、`frontend/src/views/RagConsoleView.vue` |
| 路由/侧栏 | `frontend/src/router/index.ts`、`frontend/src/components/AppShell.vue` |
| 测试 | `frontend/src/views/__tests__/E5Views.spec.ts`、`backend/tests/test_enterprise_e5_ops.py` |
| 设计审计 | `docs/project-development/assets/e501/audit-note.md` |

## 9. 范围外与残余风险（诚实）
- 云端生产环境的浏览器验收、HTTPS、监控告警、多副本压测、长稳与故障演练仍属于 E6，不在 E5 DONE 证据内。
- 页面数据依赖真实后端、租户上下文和索引状态；本地没有对应数据时页面按 empty/loading/error/forbidden 状态呈现，不使用 demo 数据填充。
- E5 结论仅表示企业前端完整化范围已验收，不代表企业版整体或生产运维已全部完成。

## 10. 收口结论
E5-01-FRONTEND-01 已完成本轮定义的企业前端完整化：7 个企业页面统一企业后台视觉和交互，真实 API 数据与权限边界闭环，运维页后端 `org:manage` 门禁生效，管理员/developer/reviewer/auditor/tenant_admin 角色走查完成，桌面与 390px 响应式核验通过，前后端回归和构建全绿。后续工作进入 E6 生产交付强化，不回写为 E5 未完成项。
