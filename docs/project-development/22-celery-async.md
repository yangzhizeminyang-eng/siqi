# P1a Celery 异步任务链路（索引/同步/Embedding/Agent/测试）

## 目标与结论

长任务（索引构建、代码同步、Embedding、Agent 长任务、测试执行）从 HTTP 请求线程拆出，
由 Celery Worker 执行；HTTP 只负责创建任务与返回状态。**未配置 Redis 时仅允许测试/本地降级**
（eager 内联），并明确标记 `mode="eager(test)"`；接生产必须配置真实 broker。

## 运行方式

### 后端 API
```powershell
# working dir = backend
$env:USE_SQLALCHEMY="true"                 # 启用业务持久化
$env:DATABASE_URL="sqlite:///.data/app.db" # 本地 SQLite（生产用 MySQL）
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

### Celery Worker（接真实 Redis 时）
```powershell
# 先在 Docker Desktop 启动 redis-celery，再回到项目根目录：
.\start-dev.ps1
```
开启 `USE_CELERY=true` 且配置 broker 后，索引触发/重建请求会把任务派发给 Worker，
由 Worker 执行 `drive_index_to_completion` 等真实工作，HTTP 返回立即退出的任务引用与状态。
`start-dev.ps1` 会自动补齐本地 Redis 配置，并复用或启动 Worker；如需单独启动 Worker，
仍可执行 `.\deploy\dev-worker.ps1`。

### 关键环境变量
| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `USE_CELERY` | `false` | 是否接入真实 Celery（需同时配置 broker） |
| `CELERY_BROKER_URL` | 空 | Redis broker，如 `redis://127.0.0.1:6379/0` |
| `CELERY_RESULT_BACKEND` | 空 | Redis 结果后端 |
| `USE_SQLALCHEMY` | `true` | 业务持久化总开关（测试关闭） |

## 降级契约（明确标记）
- `USE_CELERY=true` 但未配 broker：`dispatch.enqueue_or_run` 走 eager 内联，并写告警日志 +
  返回 `mode="eager(test)"`，**仅用于测试/本地**。
- `USE_CELERY=false`（默认）：eager 内联，行为与改造前完全一致（既有测试不受影响）。
- Worker 内执行的就是与内联同一段的真实工作函数（`drive_index_to_completion`、
  `execute_task_tests_worker`、`sync_request_to_gitee_worker`、LangGraph `run_agent`），
  非空壳，eager 可逐条验证。

## 真实任务清单（`app/workers/tasks.py`）
| 任务名 | 工作函数 | 说明 |
| --- | --- | --- |
| `backend.rebuild_repository_index` | `main.drive_index_to_completion` | 索引进到终态（解析/Embedding/存储） |
| `backend.run_agent` | LangGraph 工作流 / 内联推进 | Agent 状态流转长任务 |
| `backend.run_test` | `main.execute_task_tests_worker` | 真实测试执行并落库 |
| `backend.sync_gitee_request` | `main.sync_request_to_gitee_worker` | 平台需求同步 Gitee |

## P1a2 增强（Agent 推进真实接入 Celery）
- `main.advance_task`：启用 Celery（`USE_CELERY=true`+broker）时先把任务推进**投递给 `backend.run_agent` 到 Worker**（每任务去重一次，
  `_AGENT_ENQUEUED`），HTTP 只读，不在请求线程跑长链路；投递失败回退内联推进，保证任务仍能前进。
- `main._dispatch_agent_if_needed`：任务创建（`create_task`）即投递一次（"API 返回后任务进入队列"）。
- `run_test`：HTTP `POST /api/tasks/{id}/tests/run` 在启用 Celery 时把测试投递给 `backend.run_test` 并返回
  `queued=true`（`TEST_RUNNING`），eager/本地仍走同步路径，行为与改造前一致。
- Gitee 同步：HTTP `sync_team_request_to_gitee` 在启用 Celery 时投递 `backend.sync_gitee_request` 并返回
  `SYNCING + queued=true`（前端轮询）；Worker 内完成 create_gitee_issue → SYNCED + `GITEE_ISSUE_SYNCED` 审计 →
  **派发独立任务 `backend.create_task_from_team_request`**（自动建任务，DEV-016-C，可独立重试）→ 审计。
  失败落 `SYNC_FAILED` + `GITEE_ISSUE_SYNC_FAILED` 审计；草稿与映射状态保留。eager/本地仍走内联（行为不变）。
- 自动建任务 worker（`main.create_task_from_team_request_worker`）：仅 `SYNCED` 且仓库开自动建任务开关时创建/复用任务并置 `TASK_CREATED`；否则 no-op。
- 真实 worker 消费日志：每任务消费时 `logger.info("[celery] Worker 消费 backend.<task>(...) 开始/完成")`。

