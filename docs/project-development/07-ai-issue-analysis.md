# DEV-007 AI Issue 分析模块

## 功能目标与范围

- 任务进入分析队列后，基于 Issue 快照生成结构化 AI 分析：决策摘要、分析概述、发现项、影响域、建议风险等级、风险原因、不确定点、待确认问题和引用来源。
- 分析结果带版本（`revision`）和提供方（`provider`），来源逐条标注相关度，可追溯、可回放；回放页直达/刷新会从后端重新拉取真实任务与审计事件，不回退到本地 mock。
- 分析命中阻塞问题规则时，任务暂停自动流转：`manual_required=true`、`manual_reason=answer_required`、`automation_state=blocked`、`next_action=answer_analysis_question`；全部阻塞问题确认后自动重新生成分析（`revision` 递增），历史问答作为来源进入 `sources`，任务恢复自动流转。
- 每个未确认问题（阻塞与非阻塞）都可以执行三种操作：提交自定义回答、使用建议回答、跳过；跳过属于人工确认而非绕过（`answer` 记为「跳过」、`answered=true`），每次有效确认追加 `ANALYSIS_ANSWERED` 审计事件。
- 全部阻塞问题确认后自动重新生成分析（`revision` 递增），所有已确认问题作为 `source_type=history` 写入 `sources` 并追加 `ANALYSIS_REGENERATED`，任务恢复自动流转；未回答的非阻塞问题不写入历史来源。
- Issue 内容只作为数据分析，检测到疑似指令注入片段时写入 `prompt_injection_flags` 并透明告警，不作为指令执行。
- 任务状态推进到 `ANALYZED` 即止，修改计划生成属于 DEV-009，不再自动演示到任务完成。
- 模型定义按评审约定执行：`AnalysisSource`、`AnalysisQuestion`、`AnalysisResult` 三组结构前后端保持一致。
- 本模块不包含：真实代码修改、真实 Diff、真实测试、PR 创建、完整计划生成、真实后台 Worker 和新增数据库依赖。

## 业务流程图

```mermaid
flowchart TD
    A["任务进入分析队列<br/> ANALYSIS_QUEUED"] --> B["GET /api/tasks <br/>或详情读取时推进状态机<br/>progress_task_automation<br/>backend/app/main.py"]
    B --> C{"Issue 标题/正文/评论/标签<br/>是否足够形成上下文"}
    C -- "上下文过短" --> D["FAILED<br/>manual_reason=<br/>context_missing<br/>failure_node=上下文聚合<br/>详情页展示<br/>失败原因与一键重试"]
    C -- "足够" --> E["generate_mock_analysis <br/>规则型分析<br/>决策摘要 / 风险建议 /<br/> 不确定点 / 来源<br/>backend/app/main.py"]
    E --> F{"是否命中阻塞问题规则<br/>中高风险 + 兼容/历史<br/>或 是否需要/是否支持/<br/>是否保留 句式"}
    F -- "是" --> G["生成 1 个阻塞问题<br/>ANALYZED + manual_required=true<br/>manual_reason=answer_required<br/>automation_state=blocked<br/>next_action=answer_analysis_question"]
    F -- "否" --> H["ANALYZED 自动流转<br/>manual_reason=none<br/>next_action=auto_generate_plan"]
    G --> I["任务详情问题确认面板<br/>frontend/src/views/TaskDetailView.vue<br/>阻塞与非阻塞问题均可<br/>提交回答 / 使用建议回答 / 跳过"]
    I --> J["POST /api/tasks/{task_id}/<br/>analysis/questions/<br/>{question_id}/answer<br/>空回答 422<br/>重复回答 409 / 问题不存在 404<br/>每次有效确认追加<br/>ANALYSIS_ANSWERED"]
    J --> K{"全部阻塞问题已确认<br/>（回答或跳过都算确认）"}
    K -- "是" --> L["自动重新生成分析 revision+1<br/>已确认问题写入 sources<br/>source_type=history<br/>追加 ANALYSIS_REGENERATED 审计事件"]
    L --> H
    K -- "否" --> I
    D --> M["POST /api/tasks/{task_id}<br/>/retry<br/>重新进入<br/> ANALYSIS_QUEUED"]

```

