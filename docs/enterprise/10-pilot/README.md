# E7 商业试点交付包（docs/enterprise/10-pilot/）

> 阶段：E7 · 商业试点交付
> 前置：E1–E6（租户/RBAC、审批治理、RAG、Runtime、前端、生产运维）
> **状态：DONE**（交付包已形成并完成脱敏、边界和索引验收）
> 基线：E6 已判 `DONE（带已知限制）`，E7 进度板为 `34/34 (100%)`；本包在真实云端 `agentforge.ltd` 取证。试点现场执行与客户走查截图属于交付后的客户环境优化，不混入本包验收。

## 交付物清单

| # | 文件 | 内容 | 对应验收项 |
|---|---|---|---|
| 1 | `01-demo-data-and-walkthrough.md` | 脱敏演示数据 + 可复现走查脚本（团队工作台/成员/审批/审计/RAG/Runtime） | 可复现演示流程、脱敏材料 |
| 2 | `02-customer-onboarding-ops-manual.md` | 客户部署与运维手册（调用 E6 §1–34 真实命令/路径/端点） | 客户手册 |
| 3 | `03-sla.md` | SLA：可用性/RPO/RTO/容量/证书续期/责任边界 | SLA |
| 4 | `04-delivery-checklist.md` | 交付清单：环境/域名/证书/备份/回滚/监控/安全基线核对表 | 交付清单 |
| 5 | `05-pilot-acceptance-script.md` | 试点验收脚本：可复现验收步骤 + 成功判据 + 失败判据 | 试点验收脚本 |
| 6 | `06-boundaries-and-limitations.md` | 边界与免责声明：E6 已知限制、受支持回滚点、`ALLOW_UNKNOWN` 禁生产、备案/网络依赖 | 试点边界说明 |

## 关键真实基线（验收引用依据）

- **公网入口**：`https://agentforge.ltd/`（`A→<YOUR_SERVER_IP>`；`http→308→https 200`；TLS `Verify ok`；证书 `agentforge.ltd_bundle.crt` `notAfter Dec 20 2026`，每日 cron 03:17 检查）。
- **端点**：`/health`、`/api/health`、`/api/enterprise/ops/metrics`、`/api/enterprise/ops/status`(需 `org:manage`)。
- **生产栈（云端 <YOUR_SERVER_IP>）**：project=`agent-enterprise`；`docker-compose.enterprise.apponly.yml` + `host-tls.yml` + `redis-nonroot.yml`；backend `e6-61a6752-replica`×2；frontend（nginx，`127.0.0.1:8081`）；redis `--user 999`；DB `agent_enterprise`（外部 MySQL，29 表）；团队版 8080 保留。
- **迁移**：head=`e5f6071a2b3c`。
- **回滚点**：`agent-enterprise-backend:e6-rollback-compat-61a6752`（id `62f50ad3aa2e`）。
- **DB 基线**：users=5 / tenants=1 / tenant_memberships=5 / audit_events=2 / repositories=1 / approval_applications=0 / enterprise_runtime_tasks=0。
- **安全基线**：backend/frontend 非 root；企业 8081 仅 `127.0.0.1`；DB/Redis/Milvus 不公网；`enterprise.env` 600 不入库；`AUTH_COOKIE_SECURE=true`、`RAG_MODEL_MOCK_FALLBACK=false`。
- **监控**：`/opt/agent-monitor.py`（cert path 校正后），告警日志 `14 ALERT / 10 RECOVERY`，证书剩余天数检查（P2<30d）。

> 脱敏说明：本文档仅引用镜像 ID、sha256、路径、端点与**计数**；任何真实密码/Token/私钥不写入。演示登录账号在生产部署时须替换为强随机凭据。
