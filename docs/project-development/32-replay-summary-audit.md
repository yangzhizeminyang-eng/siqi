# 回放摘要与审计（DEV-020）

> 关联：`14-task-replay-audit.md`（回放/审计基线）、`12-test-execution.md`、`13-pr-description.md`、`31-comments-notifications-nudge.md`。

## 功能目标

让团队在任务上下文内完整复盘一次任务执行过程：task_events、execution_steps、检索记录、AI 分析、修改计划及版本、审批动作、风险调整、Diff 产物、测试记录、PR 描述、评论、自动推进、Gitee 同步失败、Worker 重试、24h 催办、失败节点及原因。

**约束**：回放是只读的（不推进会务状态、不改审计日志）；持久化模式下以 DB 为真源；复用现有权限（`replay:view` + `task_for_mutation` 统一 404/403）；不新建重复审计体系；摘要为确定性模板（离线可用），绝不替代原始审计/事件/产物。

## 现状扫描结论（以源码为准）

- 已有 `GET /api/tasks/{id}/timeline`（task.events 按 created_at 升序 + 内嵌 execution_steps）与 `GET /api/tasks/{id}/events/{id}`（单事件+步骤）。本项目按「优先增强」思路：新增完整的 `GET /api/tasks/{id}/replay` 与 `/replay/summary`，`/timeline` 保持轻量兼容。
- 数据模型（`app/main.py`）：`TaskRecord.events(list[AuditEventRecord])` / `.execution_steps(list[ExecutionStepRecord])` / `.plans / diffs / test_runs / pr_descriptions / analysis / code_contexts / similar_task_refs`；`RetrievalRecord` 独立于 `RETRIEVAL_RECORDS`/`reads`；`CommentRecord`/`NotificationRecord`。
- reads 层 DB 真源：`list_task_comments` / `list_retrieval_records` / `get_task` 均已存在；主任务读取走 `task_for_mutation`。
- 权限：全角色均有 `replay:view`；无权任务语义 = `require_permission(replay:view)` 403 + `task_for_mutation` 404。

> 备注：文档与源码基本一致；唯「执行环境」字段此前 `/timeline`硬编码「正式流程」，本模块改为按事件派生（unavailable→不可用 / blocked→拦截 / 默认正式流程），并在报告指出。

## 架构

```mermaid
flowchart TD
    A[原始持久化数据 task_for_mutation + reads] --> B[replay_service.build_task_replay]
    B --> C[任务权限校验 replay:view / task_for_mutation]
    C --> D["事件与产物统一关联（diff_summary 解析 + 产物集合）"]
    D --> E[稳定排序与去重 occurred_at asc + 原序 + id]
    E --> F[ReplayTimeline DTO timeline + artifacts + summary]
    F --> G[GET /api/tasks/{id}/replay]
    F --> H[build_replay_summary 确定性模板]
    H --> I[GET /api/tasks/{id}/replay/summary]
    G --> J[ReplayView.vue real 模式时间线 + 失败展开 + 复制摘要]
    F --> L[失败节点展开（失败原因/重试/节点/执行环境）]
```

## API 与数据结构

- `GET /api/tasks/{id}/replay` → `{task_id, generated_at, timeline:[ReplayTimelineEvent], artifacts:{plans,diffs,test_runs,pr_descriptions,comments,retrievals}, summary}`
- `GET /api/tasks/{id}/replay/summary` → `{task_id, generated_at, summary:{text, source, input_fingerprint}}`
- `ReplayTimelineEvent`: `id / event_type / occurred_at / actor_id / actor_name / actor_type(user|system|worker) / status(completed|failed|skipped) / node / message / failure_reason / retry_count / metadata / relations{task_id,plan_id,plan_version,diff_id,test_run_id,pr_description_id,comment_id}`
- 关联来源：事件 `diff_summary` 自带 `plan_id=…/plan_version=…/diff_id=…/test_run_id=…/pr_description_id=…/评论ID=…`，直接解析映射；不凭空创建事件。
- 失败/降级不伪装：`TEST_RUN_UNAVAILABLE`→skipped + `execution_env=不可用`；`TEST_BLOCKED`→拦截；Diff `DEMO_FALLBACK`/`is_demo_fallback` →摘要标注「演示兜底/降级」；缺失字段写「暂无记录」。

## 权限

- 角色级：`require_permission(replay:view)`（缺权限 403）；任务缺失 `task_for_mutation` 404；未登录 401。
- **项目级可见（DEV-020 验收二.1/二.2，`_can_view_replay`）**：
  - admin：全量；
  - lead：仅本人默认审批的仓库所在项目（与审批中心 lead 口径一致）；
  - developer/approver/observer：仅任务创建者或任务指派审批人（显式参与）。
- 回放与摘要**同一权限**；GET-only，无任何写接口，不能修改/删除 audit_events/task_events；不信任请求体伪造（GET 身份来自 Cookie+path，无 body 注入向量）。

## 关键源码

| 文件 | 职责 |
| --- | --- |
| `backend/app/services/replay_service.py` | `build_task_replay` / `build_replay_summary` / 时间线聚合与关联、稳定排序、指纹 |
| `backend/app/main.py` | `GET /api/tasks/{id}/replay`、`/replay/summary`（`replay:view` + `_can_view_replay` 项目级 + `task_for_mutation`） |
| `frontend/src/views/ReplayView.vue` | real 模式真实调用 `/replay`，DB 时间线 + 摘要 + 失败展开 + 复制 |

## 测试