## 关键代码说明

- `backend/app/main.py`：`AnalysisSource` / `AnalysisQuestion` / `AnalysisResult` 三个 Pydantic 模型，`TaskRecord` 增加 `analysis` 字段。
- `backend/app/main.py`：`build_analysis_sources` 把 Issue 标题、正文、评论、标签和仓库配置转成带相关度的来源，`source_ref` 带 Issue 号、评论号等可核对标识；重新生成时把所有已确认问题（含「跳过」）作为 `source_type=history` 追加，未回答的非阻塞问题不写入。
- `backend/app/main.py`：`build_blocking_question` 按 mock 规则生成阻塞问题：风险不低于中且正文含「兼容/历史」或命中「是否需要/是否支持/是否保留」决策句式；规则不满足时返回 `None`，不阻塞流转。
- `backend/app/main.py`：`generate_mock_analysis` 生成决策摘要（改什么/影响哪里/风险点）、发现项、影响域、建议风险等级和风险原因，并扫描 `PROMPT_INJECTION_PATTERNS` 写入注入标记。
- `backend/app/main.py`：`calculate_task_risk` 无关键词命中时对称回落到仓库 `risk_threshold`（low/medium/high 三档都生效），`build_risk_reason` 按命中与阈值两类情况分别给出原因文案。
- `backend/app/main.py`：`attach_analysis` 统一设置 `ANALYZED`、风险等级/来源和人工介入字段，阻塞问题与非阻塞问题走同一入口，避免前后端约定漂移。
- `backend/app/main.py`：`progress_task_automation` 只保留 `ANALYSIS_QUEUED → ANALYZED` 分支，分析完成后任务停留，等 DEV-009 生成计划。
- `backend/app/main.py`：`POST /api/tasks/{task_id}/analysis/questions/{question_id}/answer` 记录回答或跳过（跳过即人工确认，`answer` 记为「跳过」），每次有效确认追加 `ANALYSIS_ANSWERED`，全部阻塞问题确认后重新生成分析并追加 `ANALYSIS_REGENERATED`。
- `backend/app/main.py`：`GET /api/tasks/{task_id}/analysis` 在任务失败且无分析时返回带 `failure_node`/`failure_reason` 的 `409`，不再提示误导性的「请稍后刷新」。
- `frontend/src/views/TaskDetailView.vue`：分析区优先展示决策摘要，再展示概述、风险、关键发现、影响范围、不确定点（带展开/收起交互）和问题确认面板；每个未确认问题（阻塞与非阻塞）都提供文本输入框、使用建议回答、跳过、提交回答四种控件，已回答问题展示保存结果（跳过展示「已跳过该问题。」）；注入片段用红色告警条展示。
- `frontend/src/stores/workspace.ts`：`registerAgentTaskArtifact` 优先使用真实分析产物（含 `findings`、`affected_areas`），无分析时回落到占位文案；`nextActionText` 把 `next_action` 枚举转成用户可读文案。
- `frontend/src/views/TaskListView.vue`：状态筛选覆盖全部 13 种任务状态，处理状态列展示「待补充回答」，已取消任务显示「已取消」并参与手动优先排序。

## 相关源码文件位置