## 运行态判定（eager 仅限开发/测试，非默认主链路）
- `lifespan` 启动 `_warn_celery_mode()`：`USE_SQLALCHEMY=true`（生产态）但未配置 Redis/Celery 时，按 `APP_ENV`：
  - local/test → warning；team/production → **ERROR**（不允许静默 eager），并提示必须配置 `CELERY_BROKER_URL/CELERY_RESULT_BACKEND` 并启动 Worker。
- `enqueue_or_run` 在无 Redis 时返回 `mode="eager(test)"` 并写告警；`eval` 仅为本地/test 降级，不构成异步验收证据。

## 专项（Redis+Worker 验收链路 + 失败重试语义）
### Worker 运行时恢复（不依赖 FastAPI lifespan）
- Worker 进程不跑 FastAPI lifespan，否则 `TASKS/TEAM_REQUESTS/REPOSITORY_INDEXES/...` 运行时缓存为空，真实实体任务会 missing/404。
- `app/workers/runtime.py`：`init_worker_runtime()`（create_schema + restore_from_db）+ `ensure_worker_runtime_ready()`（每任务开头调用）。
- `app/workers/runtime.py`：`init_worker_runtime()`（create_schema + restore_from_db，`_schema_done` 幂等守卫）
  + `ensure_worker_runtime_ready()`（每任务开头调用，幂等；restore 失败不硬崩——记日志后按缺失实体→可序列化 WorkerTaskError 处理）。
- `celery_app.on_after_configure → _worker_init → init_worker_runtime()`：worker 启动即恢复一次；每个任务体开头 `ensure_worker_runtime_ready()` 兜底（单任务/测试直跑也生效）。
- **worker 独立 restore 已生效**：不依赖 FastAPI lifespan；`tests/test_celery_worker_runtime.py` 覆盖「无 lifespan 恢复 / 幂等 / 失败不硬崩 / 真实实体非 missing」。

## Worker 异常可序列化 + 失败重试
- `WorkerTaskError`（可 pickle）：worker 边界把 FastAPI HTTPException / 任意异常转成它（status_code/error_code/resource_id），
  避免 HTTPException 不可序列化导致 worker 崩溃/exit 127。
- 重试语义：非最后尝试（`self.request.retries < max_retries`）只记可恢复态（retry_count/failure_reason/next_action），不置终态；
  最后尝试才 mark_task_failed(任务) / SYNC_FAILED(需求) / 索引 FAILED + 审计。
- 真实证据（Redis 7.4.11 + 真实 worker）：`run_agent` 失败以 `WorkerTaskError` 重试 1/3→2/3→3/3（worker 不崩溃），
  `sync_gitee_request` 读到真实 REQ → 真实 gitee 401 → DB `REQ→SYNC_FAILED`（业务状态真实落库）。

## 真实业务闭环验收（scripts/celery_real_smoke.py）
```powershell
# 终端1：在 Docker Desktop 启动 redis-celery
# 终端2：项目根目录启动前后端与 Worker
.\start-dev.ps1
# 终端3：backend 目录执行验收
.\.venv\Scripts\python.exe -m scripts.celery_real_smoke   # 建真实 REQ/TASK → 投递 → 验 DB 业务状态变化
```
- 验收证据：worker 日志 `Task backend.<t> received → Worker 消费 …开始 → retry → succeeded/FAILURE`；DB 中 REQ/TASK 状态真实变化；worker 不崩溃。

## 配置收敛
- `runtime_config` 新增 `APP_ENV` 与 `settings.celery_required()`（team/production 必须真实 Celery）。
- `deploy/env/team.env.example` / `enterprise.env.example` 默认 `USE_CELERY=true` + `CELERY_BROKER_URL/CELERY_RESULT_BACKEND`；
  多 Worker 须配 `REDIS_URL` 作为分布式锁。local/test 可 `USE_CELERY=false`/eager。
- `USE_CELERY=true` 但缺 broker：启动强告警/报错；`check_redis_worker` 会以 exit!=0 明确失败，绝不静默回退 eager。

### 失败重试语义（修复“重试空转”）
- `run_agent` 失败处理（`main.record_agent_attempt_failure(task, err, is_last)`）：
  - **非最后尝试**：不置终态 FAILED，仅保留 `retry_count` / `failure_reason` / `next_action=worker_retry` / checkpoint = 可恢复态，供 Celery 重试续跑；
  - **最后尝试**：才 `mark_task_failed`（终态）+ 失败审计。
