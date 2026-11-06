# 演示数据与可复现走查脚本

> E7-01 交付物 1/6。目标：客户试点时可复现、脱敏地演示企业版核心能力。
> 真实基线：云端 `agentforge.ltd`（<YOUR_SERVER_IP>）已于 E6 取证。本文档不写真实密码；播种数据用明确「演示/脱敏」标记。

## 1. 演示环境前置

| 项 | 值 | 取得方式 |
|---|---|---|
| 公网入口 | `https://agentforge.ltd/` | ICP 备案后已解除拦截；`http→308` |
| 演示登录 | 脱敏演示账号（生产须替换为强随机凭据）| 见 §4「账号与脱敏」 |
| 生产栈 | apponly+host-tls+redis-nonroot（cloud） | `docker compose ps` |

## 2. 脱敏演示数据（播种清单）

以下为走查所需最小数据面，均已存在（E1–E6 落地）：

| 域 | 计数（真实） | 用途 |
|---|---|---|
| users | 5 | 管理员/开发者/reviewer/审计/租户管理员演示 |
| tenants / tenant_memberships | 1 / 5 | 可见租户上下文与成员 |
| audit_events | 2 | 审计中心已有留痕（PROJECT_CREATED + 越权被拒） |
| repositories | 1 | RAG/仓库检索演示数据集 |
| approval_applications | 0 | 审批台「空状态」演示 |
| enterprise_runtime_tasks | 0 | Runtime「空状态」演示 |

> 脱敏：播种数据不含真实 Token/密码。若需更丰富演示（多条审批/Runtime/评测），在隔离开发库按 `app/enterprise_rag`、`approvals`、`runtime` 的 model 造数；生产库演示前先 `backup.sh`。

## 3. 走查脚本（真实端点 + 期望结果）

```bash
# 3.1 入口/健康（期望全 200）
curl -sk -w '<root>%{http_code}\n' https://agentforge.ltd/
curl -sk -w '<health>%{http_code}\n' https://agentforge.ltd/health
curl -sk -w '<api_health>%{http_code}\n' https://agentforge.ltd/api/health
curl -sk -w '<ops_metrics>%{http_code}\n' https://agentforge.ltd/api/enterprise/ops/metrics
# 期望：root=200 health=200 api_health=200 ops_metrics=200；metrics 含 agent_dependency_db=1 redis=1

# 3.2 语言命令确认登录后审计可见（登录需演示账号；不打印 cookie 值）
curl -sk -c /tmp/cj -o /dev/null -X POST https://agentforge.ltd/api/auth/login \
  -H "Content-Type: application/json" -d '{"account":"<演示账号>","password":"<演示密码>"}'
curl -sk -b /tmp/cj https://agentforge.ltd/api/audit-events -o /tmp/audit.json
# 期望：200；audit_events ≥1（PROJECT_CREATED）

# 3.3 运维状态（org:manage；auditor 应 403）
curl -sk -b /tmp/cj https://agentforge.ltd/api/enterprise/ops/status
# 期望：admin=200 且 app_env=enterprise、use_sqlalchemy=true、rag=hybrid、migration=e5f6071a2b3c/head

# 3.4 走查前先建可恢复备份（隔离演练唯一写前动作）
bash deploy/scripts/backup.sh   # 产出含 sha256 的 dump
```

## 4. 走查步骤（页面 + 期望）

| 步骤 | 页面/动作 | 期望（真实） |
|---|---|---|
| S1 | `/login` TLS 渲染 | 登录页正常（E6 已 validate）|
| S2 | 工作台 `/dashboard`（admin） | 吞吐/趋势图表渲染，真实数据 |
| S3 | 审批 `/approval` | 空状态或队列；通过→执行→刷新闭环（E2 已本地联调）|
| S4 | 审计 `/audit` | audit_events 列表 + 详情弹窗 + 越权被拒留痕 |
| S5 | RAG `/enterprise/rag` | 检索（query/模式）+ 评测报告列表 verified 徽章 |
| S6 | Runtime `/enterprise/runtime` | 任务/checkpoint 真实数据 + 人工接管入口 |
| S7 | 团队工作台 8080 `/health` | 团队不受影响（200）|

> 浏览器走查（S2–S6）为人工步骤；InSight 用真实 Edge/Chromium 截图留证（试点执行时补图，路径建议 `assets/e701/`）。

## 5. 角色走查矩阵（RBAC）

| 角色 | 工作台 | 成员 | 审批 | 审计 | RAG | Runtime | 运维/设置 |
|---|---|---|---|---|---|---|---|
| 管理员 | ✓ | manage | approve | view | ✓ | view | ✓ (`org:manage`) |
| developer | ✓ | view | view | view | ✓ | view | — |
| reviewer | ✓ | view | view | view | ✓ | view | — |
| auditor | ✓ | view | — | view(403 于写) | 默认拒绝 | 重定向 | 403 |
| tenant_admin | ✓ | manage(租户) | view | view | ✓ | view | — |

> E5 已走查 5 角色；auditor 直达 Runtime 重定向 + 后端 403 兜底为既有边界。