# OPT-15 GitHub/GitLab 代码托管适配（Provider 契约抽象）

> 一句话：把散落的「Gitee 逻辑」收进托管平台无关的 Provider 契约（`GitHostProvider` Protocol + `GiteeProvider` 实现），用本地真实 socket/真实 git 证据锁住传输语义；GitHub/GitLab 未实现前由「启动期配置 + 注册表调用点」双重 fail-closed 拒绝，绝不静默回退 gitee。
>
> 状态：Done（2026-10-16）。验证 harness 16/16；专项门禁 26 passed；全量回归见 §5.3。
> 证据：`backend/.data/opt15-exp/`（运行产物）；文档资产副本 `docs/project-development/assets/opt15/`。

## 1. 目标、范围与设计契约

### 1.1 问题（侦察结论）

OPT-15 立项前对仓库做了托管平台集成面盘点，确认三个缺口：

1. **没有平台无关的契约层**：Gitee 逻辑散在三处——`backend/app/services/gitee_pr_service.py`（PR 创建与 404 作用域回退）、`backend/app/services/repo_mirror.py`（git 镜像/鉴权远程/掩码）、`backend/app/main.py` 的 `gitee_api_get`/`gitee_api_post` 与 Issue/分支函数（团队版同步链路）。「适配 GitHub/GitLab」没有可实现的接口面；
2. **没有 provider 选择门**：任何"多平台"改动都隐含静默回退 Gitee 的风险——把 Issue/PR 写到非预期的托管平台，是比"不支持"更坏的失败；
3. **写入点无统一接缝**：E4 Runtime 的 PR 创建直接调用 `gitee_pr_service`，接入其它平台需要逐点改调用方。

### 1.2 范围（本次交付）

- **契约层**：`backend/app/services/git_host/contract.py`——`GitHostProvider` Protocol（8 方法 + `name`）、5 个数据类（`RepositoryRef`/`BranchHeadRef`/`IssueRef`/`IssueCommentRef`/`IssueSnapshotRef`/`PullRequestRef`）、`GitHostError(kind=…)` 错误分类、`redact()` 脱敏助手；
- **Gitee 实现**：`backend/app/services/git_host/gitee.py`——仓库解析/鉴权远程/掩码委托 `repo_mirror`（镜像与推送行为逐位一致）；分支/Issue/查重/快照为独立 urllib 传输（`api_base` 可注入）；PR 委托 `gitee_pr_service`（模块属性读取，补丁链不变）；
- **注册表**：`backend/app/services/git_host/registry.py`——`get_git_host_provider()` 默认 gitee；显式非 gitee → `unsupported_provider` fail-closed；
- **配置边界**：`GIT_HOST_PROVIDER`（默认 gitee；非 gitee 启动即拒绝，对齐 `AUTH_MODE` 先例）；
- **采纳接缝**：`backend/app/services/enterprise_release_service.py（create_runtime_pr）` 改经注册表 + Provider；
- **验证**：本地契约测试 26 条（真实 127.0.0.1 stub HTTP）+ harness 16 项（含真实 git 二进制本地全链路、子进程 fail-closed、采纳链 recorder）。

### 1.3 明确不做（边界声明）

- **不实现** GitHub/GitLab 的传输层与字段映射（无 API 适配、无 webhook、无多平台并行切换）；
- **不迁移**团队版 `backend/app/main.py` 的 Issue 同步链路到 Provider——既有链路行为冻结（大量既有测试按 `main.gitee_api_*` 注入桩），Provider 为其提供已验收的迁移目标，迁移留待后续立项；
- **不触真实远端**：本轮零真实 Gitee/GitHub/GitLab 写入；真实仓库写入按进度板口径「逐项确认」后另行执行（§7）；
- **不做**前端 UI 变更（纯后端交付）。

### 1.4 设计契约（安全默认）

