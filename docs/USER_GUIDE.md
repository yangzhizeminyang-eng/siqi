# 司契（Siqi）使用说明书

> 面向本地实际操作验收。本文覆盖 MVP 与团队版的启动、登录、核心操作、异常处理和验收记录。
>
> 当前说明基于 2026 年 9 月 9 日本地环境整理。云部署不在本文范围内。

## 1. 先看清两个运行入口

本项目当前可以同时运行 MVP 和团队版，但两套环境必须区分：

| 版本 | 前端地址 | 后端地址 | 数据与队列 |
| --- | --- | --- | --- |
| MVP 本地版 | `http://127.0.0.1:5173` | `http://127.0.0.1:8000` | 默认 SQLite，可使用本地降级 |
| 团队版本地联调 | `http://127.0.0.1:5177` | `http://127.0.0.1:8002` | MySQL、Redis、Celery、Milvus、Docker Runner |

重要：

- 直接打开 `http://127.0.0.1:5173` 时，前端默认把 `/api` 请求代理到 `8000`，这是 MVP 入口。
- 团队版前端要使用 `5177`，并把代理目标设置为 `8002`。
- 后端健康检查地址是 `/api/health`，不是 `/health`。
- 当前已存在的验收数据可以查看，但不要把 `TASK-8801` 当成真实代码变更任务；它的 Diff 标记为 `DEMO_FALLBACK`，系统不会为它创建真实 PR。

## 2. 启动前检查

在项目根目录打开 PowerShell：

```powershell
cd "D:\Enterprise‑Grade AI Agent R&D Collaboration Platform"
```

确认软件环境：

```powershell
docker --version
node --version
npm --version
.\backend\.venv\Scripts\python.exe --version
```

团队版需要确认基础服务：

```powershell
docker ps
```

至少需要确认 Redis、Milvus 相关容器处于运行状态。团队版还需要可用的 MySQL 数据库和 Docker Desktop。

## 3. 启动 MVP 本地版

### 3.1 一键启动

在项目根目录执行：

```powershell
.\start-dev.ps1
```

启动成功后访问：

- 前端工作台：<http://127.0.0.1:5173/>
- 后端接口文档：<http://127.0.0.1:8000/docs>
- 后端健康检查：<http://127.0.0.1:8000/api/health>

健康检查应返回：

```json
{"code":0,"message":"success","data":{"status":"ok"}}
```

### 3.2 分别启动

后端：

```powershell
cd backend
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

另开一个 PowerShell 启动前端：

```powershell
cd frontend
npm install
npm run dev -- --host 127.0.0.1 --port 5173
```

### 3.3 MVP 默认账号

| 角色 | 账号 | 密码 |
| --- | --- | --- |
| 管理员 | `admin@company.com` | `Admin123!` |
| 审批人 | `approver@company.com` | `Approver123!` |
| 开发者 | `developer@company.com` | `Dev123!` |
| 项目负责人 | `lead@company.com` | `Lead123!` |

这些账号只用于本地验收。公开环境必须替换密码、JWT 密钥和应急恢复码。

## 4. 启动团队版本地联调

团队版必须使用真实 MySQL、Redis、Celery、Milvus 和 Docker Runner。下面的命令使用占位符，实际值从团队环境配置中填写，不要把 Token 写进源码或提交到 Gitee。

### 4.1 一键启动团队版前后端

先确认 `backend/.env` 已存在，并已填写团队版的 MySQL 连接、Gitee Token、模型 Key 等本地配置。该文件已被 Git 忽略，不要提交到代码仓库。

之后在项目根目录执行：

```powershell
.\start-team.ps1
```

脚本会自动：

- 注入团队版固定运行参数
- 检查并启动真实 Celery Worker
- 启动后端 `http://127.0.0.1:8002`
- 启动前端 `http://127.0.0.1:5177`
- 将前端 `/api` 请求代理到 `http://127.0.0.1:8002`
- 等待前后端 HTTP 就绪后再输出成功信息

