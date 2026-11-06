# 团队版 API 设计

## 1. 文档定位

本文档只定义团队版相对 MVP 的新增或变化接口。MVP 已有接口仍以 `../08-api-design.md` 为准。

## 2. 平台需求/Issue 同步

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/team/requests` | 在平台创建需求/缺陷草稿 |
| GET | `/api/team/requests` | 查看平台需求/缺陷列表 |
| GET | `/api/team/requests/{request_id}` | 查看平台需求/缺陷详情 |
| PATCH | `/api/team/requests/{request_id}` | 更新未同步或同步失败的草稿 |
| POST | `/api/team/requests/{request_id}/sync-gitee` | 同步创建 Gitee Issue |
| POST | `/api/team/requests/{request_id}/create-task` | 基于已同步 Issue 创建 Agent 任务 |

### POST `/api/team/requests`

请求：

```json
{
  "project_id": "proj_xxx",
  "repository_id": "repo_xxx",
  "type": "bug",
  "title": "登录页刷新后误跳转",
  "body": "现象、期望结果和复现步骤",
  "acceptance_criteria": ["刷新后保持当前页面", "登录态失效时才跳转登录"],
  "labels": ["bug", "frontend"],
  "priority": "medium"
}
```

`type` 取值复用 MVP 任务类型枚举：`demand`、`bug`、`refactor`、`test`、`doc`。`demand` 表示新需求或功能增强；不使用 `feature` 或 `task`，避免和 Agent Task 对象混淆。

响应：

```json
{
  "id": "REQ-1001",
  "sync_status": "DRAFT",
  "gitee_issue_number": null,
  "gitee_issue_url": null
}
```

### POST `/api/team/requests/{request_id}/sync-gitee`

成功响应：

```json
{
  "id": "REQ-1001",
  "sync_status": "SYNCED",
  "gitee_issue_id": "34205941",
  "gitee_issue_number": "IKD5H1",
  "gitee_issue_url": "https://gitee.com/org/repo/issues/IKD5H1"
}
```

失败响应要求：

- 保留 `REQ-*` 草稿。
- 记录 `sync_status=SYNC_FAILED`。
- 记录 `failure_reason`。
- 写入审计事件 `GITEE_ISSUE_SYNC_FAILED`。

平台需求 `sync_status` 统一取值：

| sync_status | 说明 |
| --- | --- |
| `DRAFT` | 已保存平台草稿，尚未同步 |
| `SYNCING` | 正在同步 Gitee Issue |
| `SYNCED` | 已同步 Gitee Issue |
| `SYNC_FAILED` | 同步失败，可重试 |
| `TASK_CREATED` | 已创建 Agent 任务 |
| `CLOSED` | 已关闭，不再推进 |

同步失败响应示例：

```json
{
  "id": "REQ-1001",
  "sync_status": "SYNC_FAILED",
  "failure_reason": "Gitee Token 已过期，请重新授权后重试",
  "manual_reason": "auth_expired"
}
```

幂等与并发边界（DEV-016-B/C 生产前修复后口径）：

- 同步与建任务两条链路均使用 **Redis 分布式锁**（实现见 `backend/app/core/distributed_lock.py`）：`SET lock_key token NX PX ttl` 获取，Lua 脚本比对 token 释放，锁粒度为 `team_request:{sync|create_task}:{request_id}`，跨 Worker/跨容器互斥。
- 部署时配置 `REDIS_URL` 即启用分布式锁；锁 TTL 由 `TEAM_REQUEST_LOCK_TTL_SECONDS` 控制（默认 30 秒），持有者崩溃也会自动释放，不产生死锁。未配置 `REDIS_URL` 的本地/单进程环境降级为进程内 per-key 锁（仅保证单实例幂等，不得用于多实例生产部署）。
- 锁获取为**非阻塞**：竞争方立即收到 409（同步：「该需求正在同步中，请勿重复提交，请稍后重试」；建任务：「该需求正在创建 Agent 任务，请勿重复提交，请稍后重试」），客户端稍后重试即可拿到已同步映射或复用已有任务，服务端不排队。
- Redis 运行期瞬时不可用时，当次获取降级进程锁并累计 `degraded_count` 供观测，服务不中断；Redis 恢复后自动回到分布式锁。生产应保障 Redis 高可用，降级窗口内只保证单实例幂等。
- Gitee Issue 来源标识（`来源：平台需求 REQ-*`）查重是网络超时场景的**兜底保护**：重试前按标识在仓库 Issue 列表中翻页查重（上限 `GITEE_ISSUE_DEDUPE_MAX_PAGES`），命中即复用映射；查重命中 `auth_expired` 时按 Token 过期处理。分布式锁与查重都不替代持久化唯一约束，`gitee_issue_id` 落库后的唯一索引才是最终幂等保障。
- 创建成功响应校验：`id`、`number` 必填，`html_url`/`url` 至少其一必填；缺失时置 `SYNC_FAILED` 且不保存半截映射。

### POST `/api/team/requests/{request_id}/create-task`（DEV-016-C）

从已同步平台需求一键创建或复用 Agent 任务，与 MVP `POST /api/tasks` 共用同一套任务创建内核（幂等查重、版本号递增、工作分支生成、索引四分流），不另起状态机。

权限与状态门禁：

- 权限点走团队版 `request:create` + 请求归属校验（开发者只能为本人需求创建），不卡旧 `task:create` 门禁。
- 仅 `SYNCED` / `TASK_CREATED` 可触发；`DRAFT` / `SYNCING` / `SYNC_FAILED` 返回 409，提示先完成 Gitee 同步。
- 同一仓库同一 Issue 存在未结束任务时不新建，返回 `reused=true`、已有任务与 `jump_url`；已结束任务再次调用自动新建版本（`version` 递增）。
- Gitee Issue 拉取失败或内容为空时任务仍创建，置 `manual_required=true`、`manual_reason=context_missing`，快照用平台需求内容兜底，不直接失败。

成功响应：

```json
{
  "task": {
    "id": "TASK-8801",
    "version": 1,
    "issue_id": "IKD5H1",
    "team_request_id": "team_req_001",
    "team_request_no": "REQ-1001",
    "gitee_issue_url": "https://gitee.com/org/repo/issues/IKD5H1",
    "base_commit_sha": "c0ffee11dead..."
  },
  "request": { "id": "team_req_001", "sync_status": "TASK_CREATED", "task_id": "TASK-8801" },
  "jump_url": "/tasks/TASK-8801",
  "created": true,
  "reused": false
}
```

同步后自动创建（DEV-016-C）：

- 仓库配置新增 `auto_create_task_after_sync`（默认 `false`，经 `POST/PUT /api/repositories` 保存）。开启后 `sync-gitee` 成功分支自动调用同一创建内核。
- 自动创建失败**不回滚 Gitee 同步**：需求保持 `SYNCED`，写入审计 `TEAM_REQUEST_TASK_AUTO_CREATE_FAILED`，前端保留手动创建入口。

审计事件（`resource_type=team_request`、`resource_id=TeamRequestRecord.id`，均可经 `GET /api/team/requests/{request_id}/events` 回放）：

| event_type | 触发时机 |
| --- | --- |
| `TEAM_REQUEST_TASK_CREATED` | 手动一键创建新任务成功 |
| `TEAM_REQUEST_TASK_REUSED` | 命中未结束任务，复用并回写关联 |
| `TEAM_REQUEST_TASK_AUTO_CREATED` | 同步成功后开关自动创建 |
| `TEAM_REQUEST_TASK_AUTO_CREATE_FAILED` | 自动创建失败，同步结果保持 SYNCED |

并发边界：同一需求 id 经 Redis 分布式锁（key=`team_request:create_task:{request_id}`，见 `app/core/distributed_lock.py`）跨 Worker 串行，锁竞争立即返回 409「该需求正在创建 Agent 任务，请勿重复提交，请稍后重试」，重试时复用已建任务；任务落库仍由 `_TASK_CREATE_LOCK` 做权威查重。未配置 `REDIS_URL` 时降级为进程内锁（仅限单实例）；多实例生产必须配置 Redis，或再加数据库唯一约束 `(repository_id, issue_id, 未结束状态)` 作为最终防线。

## 3. Issue 双向同步

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/tasks/{task_id}/issue/sync-from-gitee` | 从 Gitee 拉取最新标题、正文、评论、标签 |
| POST | `/api/tasks/{task_id}/issue/comments` | 平台评论同步为 Gitee 评论 |
| GET | `/api/tasks/{task_id}/issue/sync-records` | 查看同步记录 |