| 配置/参数 | 默认 | 语义 | 钳制 |
| --- | --- | --- | --- |
| `GIT_HOST_PROVIDER` | `gitee` | 当前唯一支持值；非 gitee → 启动即 `RuntimeError`（fail-closed） | — |
| `get_git_host_provider(name)` | `None`=读配置 | 显式非 gitee 名 → `GitHostError(kind=unsupported_provider)` | 大写/空白归一 |
| `GiteeProvider.api_base` | `None`=平台地址 | 仅本地契约测试注入用；默认值时不进 PR 委托调用（补丁兼容） | 尾斜杠归一 |
| 错误 `kind` | `field_error` 兜底 | `auth_expired` / `field_error` / `network_timeout` / `response_invalid` / `invalid_repository` / `unsupported_provider` | — |
| 查重翻页 | 6 页 | `per_page=100`、`state=all`、按创建倒序；空页或不足 100 条提前停 | 上限 6 页 |
| token 位置 | — | GET 走查询参数、POST 走请求体；异常消息一律脱敏 | 结构掩码 + 字面量擦除 |

## 2. 业务流程（Mermaid，含源码位置）

### 2.1 Provider 契约与配置边界（fail-closed 分流）

```mermaid
flowchart TD
    A["GIT_HOST_PROVIDER 环境变量<br/>默认 gitee"] --> B["load_runtime_settings<br/>backend/app/core/runtime_config.py（_git_host_provider）"]
    B -- "非 gitee 值" --> B1["RuntimeError：fail-closed 拒绝启动<br/>API/Worker/CLI 全部进程失败<br/>绝不静默回退 gitee"]
    B -- "gitee" --> C["get_git_host_provider(name)<br/>backend/app/services/git_host/registry.py（get_git_host_provider）"]
    C -- "显式非 gitee（github/gitlab）" --> C1["GitHostError(kind=unsupported_provider)<br/>backend/app/services/git_host/contract.py（GitHostError）"]
    C -- "gitee" --> D["GiteeProvider 实例<br/>backend/app/services/git_host/gitee.py（GiteeProvider）"]
    D --> E1["parse_repository / authenticated_remote / mask_remote<br/>委托 backend/app/services/repo_mirror.py（parse_gitee_repository）"]
    D --> E2["fetch_branch_head / create_issue / find_issue_by_marker / fetch_issue_snapshot<br/>独立 urllib 传输（api_base 可注入）"]
    D --> E3["create_pull_request<br/>委托 backend/app/services/gitee_pr_service.py（create_gitee_pull_request）"]
```

### 2.2 E4 PR 采纳主链路（应用接缝）

```mermaid
flowchart TD
    A["_draft_pr 工具<br/>backend/app/enterprise_runtime/agents/tools.py（_draft_pr）"] --> B["create_runtime_pr<br/>backend/app/services/enterprise_release_service.py（create_runtime_pr）"]
    B --> C{"已有 pr.html_url ?"}
    C -- 是 --> C1["幂等：直接返回既有记录<br/>不重复推送/建 PR"]
    C -- 否 --> D["get_git_host_provider(E4_PR_PROVIDER)<br/>registry 默认读 GIT_HOST_PROVIDER"]
    D --> E["_prepare_and_push_runtime_branch<br/>真实 diff → worktree → commit → push"]
    E --> F["GiteeProvider.create_pull_request<br/>backend/app/services/git_host/gitee.py（GiteeProvider）"]
    F --> G["模块属性读取委托<br/>gitee_pr_service.create_gitee_pull_request"]
    G --> H["Gitee API POST /repos/{owner}/{repo}/pulls<br/>404 enterprise → owner 作用域回退"]
    H --> I["PullRequestRef → 结果 dict<br/>provider/id/number/html_url/state + 推送信息"]
    I --> J["落盘 task.pr.json（幂等键 e4-pr:task:diff_hash）<br/>token 不进结果/日志/证据"]
    D -. "非 gitee（演进场景）" .-> X["GitHostError(unsupported_provider)<br/>_draft_pr 返回 BLOCKED（不冒称成功）"]
```

### 2.3 Provider 传输语义（分支 / Issue / 查重，错误分流）