启动成功后直接访问：

<http://127.0.0.1:5177/>

如需先检查配置但不启动服务：

```powershell
.\start-team.ps1 -DryRun
```

另开 PowerShell 验证：

```powershell
$response = Invoke-WebRequest -Uri "http://127.0.0.1:8002/api/health" -UseBasicParsing
$response.Content
```

如需跳过某个组件：

```powershell
.\start-team.ps1 -SkipWorker
.\start-team.ps1 -SkipFrontend
```

如需修改端口：

```powershell
.\start-team.ps1 -BackendPort 8002 -FrontendPort 5177
```

如果首次启动需要执行数据库迁移或恢复较多数据，可延长健康检查等待时间：

```powershell
.\start-team.ps1 -StartupTimeoutSeconds 180
```

### 4.2 团队版日志与 Worker 检查

Worker 由一键启动脚本自动检查或启动。如果需要单独排查 Worker，可在项目根目录执行：

```powershell
cd "D:\Enterprise‑Grade AI Agent R&D Collaboration Platform"
.\deploy\dev-worker.ps1
```

检查 Redis 与 Worker：

```powershell
cd backend
.\.venv\Scripts\python.exe -m scripts.check_redis_worker
```

重点日志：

- `logs/celery-worker.out.log`
- `logs/celery-worker.err.log`
- `logs/team-backend.out.log`
- `logs/team-backend.err.log`
- `logs/team-frontend.out.log`
- `logs/team-frontend.err.log`

如果浏览器访问的是 `5173`，看到的是 MVP 数据，不是团队版数据。团队版必须访问 `5177`。

## 5. 页面与权限

登录后主要页面如下：

| 页面 | 路由 | 主要用途 |
| --- | --- | --- |
| 工作台 | `/dashboard` | 查看任务概览、自动化进度和待处理事项 |
| 任务列表 | `/tasks` | 筛选、查看和管理 Agent 任务 |
| 创建任务 | `/tasks/new` | 使用已有 Gitee Issue 创建任务 |
| 任务详情 | `/tasks/:id` | 查看分析、上下文、计划、Diff、测试和历史参考 |
| 平台需求 | `/requests` | 团队版创建需求草稿并同步 Gitee Issue |
| 创建需求 | `/requests/new` | 填写需求、缺陷、复现步骤和验收标准 |
| 审批中心 | `/approvals` | 审批计划、调整风险或要求重写 |
| Diff 审核 | `/diff/:id` | 查看变更文件、Patch 和执行状态 |
| 任务回放 | `/replay/:id` | 查看事件时间线、执行步骤和审计记录 |
| 索引管理 | `/indexes` | 查看仓库索引状态、重试或重建索引 |
| 项目配置 | `/settings` | 管理项目、仓库、分支、测试命令和安全规则 |
| 账号恢复 | `/account-recovery` | 管理员处理账号恢复申请 |

建议用不同账号验证权限：

- `admin`：可配置项目、仓库、审批、索引和用户。
- `approver`：重点验证审批中心和审批动作。
- `developer`：重点验证创建需求、创建任务、补充上下文和查看自己的任务。
- `lead`：重点验证项目协作和负责人相关权限。

## 6. 推荐实际操作顺序

建议先使用团队版 `admin` 账号检查配置，再用 `developer` 和 `approver` 账号走完整流程。

### 6.1 第一步：检查项目配置

1. 打开“项目配置”。
2. 确认项目存在，例如“支付中台”。
3. 确认仓库地址、默认分支和测试命令。
4. 确认命令白名单包含当前测试命令。
5. 确认敏感路径、风险阈值、最大改动文件数和审批模式。
6. 不要在页面或日志中寻找明文 Gitee Token；系统只展示是否配置、别名和更新时间。

团队版真实执行建议使用：

- `RAG_MOCK_FALLBACK=false`
- `TEST_RUNNER_MODE=docker`
- `USE_CELERY=true`
- `DATABASE_URL` 指向 MySQL