阶段归属：`sync-from-gitee` 属于 DEV-017 自动上下文补齐；平台评论同步为 Gitee 评论属于团队版 V0.3 增强，DEV-019 第一版先实现平台内评论和通知。

## 4. 审批分级与自动推进

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/approvals/pending` | 沿用 MVP，只返回当前用户可审批任务 |
| POST | `/api/tasks/{task_id}/plans/{plan_id}/auto-approve` | 系统自动推进低风险计划，仅系统身份可调用 |
| POST | `/api/approvals/batch` | 团队版服务端批量审批，返回每项结果 |
| POST | `/api/tasks/{task_id}/approval/remind` | 催办审批 |

自动推进要求：

- 后端重新计算风险和异常条件。
- 普通用户不能调用 `auto-approve`。
- 自动推进必须写入 `AUTO_APPROVED` 事件。
- 命中高风险、敏感文件、上下文不足、命令拦截时必须拒绝自动推进。

阶段归属：`auto-approve` 属于 DEV-018 必做；服务端 `/api/approvals/batch` 属于 DEV-018 增强项，若第一版仍采用前端逐项调用，也必须返回逐项处理结果。

## 5. 评论与通知

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/tasks/{task_id}/comments` | 查看任务评论 |
| POST | `/api/tasks/{task_id}/comments` | 新增评论，可绑定计划、Diff、执行步骤 |
| PATCH | `/api/tasks/{task_id}/comments/{comment_id}` | 修改本人评论 |
| DELETE | `/api/tasks/{task_id}/comments/{comment_id}` | 软删除本人评论 |
| GET | `/api/notifications` | 查看站内通知 |
| PATCH | `/api/notifications/{notification_id}/read` | 标记已读 |
| PATCH | `/api/notifications/read-all` | 批量已读 |