```mermaid
flowchart TD
    A["GiteeProvider 传输核心 _send<br/>backend/app/services/git_host/gitee.py（GiteeProvider）"] --> B{"method"}
    B -- GET --> B1["token 走查询参数 access_token<br/>（分支 head / Issue 列表 / 快照）"]
    B -- POST --> B2["token 仅在请求体<br/>（建 Issue：owner 作用域 + repo 参数）"]
    B1 --> C["urllib 真实 HTTP（timeout=12s）"]
    B2 --> C
    C -- 2xx --> D["json 解析 → 数据类归一化"]
    C -- "401/403" --> E["GitHostError(kind=auth_expired)<br/>函数体内红录异常消息"]
    C -- "其余 HTTP" --> F["GitHostError(kind=field_error)"]
    C -- "URLError/TimeoutError" --> G["GitHostError(kind=network_timeout)"]
    C -- "JSON 解析失败 / 结构不符" --> H["GitHostError(kind=response_invalid)"]
    D --> I["find_issue_by_marker 分页：<br/>命中即停 / 空页停 / 满 100 续翻 / 上限 6 页"]
    E --> J{"查重场景?"}
    J -- 是 --> J1["auth 错误上抛（调用方置 SYNC_FAILED）"]
    J -- "非 auth 错误" --> J2["降级 None（可重试，不伪造命中）"]
```

## 3. 关键机制与实现要点

### 3.1 契约是 Protocol，不是基类

`GitHostProvider` 用 `runtime_checkable` Protocol 声明（`backend/app/services/git_host/contract.py`）：实现方无需继承、不引入依赖倒置成本，`isinstance` 检查同时充当 conformance 测试。数据类全部 frozen——跨模块传递不可变，`PullRequestRef.state` 默认 `OPEN` 与既有 dict 语义对齐。

### 3.2 PR 委托的补丁兼容约束（为什么这么实现）

仓库有既有测试按 **模块属性** 给 `gitee_pr_service.create_gitee_pull_request` 注入桩（`test_pr_creation` / `test_opt06_agent_tool_security` / `test_enterprise_e4_agents`），且有 fake 是**无 `**kwargs` 的精确签名**。因此 `GiteeProvider.create_pull_request` 必须：

1. 调用时 `from app.services import gitee_pr_service` 并读取模块属性（而非在 provider 构造时捕获函数引用）——monkeypatch 才生效；
2. `api_base` 为平台默认（`None`）时**不向委托调用追加该 kwarg**——精确签名 fake 不炸；只有本地契约测试注入 stub 地址时才透传。

这两条由专项测试 `test_create_pull_request_delegates_without_extra_kwargs`（精确签名 recorder）与采纳兼容回归（64 passed）双向锁住。

### 3.3 fail-closed 双重门（绝不静默回退）

| 门 | 位置 | 触发面 |
| --- | --- | --- |
| 启动期 | `backend/app/core/runtime_config.py（_git_host_provider）` | `GIT_HOST_PROVIDER` 非 gitee → 所有进程启动即 `RuntimeError`（同名先例：OPT-14 `AUTH_MODE`） |
| 调用点 | `backend/app/services/git_host/registry.py（get_git_host_provider）` | 显式请求非 gitee 名（含 E4 `E4_PR_PROVIDER` 透传）→ `GitHostError(kind=unsupported_provider)` |

为什么两道都要：启动期门保证"部署配置错早暴露"；调用点门保证"哪怕运行期传入未实现平台名也不会拿到一个 gitee provider"——后者是静默回退最可能的入口（既有 `E4_PR_PROVIDER` 门语义与此一致，本轮统一收敛到注册表）。

### 3.4 传输语义与 `main.py` 对齐（但不改动它）

Provider 的独立传输对齐 `backend/app/main.py` 既有语义：GET token 走查询参数、POST token 走请求体、`timeout=12s`、401/403 → `auth_expired`、其余 HTTP → `field_error`、网络失败 → `network_timeout`。差异仅一处（有意修正）：既有 `gitee_api_get` 对"已带查询串的 path"拼 token 会形成第二个 `?`（token 参数失效）；Provider 的 `_send` 正确区分 `&`/`?` 拼接。团队版 `main.py` 链路本轮冻结不动，语义修正只存在于 Provider（未来迁移时自动继承）。

