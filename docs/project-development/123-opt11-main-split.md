# OPT-11 main.py 渐进拆分

> 状态：DONE（2026-10-16）· 对应任务：OPT-11 · 证据目录 `docs/project-development/assets/opt11/`
> 关联代码：`backend/app/api/__init__.py`、`backend/app/api/system.py`、`auth.py`、`users.py`、`projects.py`、
> `comments.py`、`notifications.py`、`repositories.py`、`repository_indexes.py`、`dashboard.py`、`audit.py`、
> `history.py`、`team_requests.py`（12 个领域路由模块，本轮新建）；
> `backend/app/main.py`（路由注册块 + 49 个核心处理器迁出）；`backend/tests/test_opt11_main_split.py`（路由冻结锚点）。

## 1. 目标、范围与设计契约

**目标**（OPT-11 验收口径）：「按领域迁移 API/服务/依赖边界；每步有行为回归，不一次性重写，不覆盖既有改动」。

本项把 `app/main.py` 中 83 条核心路由里的 **49 条处理器函数**按领域迁到 `app/api/<domain>.py`
（`APIRouter` 模块），迁移过程中：

- **不改变任何对外行为**：HTTP 状态码、响应结构、operationId、路由路径/方法逐条冻结（见 §2 锚点测试）；
- **不一次性重写**：分 6 个步骤（系统/认证 → 评论/通知 → 仓库/索引 → 看板/审计 → 历史复用 → 平台需求），
  每步「新建模块 + 注册 router + main.py 删处理器 + 定向回归」后才进入下一步；
- **不覆盖既有改动**：全程只做增量编辑，用户工作区其他未提交修改（OPT-08/09/10 等）保持原样。

### 1.1 迁移清单（49 条处理器 → 12 个领域模块）

| 领域模块 | 处理器数 | 路由前缀 | 备注 |
| --- | --- | --- | --- |
| `app/api/system.py` | 1 | `/api/health` | 依赖探活经 `main.check_milvus_health` / `main._check_redis_health` 惰性读取（测试 monkeypatch 生效） |
| `app/api/auth.py` | 7 | `/api/auth/*` | 登录/登出/会话/密码找回与应急重置 |
| `app/api/users.py` | 2 | `/api/users*` | `ROLE_PERMISSIONS` 经 `main.` 惰性读取（test_dev020 有整体替换补丁） |
| `app/api/projects.py` | 4 | `/api/projects*` | — |
| `app/api/comments.py` | 4 | `/api/tasks/{id}/comments*` | `main._persist_task`、`main.resolve_comment_reference` 惰性读取 |
| `app/api/notifications.py` | 6 | `/api/notifications*`、`/api/events/stream`、`/api/tasks/{id}/nudge` | `_NUDGE_HOURS` 读取 `main.`（见 §4.2 回归修复） |
| `app/api/repositories.py` | 4 | `/api/repositories*` | `REPOSITORY_TOKENS` 经 `main.` 惰性读取 |
| `app/api/repository_indexes.py` | 5 | `/api/repositories/{id}/index*` | `fetch_gitee_branch_head`（15 处补丁）、`_enqueue_index_build` 惰性读取 |
| `app/api/dashboard.py` | 1 | `/api/dashboard/automation-summary` | 汇总辅助函数定义在 main.py 末尾 → `main._dashboard_*` 惰性读取 |
| `app/api/audit.py` | 3 | `/api/audit-events`、`/api/approvals/pending|candidates` | `main.approval_candidate` 惰性读取 |
| `app/api/history.py` | 5 | `/api/history/search`、`/api/tasks/{id}/completed-summary*` | `main._can_view_replay` 惰性读取；`HistorySearchPayload` 一并迁出 |
| `app/api/team_requests.py` | 7 | `/api/team/requests*` | `main.create_gitee_issue` / `maybe_auto_create_task_after_sync` / `find_gitee_issue_by_source_marker` 惰性读取（Celery 链测试有补丁） |

