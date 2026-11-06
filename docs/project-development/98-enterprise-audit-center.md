# DEV-098 企业审计中心

> 文档类型：企业版源码功能开发与云端验收记录  
> 核实日期：2026-09-15  
> 当前状态：基础页面已完成本地实现、回归测试、云端发布和公网验收；E1-01 企业权限兼容增量已完成本地回归，待同步云端验证

## 1. 功能目标与范围

把现有项目配置页中的少量审计事件展示升级为独立的企业审计中心，支持管理员集中查看：

- 项目配置、任务流转、审批和索引相关审计事件；
- 操作者、资源类型、资源 ID、事件类型和发生时间；
- 风险事件数量和任务追踪数量；
- 关键词筛选、资源类型筛选和事件详情；
- 从任务审计事件跳转到关联任务详情。

本次复用现有 `/api/audit-events` 真数据，不新增数据库表、不伪造演示事件；同时接入 E1-01
企业权限与租户范围约束。审计导出、留存策略和字段级脱敏仍属于后续企业版治理阶段。

## 2. 业务流程

```mermaid
flowchart LR
    A[管理员或企业审计角色访问审计中心<br/>frontend/src/router/index.ts] --> B{project:manage 或 audit:view}
    B -->|无权限| C[显示权限边界提示]
    B -->|通过| D[GET /api/audit-events<br/>backend/app/main.py]
    D -->|成功| E[AuditCenterView 加载事件]
    D -->|失败| F[显示错误状态和重新加载]
    E --> G[计算事件、任务追踪和风险摘要]
    E --> H[关键词与资源类型筛选]
    H --> I[点击事件打开详情]
    I --> J{资源类型}
    J -->|task| K[跳转 /tasks/{resource_id}]
    J -->|其他| L[保留详情查看]
```

## 3. 关键实现

### 3.1 权限与数据来源

- 路由和导航使用 `project:manage` 或企业 `audit:view` 作为访问门槛；
  页面加载与刷新按钮复用同一权限判断。
- 页面加载时只请求 `/api/audit-events`，不从本地 demo 数据补齐真实模式内容。
- 后端 `/api/audit-events` 由 `require_audit_view` 最终授权，并按当前租户过滤；
  页面不信任客户端提交的操作者或资源范围。
- 越权拒绝事件显式记录当前 `tenant_id`，避免持久化后落入默认租户。

### 3.2 企业治理摘要

页面计算并展示：

- 审计事件总量；
- 任务资源事件数量；
- 风险、失败、拒绝、阻断和安全相关事件数量；
- 最新事件时间。

这些指标用于快速判断治理压力，不能替代正式报表或 SLA 指标。

### 3.3 筛选和详情

- 关键词覆盖事件类型、资源类型、资源 ID、操作者、摘要和变更摘要；
- 资源筛选支持全部、任务、仓库、项目和索引；
- 详情面板展示事件 ID、操作者、资源、时间、变更摘要和执行步骤关联；
- 任务事件提供真实任务详情跳转，其他资源保留只读详情，不生成不存在的页面。

## 4. 相关源码文件

| 文件 | 职责 |
| --- | --- |
| `frontend/src/views/AuditCenterView.vue` | 审计中心页面、筛选、详情和任务追踪 |
| `frontend/src/views/__tests__/AuditCenterView.spec.ts` | 页面加载、筛选、详情跳转、权限和错误状态测试 |
| `frontend/src/services/accessControl.ts` | 团队/企业权限任一满足门 |
| `frontend/src/router/index.ts` | `/audit` 路由和双权限门禁 |
| `frontend/src/components/AppShell.vue` | 企业控制台导航和页面标题 |
| `backend/app/main.py` | 既有 `/api/audit-events` 接口与权限校验 |
| `backend/app/tenancy/context.py` | 越权/成员操作审计的租户归属 |
| `backend/app/db/reads.py` | DB/运行时审计事件读取层 |
| `frontend/src/types/domain.ts` | `AuditEvent` 前端契约 |

## 5. 联调注意点

1. 拥有团队 `project:manage` 或企业 `audit:view` 的账号能进入页面。
2. 页面筛选在已授权事件集合内执行；后续接入多租户时，后端必须继续以认证上下文限制租户和项目范围。
3. 审计接口失败时不能用 mock 事件替代，必须显示错误状态并允许重新加载。
4. 任务资源 ID必须经过路由编码后再跳转。
5. 当前版本未实现审计导出、时间范围服务端分页和字段级脱敏扩展；这些能力应在企业版审计 API 定稿后单独开发。

## 6. 测试与验收

### 6.1 本地测试

| 项目 | 结果 |
| --- | --- |
| 审计中心定向测试 | `6 passed` |
| E1-01 相关前端权限测试 | `17 passed`（审计页 + AppShell） |
| 前端生产构建 | 通过 |
| 页面质量扫描 | 无阻断项 |
| 后端 E1-01 回归 | `9 passed`；拒绝审计事件 tenant_id 通过 |

定向测试覆盖：

- 真实审计事件加载和治理摘要；
- 关键词与资源类型筛选；
- 事件详情和任务跳转；
- 接口失败、错误提示和重新加载入口；
- 企业 `audit:view` 角色可访问、无审计权限不加载。

### 6.2 云端验收

部署目录：

```text
/home/ubuntu/enterprise-ai-agent-20260914
```

云端发布仅重建前端服务，不删除数据卷、不重启后端、Worker、Redis，不修改历史审计数据。

真实云端验收结果：

| 项目 | 结果 |
| --- | --- |
| 公网审计中心 | 通过，管理员登录后访问 `http://<YOUR_SERVER_IP>:8080/audit` |
| 审计事件接口 | 通过，登录态 `GET /api/audit-events` 返回 HTTP 200，页面加载 `377` 条事件 |
| 权限门禁 | 通过，未登录直接请求 `GET /api/audit-events` 返回 HTTP 401 |
| 事件详情 | 通过，详情展示事件 ID、操作者、资源、发生时间和变更摘要 |
| 任务关联跳转 | 通过，从任务事件打开详情并跳转到 `/tasks/TASK-8806` |
| 健康检查 | 通过，`/health` HTTP 200，`/api/health` HTTP 200 |
| 云端服务 | 通过，前端、后端、Redis healthy，Worker 正常运行 |

真实运行截图：

- 审计中心列表：[audit-center-list.png](assets/screenshots/audit-center-list.png)
- 任务审计详情与关联任务：[audit-center-task-detail.png](assets/screenshots/audit-center-task-detail.png)

验收说明：

- 截图来自 `http://<YOUR_SERVER_IP>:8080` 真实运行页面，不是示意图。
- 本次未执行 `docker compose down -v`，未删除业务数据卷。
- 未登录接口返回 `401` 属于预期安全行为，不视为链路错误。

## 7. 回滚

本功能只涉及前端页面、路由、导航和测试文件。回滚时恢复对应文件后，仅重建前端容器：

```bash
cd /home/ubuntu/enterprise-ai-agent-20260914
docker compose --env-file deploy/env/team.env -f deploy/docker-compose.team.yml up -d --build frontend
```

不执行 `docker compose down -v`，避免删除业务数据卷。

## 8. 复盘

- 做得好的地方：把已有真实审计数据从配置页局部展示提升为可筛选、可追踪的独立工作流，并保留权限和错误边界。
- 需要加强的地方：企业版后续应补服务端时间范围、分页、导出和多租户审计字段。
- 下次重点：把企业角色权限的真实浏览器走查补上，并将本轮增量同步到云端后重新验收。
