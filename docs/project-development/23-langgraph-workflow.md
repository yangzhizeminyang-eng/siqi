# P1b LangGraph 任务状态机

## 结论
把任务状态流转真正交给 LangGraph 承载：拆分节点、审批暂停/恢复（checkpoint）、失败分流，
Celery 的 `run_agent` 调用本图，不再绕过它直接跑 main 状态机。

## 节点与路由（`backend/app/agent/workflow.py`）
| 节点 | 职责 | 触发状态 |
| --- | --- | --- |
| `prepare_context` | 前序阶段（复用 `main._prepare_context`；HTTP 与 Worker 均经同图） | `CREATED/ISSUE_FETCHED/CONTEXT_INDEXED/...` |
| `ai_analyze` | AI 分析（统一走 `main._run_analysis_stage`，可注入 DeepAgent 叙述；事件/步骤/落库单实现） | `ANALYSIS_QUEUED` |
| `generate_plan` | 计划生成（复用 generate_plan/attach_plan） | `ANALYZED/PLAN_GENERATED` |
| `wait_approval` | 审批等待：未审批则落 checkpoint 暂停 | `WAITING_APPROVAL` |
| `generate_diff` | Diff 生成（复用 diff_service + store_diff_artifact） | `APPROVED` |
| `run_test` | 测试执行（复用 execute_task_tests_worker） | `DIFF_GENERATED/TEST_FAILED` |
| `finish` | 完成/终态 | 终态 |
| `fail` | 失败分流 | 缺任务/异常 |

- 有向边：`START→prepare_context`；各节点经 `_router(status, eager)` 条件路由到下一节点；`后续→END`。
-- **统一执行路径**：HTTP 轮询与 Celery 都经同一张图——
  - HTTP（`main.advance_task`）：非 Celery 降级时也直接经图驱动到终态/暂停点，与 Worker 共用同一编排路径；
  - Celery Worker（`run_agent`）：同图执行，驱动到终态（分析→计划→等待/审批→Diff→测试）。
  - 由此 HTTP 不再直接调用 `progress_task_automation`，该函数仅保留为兼容回退，不再作为主路径。
  - **旧状态机边界**：`main.progress_task_automation` 仅保留为兼容回退；**前序阶段 `_prepare_context` 与 AI 分析 `_run_analysis_stage` 是 LangGraph 节点与兼容回退共用的单实现**
    （分析规则（上下文不足→人工/生成+事件+步骤+落库）只在 `_run_analysis_stage` 一处维护）。LangGraph 是唯一主编排主线，非壳。

**HTTP 与 Worker 的状态口径边界**：
- 两者都经同一张图（`build_task_workflow`），区别仅在调用入口：HTTP 降级入口与 Worker 都走同图推进到终态/暂停点；
- 相同输入下两路状态迁移一致（同一 `_router(status, eager)` 决策）；checkpoint / pause / resume 语义一致（`wait_approval` 落 `upsert_checkpoint`，审批恢复后重入图 diff/test）。
- 已验证：`test_workflow_graph` 覆盖 计划→审批暂停→恢复→Diff→Test；`test_celery_worker_runtime` 覆盖 worker 真实实体非 missing + 失败可序列化重试。
- 暂停/恢复：`wait_approval` 未审批时 `upsert_checkpoint(cp_graph_*)` 写 checkpoint；审批通过后重新入图即恢复。
- 失败分流：缺任务路由到 `fail`；节点抛异常由 `run_agent` 捕获→`mark_task_failed`→Celery 记 FAILURE。
- 职责边界：LangChain 负责模型调用（build_task_deep_agent），DeepAgents 负责 Agent 封装；各阶段工作复用 main 服务函数。
- **DeepAgents 真实进入主链路（非死代码，真实 LLM 实证）**：`node_ai_analyze` 配置 LLM 时经 `_deep_agent()→_analyze_with_agent→agent.invoke()` 真实调用深度代理（provider=deepagents）。
  - **构建修复（真实 DeepSeek 2026-09-08 实证）**：装 `langchain-openai`；`build_task_deep_agent` 对 deepseek/openai provider 直接用 `ChatOpenAI(model=裸模型名, base_url, api_key)`（去掉错误前缀）；
    `_analyze_with_agent` 适配 deepagents 返回（`agent.invoke` 返回 dict{`messages`:[...AIMessage(content='{json}')]}，取末条 AIMessage.content + 稳健截取 `{..}`）。
  - **调用点**：`workflow.py` node_ai_analyze→`_analyze_with_agent`→`agent.invoke`；grep 可证。
  - **显式回退（可观测、可测，不静默）**：未配置 LLM（None）/ 输出非 JSON / 代理异常 → 回退 `mvp-rule-based`，写 `[agent] DeepAgent ... 显式回退规则分析` 日志。
  - 测试：`tests/test_workflow_graph.py` — `test_deep_agent_invoke_really_called`(invoke 命中+provider=deepagents)、`test_ai_analyze_falls_back_when_no_deep_agent`、`test_ai_analyze_falls_back_on_agent_error`(provider=mvp+回退日志)。
  - 「预留（无 LLM→显式回退+日志）」与「真实调用（有 LLM→深度代理产出，实测 provider=deepagents 且返回真实决策摘要）」明确区分。
  - **HTTP 与 Worker 同图同语义（含 DeepAgents）**：HTTP `advance_task` 与 Worker `run_agent` 都经同一张图、同 `node_ai_analyze`；配置 LLM 时**两条路径都会真实调用 DeepAgents**（`progress_task_automation` 仅在图构建失败时兜底，纯规则）。
  - **唯一时序差异（非语义）**：HTTP 在 `ANALYSIS_QUEUED` 后经 `ANALYSIS_QUEUE_DELAY_SECONDS`(8s) 延迟门槛才触发分析（`node_ai_analyze` 的 `deferred` 分支）；Worker `run_agent` 无此门槛、立即分析。两者都到同一个 `_run_analysis_stage`/DeepAgents 内核。
  - **真实 DeepSeek 实证（2026-09-08，旧模型配置）**：worker 整图 eager=True → `node_ai_analyze`→`agent.invoke`→provider=deepagents、真实叙述；
    事件 `ANALYSIS_GENERATED/PLAN_GENERATED`、执行步骤 `ai_analysis/plan_generation` 完成、任务到 `WAITING_APPROVAL` 并落库（`build_task_deep_agent` 用 `langchain-openai.ChatOpenAI(model=裸名)`，`_analyze_with_agent` 适配 deepagents 的 dict{messages} 返回）。

## 测试
| 项 | 结果 |
| --- | --- |
| 专项回归 | workflow / Celery / analysis / plan / approval / diff 已通过 |
| 图推进 | ANALYSIS_QUEUED→计划→WAITING_APPROVAL（待审批暂停） |
| 审批后恢复 | APPROVED→生成 Diff（并继续测试） |
| 终态 | TESTED/COMPLETED 直接 finish |
| 失败分流 | 缺任务路由到 fail |