### 6.2 第二步：创建团队需求草稿

1. 登录开发者账号。
2. 打开“平台需求”。
3. 点击创建需求。
4. 填写：
   - 标题：明确说明要改什么。
   - 正文：说明背景、目标和约束。
   - 复现步骤：按顺序填写可操作步骤。
   - 验收标准：每行一条，写成可检查结果。
   - 优先级和标签：按任务实际情况填写。
   - 目标分支和测试命令：使用仓库允许的值。
5. 保存后确认生成 `REQ-*` 编号。

推荐输入示例：

```text
标题：修复支付回调重复入账

正文：
支付渠道在网络抖动时会重复发送同一个回调事件，当前服务可能重复创建入账记录。
需要增加稳定的幂等键校验，并保留重复请求的审计信息。

复现步骤：
1. 使用相同事件编号连续发送两次支付回调
2. 查询订单入账记录
3. 查看任务审计记录

验收标准：
相同事件只能成功入账一次
重复回调返回可接受的 ACK
重复请求写入审计记录
补充重复投递回归测试
```

### 6.3 第三步：同步 Gitee Issue

1. 在需求详情页点击“同步 Gitee”。
2. 等待 Worker 处理。
3. 成功时应看到 Gitee Issue 编号、地址和同步状态。
4. 失败时保留 `REQ-*` 草稿，查看失败原因后更新 Token、仓库权限或网络，再点击重试。

注意：

- 没有有效 Gitee Token 时，不会伪装成同步成功。
- 同一需求重复点击时，系统使用锁和幂等逻辑，避免重复创建 Issue。
- 建议使用测试仓库或专用测试 Issue，不要直接对生产仓库反复操作。

### 6.4 第四步：从同步后的需求创建 Agent 任务

1. 在需求详情页点击“创建 Agent 任务”。
2. 确认任务生成 `TASK-*` 编号。
3. 打开任务详情，观察状态从创建、拉取 Issue、索引、分析逐步变化。
4. 团队版异步链路正常时，接口会先返回排队或处理中状态，Worker 在后台继续执行。
5. 刷新任务详情，确认页面展示的是数据库中的最新状态。

常见主链路：

```text
REQ 草稿
  -> Gitee Issue 已同步
  -> Agent Task 已创建
  -> Issue 快照已拉取
  -> 代码索引已复用或重建
  -> AI 分析完成
  -> 修改计划生成
  -> 自动审批或人工审批
  -> Diff 生成
  -> Docker 测试
  -> PR 描述
  -> 回放摘要
  -> 历史任务复用索引
```

### 6.5 第五步：处理上下文不足

如果任务出现 `context_missing` 或“需要补充信息”：

1. 打开任务详情中的人工处理区域。
2. 补充明确的目标文件、业务约束或验收条件。
3. 提交后点击恢复或重新分析。
4. 确认任务回到分析链路，而不是直接标记失败。

### 6.6 第六步：检查分析与修改计划

重点检查：

- AI 分析是否引用 Issue、仓库规则和代码上下文。
- 风险等级是否合理。
- 影响范围是否具体到模块或文件。
- 是否列出不确定性和待确认问题。
- 修改计划是否包含目标、文件范围、步骤、测试建议和回滚方案。
- 页面是否展示历史相似任务来源。

历史任务只能作为参考：

- 可以查看相似任务标题、摘要、来源和相似度。
- 不会自动复制旧计划、旧 Diff 或旧结论。
- 历史索引失败不应阻断当前任务主链路，但应记录失败原因。

### 6.7 第七步：审批

低风险任务在满足策略时可能自动推进。高风险任务进入“审批中心”：

1. 用审批人账号登录，打开审批中心。
2. 查看任务背景、风险原因、文件范围、测试命令和回滚方案。
3. 选择：
   - 通过：允许生成 Diff。
   - 要求重写：填写意见并生成新的计划版本。
   - 拒绝：填写原因，终止当前任务版本。
4. 刷新任务详情，确认审批事件和下一步动作已写入回放。