后端 `tests/test_dev020_replay.py`（15）：全量数据源进入回放、时间线稳定排序、事件去重、事件-产物关联、失败节点(原因/重试)、自动/人工审批/风险调整/催办可见、越权 403、**项目级隔离（未参与用户 403、审批人可读、创建者正向 200、lead 仅本项目）**、管理员可读、回放只读不改状态/审计、摘要不改原始数据、DB 真源(清内存)、unavailable 不伪装成功、回放与摘要 `generated_at` 对齐。

前端 `ReplayViewReal.spec`（5，real 模式）：请求 `/replay`、DB 时间线渲染、失败展开、复制摘要（clipboard+成功提示）、`/replay` 失败错误提示、不使用静态 dashboard 事件。

后端全量：`python -m pytest backend/tests -q` → **467 passed, 15 skipped, 0 failed**（在项目 venv 运行；`test_migrations_upgrade.py` 依赖 alembic，须在含 alembic 的 venv 执行，全局/缺依赖环境会 ModuleNotFoundError——环境差异，非回放回归）。
前端：`vitest run` → **189 passed**（19 files）；`vue-tsc -b` 无错误；`vite build` → 构建成功。

## 当前结论

源码 + 测试 + 构建通过；真实环境浏览器截图与 Network/MySQL host 现场回放证据 **已于 2026-09-09 补齐**，DEV-020 可判为**通过**。证据文件：
- `docs/assets/dev020-replay-summary-real.png`（admin 正例：确定性摘要+指纹+16 事件时间线）
- `docs/assets/dev020-replay-permission-403-real.png`（非参与 developer 越权 → 页面错误「无权查看该任务回放」）
- `docs/assets/dev020-replay-network.json`（真实后端 `:8001`+`agent_team` MySQL 8.0.44 的 CDP Network 抓包：`/replay`→200 / `source=deterministic` / `fingerprint=1fac8655aa871494` / 16 事件 / artifacts 六类；`/replay/summary`→200 同指纹）

> 采集方式：`.\start-dev.ps1` 端口冲突（`:8000` 被 attu/Milvus GUI 占用），故以 venv 起后端 `:8001`（`USE_SQLALCHEMY=true`、`DATABASE_URL=…agent_team`）并起第二个 vite `:5174`（`VITE_API_PROXY_TARGET=:8001`），用 CDP 驱动真实 Chrome 抓取。

## 真实环境补录走查单（P2 采集指引）

> 目标：补足评审唯一剩余缺口——DEV-020 回放/摘要页真实浏览器截图 + Network 抓包 + MySQL host 现场回放。
> 全部命令以仓库根目录为工作目录；后端须用含 alembic 的 venv（`backend\.venv\Scripts\python.exe`）。

| 步骤 | 操作 | 核对点 |
| --- | --- | --- |
| 1. 起后端+前端 | `.\start-dev.ps1`（backend venv → `:8000`，frontend vite → `:5173`，`VITE_DATA_SOURCE=real`） | `http://127.0.0.1:8000/openapi.json` 可达、`http://127.0.0.1:5173/` 可登录 |
| 2. 接 MySQL host | 置 `USE_SQLALCHEMY=true`、`DATABASE_URL=mysql+pymysql://<user>:<pass>@<host>:3306/<db>?charset=utf8mb4`（参考 `deploy/env/*.env.example`）；如验证异步真实流量另起 `.\deploy\dev-worker.ps1` | `restore_from_db` 启动日志恢复用户/项目/任务/审计 |
| 3. 准备可回放任务 | 复用 MySQL 中已有全链路完成态任务；若无则走真实链路造一条（创建任务→索引→分析→计划→审批→Diff→测试→PR 描述→评论/催办），可用 `backend/dev_demo_dev007.py`、`backend/dev_demo_dev009.py`、`backend/dev_demo_dev010.py`、`backend/dev_demo_dev017.py`、`backend/dev_demo_dev018.py` 造真实种子任务 | 任务 `events` 含 `TASK_CREATED…PR_DESC_GENERATED/NUDGE_SENT` 等完整事件链 |
| 4. 打开回放页 | 浏览器打开 `http://127.0.0.1:5173/replay/<task_id>` | 时间线按 occurred_at 升序、去重、失败节点可展开、降级被标注（unavailable/DEMO_FALLBACK 不伪装成功） |
| 5. Network 抓包 | DevTools → Network 过滤 `replay`，核对 `GET /api/tasks/{id}/replay` 与 `/replay/summary` 均 `200`；查看响应 `summary.source=deterministic`、`summary.input_fingerprint` | 页面真正请求回放 API（非静态/dashboard 主源）；摘要直接来自后端 |
| 6. 摘要与复制 | 点「复制摘要」校验 clipboard/成功提示；核对摘要含任务/状态/计划版本/审批/风险调整/Diff/测试/PR/失败/重试/降级/后续动作 | 复制全文为 Markdown/纯文本可用 |
| 7. 权限 403 | 用非参与角色账号（非创建者/非审批人/非归属 lead 的 developer 或 observer）打开同一回放 → 403「无权查看该任务回放」 | 项目级 `_can_view_replay` 拦截生效 |
| 8. 落图 | 截图存 `docs/assets/`，命名如 `dev020-replay-summary-real.png`、`dev020-replay-permission-403-real.png`；Network 面板另截或导出 HAR | 截图与 00 表 / doc32 填的路径一致 |

采集完成后：把图片路径回填到 `00-project-development-log.md` 的 DEV-020 行，并将状态由「功能完成，待补真实页面截图（有条件通过）」更新为「已完成」。填充 `source:deterministic`、指纹、复制、时间线升序、失败展开、降级标注、403 均可作为「通过」证据。