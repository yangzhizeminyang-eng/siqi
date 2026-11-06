# 试点验收脚本（可复现）

> E7-01 交付物 5/6。试点客户验收执行脚本。每步给出命令/动作 + 成功判据；任一硬判据失败即 BLOCKED。
> 在真实云端 `agentforge.ltd` 执行（已取证基线见 `README.md`）。

## 0. 前置

- [ ] 备份当前生产（唯一写前动作）：`bash deploy/scripts/backup.sh` → 记录 sha256。
- [ ] 记录 DB 基线计数（users=5/tenants=1/tenant_memberships=5/audit_events=2/repositories=1）。

## 1. 公网入口与安全

| 步骤 | 命令 | 成功判据 |
|---|---|---|
| 1.1 | `curl -sI http://agentforge.ltd/` | `308` 前缀 → https |
| 1.2 | `curl -skI https://agentforge.ltd/` | `200` + `Strict-Transport-Security` |
| 1.3 | `openssl s_client …/443` | `Verify return code: 0`, CN=agentforge.ltd |
| 1.4 | `curl -sk /api/health` | `data.dependencies.db=ok, redis=ok` |

## 2. 功能（浏览器走查 + 断言）

| # | 动作 | 成功判据 |
|---|---|---|
| 2.1 | 登录 `/login`（TLS） | 登录成功，Cookie `HttpOnly;SameSite=lax;Secure` |
| 2.2 | 工作台 `/dashboard` | 吞吐/趋势图表渲染（真实数据）|
| 2.3 | 审批 `/approval` | 通过→执行→刷新闭环（E2 已验）|
| 2.4 | 审计 `/audit` | 事件列表 + 详情 + 任务跳转 |
| 2.5 | RAG `/enterprise/rag` | 检索结果 + 评测报告 verified 徽章 |
| 2.6 | Runtime `/enterprise/runtime` | 任务/checkpoint 真实数据 + 人工接管 |
| 2.7 | 团队 8080 `/health` | `200`（不受影响）|

## 3. 权限门禁

- [ ] auditor 访问 `/api/enterprise/ops/status` → `403`（无 `org:manage`）。
- [ ] 未登录访问 `/api/health` 外的受保护接口 → `401`。
- [ ] auditor 直输 `/enterprise/runtime` → 前端重定向 + 后端 403 兜底。

## 4. 运维回归（非破坏）

| # | 动作 | 成功判据 |
|---|---|---|
| 4.1 | `GET /api/enterprise/ops/metrics` | `200` + 四条依赖行 |
| 4.2 | `GET /api/enterprise/ops/status`(admin) | `app_env=enterprise`、`migration=e5f6071a2b3c/head` |
| 4.3 | 证书 `check-cert-expiry.sh` | 剩余天数 >30d → OK（<30 触 WARN）|
| 4.4 | 隔离恢复演练（可选） | 还原 `agent_e4_restore_*` 后表/行数对拍一致 |

## 5. 失败判据（任一 → BLOCKED，禁止继续危险操作）

- `/api/health`、`/`、`/ops/metrics` 任一非 200；团队 8080 异常。
- 生产 DB 表数≠29 或关键计数变化。
- 需要 DROP/ALTER/TRUNCATE/DELETE 生产库的任何操作 → 直接拒绝。
- 需开启 `ALLOW_UNKNOWN_MIGRATION_REVISION` 才能让旧镜像启动 → 判「不受支持路径」，BLOCKED。
- 备份/恢复/回滚任一失败未回退到可恢复点。

## 6. 验收结论判定

| 判据 | 通过条件 | 当前状态 |
|---|---|---|
| 公网入口安全 | §1 全过 | ✅（E6 已取证）|
| 核心功能走查 | §2 全过 | ⏳ 待客户环境截图 |
| 权限门禁 | §3 全过 | ✅（E1-01/E5 已验）|
| 运维回归 | §4 全过 | ✅（E6 已取证）|
| 无失败判据触发 | §5 均未触发 | ✅ |

> 全部通过 → 试点验收 PASS；任一 §5 触发 → BLOCKED 并输出真实错误与影响。