### 3.5 本地契约证据策略（零外网锁真实语义）

「不调真实平台」与「证据必须真实」不是矛盾的：

1. **真实 socket stub**：127.0.0.1 `ThreadingHTTPServer`（随机端口）承认真实 HTTP 往返——scripted 响应队列（按序消费）+ 路由表 + 请求捕获（method/path/query/body/headers），断言直接读捕获到的真实请求；
2. **真实 git 二进制**：本地 bare 仓库作为 origin，`ensure_mirror → checkout_commit → commit_changes → push_head_branch` 全链路真跑，比对 bare 侧分支 sha；
3. **真实子进程**：fail-closed 与注册表拒绝均在子进程中验证退出码与 stderr；
4. **recorder 采纳链**：包裹委托目标函数，验证 `release_service → registry → GiteeProvider → gitee_pr_service` 的调用确实发生且参数契约正确。

### 3.6 TDD 红灯修正如实记录

实现首次跑绿前，专项测试红 6 条：其中 5 条源于**测试自身缺陷**——stub 路由匹配把 `/repos/{owner}/issues` 的路径段数写错（去掉 `/api/v5` 前缀后建 Issue 是 3 段、列表 4 段、详情 5 段、评论 6 段），已修正为精确段数+位置匹配；修正后另有 1 条让"空页停"用例从"偶然通过"变为真正断言分页行为。**未发生「改断言迁就实现」**：26 条断言全部保留原契约语义。

## 4. 关键代码说明

### 4.1 契约层 —— `backend/app/services/git_host/contract.py`

- `GitHostProvider`：8 方法 Protocol（parse/remote/mask/branch/issue×3/snapshot/PR）；
- `GitHostError.__init__(message, *, kind)`：带分类的运行时异常，`kind` 供上层分流（上抛 vs 降级）；
- `redact(text, token)`：先擦除已知 token 字面量，再按 `//oauth2:<token>@` 与 `access_token=<token>` 形态结构掩码——第三方回显 token（stub 故意回显的用例）也被打码。

### 4.2 Gitee 实现 —— `backend/app/services/git_host/gitee.py`

- `_send`：GET/POST 双语义传输核心（§3.4），异常→`kind` 映射；
- `create_issue`：owner 作用域端点 `POST /repos/{owner}/issues` + 请求体 `repo` 参数（Gitee 端点坑的既有结论，`main.py（create_gitee_issue）` 注释同源）；
- `find_issue_by_marker`：`_MAX_DEDUPE_PAGES=6` / `_PAGE_SIZE=100`，auth 上抛、非 auth 与未命中返回 None；
- `fetch_issue_snapshot`：labels 取 `name` 过滤无效项、评论用户名兼容 `name`/`login`/缺省"未知用户"（与 `main.py（fetch_gitee_issue_snapshot）` 归一化口径一致）；
- `create_pull_request`：§3.2 委托约束的实现点。

### 4.3 注册表 —— `backend/app/services/git_host/registry.py`

`get_git_host_provider(name=None)`：`None` 时读 `load_runtime_settings().git_host_provider`；归一化后非 gitee 抛 `unsupported_provider`（消息含请求值与"绝不静默回退 gitee"语义）。

### 4.4 配置层 —— `backend/app/core/runtime_config.py`

新增 `git_host_provider: str` 字段 + `_git_host_provider()`（§3.3 启动期门），位于 frozen dataclass 末尾、构造 kwargs 末尾——既有字段顺序与语义零改动。

### 4.5 PR 服务 —— `backend/app/services/gitee_pr_service.py`

`_post` 与 `create_gitee_pull_request` 增加可选 `api_base`（默认 `None` = 平台地址，零行为变化；非默认才向 `_post` 透传）——使 PR 链路能对本地 stub 真实发 HTTP，同时不破坏既有精确签名桩。