- `backend/app/main.py`
- `backend/tests/test_dev007_analysis.py`（pytest 测试套件，13 个用例）
- `backend/tests/conftest.py`（测试夹具：内存态重置与登录客户端）
- `backend/dev_demo_dev007.py`（演示数据种子脚本）
- `frontend/src/types/domain.ts`
- `frontend/src/stores/workspace.ts`
- `frontend/src/views/TaskDetailView.vue`
- `frontend/src/views/TaskListView.vue`
- `frontend/src/views/CreateTaskView.vue`
- `frontend/src/style.css`
- `frontend/vitest.config.ts`（Vitest 配置，jsdom 环境，匹配 `src/**/*.spec.ts`）
- `frontend/src/test/fixtures.ts`（共享测试夹具：分析产物与任务构造器）
- `frontend/src/stores/__tests__/workspace.spec.ts`（store 映射测试，5 个用例）
- `frontend/src/views/__tests__/TaskDetailView.spec.ts`（详情页交互测试，7 个用例）
- `frontend/src/views/ReplayView.vue`（任务回放页：直达/刷新时从后端拉取真实事件并重建时间线）
- `frontend/src/views/__tests__/ReplayView.spec.ts`（回放页测试，5 个用例）
- `backend/app/services/ai_analysis_service.py`（DeepSeek 分析叙述字段接入与 JSON 解析兜底）
- `backend/app/model_clients/deepseek_client.py`（DeepSeek Chat Completions 客户端）
- `backend/tests/test_model_integration.py`（模型接入与规则兜底测试）
- `docs/02-prd.md`
- `docs/08-api-design.md`

## 联调注意点

- 当前 MVP 已支持可选真实 LLM：配置 `LLM_API_KEY` 时由 DeepSeek 生成 `overview` / `findings` / `uncertainties`，`provider=deepseek`；未配置或调用失败时回退规则生成，`provider=mvp-rule-based`。
- 状态推进发生在读取 `/api/tasks` 或 `/api/tasks/{task_id}` 时，真实 AI 分析 Worker 接入后由后台任务主动更新。
- 阻塞问题规则是 MVP mock 约定：风险不低于中且正文含「兼容/历史」或命中决策句式；单独的「是否」不会触发，避免误判。
- 回答接口约定：`skip=true` 表示跳过（视为人工确认，`answer` 记为「跳过」），对阻塞与非阻塞问题同样生效；非跳过且回答为空返回 `422`，问题已回答返回 `409`，问题不存在返回 `404`；每次有效确认追加 `ANALYSIS_ANSWERED`，事件 `summary` 区分「已回答问题/已跳过问题」，`diff_summary` 记录问题 ID、问题类型、处理方式、回答与剩余阻塞问题数，回答后仍阻塞时单靠事件即可还原具体问题与操作类型。
- 重新生成口径：只有已确认问题（回答或跳过）写入 `history` 来源，未回答的非阻塞问题不写入；`source_ref` 格式为「问题确认 {question_id}」。
- 风险兜底：未命中风险关键词时按仓库 `risk_threshold` 取值，medium 阈值与 high 阈值同样生效，仓库默认阈值为 medium。
- 演示后端 `dev_demo_dev007.py` 内置 4 个种子任务，仓库 Gitee Token 为无效占位值，真实创建任务需要先在项目配置页保存有效 Token。
- 详情页和列表页当前不自动轮询，状态流转需要手动刷新才能看到；MVP 可接受，后续模块补定时轮询。
- 注入检测只做透明展示，不改变分析结论；Issue 内容全程按数据对待。
- 回放页 `ReplayView.vue` 进入时即调用 `GET /api/tasks/{task_id}` 拉取任务与审计事件并重建时间线，直达/刷新不会回放本地 mock 或陈旧事件；拉取失败按状态分流：404 显示「任务不存在或无权查看」、401 显示「登录状态已失效」、其他异常用 ElMessage 提示，均不回退到 mock 数据；路由切换时只允许最新一次请求更新页面，旧响应直接丢弃。

## 真实运行截图

任务列表展示「待补充回答」处理状态，需人工介入任务优先排序。

![任务列表待补充回答](../assets/dev007-task-list-answer-required-real.png)