观察者账号只能查看，不能审批。

### 6.8 第八步：查看 Diff

在任务详情或 Diff 审核页检查：

- Diff 是否对应当前计划版本。
- 变更文件是否超出计划范围。
- 是否命中敏感路径。
- 是否显示真实执行模式。
- 是否标记 `DEMO_FALLBACK`。

判断标准：

- `execution_mode=REAL` 且 `is_demo_fallback=false`：可以进入真实测试和 PR 后续流程。
- `DEMO_FALLBACK`：仅为演示产物，不代表真实修改了仓库代码，不应据此创建真实 PR。

### 6.9 第九步：执行测试

1. 确认测试命令来自任务、仓库或项目默认配置。
2. 确认命令命中白名单。
3. 团队版确认测试由 Docker Runner 执行。
4. 查看退出码、标准输出、错误输出、耗时和 Runner 模式。
5. 测试失败时，确认 Diff 被保留，并使用有限次数重试。
6. 危险命令被拦截时，确认系统没有执行命令，而是记录阻断原因。

### 6.10 第十步：查看 PR 描述与任务回放

测试完成后：

1. 打开 PR 描述区域。
2. 检查背景、变更摘要、影响范围、测试结果、风险说明和回滚说明。
3. 使用复制按钮复制 PR 描述。
4. 打开任务回放，按时间线检查 Issue、索引、分析、计划、审批、Diff、测试和 PR 描述。
5. 任务完成后打开历史复用区域，确认生成摘要并进入索引。

## 7. 历史任务复用验收

团队版已经准备了一条真实历史验收数据：

- 任务：`TASK-TEAM-HIST-0909`
- 状态：`COMPLETED`
- 主题：支付回调重复入账修复
- 索引状态：`INDEXED`
- 向量来源：Milvus

操作方式：

1. 登录团队版。
2. 打开任务列表，搜索 `TASK-TEAM-HIST-0909`。
3. 打开任务详情，确认复用摘要包含 Issue、上下文、分析、计划、Diff、测试、PR 描述和回放摘要。
4. 创建或查看一个相关任务。
5. 在分析或修改计划页面确认出现“历史任务参考”。
6. 打开历史任务详情，确认引用来源清晰。
7. 确认当前任务没有自动套用历史 Diff。

后端也可以用接口检查：

```powershell
$body = @{account="admin@company.com"; password="Admin123!"} | ConvertTo-Json
$session = New-Object Microsoft.PowerShell.Commands.WebRequestSession
Invoke-RestMethod `
  -Uri "http://127.0.0.1:8002/api/auth/login" `
  -Method Post `
  -ContentType "application/json" `
  -Body $body `
  -WebSession $session

$payload = @{query="支付回调重复入账"; limit=5} | ConvertTo-Json
Invoke-RestMethod `
  -Uri "http://127.0.0.1:8002/api/history/search" `
  -Method Post `
  -ContentType "application/json" `
  -Body $payload `
  -WebSession $session
```

## 8. 页面验收清单

每次你自己操作时，可以按下面顺序勾选：

- [x] MVP 登录、退出和刷新会话正常。
- [x] 团队版登录、退出和刷新会话正常。
- [x] 工作台能显示任务和待处理事项。
- [x] 任务列表筛选、搜索和详情跳转正常。
- [x] 项目配置可以读取项目和仓库真实配置。
- [x] 平台需求可以保存 `REQ-*` 草稿。
- [x] Gitee 同步成功或失败原因清晰可见。
- [x] 已同步需求可以创建 `TASK-*`。
- [ ] Worker 可以消费索引、Agent、测试和同步任务。
- [ ] 上下文不足时可以补充并恢复。
- [x] 低风险任务可以按策略自动推进。
- [x] 高风险任务进入审批中心。
- [x] 审批通过、拒绝和要求重写均有记录。
- [x] Diff 能区分真实产物和 `DEMO_FALLBACK`。
- [ ] Docker Runner 能返回测试结果。
- [ ] 测试失败可以有限重试。
- [x] PR 描述可以生成和复制。
- [ ] 回放页能显示完整事件和执行步骤。
- [ ] 完成任务后能生成历史复用摘要。
- [ ] 新任务能看到历史相似任务参考。
- [ ] 历史索引失败不会阻断当前任务。
- [x] 观察者无法审批或修改受保护配置。
- [x] 通知、评论和 @ 提醒能够显示并写入记录。
- [x] 服务重启后任务、需求、审批和历史摘要仍可读取。

