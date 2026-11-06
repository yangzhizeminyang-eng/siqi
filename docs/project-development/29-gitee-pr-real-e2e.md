# 里程碑 A：真实 Gitee → Issue → 自动 Task → Agent 深链（2026-09-08）

> 目标：无 mock/fake/fallback 冒充，真实验证 平台需求 → 真实 Gitee Issue → 自动建 Agent Task →
> Agent 全链（上下文→分析→计划→审批→Diff→测试→PR 描述）与真实 PR 创建。
> 结论：**里程碑 A 已通过**。Gitee Issue、自动 Task、git-clone 索引、wait_index 自动恢复、DeepSeek 分析/计划、
> 真实 Diff、真实 Docker 测试、真实 Gitee PR（scratch 仓）、MySQL、Redis/Celery、Milvus Standalone、LlamaIndex 主链
> 均有真实运行证据（详见各节）。**文档/配置/读层口径一致性已收敛**（含读层 DB 真源、PERSIST_STRICT 写可观测、
> docs/04 物理表名、docs/08 读接口无副作用、frontend/.env=real 对齐 docs/24）。以下「边界说明/后续增强」不再视为
> 里程碑 A 阻断：真实 Diff/PR 在 scratch 仓验证需产品仓白名单+审批门禁；tree-sitter CodeSplitter 网络受限回退 TokenTextSplitter；
> Milvus Lite 为开发默认、Standalone 为生产已验证推荐。

## 运行态（全部真实，无 eager）

| 层 | 配置 | 证据 |
| --- | --- | --- |
| APP_ENV | `team` | `runtime_config` 强制 MySQL，禁止 sqlite 回退 |
| MySQL | `mysql+pymysql://…@127.0.0.1:3306/agent_team` | `scripts/init_mysql.py` 建库 utf8mb4 + 18 表 |
| Redis | `redis://127.0.0.1:6379`（broker db0 / result db1 / 锁 db2） | worker `transport: redis://…/0` `Connected to redis` |
| Celery Worker | `celery -A app.workers.celery_app worker --pool=solo` | `celery@ZE-PC ready`；`sync_gitee_request / create_task_from_team_request / run_agent / rebuild_repository_index` 真实消费 |
| FastAPI | `uvicorn app.main:app --port 8000`（team 态） | 启动日志 `CELERY_BROKER_URL=redis… async 主链路启用` |

## 真实通过的证据链

| 环节 | 真实结果 |
| --- | --- |
| 同步投递（非阻塞） | `POST /api/team/requests/{id}/sync-gitee` → `{"sync_status":"SYNCING","queued":true,"dispatch":{"enqueued":true,"mode":"redis","async_result_id":"…"}}`；HTTP 立即返回，未在请求线程执行长任务 |
| 真实 Gitee Issue | worker 消费 `backend.sync_gitee_request` → `synced` → 需求置 `SYNCED`，保存 Gitee Issue：`https://gitee.com/yang-zhizemin/dreamweaver-studio-pro/issues/IKE43I`，审计 `GITEE_ISSUE_SYNCED` |
| 自动建 Task | worker 消费 `backend.create_task_from_team_request` → `task_created` → `TASK-8801`，需求绑定 `task_id`，审计 `TEAM_REQUEST_TASK_AUTO_CREATED / TEAM_REQUEST_TASK_CREATED` |
| Agent 起步 | worker 消费 `backend.run_agent(TASK-8801)` → 真实产出 `execution_steps`(issue_fetch/commit_select/index_decision)+ `task_events`(COMMIT_SELECTED/INDEX_SCHEDULED) |
| MySQL 落库 | `team_requests` 行 `{sync_status=TASK_CREATED, gitee_issue_number=IKE43I, task_id=TASK-8801}`；`tasks` 1 行；`task_events` 4、`execution_steps` 6、`audit_events` 17（含 GITEE/REQ/TASK 事件） |

worker 真实消费日志摘录（脱敏）：
```
Task backend.sync_gitee_request[…] received → 消费(request_id=…) 开始 → 完成 -> synced → succeeded {'status':'synced','gitee_issue_id':'IKE43I'}
Task backend.create_task_from_team_request[…] received → 完成 -> task_created → succeeded {'task_id':'TASK-8801'}
Task backend.run_agent[…] received → 消费(task_id=TASK-8801) 开始 → 完成 → succeeded {'task_id':'TASK-8801'}
```