### 4.6 采纳接缝 —— `backend/app/services/enterprise_release_service.py`

`create_runtime_pr`：幂等检查后 `get_git_host_provider(os.environ.get("E4_PR_PROVIDER") or None)`，结果从 `PullRequestRef` 字段组装（键集合与既有完全一致：`provider/id/number/html_url/state` + 推送信息 + `task_id/idempotency_key/created_at`）。`gitee_pr_service` 模块引用**保留**（注释说明）：既有测试经 `release_service.gitee_pr_service` 定位注入目标，删掉会破坏补丁面。

### 4.7 验证 harness —— `backend/scripts/opt15_verify.py`

A1-A9（真实 socket stub：契约/仓库语义/分支/建 Issue/分页四态/错误分类/脱敏/快照/PR）、B1（真实 git 本地全链路）、C1-C3（子进程 fail-closed 与注册表拒绝）、D1（recorder 采纳链 + token 不落盘）、E1-E2（pytest 门禁），16 项断言全部来自实际返回值与退出码。

## 5. 测试

### 5.1 专项门禁（26 passed）

```bash
cd backend && .venv/Scripts/python.exe -m pytest tests/test_opt15_git_provider_contract.py -q
# 26 passed in 24.60s
```

- `backend/tests/test_opt15_git_provider_contract.py`（26）：契约 conformance 1 + 仓库语义 4（多形态解析/非法拒绝/远程构造/掩码）+ 传输 19 条（分支 head 正常与畸形、500 分类、建 Issue 契约、查重分页四态、auth 上抛与非 auth 降级、快照归一化与畸形、脱敏、断网 kind、PR stub 契约、委托精确签名兼容）+ 注册表 3（默认/归一/双重拒绝）+ 启动期子进程 2。

### 5.2 采纳兼容回归（64 passed, 2 skipped）

```bash
cd backend && .venv/Scripts/python.exe -m pytest \
  tests/test_pr_creation.py tests/test_opt06_agent_tool_security.py tests/test_enterprise_e4_agents.py -q
# 64 passed, 2 skipped, 1 warning in 4.11s（2 skip 为真实 Gitee E2E 默认门控）
```

补丁链三形态全部保持生效：`gitee_pr_service._post` 替换（test_pr_creation）、`release_service.gitee_pr_service.create_gitee_pull_request` 替换（test_opt06 / test_enterprise_e4_agents）、`release_service.create_runtime_pr` 整体替换（test_opt06 另有用例）。

### 5.3 全量回归（backend 全套）

```bash
cd backend && .venv/Scripts/python.exe -m pytest -q
# （终稿，第二轮）1055 passed, 17 skipped, 4 warnings in 1338.55s (0:22:18)
# PYTEST_EXIT=0（= OPT-14 基线 1029 + 本轮新增 26）
```

**首轮红灯如实留档（环境性假红，非本轮代码路径）**：首轮全量 `3 failed, 1052 passed, 17 skipped`（1424.17s，PYTEST_EXIT=1）——3 处失败全部为 OPT-10 子进程用例（`test_parsing_config_env_override_subprocess`、`test_parsing_config_invalid_falls_back_and_clamps`、`test_ablation_cli_quick_end_to_end`），错误一致为子进程内 `OpenBLAS error: Memory allocation still failed after 10 retries`（整机内存压力下的资源性分配失败；本轮变更未触碰解析配置/消融/数值库任何代码路径）。隔离复跑三用例 `3 passed in 84.17s`（EXIT=0）后，第二轮全量复跑全绿（上）。首轮原始输出留档 `assets/opt15/opt15_full_pytest_run1_red.txt`，隔离复跑记录 `assets/opt15/opt15_opt10_isolated_rerun.txt`。

## 6. 真实运行证据（2026-10-16）

### 6.1 harness 命令与结果（16/16）

```bash
cd backend && .venv/Scripts/python.exe -m scripts.opt15_verify
# [opt15-verify] 16/16 checks passed
# 报告: backend/.data/opt15-exp/opt15_evidence.json
```