**仍在 `app/main.py`（本轮未迁，如实声明）**：任务主链路（tasks/plans/approvals/diffs/tests/PR/回放等
34 条核心路由）、企业域 router（复用既有 `include_router` 块）、全部内存容器（`USERS`/`TASKS`/`TEAM_REQUESTS`…）
与 Worker 入口函数（`sync_request_to_gitee_worker` 等）。容器与 Worker 作为兼容层保留在 main，
迁移模块经 `from app import main` + `main.X` 在**调用时**读取，保证既有 `monkeypatch.setattr(main, "X", ...)`
全部继续生效。

### 1.2 拆分规模（实测）

- `app/main.py`：分支基线 `HEAD` 9177 行 → 当前工作区 **7651 行**（含此前未提交的 OPT-08/09/10 改动）；
- 新增 `app/api/` 12 个领域模块合计 **2042 行**（含 `__init__.py` 8 行）；
- 本轮共迁出 **49 个处理器函数**（83 条核心路由中的 59%）。

## 2. 路由冻结锚点（TDD：红 → 分步转绿）

`tests/test_opt11_main_split.py` 四个用例构成本次重构的「行为不变」契约：

1. `test_frozen_effective_table_matches`：以运行时 `app.openapi()` 为准导出 **117 条** `/api` 操作
   （83 核心 + 34 企业），逐条与冻结三元组 `(method, path, operationId)` 对拍；
2. `test_frozen_core_routes_all_still_served`：83 条迁移前核心路由仍被服务，且 operationId 与公式
   `re.sub(r"\W", "_", f"{name}{path}") + f"_{method.lower()}"` 反推一致（防处理器改名/漂移）；
3. `test_migrated_handlers_live_in_domain_modules`：12 个领域模块存在且处理器可调用，`app.main`
   不残留同名处理器；
4. `test_health_smoke_after_split`：拆分后 `/api/health` 冒烟可用。

**真实红**（首跑，领域模块均未创建）：

```text
# 2 passed, 1 failed —— test_migrated_handlers_live_in_domain_modules
# AssertionError: 未在领域模块找到处理器: ['app.api.system（模块未创建）', ... 共 12 项]
```

随后按 §1.1 的 6 个步骤逐批迁移，锚点失败清单逐步收窄（3 步后剩 4 个模块 → 4 步后剩 2 个 → 5 步后剩 1 个 →
第 6 步全绿）：

```text
# Step3 定向回归：1 failed, 44 passed（剩 dashboard/audit/history/team_requests）
# Step4 定向回归：1 failed, 45 passed（剩 history/team_requests）
# Step5 定向回归：1 failed, 16 passed（剩 team_requests）
# Step6 定向回归：104 passed（全绿）
# 锚点终态：tests/test_opt11_main_split.py 4 passed in 5.15s
```

### 2.1 关键环境发现：FastAPI 0.141 惰性 `_IncludedRouter`

`include_router` 在 FastAPI 0.141.1 中不再把路由扁平化进 `app.routes`，而是追加包装对象。
实测（迁移完成后）：`app.routes` = `APIRoute` 34 条（只剩未迁的任务主链路）+ `Route` 4 条（非 /api）
+ `_IncludedRouter` 包装器 17 个（12 个新领域 router + 5 个既有企业 router），扁平扫描完全看不到
包装器内的 117 条 `/api` 操作。**有效路由表的唯一真源是 `app.openapi()`**——锚点测试据此改写，
并已程序化验证 83 条冻结路由在 117 条有效操作中全部命中、operationId 0 处不匹配（先验证、后落测）。

## 3. 主链路（Mermaid，含源码位置）