## 9. 常见问题

### 9.1 打开页面显示的是另一套数据

检查地址和代理：

- MVP：`5173 -> 8000`
- 团队版：`5177 -> 8002`

团队版前端启动前必须设置：

```powershell
$env:VITE_API_PROXY_TARGET = "http://127.0.0.1:8002"
```

### 9.2 访问 `/health` 返回 404

正确地址是：

```text
http://127.0.0.1:8000/api/health
http://127.0.0.1:8002/api/health
```

### 9.3 任务一直排队

按顺序检查：

1. Redis 是否运行。
2. Celery Worker 是否在线。
3. `logs/celery-worker.out.log` 是否有 `received`、`succeeded` 或错误信息。
4. Worker 和 API 是否使用同一套 `DATABASE_URL`、Redis 和团队环境变量。

### 9.4 Gitee 同步失败

常见原因：

- Token 未配置或已过期。
- Token 没有目标仓库权限。
- 仓库地址或 Issue 编号错误。
- 网络请求超时。

系统应保留需求草稿并显示失败原因，不应伪装为同步成功。

### 9.5 出现 `context_missing`

这表示当前 Issue 和代码上下文不足以安全制定修改计划。补充目标文件、业务约束或验收标准后，再恢复任务。

### 9.6 出现 `DEMO_FALLBACK`

这表示当前产物是本地演示或兜底结果，不代表 Agent 已对真实仓库产生代码变更。不要把它当作真实 Diff 或真实 PR。

### 9.7 历史搜索没有结果

检查：

- 团队版是否连接到 `agent_team`。
- `USE_MILVUS=true`。
- `MILVUS_URI` 是否正确。
- `RAG_MOCK_FALLBACK=false` 时，查看 API 或 Worker 的真实错误。
- 历史任务是否已经生成完成摘要并进入索引。

## 10. 当前验证基线

截至 2026 年 9 月 9 日，本地验证结果：

- 后端：`487 passed, 12 skipped`
- 前端：`196 passed`
- 前端生产构建：成功
- 团队版 API：`http://127.0.0.1:8002/api/health` 返回 200
- MySQL、Redis、Milvus、Docker Runner：已完成真实联调
- Celery Worker：已注册任务执行、测试执行、Gitee 同步和历史索引任务

这代表 MVP 与团队版的本地核心主链路已经跑通。真实 Gitee PR 和云部署仍然需要单独的真实环境验收。

## 11. 下一阶段：源码开源化整理计划

你完成本说明书的实际操作后，再进行源码整理。计划分三步：

1. 盘点仓库：区分业务源码、测试、文档、部署文件、运行日志、临时数据和 IDE 配置。
2. 规范开源结构：补齐许可证、贡献指南、安全政策、变更记录、环境变量模板和 GitHub/Gitee 工程入口。
3. 清理发布内容：排除数据库文件、Milvus 临时文件、日志、构建产物、缓存、`__pycache__`、本地 IDE 配置和任何 Token。

拟整理为：

```text
/
├─ README.md
├─ LICENSE
├─ CONTRIBUTING.md
├─ CODE_OF_CONDUCT.md
├─ SECURITY.md
├─ CHANGELOG.md
├─ AGENTS.md
├─ .gitignore
├─ backend/
├─ frontend/
├─ docs/
├─ deploy/
├─ scripts/
├─ tests/
└─ .github/
```

源码清理涉及删除或移动前，会先列出具体文件清单给你确认，不会直接删除现有内容。