检查面：A1-A9（真实 socket stub HTTP）、B1（真实 git 二进制本地全链路）、C1-C3（fail-closed 子进程）、D1（采纳链）、E1-E2（pytest 门禁）。

### 6.2 关键证据（摘自 harness 报告）

```text
# A4 建 Issue 契约（真实 HTTP 捕获）
path: /api/v5/repos/{owner}/issues        # owner 作用域
repo_param: {repo}  labels: bug,sync       # 仓库名走请求体；labels 逗号连接
token_in_body: true  token_not_in_url: true

# A5 查重分页四态（真实 HTTP 捕获的页码序列）
hit:      number=I00104  pages=["1","2"]          # 命中即停
empty:    result=null    pages=["1"]              # 空页停
continue: number=I00203  pages=["1","2","3"]      # 满 100 条续翻
cap:      result=null    pages=["1","2","3","4","5","6"]  # 上限 6 页

# A6 错误分类（一次全过）
malformed=response_invalid  http_500=field_error
auth_find=auth_expired      non_auth_find=null     network=network_timeout

# A7 脱敏：stub 故意回显 token，异常消息仍为（节选）
kind=auth_expired  message="bad ***; access_token=*** https://oauth2:***@gitee.com/x.git"

# B1 本地 git 全链路（真实 git 二进制）
local_path_passthrough: true
base_sha -> new_sha -> pushed_sha: 一致（bare 侧 refs/heads/agent/opt15/local-e2e == 新提交 sha）

# D1 采纳链（recorder）
provider=gitee  number=PR-OPT15  recorded.token 已置 ***
token_leaked_in_result: false  token_leaked_in_file: false
```

### 6.3 fail-closed 证据（子进程）

```text
# C2 GIT_HOST_PROVIDER=gitlab
RuntimeError: GIT_HOST_PROVIDER=gitlab 未实现（GitHub/GitLab 适配规划中）：fail-closed 拒绝启动，
绝不静默回退 gitee 托管。见 docs/project-development/127。
退出码: 非 0（断言 returncode != 0 且 stderr 含 GIT_HOST_PROVIDER / fail-closed）

# C3 注册表显式请求 github
GitHostError(kind=unsupported_provider)：代码托管平台 github 未实现（GitHub/GitLab 适配规划中，
GIT_HOST_PROVIDER 配置）：fail-closed 拒绝，绝不静默回退 gitee 托管。
```

### 6.4 证据文件清单

- `docs/project-development/assets/opt15/opt15_evidence.json`：harness 全量报告（16 项检查完整 evidence + validation_status 边界声明）；
- `docs/project-development/assets/opt15/opt15_pytest_contract.txt`：专项 pytest 原始输出（26 passed in 24.60s）；
- `docs/project-development/assets/opt15/opt15_pytest_compat.txt`：采纳兼容回归原始输出（64 passed, 2 skipped）；
- `docs/project-development/assets/opt15/opt15_full_pytest.txt`：全量 pytest 终稿原始输出（第二轮：1055 passed, 17 skipped, EXIT=0，见 §5.3）；
- `docs/project-development/assets/opt15/opt15_full_pytest_run1_red.txt`：全量 pytest 首轮原始输出（3 failed，OPT-10 三用例 OpenBLAS 内存分配环境性假红，如实留档，见 §5.3）；
- `docs/project-development/assets/opt15/opt15_opt10_isolated_rerun.txt`：三用例隔离复跑记录（3 passed in 84.17s，EXIT=0）；
- `docs/project-development/assets/opt15/opt15_doc_consistency.json`：文档检查器 JSON 输出（75 份文档 0 违规，退出码 0）。

**关于截图**：本任务为纯后端契约 + 服务层交付，无前端 UI 变更，故无界面截图；上列证据均为真实运行的命令输出与机器可读报告（真实 127.0.0.1 HTTP / 真实 git 二进制 / 真实子进程退出码），不含任何示意图。