```mermaid
flowchart TD
    subgraph MAIN["backend/app/main.py（7651 行）"]
        M1["内存容器/模型/权限表<br/>USERS·TASKS·TEAM_REQUESTS·ROLE_PERMISSIONS"]
        M2["企业域 include_router 块<br/>tenancy/approvals/rag/runtime/ops"]
        M3["OPT-11 路由注册块<br/>app/api/*.py 12 条 include_router"]
        M4["任务主链路处理器（未迁，34 条核心路由）<br/>tasks/plans/approvals/diffs/tests/replay"]
        M5["Worker 入口<br/>sync_request_to_gitee_worker 等"]
    end

    subgraph API["backend/app/api/（12 模块 · 2042 行）"]
        A1["system.py · auth.py · users.py · projects.py"]
        A2["comments.py · notifications.py"]
        A3["repositories.py · repository_indexes.py"]
        A4["dashboard.py · audit.py · history.py · team_requests.py"]
    end

    T["tests/test_opt11_main_split.py<br/>117 操作冻结 + 83 路由名冻结 + 处理器归属"]
    C["客户端/前端"]
    W["Celery Worker<br/>app/workers/tasks.py"]

    C -->|"HTTP /api/*（117 条操作不变）"| MAIN
    M3 --> A1 & A2 & A3 & A4
    A1 & A2 & A3 & A4 -. "函数体内 from app import main<br/>main.X 调用时读取（补丁安全）" .-> M1
    A1 & A2 & A3 & A4 -->|"业务读写"| L["app/db/reads.py 门面 / 内存容器"]
    W -->|"from app.main import ...worker"| M5
    M4 -.->|"内部调用"| M1
    T -. "运行时对拍 app.openapi() + 源码扫描" .-> MAIN
    T -.-> API

    style MAIN fill:#eef5ff,stroke:#3366cc
    style API fill:#eaf7ea,stroke:#33aa33
    style T fill:#fff4e5,stroke:#cc8800
```

## 4. 关键代码说明

### 4.1 补丁安全导入规则（迁移的核心约束）

Python 模块级 `from app.main import X` 在 main.py 执行到路由注册块（约 4330 行附近）时发生，
此时 main 只执行到该行。据此确立并全程执行的规则：

- **模块级导入**：只用于注册块之前定义、且从未被测试整体替换的符号
  （如 `api_success`、`forbidden`、`require_permission`、`TEAM_REQUESTS`、`TeamRequestRecord` 等）；
- **函数体内 `from app import main` + `main.X`**：用于 ①注册块之后定义的符号
  （`find_team_request`、`_can_view_replay`、`_dashboard_summary_payload`、`approval_candidate`…）；
  ②被 `monkeypatch.setattr(main, "X", ...)` 打过补丁的符号（`create_gitee_issue`、`ROLE_PERMISSIONS`、
  `_persist_task`、`fetch_gitee_branch_head`、`REPOSITORY_TOKENS`、`check_milvus_health`、`_check_redis_health`…）。

被补丁符号清单来自对 `tests/*.py` 的全量扫描（含 `import app.main as m` 别名）。

### 4.2 迁移中发现并修复的真实回归

`_NUDGE_HOURS`：最初的迁移把 `@app.post("/api/tasks/{task_id}/nudge")` 连同模块常量
`_NUDGE_HOURS = 24` 一起移入 `app/api/notifications.py`，导致
`app/services/notification_service.py:73`（`send_approval_reminder` 读取 `m._NUDGE_HOURS`）抛
`AttributeError`，3 个 DEV-019 用例红。修复：main 作为单一真源恢复 `_NUDGE_HOURS = 24`，
迁移侧全部改为 `main._NUDGE_HOURS`（4 处）。这也是「模块级移动常量前先全仓 grep 读取方」的教训。

### 4.3 测试适配（唯一一处测试改动）

`tests/test_celery_async_chains.py` 有两处**直接函数调用** `main.sync_team_request_to_gitee(...)`；
处理器迁出后改为 `from app.api.team_requests import sync_team_request_to_gitee` 调用，
参数与断言不变（HTTP 行为另由 `test_dev016b_sync_gitee.py` 覆盖，全绿）。