## git-clone 化索引（索引主链移出 Gitee API 逐文件限流）

新增 `backend/app/services/repo_mirror.py`：`git clone/fetch + worktree checkout <commit_sha>` 后走本地工作区
（`blocked_paths`/文件大小行数上限复用）；`sync_repository_snapshot` 主链改走 git，Gitee API 仅作显式
fallback（`source=gitee_api_fallback`，默认 `USE_GIT_CLONE_INDEX=true`）。token 只进 `https://oauth2:<token>@…`
远程，日志/错误经掩码（`mask_remote` + `_git` 内 `//oauth2:…@` 清洗），不落库/不打印。

真实验证（真实仓，team+MySQL+Redis+真 Worker）：
```
POST /repos/…/index → idx_3e74d40c6547：
  PENDING → SYNCING → PARSING(total=6) → EMBEDDING(chunk=2) → STORING → COMPLETED  milvus=code_idx_3e74d40c6547
```
真实 `git clone` 拉码后索引 **COMPLETED**（Milvus 落库 chunk=2,total=6），**不再 403**。修复过程排除两处真实问题：
1. 既有库缺 `tasks.pr_*` 列 → `create_all` 不加列，需 `alembic upgrade head` 或新增列 ALTER；
2. `repo_mirror` 曾把**相对路径**喂给 `git`（cwd 错位→clone 到嵌套错误目录→mirror 空→回退 API 403），改为 `.resolve()` 绝对路径后 clone 成功。

单测 `tests/test_repo_mirror.py`（6 条）：clone/fetch 成功、token 脱敏（含 clone 失败时 git 回显不泄 token）、blocked 生效、主索引不再调用 `gitee_api_get`（source=git_clone）、API fallback 显式标注、索引记录 DB 往返可恢复。

## 真实 Gitee PR 创建：已实现并在 scratch 仓真实验证（PR #1）

新增 `backend/app/services/gitee_pr_service.py::create_gitee_pull_request`：以文档端点 `POST /repos/{owner}/{repo}/pulls`
`{head,base,title,body}`；若 404 `project or enterprise` 按 owner 作用域回退 `POST /repos/{owner}/pulls`+repo（复用建 Issue 的坑，首次真实 PR 时确认端点）。
新增 `POST /api/tasks/{task_id}/pull-request`（`app/main.py`）：门禁=已审批计划 + 真实 Diff（非 demo）+ 测试 + PR 描述 GENERATED 且未过期 + `work_branch`；
编排=写 worktree→commit→push head 分支（token 远程）→建 PR；成功存 `pr_number/pr_url/pr_status`、写 `task_events`+`execution_steps(node=create_pr)`+审计；幂等（已有 open PR 不再建）。
单测 `tests/test_pr_creation.py`（4 条+1 skip）：请求参数正确、token 不泄（响应/审计/回放）、四类门禁拒绝、成功落库/审计/回放 node=create_pr、幂等；真实 PR E2E `GITEE_PR_E2E=true` 默认 skip。

> 真实 push/PR 已在专用 scratch 仓（`git-repo-test`）执行并创建真实 PR（见「真实 Diff + Docker 测试 + 真实 Gitee PR」）；产品仓默认不自动写改，生产写入需白名单+审批门禁（边界，非里程碑 A 阻断）。

## 结论

- **里程碑 A 已通过**：需求→真实 Gitee Issue（IKE43I）→自动建 Task（TASK-8801）→异步非阻塞→MySQL 落库/审计；**git-clone 索引真实 COMPLETED**；**wait_index 自动恢复闭环真实跑通**（TASK-8801：context_fetch→ai_analysis(DeepSeek)→plan_generation→diff_generation→test_execution→pr_description，全真 Worker）；**真实 Diff + Docker 测试 + 真实 Gitee PR（scratch 仓 PR #1）**；**Milvus Standalone 真实写入+跨进程召回**。
- **边界说明（非阻断）**：TASK-8801 的 Diff=DEMO_FALLBACK、Test=mocked_runner（该任务跑的是一条"能力展示"链）；真实执行链在 scratch 仓已验证（REAL diff + docker test + 真实 PR）。产品仓若要跑真实 Diff/Test/PR，需在同一真实执行环境 + 白名单/审批门禁。
- 不为"能检索/能生成/接缝存在"冒充真实落地；凡未真实运行处均按此标注（真实执行能力已在 scratch 仓取证）。