- 判定最后尝试：`self.request.retries >= self.max_retries`。
- `_AGENT_ENQUEUED` 清理：任务进终态（advance/cancel/reject）、审批通过（approve）、恢复（resume/retry）时调用 `_clear_agent_enqueued(task_id)`，允许后续重新派发 worker 续跑，不再“每任务只投一次”卡住恢复推进。
- run_test / sync / rebuild 业务侧失败返回错误 dict（不抛→不误触 autoretry 且业务终态可恢复重试）。

### 真实验收命令
```powershell
# 终端1 启动 Redis（三选一）
docker start redis-celery      # Docker Desktop 中已有容器
# 或用已有 Redis 服务 / 远程 Redis
# 终端2 项目根目录启动前后端与 Worker
.\start-dev.ps1
# 终端3 验收
cd backend
.\.venv\Scripts\python.exe -m scripts.check_redis_worker
# 期望：Redis 可达 → 在线 Worker → backend.smoke 被消费 → logs/celery-worker.out.log 出现
#   "Worker 消费 backend.smoke"
```
- 四条链路统一走 dispatch：index（`_enqueue_index_build`）、agent（`advance_task`→`_dispatch_agent_if_needed`）、
  test（`run_task_tests`）、gitee sync + auto-create（`sync_team_request_to_gitee` + `create_task_from_team_request`）。
  API handler 在 Celery 模式只投递、返回 queued；进度经任务详情/回放/事件查看。
- 失败重试：`backend.*` 四任务统一 `autoretry_for=(Exception,), max_retries=3, retry_backoff=True, retry_jitter=True`；任务体 `update_state` 向上报 `SUCCESS`，异常→`mark_task_failed` 审计留痕→自动重试。
- 降级标记：`enqueue_or_run` 在无 Redis 时返回 `mode="eager(test)"` 并写告警日志（仅测试/本地）。

## 测试与验收
| 项 | 结果 |
| --- | --- |
| 全量后端测试 | `319 passed`（含 P1a2 新增 5 条投递/降级/重试配置测试） |
| eager 降级标记 | `enqueue_or_run` 返回 `mode="eager(test)"` 且内联执行同一段工作 |
| 投递去重 | Celery 模式下 `advance_task` 投递一次、同任务不重复投递 |
| 回退 | 投递失败回退内联推进 |
| 重试配置 | 四任务 `max_retries=3` + `autoretry_for=(Exception,)` |

## Gitee→Issue 真实 E2E 状态（2026-09-08，已通过）

- **根因（曾误判为平台限制，实为代码端点写错）**：`create_gitee_issue` 调用的是 `POST /repos/{owner}/{repo}/issues`，
  但 **Gitee SDK 建 Issue 的端点是 `POST /repos/{owner}/issues`，仓库通过请求体参数 `repo` 传入**。
  错误路径被 Gitee 当"项目或企业"返回 `404 {"message":"project or enterprise"}`。
- **修复**：`app/main.py::create_gitee_issue` 改为 `POST /repos/{safe_owner}/issues`，并把 `repo` 并入
  `build_gitee_issue_payload` 载荷。同一账号级令牌（指纹 `452c9d04`）、同一仓库 `yang-zhizemin/dreamweaver-studio-pro` 复验：
  - 正确端点 + repo 参数 → **201**（真实 Issue：`https://gitee.com/yang-zhizemin/dreamweaver-studio-pro/issues/IKE3PQ`）；
  - 旧错误路径 → 404（对照）。
- **回归测试**：`tests/test_dev016b_sync_gitee.py` 新增 2 条，断言创建必须走 `/repos/{owner}/issues` 且 `repo` 入参；
  `tests/test_gitee_e2e_live.py` 为默认 skip 的真实 E2E（`GITEE_E2E=true` + `GITEE_TOKEN/GITEE_OWNER/GITEE_REPO` 配齐才跑）。
- **自动建任务**：真实同步 → `SYNCED` → `GITEE_ISSUE_SYNCED` 审计正常；自动建任务在此裸脚本下因未 seed 项目而 `TEAM_REQUEST_TASK_AUTO_CREATE_FAILED`
  （非代码回归；真实部署中以真实已索引仓库 + 已存项目跑通 `create_task_from_team_request`）。
- **顺带修复的配置缺口**：`REPOSITORY_TOKENS` 只有自带 demo token，DB 不落明文 token，env `GITEE_TOKEN` 此前从未被 app 读取（重启丢全部真实 token）。
  已加 `app/db/bootstrap.py::_restore_gitee_tokens`：env 账号令牌 → 无按库令牌仓库兜底（幂等、限生产路径、绝不打印/落库）。
- worker 消费、幂等、审计已单测 + 真实多 Worker 验证。