## 5. 回归与验证（真实执行）

- 六个迁移步骤的代表性回归链（认证/评论通知/仓库索引/看板审计/历史/平台需求/Celery/索引生命周期），
  **`151 passed in 125.82s`**（命令与环境见 §6 证据文件头）；
- 锚点终态 **`4 passed in 5.15s`**；
- 全量后端套件 **`981 passed, 17 skipped, 4 warnings in 1572.75s (0:26:12)`，`EXIT=0`**
  （998 收集 = 981 + 17；对照 OPT-10 基线 977 passed/17 skipped，差值 +4 即本轮新增的
  `test_opt11_main_split.py` 4 例——该文件为本项新建，此前不在基线内）。

### 5.1 首次全量跑出的假红与修复（如实记录）

首轮全量（`assets/opt11/opt11_full_backend_run1_flaky.txt`）为 `1 failed, 980 passed`：
`tests/test_enterprise_mcp_client.py::test_client_cancel_interrupts_inflight_call_and_audits`
断言 `elapsed < 3.0` 实测 `3.0166s`（超出 0.55%）。根因不是 OPT-11 迁移（该用例不经过任何被迁移路由）：
取消路径 = 0.3s 定时 → 轮询命中 → `_abandon`（`mcp_client.py:92` 最多再等 2s）→ 会话/子进程清理，
满负载下清理耗时把总时长顶出 3.0s 上界。隔离复跑 3/3 通过（9.88s/7.20s/11.19s）证明为负载敏感假红。
修复：上界放宽到 5.0s（仍远低于 30s 工具超时），同时保留「取消应迅速生效」的语义；
`tests/test_enterprise_mcp_client.py` 16 passed 后复跑全量得上述 981 全绿。

## 6. 证据文件清单

- `assets/opt11/opt11_anchor.txt`：路由冻结锚点 `4 passed in 5.15s`（含逐用例 PASSED 与 `EXIT=0`）；
- `assets/opt11/opt11_stream_regressions.txt`：六步迁移回归链 `151 passed in 125.82s`；
- `assets/opt11/opt11_split_metrics.txt`：main.py 行数对比（HEAD 9177 → 7651）与 `app/api/` 12 模块行数清单；
- `assets/opt11/opt11_route_table.txt`：路由表形态实测（扁平 `app.routes` = APIRoute 34 + Route 4 + `_IncludedRouter` 17；
  `app.openapi()` 117 条 `/api` 操作）；
- `assets/opt11/opt11_full_backend.txt`：全量后端套件 `981 passed, 17 skipped`（1572.75s，`EXIT=0`，迁移后终极回归）；
- `assets/opt11/opt11_full_backend_run1_flaky.txt`：首轮全量原始输出 `1 failed, 980 passed`
  （MCP 取消计时假红 3.0166s，如实保留未删）。

## 7. 联调注意点

- 迁移模块的 `from app import main` 是**运行时**导入，切勿改成模块级导入（会截断 main 初始化或绕过补丁）；
- 后续如需再迁移任务主链路（tasks/plans/approvals/replay），直接复用本锚点：往
  `MIGRATED_HANDLERS` 增加目标模块与处理器名即可获得契约保护；
- 新增路由必须同时更新 `FROZEN_EFFECTIVE_OPERATIONS`（否则 117 条对拍红），这是刻意设置的「防漂移税」；
- **遗留的文档行号漂移**：11 份历史开发文档（10/12/14/16/17/18/19/20/90/94/121）内引用过
  `main.py:<行号>` 定位源码，这些行号在本次拆分（main.py 9177→7651 行）后进一步失效；其中多份在拆分前
  就已漂移（如 16/17/18 指的行号对应更早版本）。按「一次只推进一个明确功能」原则不在本项内批量改写，
  已登记为 OPT-12（文档状态与时间线一致性）的核查输入。