阻塞问题详情：决策摘要、关键发现、影响范围、不确定点（可展开/收起）和问题确认面板。该图保留问题确认面板的位置与样式；当前阻塞/非阻塞问题的最新交互以自动化测试和回放页真实联调为准。

![阻塞问题与回答面板](../assets/dev007-analysis-blocking-question-real.png)

回答提交后自动重新生成第 2 版分析，历史问答进入分析来源，任务恢复自动流转。

![回答后重新生成第 2 版](../assets/dev007-analysis-regenerated-v2-real.png)

任务回放页直达/刷新：`/replay/TASK-8801` 直达后由后端重新拉取任务详情与审计事件，刷新后仍保留真实时间线节点，不回退到本地 mock。

![任务回放页直达与刷新](../assets/dev007-replay-task-8801-real.png)

Issue 正文包含「忽略之前的指令」时展示注入告警，分析来源带 Issue 号、评论号等可核对标识。

![注入片段告警](../assets/dev007-injection-alert-real.png)

Issue 上下文过短时任务在「上下文聚合」节点失败，展示失败原因和重试入口。

![短上下文失败与重试](../assets/dev007-analysis-failure-short-context-real.png)

验收后修复：已取消任务的处理状态显示「已取消」，不再误显示「已完成」。

![已取消任务徽标](../assets/dev007-task-list-cancelled-chip-real.png)

验收后修复：创建页工作分支预览按已有任务计算版本号，与后端实际生成一致。

![创建页分支预览 v2](../assets/dev007-create-branch-preview-v2-real.png)

## 测试和验收

| 验收项 | 结果 |
| --- | --- |
| 任务进入分析队列后自动生成分析 | 已通过，`ANALYSIS_QUEUED → ANALYZED`，产出决策摘要、概述、发现项、影响域 |
| 分析给出建议风险等级和风险原因 | 已通过，高风险关键词命中展示「高风险 / 命中高风险关键词：支付」 |
| 分析来源逐条标注相关度 | 已通过，标题、正文、评论、标签、仓库配置全部进入来源并展示相关度 |
| 命中阻塞问题规则时任务暂停自动流转 | 已通过，`manual_required=true`、`manual_reason=answer_required`、`automation_state=blocked`、`next_action=answer_analysis_question` |
| 阻塞与非阻塞问题均支持提交回答、使用建议回答和跳过 | 已通过（第三轮调整），跳过视为人工确认（`answer=跳过`），不再对阻塞问题跳过返回 422 |
| 非阻塞问题确认后不阻断自动流转 | 已通过，无阻塞问题的任务确认非阻塞问题后保持 `manual_reason=none`、`next_action=auto_generate_plan` |
| 未回答的非阻塞问题不写入历史来源 | 已通过（第三轮调整），历史来源只收录已确认问题 |
| 全部阻塞问题确认后自动重新生成分析 | 已通过，`revision=2`，历史问答写入来源，追加 `ANALYSIS_REGENERATED` 审计事件 |
| 任务恢复自动流转 | 已通过，`manual_reason=none`、`next_action=auto_generate_plan`，列表徽标恢复「自动运行」 |
| 空回答 422、重复回答 409、问题不存在 404 | 已通过，pytest 覆盖三个分支（空回答提示「不能为空」） |
| 关键发现与影响范围在详情页展示并支持展开/收起 | 已通过，分析区新增「关键发现」「影响范围」，与不确定点一起支持展开/收起交互 |
| 中风险阈值兜底生效 | 已通过，未命中关键词时按仓库 `risk_threshold`（含 medium）取值，风险原因文案对称 |
| 分析来源带可核对标识 | 已通过，来源标注 Issue 号、评论号、仓库 URL，正文截断带省略号 |
| FAILED 任务的分析接口返回真实失败信息 | 已通过，`GET /analysis` 返回 `failure_node`/`failure_reason`，不再提示「请稍后刷新」 |
| Issue 包含疑似指令注入片段时透明告警 | 已通过，展示「已按数据分析处理，未作为指令执行」，pytest 断言 `prompt_injection_flags` 命中 |
| Issue 上下文过短时任务失败并支持重试 | 已通过，`FAILED` + `failure_node=上下文聚合`，重试后重新进入分析队列 |
| 分析完成后任务停留在 ANALYZED | 已通过，不再自动演示到 COMPLETED |
| 列表展示「待补充回答」并优先排序 | 已通过，需处理任务排在自动任务之前 |
| 状态筛选覆盖全部 13 种任务状态 | 已通过，验收后补齐 CREATED/ISSUE_FETCHED/CONTEXT_INDEXED/PLAN_GENERATED/APPROVED/COMPLETED |
| 已取消任务处理状态显示「已取消」 | 已通过，验收后修复，不再误显示「已完成」 |
| 创建页工作分支预览与后端版本一致 | 已通过，验收后修复，同仓库同 Issue 已有 v1 时预览显示 v2 |
| ANALYSIS_ANSWERED 审计事件可还原具体问题与操作 | 已通过（第四轮补齐），事件 `summary` 区分「已回答问题/已跳过问题」，`diff_summary` 记录问题 ID、问题类型（阻塞/非阻塞）、处理方式（提交回答/跳过）、回答内容与剩余阻塞问题数；pytest 断言跳过与回答两条路径的事件字段 |
| 回放页直达/刷新可回放真实任务事件 | 已通过（第四轮补齐），进入回放页即从后端拉取任务与审计事件重建时间线，不落 mock/陈旧数据；404/401/其他异常分别展示对应错误态；Vitest 覆盖直达加载、陈旧刷新替换、404 无兜底、401 文案分流、路由切换竞态（A 迟到响应不覆盖 B）；真实联调截图见 `docs/assets/dev007-replay-task-8801-real.png` |
| 前端自动化测试（criterion 13） | 已通过，Vitest 17 个用例：store 映射 5 个 + 详情页交互 7 个 + 回放页 5 个（含跳过视为人工确认、展开/收起、建议回答填充、提交回答接口调用、失败重试入口、回放直达加载、陈旧刷新、404/401 分流、路由竞态） |