## 6. 任务复用索引

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/tasks/{task_id}/reuse-summary` | 任务完成后生成复用摘要（DEV-021） |
| GET | `/api/tasks/{task_id}/similar` | 查看历史相似任务；DEV-017 已落地轻量版（同仓库 COMPLETED 任务实时相似度打分，只返回引用、不返回旧 Diff），DEV-021 替换为复用摘要向量索引 |
| POST | `/api/reuse-index/rebuild` | 重建历史任务复用索引（DEV-021） |

复用索引只作为参考，不允许自动套用旧 Diff。

### 6.1 DEV-017 自动上下文补齐增量接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/tasks/{task_id}/contexts/resume` | 上下文不足（`manual_reason=context_missing`）后重新自动检索；命中则解除人工态并自动推进到分析队列，返回 `resumed=true`；索引运行中返回 `resumed=false` 与等待提示；仍为空保持人工补充态；`answer_required` 等其他人工态返回 409；`status=FAILED` 也返回 409，必须走 `/retry`。权限 `task:operate` |
| POST | `/api/tasks/{task_id}/contexts/manual` | 既有手动指定文件接口；DEV-017 起当任务处于 `context_missing` 时，补充成功会同时恢复自动流转，响应新增 `resumed` 字段 |
| GET | `/api/tasks/{task_id}/similar` | 见上，权限 `task:view` |

DEV-017 回放事件增量：`COMMIT_SELECTED`（选定基线 Commit）、`CONTEXT_RETRIEVAL_EMPTY`（自动检索为空转人工补充）、`CONTEXT_SUPPLEMENTED`（人工补充后恢复流转）、`HISTORICAL_TASKS_MATCHED`（历史相似任务检索结果，含 0 命中与检索失败不阻断）。`context_missing` 与 `answer_required` 严格区分：前者发生在上下文聚合阶段、走 resume/manual 恢复；后者发生在 AI 分析阶段、走分析问题回答。失败任务不走上下文恢复，必须通过 `/retry` 恢复。

### 6.2 团队 7.5 AI 分析与修改计划字段增量

沿用 MVP 分析与计划接口（`POST /api/tasks/{task_id}/analysis`、`POST /api/tasks/{task_id}/plans`、`POST /plans/{plan_id}/rewrite|approve|reject`、`GET /plans`），不新增路由，仅补字段与门禁口径：

- 分析来源 `analysis.sources[].source_type` 在原有基础上新增 `project_default`（项目默认规则：默认分支/测试命令/命令白名单/风险阈值/最大文件数/禁改路径/审批模式）与 `answered_question`（人工问题确认记录）；`history` 专门表示历史相似任务（仅参考）。一次完整分析的输入应覆盖 Issue、`code_context`、`history`、`project_default` 四类来源。
- 修改计划对象新增 `analysis_risk_level`（生成时的分析风险初判快照）与 `referenced_historical_tasks`（历史相似任务引用列表，字段同 `SimilarTaskRef`，不含旧 Diff/计划/代码上下文，仅作参考）。
- 结构化完整性门禁：`POST /plans` 与 `rewrite` 在计划落库前校验五要素（目标 goal、文件范围 file_scope、步骤 steps、风险 risk_level、测试建议 test_suggestions），残缺时返回 **409**，消息形如「修改计划结构化不完整（…），请重新生成或补充后再提交审批」，计划不落库、任务保持 `ANALYZED`。
- 重写上限：单轮次最多重写 `MAX_PLAN_REWRITES=2` 次，超限返回 409 并写 `PLAN_REWRITE_BLOCKED`，引导审批当前版本 / 拒绝终止 / 重试任务开新轮次。
- 审批调整风险：`approve` 传入与当前计划不同的 `risk_level` 时必须同时传非空 `risk_adjust_reason`，否则 **422**；计划回传 `risk_adjust_from`（原等级）与 `risk_adjust_reason`。

## 7. 错误码增量

| code | 说明 |
| --- | --- |
| 40920 | 平台草稿尚未同步 Gitee |
| 40921 | Gitee Issue 同步失败，可重试 |
| 40922 | 自动审批条件不满足 |
| 40923 | 历史复用索引不可用 |
| 42220 | 平台需求正文不足 |
| 42920 | 催办过于频繁 |

## 8. 验收标准

- 平台草稿可以同步创建 Gitee Issue。
- 同步失败不丢草稿，失败原因可见。
- 已同步 Issue 可以自动创建 Agent 任务。
- 自动推进低风险计划时写入审计事件。
- 批量审批必须返回每个任务的处理结果。
- 评论、通知和同步记录都能在回放中追溯。
