# 团队版部署与验收

## 1. 部署定位

团队版建议先做单机可用部署，独立于 MVP 环境。当前云部署入口为 `8080`，不能覆盖服务器上已有的 `8002` 进程。

| 环境 | 建议 |
| --- | --- |
| 应用环境 | `APP_ENV=team` |
| 前端端口 | 生产 Nginx `8080` |
| 后端端口 | 容器内部 `8000` |
| 云服务器 | `<YOUR_SERVER_IP>` |
| MySQL | `agent_team` |
| Redis DB | `1` |
| Milvus Collection 前缀 | `team_code_`、`team_reuse_` |
| 日志目录 | `logs/team` |

## 2. 必需服务

- 后端 API。
- 前端 Web 控制台。
- MySQL。
- Redis。
- Milvus。
- Docker Runner。
- Gitee API 授权。
- DeepSeek 文本模型。
- Embedding 模型。

## 3. 环境变量

| 配置 | 说明 |
| --- | --- |
| `APP_ENV=team` | 团队版环境 |
| `DATABASE_URL` | MySQL 连接 |
| `USE_CELERY=true` | 团队版必须真实 Celery（不允许静默 eager） |
| `CELERY_BROKER_URL` | Redis broker（如 `redis://127.0.0.1:6379/1`） |
| `CELERY_RESULT_BACKEND` | Redis 结果后端（如 `redis://127.0.0.1:6379/1`） |
| `REDIS_URL` | Redis 连接；配置后平台需求同步/建任务启用 Redis 分布式锁，多 Worker 必须配置 |
| `REALTIME_REDIS_URL` | 实时同步 Redis 连接；未配置时复用 `REDIS_URL`，再回退到 `CELERY_BROKER_URL` |
| `REALTIME_ENABLED` | 是否启用 Redis Pub/Sub + SSE；团队环境默认按 Redis 配置启用 |
| `TEAM_REQUEST_LOCK_TTL_SECONDS` | 平台需求操作锁 TTL，默认 30 秒，持有者崩溃后自动释放 |
| `MILVUS_URI` | Milvus 连接 |
| `GITEE_CLIENT_ID` / `GITEE_CLIENT_SECRET` | 后续 OAuth 预留 |
| `LLM_PROVIDER` / `LLM_MODEL` / `LLM_BASE_URL` | 文本模型 |
| `EMBEDDING_PROVIDER` / `EMBEDDING_MODEL` | 向量模型 |
| `TEST_RUNNER_MODE=docker` | 团队版建议默认真实 Runner |
| `TEST_RUNNER_IMAGE=platform-test-runner:base` | 服务器需先单独构建受控测试镜像 |
| `TEST_RUNNER_NETWORK=none` | 默认禁止测试容器联网 |
| `TEST_RUNNER_INSTALL_MODE=image` | 默认只使用预构建镜像依赖 |
| `DOCKER_GID` | 服务器执行 `stat -c '%g' /var/run/docker.sock` 后填写 |

## 4. 验收场景

| 场景 | 操作 | 通过标准 |
| --- | --- | --- |
| 平台创建需求 | 开发者填写标题、正文、验收标准 | 生成 `REQ-*` 草稿 |
| 同步 Gitee Issue | 点击同步 Gitee | 返回 Gitee Issue 编号和 URL |
| 自动创建任务 | 基于已同步 Issue 创建 Agent 任务 | 自动进入索引和分析 |
| 空 Issue 处理 | 创建正文不足的 Issue | 进入人工补充信息，不直接失败 |
| 索引复用 | 同仓库同 Commit 创建第二个任务 | 复用已有索引 |
| 低风险自动推进 | 创建低风险文案类任务 | 自动通过分级门禁并记录事件 |
| 高风险审批 | 创建权限、支付、密钥类任务 | 进入审批中心 |
| 测试失败重试 | 配置失败测试命令 | 保留 Diff，允许有限重试 |
| PR 描述生成 | 测试结束后生成 PR 描述 | 六段式结构可复制 |
| 回放审计 | 打开回放页 | 能看到 Issue 同步、索引、分析、计划、审批、Diff、测试、PR 描述 |
| 成员权限 | 观察者尝试审批 | 前端禁用，后端拒绝 |
| 并发锁 | 多窗口同时点同一需求的同步/建任务 | 只成功一次，其余收到 409「正在处理，请稍后重试」，重试后复用结果，不产生重复 Issue/任务 |
| Celery 异步 | 索引/Agent/测试/Gitee 同步走 Worker | API 返回 queued；Worker 消费日志见 `logs/celery-worker.out.log`（`check_redis_worker` 验收 PASS） |
| 失败重试 | Worker 中途失败 | 非最后尝试保留可恢复态（retry_count/failure_reason/next_action）可续跑，非直接 FAILED；末次才 FAILED+审计 |
| 多人实时同步 | 用户 A 修改任务/评论/审批，用户 B 保持页面打开 | MySQL 写入成功后通过 Redis Pub/Sub 发布变化，B 的 SSE 收到 `team-update` 后重新请求 API 并刷新；Redis 不可用时页面保留轮询/手动刷新兜底并明确记录实时通道不可用 |

## 5. 自动化测试清单

- 平台需求 CRUD。
- Gitee Issue 同步成功、失败、重试。
- 已同步 Issue 创建任务。
- 重复 Issue 幂等。
- 空 Issue 转人工补充。
- 自动审批门禁。
- 批量审批逐项结果。
- Celery 四链路 dispatch（index/agent/test/sync）在 `USE_CELERY=true` 时派发、eager 降级标记清晰。
- run_agent 首败不置终态、末次重试失败才 FAILED。
- `_AGENT_ENQUEUED` 终态/恢复后清理、允许重新派发。
- `scripts.check_redis_worker` 验收（Redis 可达 + worker 在线 + smoke 被消费）。
- 服务重启后数据仍可读取。
- Redis 可达且 `/api/events/stream` 返回 `200`；发布任务、评论、审批或通知变化后，在线页面能收到对应 `team-update`。

## 6. 上线前清单

- 已在腾讯云防火墙开放 TCP `8080`，未开放数据库、Redis、Milvus 公网端口。
- 已执行 `docker compose -f deploy/docker-compose.team.yml config --quiet`。
- 已构建 `platform-test-runner:base`。
- 已配置真实 MySQL、Redis、Milvus。
- 已配置真实 Gitee 授权。
- 已配置真实文本模型和 Embedding 模型。
- Docker Runner 能执行项目测试命令。
- 默认审批人和风险阈值已配置。
- 敏感路径和命令白名单已配置。
- 审计日志不可被普通用户修改。
- 关键页面有真实截图。

详细命令、资源约束、备份和回滚见 [`96-team-cloud-deployment.md`](../project-development/96-team-cloud-deployment.md)。

## 7. 是否进入企业版的判断标准

团队版满足以下条件后，再进入企业版：

- 3 到 10 人团队连续使用核心流程稳定。
- 平台内创建 Issue 到 PR 描述闭环稳定。
- 数据持久化和队列稳定。
- 真实 Runner 稳定。
- 审批、通知、回放能支撑团队日常复盘。
- 需要多租户、SSO、合规留存或高可用部署时，再进入企业版。