验证命令：

- `backend/.venv/Scripts/python.exe -m py_compile app/main.py` 通过。
- `backend/.venv/Scripts/python.exe -c "from app.main import app; print(len(app.routes))"` 通过，当前 27 条路由。
- `PYTHONIOENCODING=utf-8 uv run pytest tests -q` 通过，13 个用例全部 PASS（含跳过视为人工确认、非阻塞确认不阻断流转、中风险阈值兜底、注入标记断言、失败任务分析接口等回归用例）。
- `npm test` 通过，17 个用例全部 PASS（store 映射 5 个 + TaskDetailView 交互 7 个 + ReplayView 回放 5 个）。
- `npm.cmd run build` 通过（vue-tsc + vite）。
- 浏览器真实联调通过：登录、阻塞问题渲染、回答、重新生成、注入告警、失败重试、取消、筛选和创建页预览。

## 第二轮验收修复清单

按验收评审意见逐项处理，处理结果如下：

1. `calculate_task_risk` 忽略 `risk_threshold="medium"`：已修复，无关键词命中时对称回落到仓库阈值，medium 与 high 同样生效，`build_risk_reason` 补齐阈值分支文案。
2. `findings` 与 `affected_areas` 未展示、无展开交互：已在详情页分析区新增「关键发现」「影响范围」，与不确定点一起支持展开/收起。
3. 阻塞问题可被「跳过」绕过：前端移除阻塞问题跳过按钮，后端对阻塞问题 `skip=true` 返回 422。（第三轮已调整：跳过视为人工确认而非绕过，见「第三轮：问题确认闭环」）
4. 非阻塞问题默认建议不生效：重新生成时把未回答的非阻塞问题默认建议以「（默认建议）」写入历史来源。（第三轮已调整：未回答的非阻塞问题不写入历史来源，见「第三轮：问题确认闭环」）
5. FAILED 任务 `GET /analysis` 返回误导性 409：改为返回 `failure_node`/`failure_reason` 的真实失败信息，问题列表接口同步处理。
6. 来源引用不可核对：`source_ref` 补充 Issue 号、评论号，仓库来源保留 URL，截断内容带省略号。
7. 后端无 pytest 套件：新增 `backend/tests/test_dev007_analysis.py`（12 个用例）+ `conftest.py`，`pytest` 加入 dev 依赖组，原 `stub_dev007.py` 桩测脚本已删除。
8. Prompt Injection 无自动化断言：pytest 断言注入任务 `prompt_injection_flags` 命中「忽略之前的指令」，且风险等级按真实文本计算（注入内容不改变结论）。
9. 文档缺口：DEV-007 独立文档、开发总表状态、截图引用此前已完成；本次按 `-real.png` 约定重命名全部截图并更新引用，`docs/22-project-development-log.md` 旧总表已加说明（见下）。
10. 验收项与架构不一致（criterion 7）：代码与 API 文档一致使用 `manual_reason=answer_required`（评审决定的专用枚举），验收项中的 `context_missing` 为笔误，建议以 `answer_required` 为准修订验收清单。
11. 模块标题与交付范围不符：模块已更名为「AI Issue 分析」，DEV-009 修改计划、DEV-010 审批模块、DEV-011 Diff/测试/PR、DEV-012 回放审计按新编号推进。
12. 前端自动化测试缺失（criterion 13）：新增 Vitest + @vue/test-utils + jsdom，`package.json` 增加 `test`/`test:watch` 脚本，`vitest.config.ts` 匹配 `src/**/*.spec.ts`；store 映射与详情页交互共 10 个用例全部 PASS，`vue-tsc` 构建同时覆盖测试文件类型检查。
13. 空回答 422 文案与规则冲突：原提示「请填写回答内容，或选择跳过该问题」暗示阻塞问题可跳过，已按问题类型区分提示（阻塞问题必须填写、非阻塞问题可跳过），`HTTP_422_UNPROCESSABLE_ENTITY` 弃用告警同步消除。