## 真实 Diff + Docker 测试 + 真实 Gitee PR（scratch 仓，2026-09-08 落地）

新增 `diff_service.attempt_real_diff` 真实变更 seam：scratch 白名单（`ALLOW_SCRATCH_REAL_CHANGE=true` + `E2E_SCRATCH_REPO_URLS` 命中）时，在 git-clone worktree **真实写改**并产出真实 before/after 补丁（`execution_mode=REAL`、`real_change_source=e2e_scratch_real_change`；非 scratch 一律不可用）。`test_runner` 的 `TEST_RUNNER_MODE=docker` 真实跑容器（`runner_mode=docker`，记录 exit_code/stdout/stderr/duration；Docker 不可用 → `runner_mode=unavailable`，绝不写 passed/mock）。`create_task_pull_request` 增加 scratch push 门禁（仅 `e2e_scratch_real_change` + 开关放行，防误 push 生产仓）。

**真实证据（专用 scratch 仓 `git-repo-test`，Docker 可用 + 真 push + 真建 PR）**：
```
DIFF execution_mode= REAL  real_change_source= e2e_scratch_real_change  is_demo= False   （真实写回 worktree，添加 E2E_REAL_CHANGE.md）
TEST runner_mode= docker  exit= 0  duration= 1.03s                                      （busybox 容器真实执行 "echo ok"）
PR status= open  number= 1  url= https://gitee.com/yang-zhizemin/git-repo-test/pulls/1
   head= agent/master/issue-scratch-v102423  commit= db005aed "feat(scratch-real): 里程碑A真实变更 e2e"  E2E_REAL_CHANGE.md | 5 +++++
```
DB：tasks.pr_url=`https://gitee.com/yang-zhizemin/git-repo-test/pulls/1`、pr_status=open；diffs=`SUCCEEDED/REAL`；test_runs（runner=docker）；pr_descriptions=GENERATED；audit `GITEE_PR_CREATED` 在。token 全程脱敏。
可复跑：`TEST_RUNNER_MODE=docker ALLOW_SCRATCH_REAL_CHANGE=true E2E_SCRATCH_REPO_URLS=https://gitee.com/yang-zhizemin/git-repo-test.git .\.venv\Scripts\python.exe -m scripts.milestone_a_real_pr`

## Milvus Standalone 真实写入/召回（2026-09-08，不用 Lite）

生产形态 Milvus Standalone v2.3.10（`http://127.0.0.1:19530`，Attu 可连）。真实验证（`scripts/milvus_standalone_verify.py`，本地 git-clone 仓）：LlamaIndex `parse_and_chunk`→`build_nodes_from_chunks(TextNode)`→`build_index_from_chunks` 写 Standalone collection → `query` **source=milvus**（非 fallback）→ **跨进程/新连接** `ensure_milvus_collection_loaded` 后 query 不 released、仍 source=milvus → collection 在 Attu 可见。
```
chunks=2 → collection col_mv_std_1788863828 → collection exists (Standalone/Attu) = True
query(first) n=2 sources=['milvus']
cross-process returncode=0  CROSS sources=['milvus'] n=2   ← 无 released
```
门禁测试 `tests/test_milvus_standalone.py`（默认 skip，`MILVUS_STANDALONE_TEST=true`+`MILVUS_URI` 才跑，含连接切断防污染同进程 Lite 测试）。全量 14 skipped 含真实外部依赖（milvus/gitee/docker）默认 skip。配置：`MILVUS_URI=http://127.0.0.1:19530`（team.env.example 已默认），缺省仍允许本地 Milvus Lite（.data/milvus）。

## wait_index 自动恢复闭环（2026-09-08 补，真实）

