# 后端服务说明

## 启动

```powershell
cd backend
python main.py
```

服务默认运行在 `http://127.0.0.1:8000`。

## 认证

- `POST /api/auth/login`
- `POST /api/auth/logout`
- `GET /api/auth/me`
- `POST /api/auth/password/forgot`
- `POST /api/auth/password/emergency-reset`
- `GET /api/auth/password/requests`
- `POST /api/auth/password/requests/{request_id}/reset`
- `GET /api/users`
- `GET /api/users/{user_id}`

## 项目与仓库配置

- `GET /api/projects`
- `POST /api/projects`
- `GET /api/projects/{project_id}`
- `PUT /api/projects/{project_id}`
- `GET /api/repositories?project_id=proj_payment`
- `POST /api/repositories`
- `PUT /api/repositories/{repository_id}`
- `GET /api/audit-events?resource_type=repository`

- 业务数据经 SQLAlchemy 落 MySQL（team/prod 强制，`DATABASE_URL` 指向 mysql）或 SQLite（dev 默认）；启动时 `restore_from_db` 从 DB 恢复。开发引导种子如下：

- 默认项目：`支付中台`
- 默认仓库：`payment-service`
- 管理员权限：需要 `project:manage`
- Gitee Token：从环境变量（`GITEE_TOKEN`）或按仓库项目配置注入，**不写死、不提交**；后端仅在内存私有存储供 Gitee 调用，响应只返回 `token_configured`、`token_alias`、`token_updated_at`，不回显 `gitee_token`
- 仓库地址：仅支持 `https://gitee.com/org/repo(.git)` 与 `git@gitee.com:org/repo(.git)`
- 测试命令：必须命中 `command_whitelist`
- 保存项目或仓库配置会写入 `AUDIT_EVENTS`

## Gitee Issue 创建任务

- `GET /api/tasks`
- `GET /api/tasks/{task_id}`
- `POST /api/tasks`
- `POST /api/tasks/{task_id}/cancel`
- `POST /api/tasks/{task_id}/retry`

创建任务请求只需要：

```json
{
  "project_id": "proj_payment",
  "repository_id": "repo_payment_service",
  "issue_id": "I8ABC1"
}
```

后端会从仓库配置自动带出默认分支、测试命令、默认审批人和工作分支，并调用 Gitee OpenAPI 拉取 Issue 标题、正文、评论和标签。Issue 快照会保存 `title`、`body`、`comments`、`labels` 和 `raw_response`，避免外部 Issue 后续变化影响回放。

注意：

- 需连接真实 Gitee 前，先在项目/仓库配置按库注入有效 Gitee Token（`GITEE_TOKEN`）；未配置则不访问真实 Gitee，同步按实际结果标识 `SYNC_FAILED`，不伪装成功。
- 同一仓库、同一 Issue 如果已有未结束任务，`POST /api/tasks` 返回 `409` 和 `existing_task`，前端提示跳转已有任务。
- 旧任务进入 `COMPLETED`、`FAILED` 或 `CANCELLED` 后，可以再次创建新版本。
- 风险等级在创建阶段保持为空，后续由 AI 分析、修改计划和安全策略共同计算。
- 工作分支按 `agent/{base_branch}/issue-{issue_id}-v{task_version}` 自动生成，例如 `agent/main/issue-IKD5H1-v1`。
- 任务列表支持 `project_id`、`status`、`risk`、`keyword`、`manual_only`、`time_range` 查询参数。
- `GET /api/tasks` 和 `GET /api/tasks/{task_id}` 为只读接口（经 DB 读层返回最新状态）。任务推进由 Celery Worker / LangGraph 主链承载（历史 MVP 的"HTTP 触发内联状态机推进"已废弃，读接口不再推进）。
- 非终态任务可取消为 `CANCELLED`；失败任务可通过 `/retry` 重新进入 `ANALYSIS_QUEUED`。

登录请求：

