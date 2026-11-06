# E5-01 前端现状审计（改码前）

## 1. 现状
- 前端 Vue3 + Pinia + TS；`services/api.ts::apiRequest` 统一 fetch（credentials include + `code` 解包 + 401/422/502 错误映射）；`services/accessControl.ts::hasAccessRequirement(auth, tenancy, perm)` 做前端展示层权限。
- AppShell：`navEntries`（工作台/任务/需求/审批/回放/Diff/索引/设置/账号恢复/审计中心/成员管理/企业审批/企业 RAG），按 `permission` 过滤；顶栏租户 chip + 切换、项目切换器、通知、主题/语言切换；已有非 root 化、键盘 focus、响应式基础。
- Router：`/enterprise/*` 企业路由已接（members/approvals/rag），守卫 `hasAccessRequirement` 无权→重定向+提示。
- 设计令牌（style.css :root）：`--shell-bg/--panel-bg/--panel-border/--text-*/--brand/--success/--warning/--danger` + `--theme-*` 语义 token（含 dark 契约）；全局类 `.page-content/.panel/.panel__header/.page-notice/.page-toolbar/.data-table/.btn/.text-input/.metric`。
- 既有企业页：`EnterpriseMembersView`、`EnterpriseApprovalsView`、`RagConsoleView`（E3-08）、`AuditCenterView`、`EnterpriseDashboardView`（无，工作台仍为团队 `DashboardView`）。

## 2. 重复组件 / 视觉不一致
- **重复**：状态徽章（`StatusTag` vs RagConsole 的 `pill--*` scoped 类 vs Audit 的 `.page-notice`）；空/加载/错误态散落各页（`page-notice`/scoped loading）；详情弹层（Audit 用 confirm-modal、Approvals 用侧栏，样式各自实现）。
- **不一致**：RagConsole 用 scoped 自定义 pill/卡片，与其他页的全局 `.data-table/.page-notice` 风格不同；状态色映射未统一（verified/local_only/blocked vs succeeded/failed 等）。
- **最严重 Demo 感来源**：无统一 EnterpriseAppShell/PageHeader/状态组件；RAG 报告列表是自绘卡片堆叠；缺少统一 DetailDrawer/ConfirmDialog/ForbiddenState；部分页面窄屏未验证。

## 3. 可复用资产
- 语义 token（--brand/success/warning/danger + --theme-*）+ 全局类 + `StatusTag`/`EmptyPanel` + `services/accessControl` + `stores/tenancy`（tenant context/permissions）+ `services/api`。

## 4. 需新增页面边界
- 企业工作台（真实数据摘要：审批待办、审计最近、Runtime 摘要、租户上下文）；
- Runtime 控制台（真实 E4-01 `/api/enterprise/runtime/*`：状态、checkpoint、人工接管）；
- 企业设置/运维（健康、索引、配置摘要——用真实接口，不展示密钥）；
- 共享组件库（StatusBadge/PageHeader/AsyncState/DetailDrawer/ConfirmDialog/PermissionGate/MetricSummary）。

## 5. 不应触碰的团队版行为
- 团队 `DashboardView/Task*/Request*/Replay*/Diff*/IndexView`、登录/找回/法律页、团队审批 `ApprovalView` 行为、`workspace/approvals/auth` store 语义。
- 不改后端权限边界；前端只做展示/导航（后端权威）。

## 6. 结论
在不破坏团队版的前提下，抽取统一企业组件、新增真实数据的企业工作台 + Runtime 控制台，统一状态/空/错/403 呈现，补齐路由/侧栏权限与多角色+移动端真实浏览器走查。详见 `docs/project-development/111-enterprise-e5-01-frontend.md`。