## 第三轮：问题确认闭环

问题确认模块联调后，按小泽确认的两条行为决策，对第二轮验收修复的两处处理做了方向性调整：

1. **允许阻塞问题跳过（D1）**：跳过属于人工确认而非绕过——`answer` 记为「跳过」、`answered=true`，与填写回答同样计入已确认，每次有效确认追加 `ANALYSIS_ANSWERED` 审计事件。推翻了第二轮修复清单第 3 条（阻塞问题 `skip=true` 返回 422）。
2. **未回答的非阻塞问题默认建议不写入 history（D2）**：重新生成时历史来源只收录已确认问题（回答或跳过），未回答的非阻塞问题不再以「（默认建议）」写入。推翻了第二轮修复清单第 4 条。

本轮改动：

- 后端 `answer_task_analysis_question`：删除阻塞问题跳过 422 分支，`skip=true` 统一按人工确认处理；每次有效确认追加 `ANALYSIS_ANSWERED`；仍存在未确认阻塞问题时任务保持 `manual_required=true`、`automation_state=blocked`、`next_action=answer_analysis_question`；全部阻塞问题确认后重新生成分析（`revision+1`）并追加 `ANALYSIS_REGENERATED`。
- 后端 `build_analysis_sources`：历史来源只写入已确认问题，`source_ref` 格式「问题确认 {question_id}」，内容 `Q: …\nA: {回答或「跳过」}`。
- 前端 `TaskDetailView.vue`：阻塞与非阻塞问题统一提供文本输入框、使用建议回答、跳过、提交回答四种控件；跳过保存后展示「已跳过该问题。」；非阻塞问题确认后不阻断自动流转。
- 测试补齐：pytest 新增/调整「阻塞问题跳过视为人工确认」「非阻塞问题确认保持自动流转」「未回答的非阻塞问题不写入历史」等用例；Vitest 详情页新增跳过交互用例。
- 文档同步：`docs/08-api-design.md` 回答接口规则、`docs/02-prd.md` 问题确认模块与边界表同步更新。