```json
{
  "account": "developer@company.com",
  "password": "Dev123!",
  "remember_me": true
}
```

登录成功后由后端签发 `httpOnly` Cookie `agent_session`：

- 普通会话有效期：8 小时；
- `remember_me=true`：7 天；
- `SameSite=lax`、`Path=/`；
- `Secure` 由 `AUTH_COOKIE_SECURE` 环境变量控制；
- 前端不保存明文密码，也不直接读取 JWT。

前端通过 `GET /api/auth/me` 恢复当前用户，退出时调用 `POST /api/auth/logout` 清理 Cookie。

后端启动时会初始化数据库 schema，并装载任务工作流与 DeepAgents 入口。生产环境请显式设置 `AUTH_JWT_SECRET`、`AUTH_COOKIE_SECURE`、`REDIS_URL` 等安全和并发相关环境变量。

团队需求同步和 Agent 任务创建使用 Redis 分布式锁。Redis 短暂不可用时会记录降级计数并临时使用进程锁，因此该窗口只保证单实例幂等；多 Worker 或多容器部署必须确保 Redis 高可用，并监控锁降级指标。

### 密码恢复责任

- 普通员工在登录页提交企业邮箱，申请进入管理员的账号恢复待办；
- 管理员在登录后进入“账号恢复”，设置临时密码，再通过企业内部渠道告知员工；
- 管理员忘记密码时，使用登录页“管理员应急恢复”，由部署负责人或平台安全联系人提供 `AUTH_RECOVERY_CODE`；
- 生产环境必须通过环境变量配置 `AUTH_RECOVERY_CODE`，并定期轮换，不能留空；
- 配置/Token/找回申请/密码变更经 DB 持久化，服务重启后 `restore_from_db` 恢复；生产必须配置 `AUTH_RECOVERY_CODE`、密钥管理与审计，谨防凭据落表或日志。

详细开发记录见：

`docs/project-development/02-login-authentication.md`

## 模型接入（可选）

文本大模型与向量模型均为可选接入，Key 留空时自动回退规则/本地兜底，本地测试无需任何 API Key。

| 环境变量 | 说明 |
| --- | --- |
| `LLM_PROVIDER` | 文本大模型提供商，默认 `deepseek` |
| `LLM_MODEL` | 模型名，默认 `deepseek-chat`（非 thinking，兼容 tool-calling/structured-output） |
| `LLM_BASE_URL` | DeepSeek 接口地址，默认 `https://api.deepseek.com` |
| `LLM_API_KEY` | DeepSeek API Key；留空时分析回退规则生成 |
| `EMBEDDING_PROVIDER` | 向量模型提供商，默认 `zhipu` |
| `ZHIPU_MODEL` | 模型名，默认 `embedding-3` |
| `ZHIPU_BASE_URL` | 智谱接口地址，默认 `https://open.bigmodel.cn/api/paas/v4` |
| `ZHIPU_API_KEY` | 智谱 API Key；留空时 Embedding 回退本地 token-hash |
| `EMBEDDING_DIMENSIONS` | 向量维度，`embedding-3` 实际返回 2048 维 |
| `MODEL_TIMEOUT_SECONDS` | 模型调用超时（秒），默认 60 |
| `MODEL_MAX_RETRIES` | 模型调用重试次数，默认 2 |
| `MODEL_MOCK_FALLBACK` | 失败时是否回退本地兜底，默认 `true` |

安全约束：

- 真实 `LLM_API_KEY` / `ZHIPU_API_KEY` 只从环境变量读取，不得写入代码、测试、文档或提交记录。
- 模型只负责生成分析叙述字段 `overview` / `findings` / `uncertainties`；风险等级、阻塞问题、注入识别、来源引用等安全/决策字段始终由规则计算。
- 模型错误信息经脱敏处理，绝不回显 API Key。

## 测试账号

- `admin@company.com / Admin123!`
- `approver@company.com / Approver123!`
- `developer@company.com / Dev123!`
- `lead@company.com / Lead123!`