## 7. 生产边界与未采集项（忠实报告）

| 项 | 状态 |
| --- | --- |
| GitHub / GitLab 实现 | **未实现**（启动期 + 注册表双重 fail-closed 已验证；契约面已就绪） |
| 真实 Gitee 远端写入（PR/Issue） | **not_collected**：本轮零真实远端写入（stub + 本地 bare 仓库替代）；真实写入按「逐项确认」执行 |
| GitHub/GitLab API 字段差异映射 | **not_collected**（无实现，无采样） |
| 团队版 Issue 链路迁移到 Provider | 未迁移（行为冻结；大量既有测试按 `main.gitee_api_*` 桩） |
| 前端 UI | 无变更（无浏览器路径可验） |
| 多 Provider 并行/动态切换 | 未做（注册表单值语义） |

## 8. 相关源码文件位置

| 文件 | 角色 |
| --- | --- |
| `backend/app/services/git_host/contract.py` | 契约 Protocol + 数据类 + `GitHostError` + `redact`（新文件） |
| `backend/app/services/git_host/gitee.py` | Gitee 实现：传输 + 委托（新文件） |
| `backend/app/services/git_host/registry.py` | 注册表：默认 gitee / 显式非 gitee fail-closed（新文件） |
| `backend/app/services/git_host/__init__.py` | 包导出（新文件） |
| `backend/app/core/runtime_config.py` | `git_host_provider` 字段 + `_git_host_provider` 启动期 fail-closed |
| `backend/app/services/gitee_pr_service.py` | `_post` / `create_gitee_pull_request` 增加可选 `api_base`（默认零行为变化） |
| `backend/app/services/enterprise_release_service.py` | `create_runtime_pr` 采纳注册表 + Provider（保留 `gitee_pr_service` 补丁面） |
| `backend/app/services/repo_mirror.py` | 仓库解析/鉴权远程/掩码（Provider 委托源，未改） |
| `backend/app/main.py` | `gitee_api_get`/`gitee_api_post`/`create_gitee_issue`/`fetch_gitee_issue_snapshot`（团队版链路，未改） |
| `backend/app/enterprise_runtime/agents/tools.py` | `_draft_pr` 工具（调用方，未改） |
| `backend/tests/test_opt15_git_provider_contract.py` | 本地契约测试 26 条（新文件） |
| `backend/scripts/opt15_verify.py` | 验证 harness 16 项（新文件） |

## 9. 联调注意点

1. **`GIT_HOST_PROVIDER` 非 gitee 会让所有进程拒绝启动**：部署脚本/CI 改动前先自检 `python -c "from app.core.runtime_config import load_runtime_settings as L; print(L().git_host_provider)"`；
2. **两个 provider 变量的关系**：`E4_PR_PROVIDER`（E4 专项，显式名优先）→ 注册表；未设时读 `GIT_HOST_PROVIDER`（全局）。两者都只会接受 `gitee`，非 gitee 一律拒绝而非回退；
3. **补丁面契约（写测试必读）**：PR 委托链上可注入的合法锚点是 `release_service.gitee_pr_service`（模块引用）、`gitee_pr_service.create_gitee_pull_request`、`gitee_pr_service._post` 三处；Provider 保证按模块属性在调用时读取，且默认不追加 `api_base` kwarg；
4. **`api_base` 仅限本地契约测试注入**：生产路径永远 `None`（平台地址），且 token 永不进入异常消息/日志——新增 provider 代码时沿用 `redact()`；
5. **未来适配 GitHub/GitLab 的落地顺序**（本次仅备契约）：先实现新 Provider 类 → 注册表放行名称 + 运行时配置放行 → 迁移团队版 `main.py` Issue 链路 → 最后才允许真实远端写入（逐项确认）；
6. **Provider 不可用于绕过写入门**：真实推送/建 PR 的前置门（`ALLOW_SCRATCH_REAL_CHANGE`/scratch 白名单/幂等键）在 `enterprise_release_service` 保留，Provider 只是传输与契约层。