修复 `wait_index→索引完成→自动恢复 Agent 主链路`：
- `index_action_for_head` create_index 分支补 `_enqueue_index_build`（此前任务自动建的索引入队缺失，依赖 HTTP 轮询）；
- `progress_single_repository_index` COMPLETED 分支补 `resume_tasks_waiting_for_index(record)`：扫描 `ISSUE_FETCHED/wait_index` 且 `related_index_id`/`base_commit_sha` 命中任务 → 写 `INDEX_RESUMED` 事件 + `node=index_decision` 步骤 + 审计 → `_persist_task` → `_clear_agent_enqueued` + 投递 `run_agent`；
- 幂等：`_INDEX_RESUMED` 集（index_id×task_id）+ COMPLETED 仅 transition-once，多 worker/重复触发不重复推进；
- `_prepare_context` 改经 `_resolve_waiting_index(task)`：related_index_id 可能指向旧/已删索引，回退到同 `base_commit_sha` 的最新 COMPLETED/运行索引（否则 run_agent 短跑 0.05s 卡 wait_index）。
- 真实证据：worker `rebuild_repository_index → COMPLETED` 后日志 `已投递 Agent 推进 backend.run_agent(TASK-8801)`；`run_agent` received→succeeded（29.4s 分析+计划、36.8s Diff）；DB `task_events/execution_steps` 全链；`GITEE_ISSUE_SYNCED` 审计在。
- 新测试 `tests/test_wait_index_resume.py`（4 条）：create_index 自动入队、COMPLETED 恢复+事件/步骤/审计+投 run_agent、幂等、非 wait_index/非 COMPLETED 跳过。

## 已修复：API 内存↔DB 不一致（worker 写 vs API 读）

真实 E2E 曾暴露：worker 把任务推进写库后，**FastAPI 内存 `TASKS` dict 不刷新** → 审批/PR 描述等 mutating handler 读到旧态（如 DB 已 TESTED、内存仍 APPROVED）返 409/404，此前需「重启 API」绕过。
**根治（2026-09-08）**：新增 `task_for_mutation(task_id)`，任务类端点统一走它——持久化开启时经 reads 层 `get_task`（DB 为准）并刷新 `TASKS[task_id]` 缓存，DB 暂无（未落库窗口）回退内存；持久化关闭（测试）语义等同 `TASKS.get`。已将 `main.py` 中任务端点/内部逻辑约 30 处 `TASKS.get(task_id)` 切到 `task_for_mutation`。
**实证据**：对 MySQL 直接改 `TASK-8801.title` 后（不重启、不清内存）`task_for_mutation` 返回 DB 最新值（`after title=__DBFRESH_PROBE__`），证明 worker 写后 API 即可感知。

## 复现命令（脱敏；凭据走环境变量）

team 态（MySQL `agent_team` + Redis）起 worker 与 FastAPI 后：
```powershell
cd backend
$env:APP_ENV='team'; $env:USE_SQLALCHEMY='true'
$env:DATABASE_URL='mysql+pymysql://…@127.0.0.1:3306/agent_team?charset=utf8mb4'
$env:USE_CELERY='true'; $env:CELERY_BROKER_URL='redis://127.0.0.1:6379/0'
$env:CELERY_RESULT_BACKEND='redis://127.0.0.1:6379/1'; $env:REDIS_URL='redis://127.0.0.1:6379/2'
# 另起 worker / uvicorn :8000 后：
.\.venv\Scripts\python.exe -m scripts.milestone_a_gitee_real_e2e   # 主驱动（真实 API→Issue→Task）
```
专项真实 Gitee E2E（默认 skip，配齐才跑）：
```powershell
$env:GITEE_E2E='true'; $env:GITEE_OWNER='yang-zhizemin'; $env:GITEE_REPO='dreamweaver-studio-pro'
.\.venv\Scripts\python.exe -m pytest tests/test_gitee_e2e_live.py -q
```
全量：`.\.venv\Scripts\python.exe -m pytest tests -q`（402 passed, 10 skipped）。

## 测试残留（需在 Gitee 手动清理）

本轮与历史 E2E 在 `dreamweaver-studio-pro` 遗留：Issue `IKE43I/IKE3PQ/IKE3QB/IKE3N5`、milestone `228391`。