验证结果：pytest 13/13 PASS、Vitest 11/11 PASS（store 映射 5 个 + 详情页交互 6 个）、`npm run build`（vue-tsc + vite）通过。本轮未重新跑页面，仅更新文字与测试结果；第 2 张「阻塞问题与回答面板」截图与当前交互不一致（当时不提供跳过），待下次联调时重新截图。

## 第四轮：审计事件还原与回放页直达可靠性

针对“问题可回放”链路的两个 P1 缺口做修复，让单条审计事件和回放页都能还原真实处理过程：

1. **ANALYSIS_ANSWERED 审计事件字段补全**：事件除了问题文本、回答内容和剩余阻塞数量外，`diff_summary` 明确记录问题 ID、问题类型（阻塞/非阻塞）、处理方式（提交回答/跳过）与回答内容，单靠事件即可还原具体问题和操作类型，不依赖任务当前状态。
2. **回放页直达/刷新读取真实事件（ReplayView）**：原实现只从 Pinia 本地 artifact 读时间线，直接访问 `/replay/{task_id}` 或刷新时可能回放 mock 或陈旧事件；现在路由带任务 ID 时严格匹配 store 中同 ID 任务，进入回放页即调用 `GET /api/tasks/{task_id}` 拉取任务与审计事件并重建时间线。

本轮改动：

- 后端 `answer_task_analysis_question`：事件 `summary` 区分「已回答问题/已跳过问题」，`diff_summary` 追加问题 ID、问题类型、处理方式与剩余阻塞问题数；pytest 用例断言跳过与回答两条路径的事件字段。
- 前端 `ReplayView.vue`：fetch-on-entry 拉取任务详情；请求序号 + 卸载标记 + 当前路由校验三重防护，路由切换后迟到的旧响应直接丢弃；拉取失败按状态分流——404 显示「任务不存在或无权查看」、401 显示「登录状态已失效」、其他异常 ElMessage 提示，均不回退到本地 mock 数据；加载态与错误态优先于回放主体渲染，任务为空时模板不再访问空引用。
- 测试补齐：`frontend/src/views/__tests__/ReplayView.spec.ts` 5 个用例覆盖直达访问渲染 ANALYSIS_ANSWERED 完整字段、陈旧 store 刷新后真实事件替换、404 无 mock 兜底、401 文案不被误判为任务不存在、A 请求未返回时切到 B 后 A 迟到响应不覆盖 B。

验证结果：pytest 13/13 PASS、Vitest 17/17 PASS（store 映射 5 个 + TaskDetailView 交互 7 个 + ReplayView 回放 5 个）、`npm run build`（vue-tsc + vite）通过。本轮为代码、自动化验证与真实回放页联调，新增 `docs/assets/dev007-replay-task-8801-real.png` 作为闭环截图。

## 复盘

- 这次练的是“分析产物闭环”：分析不是一次性文本，而是带版本、来源、问题和审计事件的可追溯产物，人工回答能驱动分析演进。
- 做得好的地方：阻塞问题的人工介入字段约定一次定死并在前后端一致执行；回答后重新生成把历史问答纳入来源，形成可回放证据链。
- 验收暴露的问题都很小（枚举补全、语义区分、预览版本），说明模型定义和字段约定的前置评审是有效的。
- 下次要加强：把 `mvp-rule-based` 替换成真实 AI 分析 Worker，并接入 DEV-009 计划生成，让 `ANALYZED` 之后的自动流转真正跑起来。
