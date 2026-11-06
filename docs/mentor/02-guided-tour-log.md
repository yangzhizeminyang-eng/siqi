# 带读日志 · 从头到尾熟悉司契（Siqi）

> 对象：小泽 ｜ 起点：2026-09-30 ｜ 方式：讲解 → 提问 → 复述 → 纠正
> 地图参考：[`01-project-master-map.md`](./01-project-master-map.md)
> 记录原则：**只写实测过的事实**，推测和未验证的一律标注。

---

## 1. 路线与进度

| 站 | 主题 | 状态 | 验收方式 |
| --- | --- | --- | --- |
| 0 | 全局定位 | 讲解完成，**等复述** | 用自己的话讲出与 AI 编码工具的本质区别 |
| 1 | 跑起来 | 环境已跑通，**等回报** | 说清 admin vs observer 差异 |
| 2 | 主链路 | 材料已备（`workflow.py` 通读） | 白纸手画主流程图 |
| 3 | RAG 专线 | 未开始 | 讲清 RRF 与四档 rerank 结论 |
| 4 | Agent 双链 | 未开始 | 讲清两套拓扑为何并存 |
| 5 | 治理/安全/多租户 | 未开始，已攒 1 条真实发现 | 讲透纵深防御每一层 |
| 6 | 前端 | 未开始 | 讲清前后端契约与权限展示层 |
| 7 | 交付运维与真实边界 | 未开始 | 主动说出已知限制 |
| 8 | 能力体检 | 未开始 | 12 问答对 10+ |

---

## 2. 本次本地运行的真实形态（可复现）

启动方式：`start-dev.ps1` 的等价手动启动，**数据源指向团队版 MySQL 库**。

| 项 | 实测值 |
| --- | --- |
| 后端 | `http://127.0.0.1:8000`，`/api/health` → 200 |
| 健康详情 | `db.status=ok`（latency 2.5ms，pool size 10）/ `milvus.status=skipped`，`reason=use_milvus=false` |
| 前端 | `http://127.0.0.1:5173`，经 vite 代理 `/api/health` → 200 |
| 数据源 | MySQL `agent_team`：19 任务 / 418 切片 / 17 索引版本 / 53,436 事件 / 1,338 审计 |
| 环境变量 | `DATABASE_URL`=MySQL、`USE_CELERY=false`、`USE_MILVUS=false`、`RAG_MOCK_FALLBACK=true`、`APP_ENV=local` |
| 未启用 | Redis（Celery 走 **eager(dev) 内联降级**）、Milvus（走**显式 fallback**）、Docker Runner |

程序启动时自报的告警（原文保留）：

```text
[startup] APP_ENV=local：检测到 USE_SQLALCHEMY=true 但未配置可用 Redis/Celery
（USE_CELERY=False, CELERY_BROKER_URL=已配置）。长任务将走 eager(dev) 内联降级。
eager 只是本地/test 降级，非异步验收通过证据。生产/团队版请接 Redis + Worker。
```

> 教学价值：这是「降级必须显式、不许谎报」的第一手例证。health 里 Milvus 报 `skipped` 并带 `reason`，
> 而不是假装 `ok`；Celery 降级主动声明「非验收通过证据」。

### 2.1 启动踩到的两个坑（后续复用）

1. `start-dev.ps1` 的 30 秒健康检查窗口对后端偏短：`uvicorn --reload` + LangChain/LlamaIndex 重依赖
   首次导入会超过 30 秒，脚本判定失败并**杀掉自己起的进程**（`start-team.ps1` 的窗口是 90 秒）。
2. `start-dev.ps1` 失败时只杀掉直接子进程，**reloader 孙进程会存活并继续占用 8000**，导致下一次
   启动报 `WinError 10048`。排查时要用 `Get-NetTCPConnection -LocalPort 8000` 找真实占用者。

### 2.2 企业版运行形态（当前形态 · 2026-10-08 切换）

小泽要求改看**企业版（最终形态）**。企业版是唯一**禁止静默降级**的版本，因此不能沿用本地降级跑法。

**为什么不能直接用现成 compose**

| 卡点 | 实测情况 | 处理 |
| --- | --- | --- |
| `agent-enterprise-backend:e101` 镜像 | 是 **E1-01 时期**构建的，HEAD 已到 E7，用它看到的是旧版本 | 弃用镜像，改跑**当前源码**原生进程 |
| `docker-compose.enterprise.yml` 的 `mysql` 网络 | 声明为 external `projectdream_backend_net`，本机不存在；本机 MySQL 是原生进程 | 不动该文件，独立起依赖 |
| 既有 `agent_enterprise_milvus_*` 卷 | MinIO 凭据已写入旧卷，换新密码会启动失败 | 不重建，**复用已在运行的那套** |
| `agent_enterprise` 库 | 不存在 | 新建 + Alembic 迁移 |

**已跑通的企业栈（实测）**

| 组件 | 地址 / 事实 |
| --- | --- |
| 企业后端 | `http://127.0.0.1:8003`，`APP_ENV=enterprise` |
| 企业前端 | `http://127.0.0.1:5178`，vite 代理 → 8003 |
| MySQL | 原生 `127.0.0.1:3306`，独立库 `agent_enterprise`，迁移到 `a7b8c9d0e1f2` |
| Redis | 容器 `agent-enterprise-tour-redis-1`，`127.0.0.1:6379` |
| Milvus | **复用已自启的** `agent-enterprise-milvus-enterprise-1`，`127.0.0.1:19530` |
| Worker | 真实 Celery，`celery@ZE-PC` online，注册 8 个任务（含 `enterprise.agent_execute`）|
| Docker | Desktop 29.3.1 |

`/api/health` 三项依赖全真（对比团队版那次 Milvus 是 `skipped`）：

```json
{"db":{"status":"ok","latency_ms":1.3},
 "milvus":{"status":"ok","uri":"http://127.0.0.1:19530"},
 "redis":{"status":"ok","latency_ms":192.9}}
```

后端启动自报（企业版语义的关键差异）：

```text
[startup] CELERY_BROKER_URL=redis://127.0.0.1:6379/0 已配置：
async 主链路启用，长任务由 Worker 消费（不会内联/静默 eager）。
```

**企业 Milvus 里的真实资产**：42 个 collection，其中
`code_idx_health_middle_73e7e54` **303 行**（E3-06/E3-07 验证用的真实索引）、
`historical_task_summaries_v64` 8 行。

**配置陷阱（已避开）**：`main.py:1850 index_collection_name` 的逻辑是
「有前缀 → `{prefix}_code_{index_id}`，否则 `code_{index_id}`」。
本机真实 collection 名是 `code_idx_health_middle_73e7e54`（**无前缀**），
所以本地跑企业版必须让 `MILVUS_COLLECTION_PREFIX` **为空**；
若照抄 `enterprise.env.example` 的 `enterprise`，会去找
`enterprise_code_idx_health_middle_73e7e54` 而找不到 → 企业版禁止降级，直接抛错。

### 2.3 企业库的数据面（实测）

空库启动后自动播种了**身份层**，业务层为 0：

| 层 | 表 | 行数 |
| --- | --- | ---: |
| 身份 | `users` | 5 |
| 身份 | `tenants` / `organizations` | 1 / 1 |
| 身份 | `tenant_memberships` | 5 |
| 配置 | `projects` / `repositories` | 1 / 1 |
| 业务 | `tasks` | **0** |
| 业务 | `audit_events` | **0** |
| 业务 | `approval_applications` | **0** |
| 业务 | `enterprise_runtime_tasks` | **0** |
| 业务 | `repository_indexes` | **0** |

> 与 E7 试点包 `docs/enterprise/10-pilot/01-demo-data-and-walkthrough.md` §2 描述的
> 「最小数据面」一致：企业版页面初次进入应以**空状态**呈现。
> 已按小泽的选择「只接真实 RAG」补上索引（见 §2.5）。

### 2.4 两套权限体系（企业版核心差异）

同一账号登录企业版后，**同时持有两套互不相干的权限**：

| 体系 | 字段 | 角色示例 | 权限点 |
| --- | --- | --- | ---: |
| 团队角色 | `user.role` | `admin` | 15 |
| 企业角色（租户上下文） | `tenant_context.role` | `tenant_owner` | 14 |

`tenant_owner` 的 14 个企业权限点：
`approval:approve`、`approval:manage`、`approval:submit`、`approval:view`、`audit:view`、
`governance:manage`、`member:manage`、`member:view`、`org:manage`、`org:view`、
`rag:search`、`tenant:context`、`tenant:create`、`tenant:switch`。

这解释了 §5.1 那个「observer 能读审计」的疑点：`require_audit_view` 的第二条放行路径
正是企业 `audit:view`，而团队版 `observer` 在租户上下文里被映射成了 `auditor`。

### 2.5 企业 RAG 真实接入（已完成 · 只接真实数据，不造数）

按小泽的选择「只接真实 RAG」，把团队库里那份**真实索引**搬进企业库。四样东西都用原 id，
否则 Milvus 的元数据过滤对不上：

| 目标表 | 来源（`agent_team`） | 结果 |
| --- | --- | --- |
| `projects` | `proj_health_middle` | 1 行 |
| `repositories` | `repo_health_middle` | 1 行（25 列全量） |
| `repository_indexes` | `idx_health_middle_73e7e54`（commit `73e7e5497add…`，COMPLETED） | 1 行 |
| `code_chunks` | `where index_id='idx_health_middle_73e7e54'` | **303 行** |

collection 名由 `main.py:1850 index_collection_name('idx_health_middle_73e7e54')`
在**无前缀**下算出 `code_idx_health_middle_73e7e54`，与 Milvus 里那份 303 行**完全一致**。

**踩到的坑（真实复现了小泽笔记 §5.1）**：直接写库后调 `/api/enterprise/rag/search` 返回
`404 仓库不存在`。根因是 `enterprise_rag/service.py:37` 读的是 `main.REPOSITORIES`
这个**进程内存注册表**，不是数据库。**重启后端重新水合后即通过**。
这就是「API 与 Worker 双进程 + 内存缓存 vs 数据库真源」的同一根因，只是对象从任务换成了仓库。

**实测结果（真实 303 切片 + 真实 Milvus，非降级）**

`/api/health`：`db ok / milvus ok / redis ok`；`fresh=True`；`variant_count` 1~2。

| 请求 `retrieval_mode` | 结果数 | `counts` | 最高分 | 结果 `retrieval_source` |
| --- | ---: | --- | ---: | --- |
| `bm25` | 2 | `{bm25:2}` | **0.9501** | `bm25` |
| `vector` | 8 | `{vector:8}` | 0.6452 | `vector` |
| `hybrid` | 8 | `{vector:8, bm25:2, hybrid:2}` | 0.2488 | `hybrid` |

**四条可验证结论**

1. **BM25 需要精确词元**。查询「用户健康数据校验 出生年份 身高 体重」（纯中文语义）时
   BM25 召回 **0** 条；查询「validate_name validate_age 用户校验」时 BM25 返回 2 条
   **0.95** 高分命中。→ 这正是 `enterprise_rag/terms.py` 做「技术术语桥接
   （错误码 / 接口路径 / 点分↔下划线）」的动机：把自然语言查询桥成符号名。
2. **`retrieval_source=hybrid` 只在两路都有贡献时出现**（本次 `hybrid:2`）。
   `counts` 的语义见 `service.py:163-167`：统计的是**最终返回结果**里哪一路有贡献，
   不是各路召回总数——**别把它当召回量读**。
3. **不能跨模式比分数**。hybrid 的 0.2488 是 RRF 的 `1/(K+rank)` 累加尺度，
   bm25 的 0.95 是词法匹配尺度，vector 的 0.6452 是向量相似度尺度——三者不可直接比较。
4. **字段名写错会被静默忽略**。payload 字段真名是 `retrieval_mode`
   （`enterprise_rag/models.py:61`）；传 `mode=bm25` 不报错、不提示，
   直接回落到默认 `hybrid`（`service.py:101`）。这属于 DX 隐患：
   企业级接口更应 `extra="forbid"` 或至少在响应里回显被忽略的字段。

### 2.6 本地企业版启动脚本（本次新增）

新增 `deploy/dev-enterprise-local.ps1`，把这次的手工步骤固化为可复现启动：

- 从 `backend/.env` 读 `DATABASE_URL` 并派生企业库，**不写任何密钥**；
- 强制 `APP_ENV=enterprise` + `USE_CELERY=true` + `USE_MILVUS=true`
  + `RAG_MOCK_FALLBACK=false` + `MODEL_MOCK_FALLBACK=false`
  + `MILVUS_COLLECTION_PREFIX=''`；
- **依赖预检**：MySQL 3306 / Redis 6379 / Milvus 19530 任一不可达即 `exit 2`，
  因为企业版不允许降级运行；
- `-InitDatabase` 建库 + Alembic；`-BackendOnly` / `-FrontendOnly` 可拆分。

**编码坑（重要）**：本机 PowerShell 是 **5.1**，它按 ANSI 读无 BOM 的 `.ps1`，
含中文的脚本必须存为 **UTF-8 with BOM**（仓库里 `start-team.ps1`、`deploy/dev-worker.ps1`
都是带 BOM 的）。另外 `$Host` 是 PowerShell 只读自动变量，**不能用作函数参数名**。

---

## 3. 更正记录（导师自纠）

### 3.1 代码规模：首轮数字全部偏低

原因：用了 `Get-Content | Measure-Object -Line`，该命令**跳过空行**。改用 `(Get-Content).Count` 后：

| 项 | 首轮误报 | 修正后 |
| --- | ---: | ---: |
| `backend/app/main.py` | 8,195 | **9,177** |
| 后端 `app/`（106 文件） | 23,305 | **27,685** |
| 前端 `src/`（69 文件，不含测试） | 20,025 | **21,699** |
| 后端测试（77 文件） | 16,423 | **20,539** |
| 前端测试（30 文件） | 未统计 | **8,782** |

### 3.2 `TASK-8817` 的「历史参考」区块

首轮称该任务七个产物区块齐全。实测 `similar_task_refs = 0`，**历史参考区块是空的**。
其余六项均存在（见 §4）。

### 3.3 登录字段与审批接口路径

- 登录请求体字段是 `account`，**不是** `email`（传 `email` 返回 422）。
- 不存在 `/api/approvals`，审批队列真实路径是 `/api/approvals/pending`（其余返回 404 而非 403）。

---

## 4. 两个样本任务的真实产物对照（admin 视角实测）

| 产物 | `TASK-8817`（TESTED，走完整链） | `TASK-8819`（CONTEXT_INDEXED，半途） |
| --- | ---: | ---: |
| `analysis` | ✅ | ❌ |
| `code_contexts` | 5 | 5 |
| `plans` | 1 | 0 |
| `diffs` | 1 | 0 |
| `test_runs` | 1 | 0 |
| `pr_descriptions` | 1 | 0 |
| `similar_task_refs` | **0** | 0 |
| `events` | 15 | 7 |
| `execution_steps` | 10 | 4 |
| `pr_url` | 空 | — |

> `TASK-8819` 是「链路走到一半」的最佳样本：上下文已就位，但还没分析、没计划。
> 对比它和 `TASK-8817`，可以直接看出「产物是随状态逐步长出来的」。

---

## 5. 五角色 × 八接口权限矩阵（真实实测，非文档抄写）

| 角色 | 权限点 | tasks | task detail | replay | approvals/pending | projects | audit-events |
| --- | ---: | --- | --- | --- | --- | --- | --- |
| admin | 15 | 200 | 200 | 200 | 200 | 200 | 200 |
| approver | 8 | 200 | 200 | 200 | 200 | **403** | **403** |
| lead | 9 | 200 | 200 | **403** | 200 | 403 | 403 |
| developer | 10 | 200 | 200 | 403 | 403 | 200 | 403 |
| observer | 6 | 200 | 200 | 403 | 403 | 403 | **200** |

账号：`admin`/`Admin123!`、`approver`/`Approver123!`、`lead`/`Lead123!`、`developer`/`Dev123!`、
`observer`/`Observer123!`（种子见 `backend/app/main.py:1226-1254`）。

### 5.1 实测发现：审计可见性与团队版权限矩阵不一致

**现象**：权限点最少的 `observer`（6 个）能读 `/api/audit-events`（200），
而 `approver`（8 个）、`lead`（9 个）、`developer`（10 个）全部被拒（403）。

**根因**：`backend/app/main.py:1464 require_audit_view` 的放行逻辑是「团队 `project:manage`
**或** 企业 `audit:view` 任一生效」。团队版 `observer` 通过第二条路径放行——因为该账号在企业版
测试里被复用为 `auditor`（见 `backend/tests/test_enterprise_e3_08_rag_console.py:23` 注释
`"auditor": ("observer@company.com", "Observer123!")`）。

**性质判断**：observer 是只读角色，能读审计在语义上合理；但「observer 能读、lead/developer 不能」
在团队版权限矩阵里是**口径不一致**，评审被追问时需要能解释清。留到第 5 站展开。

---

## 6. 真实 API 地图（本次 `/openapi.json` 实测）

**路径总数 97 条**，按前缀分布：

| 前缀 | 数量 | 说明 |
| --- | ---: | --- |
| `/api/tasks` | 39 | 任务主链：创建、分析、计划、审批、Diff、测试、PR、回放、历史 |
| `/api/enterprise` | 24 | 企业版：租户、成员、审批、审计、RAG、Runtime、运维 |
| `/api/repositories` | 8 | 仓库与索引生命周期 |
| `/api/auth` | 7 | 登录、会话、登出、密码恢复 |
| `/api/team` | 5 | 平台需求草稿与同步 |
| `/api/notifications` | 3 | 通知 |
| `/api/approvals` | 2 | 待审批队列、指派候选人 |
| `/api/projects` | 2 | 项目配置 |
| `/api/users` | 2 | 用户列表与详情 |
| `/api/audit-events` | 1 | 审计事件 |
| `/api/dashboard/automation-summary` | 1 | 工作台 |
| `/api/replay` / `/api/history` / `/api/health` | 各 1 | 回放 / 历史复用 / 健康 |

易错点：**索引接口不在 `/api/indexes`**，而是挂在 `/api/repositories/{repository_id}/indexes`。

---

## 7. 第 2 站备用材料：`workflow.py` 骨架（已通读 445 行）

- **9 个节点**（`build_task_workflow`，`workflow.py:386-408`）：
  `prepare_context` → `ai_analyze` → `generate_plan` → `wait_approval` → `generate_diff` →
  `run_test` → `generate_pr_description` → `finish`，外加 `fail`。
- **唯一路由真源**：`_router`（`workflow.py:336`），所有节点共享同一条条件边，出口集合为
  `_SAFE_NODES + ("fail",)`（`:382-383`）。
- **暂停/恢复**：`wait_approval` 落 checkpoint（`:252`，`status="paused"`），审批后按同图恢复。
- **防死循环的两个关键设计**：
  - `prepare_context` 若本轮无状态变化则直接 `finish`（`:361-362`）；
  - `generate_pr_description` 置哨兵 `next_action="pr_description_generated"`（`:316`），
    让 `_router` 在 `TESTED` 下转到 `finish`，避免无限重入。
- **职责边界**（文件头 `:6-12` 自述）：图负责**状态流转 / 路由 / 暂停恢复 / 失败分流**；
  具体业务复用 `main.py` 既有服务函数；Chain/DeepAgents 只负责**模型调用**。三层不互相越界。
- **分析阶段的诚实降级**：`_analyze_with_agent`（`:124`）让 DeepAgent 产出叙述字段，
  任何解析失败都回退规则分析（`provider=mvp-rule-based`），**不改变任务推进**。

---

## 8. 待小泽回报（阻塞第 1 站收尾）

> 注：第 1 站的团队版已按小泽要求关停，下面 1~2 题改为**企业版**对应形式（见 §9.4）。

1. 企业 RAG 面板里用 `validate_name validate_age 用户校验` 分别跑 bm25 / vector / hybrid，
   对照 §2.5 那张表，说清三者差异。
2. admin 与 auditor 的差异表现（入口消失 / 按钮变灰 / 直接跳走 / 接口 403）。
3. 用自己的话说清「本项目与 Cursor 这类 AI 编码工具的本质区别」。

---

## 9. 企业版实测发现（第 5 站素材 · 2026-10-08）

全部为**实测 + 源码对照**，非推测。企业库 `agent_enterprise` 已播种真实索引（§2.5）。

### 9.1 空状态三分类（回答小泽「哪些空得可疑」）

| 端点 | 实测 | 判定 |
| --- | --- | --- |
| `/api/enterprise/approvals` | `[]` | **空但正确**：确实没有审批单 |
| `/api/enterprise/runtime/tasks` | `[]` | **空但正确**：确实没有 Runtime 任务 |
| `/api/enterprise/ops/status` → `dependencies` | `{}` | **空得可疑 = 缺陷**（见 §9.3）|
| `/api/enterprise/members` | 5 | 身份层已播种 |
| `/api/enterprise/governance/operations` | 7 | 7 个受治理操作码 |
| `/api/enterprise/rag/reports` | **15** | 真实评测报告（见 §9.5）|
| `/api/audit-events` | 9 | 8 条 `rag.query` + 1 条越权留痕 |

### 9.2 F1 · 未认证可读的指标端点（信息暴露）

`GET /api/enterprise/ops/metrics` **不带任何 Cookie 返回 200**，其余企业接口一律 401。

暴露内容（实测全量 670 字符）：

```text
agent_http_requests_total 50   agent_http_4xx_total 13   agent_http_5xx_total 0
agent_http_5xx_rate 0  agent_http_latency_avg_ms 321.579
agent_http_status_total{code="200"} 37 / {code="401"} 7 / {code="403"} 3 / {code="405"} 3
agent_dependency_db 1  agent_dependency_redis 1  agent_dependency_milvus 1  agent_dependency_worker 1
```

**代码事实**：`enterprise_ops/routes.py:58 def ops_metrics():` **无 `user: CurrentUser` 依赖**，
而同文件 `:66 def ops_status(user: CurrentUser)` 有。

**可达性**：`deploy/nginx.enterprise.conf:21 location /api/` 把整个 `/api/` 反代给 backend，
因此企业版真实部署（前端 8081 / 443）上该端点是**公网无凭据可达**的。

**是否故意**：是。`docs/enterprise/10-pilot/01-demo-data-and-walkthrough.md` §3.1 在**登录之前**
就调它并期望 200。但**没有任何文档声明这条暴露边界**，也没有 IP 白名单 / 限流 / 独立内部端口。

**性质**：信息暴露，不是数据泄露（无租户数据、无密钥、无代码正文）。
风险在于外部可探测服务存活与四个依赖组件的实时健康，并可长期轮询推断流量与错误率。
**建议（待小泽决策，我未改代码）**：三选一——加 `org:manage` 之类的鉴权；
或走独立内部端口 / 网络 ACL 只允许 Prometheus 抓取；
或明确保留公开但在文档里声明边界并剔除敏感 gauge。

### 9.3 F3 · `ops/status` 的 `dependencies` 恒为空（认证接口反而信息更少）

`enterprise_ops/routes.py:75-76`：

```python
resp = main.api_health() if hasattr(main, "api_health") else None
deps = dict((resp or {}).get("dependencies", {})) if isinstance(resp, dict) else {}
```

两处都错：
1. `main.api_health` **不存在**（`main.py:4809` 的函数名是 `health`，grep `api_health` 全仓仅 2 处命中）；
2. 即使存在，`/api/health` 返回的是信封 `api_success({"status":…, "dependencies":…})`（`main.py:4839`），
   `dependencies` 在 `data` 里，取 `.get("dependencies")` 也必然为空。

**铁证**：`enterprise_ops/metrics.py:37` 的注释写着
「直接调用与 /api/health 相同的依赖检查（**不再依赖不存在的 main.api_health**）」——
**同一 bug 在 `metrics.py` 已修，`routes.py` 漏改**。

**后果**：认证的 `/ops/status` 拿不到依赖健康，而**未认证的 `/ops/metrics` 全暴露**（§9.2）——
两者严重度刚好倒挂。

### 9.4 F2 · Runtime 路由无权限门禁，文档声称的 403 不成立

`enterprise_runtime/routes.py` 的 5 个路由（`:54/63/70/82/94/106`）**均未调用 `require_permission`**，
只构造租户上下文 + `list_by_tenant` 做租户隔离。

**实测**：observer（企业角色 `auditor`，权限仅 6 个：
`approval:view`、`audit:view`、`member:view`、`org:view`、`tenant:context`、`tenant:switch`，
**无任何 Runtime 相关权限**）调 `GET /api/enterprise/runtime/tasks` → **200**。

**与文档矛盾**：`docs/project-development/111-enterprise-e5-01-frontend.md` 与
`PROJECT_PROGRESS.md` 相关表述称「auditor 直输 Runtime 重定向且**后端保留 403**」。
实测后端 **没有** 403；前端才是唯一屏障（未登录时后端给 401，登录后任何租户成员都能列 Runtime 任务）。

### 9.5 F4 · Runtime 前端守卫权限语义错配

`frontend/src/router/index.ts:60` 与 `frontend/src/components/AppShell.vue:127` 均为
`permission: ['rag:search']`。Runtime 控制台的可见性被绑在 **RAG 检索权限**上，无语义依据
（前端与侧栏内部一致，所以行为自洽，属**设计耦合**而非行为 bug）。

### 9.6 F5 · 角色矩阵文档与代码不一致（auditor 审批）

E7 试点包 §5 角色矩阵写 auditor 审批列为「—」；
代码授予 auditor `approval:view`，实测 `GET /api/enterprise/approvals` → **200**。
「—」应理解为「不能 approve」，而非「不能 view」。

### 9.7 其他两处小问题

- **F6 路径泄露**：`/api/enterprise/rag/reports` 响应含 `reports_dir`，
  值为服务器绝对路径 `…\backend\evaluation\enterprise_rag\reports`。
- **F7 报告投影不完整**：15 份报告里多数 `generated_at` / `index_id` 为 `null`，
  与全景图 §6 记的「`config_snapshot` 仍为空对象」属同一类报告契约未收口问题。

### 9.8 测量更正（导师自纠）

我先前用「`data` 是数组则取 `.Count`」的通用逻辑计数，
而 `/api/enterprise/rag/reports` 的 `data` 是**对象** `{reports:[…], reports_dir:…}`，
于是被算成 1。**实际是 15 份**。结论：探针脚本必须按端点分别解析，不能一把尺子量所有接口。

### 9.9 顺带验证成立的两件事

- **审计确实落库且不含代码正文**：8 次 RAG 检索 → 8 条 `rag.query`，
  摘要形如 `企业 RAG 查询 project=… repo=… results=8 fresh=True`，无代码正文。
- **越权留痕有效**：auditor 调 `/api/enterprise/governance/operations` → 403，
  同时写入 `tenant.tenant.denied`：「缺少租户权限: action=tenant.permission.governance:manage」。
  但注意：`/ops/status` 与 `/rag/reports` 的 403 **没有**留痕——**越权审计并非全端点覆盖**。

### 9.10 站在第 3 站的顺带收获：报告列表本身就是教材

15 份报告里 `e3_07_health_middle_quality_tuned_hybrid_gate.json` 是
`retrieval_mode=hybrid` 且 **`needs_improvement=false`**；
而同一数据集、同一 commit 下 `retrieval_mode=bm25` 的报告全是 `needs_improvement=true`。
**同一份数据，换个模式结论就翻转**——这正是全景图 §6 强调「两者不可混用」的现场证据，
也印证了 §4.4 缺口 4。`/api/enterprise/ops/status` 同时证实
`rerank_enabled=false`、`cross_encoder_enabled=false`、`symbol_boost=1.0`，
即全景图 §4.4 缺口 1「实测最优配置 ≠ 生产默认配置」仍然存在。

---

## 10. 第 4 站材料：企业 6-Agent Runtime（本次备齐）

### 10.1 实证：E4 专项测试在本机通过

```text
tests/test_enterprise_e4_runtime.py + tests/test_enterprise_e4_agents.py
→ 54 passed in 4.72s
```

安全性说明：`tests/conftest.py:55-67` 默认 `USE_SQLALCHEMY=false` 并 `delenv`
掉 `LLM_API_KEY` / `ZHIPU_API_KEY` / `REDIS_URL`，**跑测试不碰企业库、不调真实模型、不写审计**。

### 10.2 两条链，别混（`models.py:50-76`）

| `workflow_version` | 步骤序列 |
| --- | --- |
| `1`（旧，兼容既有任务/旧库/旧测试） | `context_fetch` → `enterprise_rag_analysis` → `plan_generation` → `execution` → `test_execution` |
| `2`（企业 6-Agent） | `requirement_agent` → `repository_research_agent` → `planner_agent` → `code_change_agent` → `review_security_agent` → `test_release_agent` |

`steps_for_workflow(version)`：`ver <= 1` 返回旧 5 步，否则返回 `AGENT_WORKFLOW_STEPS`。

### 10.3 三个状态机（`models.py:21-47`）

- `RuntimeTaskStatus`（8）：`queued` / `running` / `waiting_approval` / `waiting_human` /
  `retrying` / `succeeded` / `failed` / `cancelled`
- `RuntimeStepStatus`（7）：`pending` / `running` / `paused` / `succeeded` / `failed` /
  `skipped` / `waiting_human`（**比任务级多 `paused` 与 `skipped`**）
- `FailureKind`（5）：`retryable` / `non_retryable` / `needs_approval` / `needs_human` / `environment`
  ——**失败先分类再决定动作**，这是 `orchestrator.py` 分流的依据。

常量：`MAX_REPAIR_ROUNDS = 2`（`graph.py:20`）；重试上限 5（`orchestrator.py:620`）。

### 10.4 6 个 Agent 的硬隔离矩阵（`agents/agent_definitions.py`，全部静态声明）

| Agent | 工具白名单 | 可读产物 | 可写产物 | 上下文域 |
| --- | --- | --- | --- | --- |
| `RequirementAgent` | `read_issue` / `read_comments` / `read_history` | **（空）** | `Requirement` | `CTX_ISSUE` |
| `PlannerAgent` | 见 `:83-100` | — | `Plan` | — |
| `CodeChangeAgent` | `workspace_read` / **`workspace_write`** / `build_diff` | `Plan` / `CodeContext` | **`Patch` / `Diff`** | `CTX_REPOSITORY` |
| `ReviewSecurityAgent` | `read_plan` / `read_diff` / `workspace_read` / `read_deps` / `read_security_policy`（**全只读**） | `Plan` / `Diff` / `Patch` | `Review` / `SecurityRisk` | `CTX_SECURITY` |
| `TestReleaseAgent` | `read_diff` / `workspace_read` / **`run_test`** / `draft_pr` / `collect_evidence` | `Diff` / `Review` | `TestReport` / `PRDescription` / `ReleaseEvidence` | `CTX_REPOSITORY` |

**四个最值得讲的点**

1. **`RequirementAgent` 的 `allowed_read_artifact_types=()`（空元组）**——
   它在结构上**读不到任何产物**，工具只有 3 个只读 Issue 类工具。不是"叫它别碰代码"，是**它没有碰代码的能力**。
2. **唯一能写文件的是 `CodeChangeAgent`**，且 prompt 明写「仅当 PlanArtifact **已审批通过**后方可执行」，
   可读产物限定为 `Plan` / `CodeContext`——**审批门禁与能力边界双重约束**。
3. **`ReviewSecurityAgent` 的工具全是只读**，所以"审查发现问题"只能走
   `decide_next_agent` 的 `back_code_change` 回退，**它自己改不了代码**。
4. **`TestReleaseAgent` 有第三层白名单**：`test_whitelist_only_tools={TOOL_RUN_TEST}`
   ——`run_test` 除了工具白名单，还额外受**测试命令白名单**约束
   （prompt 明写只允许 `pytest` / `python` / `echo` 开头，禁用 `test`/`grep`/`find`/`cd`/管道）。

另外每个 prompt 都写了**一次性执行协议**（最多读 3 个文件、每文件最多写一次、
`build_diff` 只调一次、拿到结果立即返回 JSON、禁止全仓扫描）——
这是**用工程手段防 Agent 失控循环**，不是靠模型自觉。

### 10.5 架构观察：两张图并存，生产只跑其中一张

| 图 | 入口 | 编译名 | 生产是否调用 |
| --- | --- | --- | --- |
| 完整 6-Agent 拓扑（含条件边、`NODE_APPROVAL`） | `build_agent_graph()` → `graph_for_workflow(2)` | `enterprise_agent_flow` | **否**——全仓仅 `tests/test_enterprise_e4_agents.py:290-291` 调用 |
| 单 `router` 节点图 | `_build_router_graph()` → `route_via_graph()` | `agent_router` | **是**——`orchestrator.py:643` 每步真实 invoke |

两者共享 `decide_next_agent()`，所以**不会出现状态机分歧**（C3 原则成立）。
但这是**可维护性风险**：改 `build_agent_graph()` 的边**不会改变实际行为**，
而 `graph.py:4` 的表述「LangGraph（`graph_for_workflow` v2）= v2 的唯一流程拓扑/路由来源」
容易让人误以为拓扑图在生产路径上跑。

**精确定性**：这是**刻意的分工**，不是 bug——`orchestrator.py:630` 注释写明
「由编好的 LangGraph 路由器决定下一步（C3/#6：决策与下一节点都由图计算）」。
按约束 #6，Orchestrator 不得用 `workflow_steps()[index+1]` 手动模拟主路由。

### 10.6 编排循环（`orchestrator.py:630-686`，实测读通）

1. 步骤成功后，从 `output` 提取判定字段（`review_security_agent` → `approved`；
   `test_release_agent` → `passed`）；
2. 调 `route_via_graph(...)` 拿到 `(decision, next_step)`——**决策与下一节点都由 LangGraph 算**；
3. `decision == "approve_wait"` → 建 E2 审批单 `_create_runtime_approval`（幂等）→
   释放租约 → `WAITING_APPROVAL` 停机；
4. `decision == "back_code_change"` → `repair_round += 1` →
   `_requeue_from(...)` 重置 `code_change` / `review_security` / `test_release` 三步 →
   超 `max_repair_rounds` 则 `WAITING_HUMAN` + 写 `runtime.task.waiting_human` 审计；
5. `decision == "human_wait"` → 不再投递新 Agent，直接 `WAITING_HUMAN` + 审计。

---

## 11. 第 5 站材料：治理 · 安全 · 多租户（本次备齐）

### 11.1 两套风险模型，千万别混

| 版本 | 等级 | 定义位置 |
| --- | --- | --- |
| 团队版 | `low` / `medium` / `high` | `services/approval_policy.py:16 _RISK_RANK` |
| 企业版 | `L0` / `L1` / `L2` / `L3` / `L4` | `governance/risk.py:7 RiskLevel` |

企业版五级语义（`risk.py:9-15` 原文归纳）：
`L0` 只读默认允许 / `L1` 可逆低风险允许或记录 / `L2` 影响项目·任务·知识库·代码资产需审批 /
`L3` 影响生产·发布·批量数据需**多人审批 + 测试门禁** / `L4` 租户安全·权限·密钥需**安全管理员 + 二次确认**。

### 11.2 更正全景图：团队版计划门禁实际是 **7 个判定**，不是 6 个

`services/approval_policy.py:82 evaluate_plan_gate` 返回 `(decision, reason)`，
`decision ∈ {auto_approve, require_manual}`，**任一命中即转人工**：

| # | 判定 | 代码位置 |
| --- | --- | --- |
| 1 | 计划风险等级 > 仓库 `risk_threshold` | `:90-91` |
| 2 | 计划结构不完整（`validate_plan_structure`） | `:93-95` |
| 3 | 上下文不 fresh（`plan_context_gate`） | `:97-99` |
| 4 | 存在未回答的阻塞问题（`q.blocking and not q.answered`） | `:101-104` |
| 5 | 测试命令未命中仓库白名单（`command_allowed`） | `:106-107` |
| 6 | **敏感文件独立复核**（`_sensitive_file_hits`） | `:109-111` |
| 7 | **危险命令独立复核**（`_dangerous_command_hits`） | `:113-115` |

全景图 §4.5 把第 6、7 条合并成「命中敏感文件或危险命令」一条表述为「六个条件」——
**代码事实是 7 个判定**。对外讲建议按「5 个常规 + 2 个独立复核」组织，更准确也更好记。

**第 6、7 条为什么叫「独立复核」**（`:109`、`:113` 注释原文）：
「不依赖索引过滤 / 执行期拦截」——即前面某一层被绕过时，审批门这里**还会再拦一次**。
这是纵深防御的正确姿势，也是这套门禁最该讲的设计点。

**敏感文件匹配口径**（`:18-21`）：13 个子串
`.env` / `secret` / `credential` / `id_rsa` / `password` / `token` / `.pem` / `.key` /
`.p12` / `.pfx` / `private` / `credentials` / `vault`，
对**小写化后的完整路径做子串包含判断**（`:45-54`）。

> 注意保守性带来的误报面：`private` / `credentials` 是**子串**匹配，
> 例如 `app/private_utils.py` 这类正常文件名也会命中 → 转人工。
> 这是**故意保守**（宁可多拦），但被追问「会不会误拦」时要答得出来。

`apply_auto_approval`（`:122`）置 `task.risk_source = "plan_approval"`，
并写 `PLAN_AUTO_APPROVED` 事件 + 执行步骤 + 双表审计 + 发起人通知；
`evaluate_and_apply`（`:167`）是幂等入口（非 `WAITING_APPROVAL` 直接返回 `not_waiting`）。

### 11.3 企业版 7 个受治理操作码（`governance/operations.py`，实测全量）

| operation | 资源类型 | 默认风险 | 所需业务权限 | 审批模式 | 允许自审 |
| --- | --- | --- | --- | --- | --- |
| `repository.index.rebuild` | repository | L2 | `index:manage` | single | False |
| `team_request.sync_gitee` | team_request | L2 | `request:create` | single | False |
| `runtime.execute` | runtime_task | L2 | **`task:execute`** | single | False |
| `repository.archive` | repository | L3 | `project:manage` | dual | False |
| `repository.delete` | repository | L4 | `project:manage` | security | False |
| `audit.export` | tenant | L4 | `audit:view` | security | False |
| `tenant.member.role_change` | tenant_member | L4 | `member:manage` | security | False |

**更正一处口径**：`allow_self_approve` 默认 `False` 且**7 个操作无一覆盖它**，
所以是「**全部禁止自审**」，不只是全景图 §4.5 说的「高风险默认禁止」。

### 11.4 企业治理门 `gate()` 的判定链（`governance/gate.py:31-50`）

1. 取 `OperationSpec`；未注册 → `deny_and_audit` + 403（`:34-37`）；
2. `evaluate(context, spec, has_business_permission=…, secondary_confirmation=…)` 出决策；
3. **无论决策如何都写审计** `governance.decide.<operation>`（`:43-47`）；
4. `decision == "deny"` → 403；`approval_required` → **由调用方创建审批单**（gate 自己不建单）。

`_has_business_permission`（`:18-28`）是**双通道**判定：
先查企业租户上下文 `tenancy.permissions.has_permission`，再回退查团队 `main.ROLE_PERMISSIONS[user.role]`。
→ 这解释了「团队角色与企业角色两套权限体系如何共存」：**企业上下文优先，团队角色兜底**。

### 11.5 F8 · `runtime.execute` 的治理注册「声明了但不可执行」（真实缺陷）

四点实测证据：

1. **`task:execute` 全仓只出现 1 次** —— 就是 `operations.py:80` 自己的声明。
   它**不在任何团队角色的 `ROLE_PERMISSIONS` 里，也不是任何企业权限常量**
   （`grep task:execute|TASK_EXECUTE` 仅 1 处命中）。
2. **Runtime 实际路径走 `approve()`，不校验 `required_permission`** ——
   `approvals/service.py:402 approve()` 只校验 `APPROVAL_APPROVE` 租户权限、目标租户归属、
   L4 安全管理员、自审，**没有 `op.required_permission` 检查**；
   通过后 `:443 _forward_runtime_decision` → `runtime.approve_runtime_task(...)`。
   → 所以 E4-02 的 `rt_e4a_113934` 能跑通，**但意味着该操作的业务权限约束实际未生效**。
3. **走 `execute()` 则必然 403** —— `approvals/service.py:562-563` 会校验
   `_has_business_permission(context, user, "task:execute")`；由于无人拥有该权限，
   **任何人执行该审批都会 403**：`已不具备执行所需业务权限 task:execute`。
4. **`executor.execute_operation` 没有 `runtime.execute` 分支** ——
   `governance/executor.py:19-38` 只处理 6 个操作，`runtime.execute` 会落到兜底
   `:38 return {"status": "ok", "summary": "已执行"}`，**一个未实现的操作会报「已执行」**。

**性质**：不是安全漏洞（是「约束不可满足」，比「约束不生效」更偏保守），
而是**治理注册表与实际实现脱节**：声明了一个谁都没有的权限，配了一个不存在的处理器。

**建议（未改代码，待小泽决策）**：二选一——
(a) 为 `task:execute` 定义权限常量并授予合适企业角色（如 `tenant_owner` / `security_admin`），
    同时在 executor 里补 `runtime.execute` 的真实处理器（或在 spec 上显式标注「无副作用，仅门禁」）；
(b) 把 `required_permission` 改成真实存在的权限（如 `approval:manage`），
    并让 `execute_operation` 显式声明该操作**无可执行副作用**，避免 `"已执行"` 这种不实返回。

---

## 12. 第 6 站材料：前端（本次备齐）

### 12.1 实证：前端测试全量通过

```text
Test Files  31 passed (31)
Tests       272 passed (272)
Duration    57.21s
```

**文档漂移**：`docs/project-development/111` 与 `PROJECT_PROGRESS.md` 的 E5 行记的是
`264 passed`，实测已 **272**。属正常增长，但引用时要带时点。

运行方式：`cd frontend; npm test`（`package.json:11` = `vitest run`，**非 watch**，可安全重复跑）。

### 12.2 页面与路由（实测 `router/index.ts`）

**通用页面**：`/login`、`/forgot-password`、`/terms`、`/privacy`（4 个 `public: true`）、
`/dashboard`、`/tasks`、`/tasks/new`、`/tasks/:id`、`/requests`、`/requests/new`、`/requests/:id`、
`/approvals`、`/replay/:id?`、`/diff/:id?`、`/indexes`、`/settings`、`/account-recovery`、
`/audit`、`/:pathMatch(.*)*`（404，`layout: 'blank'`）。

**企业页面**：`/enterprise/members`、`/enterprise/approvals`、`/enterprise/dashboard`（**无权限要求**）、
`/enterprise/rag`、`/enterprise/runtime`、`/enterprise/ops`。

侧栏 `AppShell.vue:110-129` 共 **16 个入口**（`navEntries`），经
`:132 navItems = navEntries.filter(item => hasAccessRequirement(auth, tenancy, item.permission))`
按权限过滤。

### 12.3 前后端同一套双通道权限语义

`services/accessControl.ts:15-23`：

```ts
return permissions.some((p) => auth.hasPermission(p as Permission) || tenancy.hasPermission(p))
```

- **团队权限 `||` 企业权限**，与后端 `governance/gate.py:18 _has_business_permission` 同构；
- 数组用 `some` → **OR 语义**（`['project:manage','audit:view']` 满足其一即可）。

→ 这就是「团队角色与企业角色如何共存」在前端的镜像实现：**任一侧满足即放行**。

### 12.4 路由守卫（`router/index.ts:66-87`，5 步）

1. `!auth.ready` → `await auth.bootstrap()`；
2. `to.meta.public` → 已登录访问 `/login` 跳 `/dashboard`，其余 public 直接放行；
3. 未登录 → 跳登录并带 `query.redirect = to.fullPath`（`90-fix-refresh-redirect-to-login` 的落点）；
4. **企业上下文懒加载**：仅在「需要权限 **且** 权限不足 **且** `!tenancy.ready`」时才
   `await tenancy.loadContext()`——避免每次导航都请求 `tenant-context`；
5. 仍无权限 → `ElMessage.warning('您暂无权限访问「…」')` + **回落 `/dashboard`**。

> **潜在边界（当前不可触发，但值得知道）**：第 5 步回落目标是 `/dashboard`，
> 而 `/dashboard` 自身要求 `dashboard:view`。若将来出现一个连 `dashboard:view` 都没有的角色，
> 会形成「重定向到自身」。**实测 5 个团队角色全部拥有 `dashboard:view`**（含权限最少的 observer），
> 所以现在不会触发；Vue Router 也会检测重定向循环并中止。
> 若要加固，建议回落目标改成无权限要求的落地页，或在守卫里显式判断目标即为 `/dashboard` 时直接放行。

### 12.5 SSE 实时同步（实测链路）

`services/realtime.ts`：
- 全局**单一** `EventSource('/api/events/stream', { withCredentials: true })`，事件名 `team-update`；
- `connectRealtime()` **幂等**（已有连接直接 return），仅 `real` 模式且浏览器支持时建立；
- **载荷仅为元数据**（`type` / `resource_type` / `resource_id` / `task_id` / `occurred_at`）；
- 解析失败**静默容忍**（注释：payload 是 advisory，格式错误不得中断 UI 连接）；
- 重连交给浏览器自动处理。

**8 个消费点**：`AppShell.vue`、`TaskCommentsPanel.vue`、`ModificationPlanPanel.vue`、
`IndexView.vue`、`ApprovalView.vue`、`TaskDetailView.vue`、`RequestDetailView.vue`、`RequestListView.vue`。

**AppShell 的消费逻辑（`AppShell.vue:500-519`）——这是全站实时同步的中枢**：

1. `scheduleRealtimeRefresh` **80ms 防抖**（`:512`）——避免事件风暴打爆接口；
2. 按 `resource_type` 分流后**走 API 重新拉取**（事件本身不带业务数据）：
   - 通知 → `loadRealNotifications()`
   - 任务 → `ensureTaskLoaded(taskId)` + 若当前在 `/tasks` 则 `refreshTaskList()`；
     **且拉审批队列前再查一次 `auth.hasPermission('approval:view')`**（`:505`）
   - 项目/仓库 → `loadProjects()` + `syncDashboardSummary()`
3. **30s 轮询兜底**：`syncDashboardSummary`（`:534`）与 `loadRealNotifications`（`:538`）
   ——**不是纯事件驱动**，事件丢了也有兜底；
4. `startRealtimeWhenAuthenticated()`（`:515-519`）要求 `isRealMode && auth.ready && auth.currentUser`
   才建连 → **SSE 是登录后建立的带凭据连接**。

这套实现与 README 的说法完全一致：「Redis 只负责广播资源变化，前端收到事件后重新请求
**受权限保护**的接口；离线用户重新进入页面仍从 MySQL 读取最新数据」。

### 12.6 F9 · `AppShell` 测试夹具路由已漂移（低危，但会掩盖真问题）

`components/__tests__/AppShell.spec.ts:36-45` 只声明了 **9 条**测试路由，
而侧栏有 **16 个入口**，其中 **8 个没有对应桩**：
`/requests`、`/settings`、`/account-recovery`、
`/enterprise/approvals`、`/enterprise/dashboard`、`/enterprise/rag`、`/enterprise/runtime`、`/enterprise/ops`。

**实测后果**：`npm test` 的 stderr 里反复出现

```text
[VUE_ROUTER_R0004] No match found for location with path "/enterprise/dashboard"
```

**性质**：不是产品缺陷（该路由在 `router/index.ts:59` 真实存在，侧栏 `:125` 也有入口），
而是**测试夹具未随侧栏增长同步**。危害是**噪声可能掩盖真实的路由错误**，拖慢排障。
**建议**：由真实路由表生成桩，或至少补齐这 8 条。

### 12.7 一个检索教训（导师自纠）

我用 `include: "*.ts"` 搜 `connectRealtime` 时，**漏掉了 `.vue` 文件**，
一度以为 SSE 客户端是死代码。改为搜整个 `frontend/src` 后，命中 **8 个真实消费点**。

**结论**：**跨技术栈检索时不要用扩展名白名单**——`grep` 的 `include` 是陷阱，
默认全搜再按需要收窄。

---

## 13. 生产环境只读验证（2026-10-08，小泽提问触发）

小泽问：「如果是企业版，为什么不直接用 `https://agentforge.ltd/`？」

### 13.1 先验证：生产确实在跑（只读 GET，未登录、未写任何数据）

| 探测 | 结果 |
| --- | --- |
| `http://agentforge.ltd/` | **308** 跳转（与 E6 声明一致） |
| `https://agentforge.ltd/` | 200，`Server: nginx/1.24.0 (Ubuntu)` |
| `https://agentforge.ltd/health` | 200 `ok` |
| `https://agentforge.ltd/api/health` | 200，`db ok latency 1.1ms` |
| `https://agentforge.ltd/login` | 200 |
| `/api/enterprise/ops/status` | **401** |
| `/api/enterprise/rag/reports` | **401** |
| `/api/audit-events` | **401** |
| **`/api/enterprise/ops/metrics`** | **200 —— 无认证可读** |

E6 的交付声明（ICP 域名 + `http→308→https` + TLS + `/api/health` 200）**被独立复验通过**。
生产实例已累计 `agent_http_requests_total = 221978`（约 22 万次），确为长期在跑的真实实例。

### 13.2 F1 升级：从「理论可达」变为「生产在线」

生产上**全部企业端点都 401，只有 `/api/enterprise/ops/metrics` 返回 200**。
未认证即可读到：请求总数、4xx/5xx 计数与比率、平均延迟、各状态码分布，
以及 `agent_dependency_db / redis / milvus / worker` 四个依赖的实时健康。
→ F1 的严重度按「**已在线**」而非「可复现」计。

### 13.3 F12 · 生产安全头被重复添加，且两个是单值头（新发现）

生产实测响应头：

| 头 | 实测值 | 性质 |
| --- | --- | --- |
| `X-Content-Type-Options` | `nosniff,nosniff` | **单值头**，逗号列表可能被严格实现视为无效 |
| `X-Frame-Options` | `DENY,DENY` | **单值头**，同上 |
| `Referrer-Policy` | `strict-origin-when-cross-origin,strict-origin-when-cross-origin` | 规范允许列表 → 无害 |
| `Strict-Transport-Security` | `max-age=31536000` | 单值 ✓（仅宿主机 nginx 添加） |

**根因**：两层 nginx 都 `add_header` 了同样的头——
宿主机 TLS 终结层 `deploy/nginx.enterprise.tls.conf:32-33`
+ 容器内前端 nginx `deploy/nginx.enterprise.conf:11-12`。
凡两层都加的头就变成逗号列表；HSTS 只在宿主机加，所以保持单值。

**为什么会漏过 E6 验收**：`deploy/scripts/verify-https.sh:37-41` 是**只查头名、不查值**：

```bash
for header in Strict-Transport-Security X-Content-Type-Options X-Frame-Options; do
  echo "$HDRS" | grep -qiE "^${header}:" || fail "缺少安全头: ${header}"
  echo "安全头 ${header}: 存在"
done
```

`grep "^X-Frame-Options:"` 对 `DENY,DENY` 同样通过并打印「存在」——
**验收脚本的盲区正是重复头能活到生产的原因**。

**影响判断（如实标注不确定性）**：按规范 `X-Frame-Options` 与 `X-Content-Type-Options`
均为单值头，严格实现遇到逗号列表会判为无效并忽略 → 理论上点击劫持防护与 MIME 嗅探防护
**可能静默失效**。**具体浏览器行为我尚未实测**，不应断言已失效；
但无论如何这是**必须修的配置错误**（同一个头只能由一个 nginx 层设置）。

**建议（未改代码）**：
1. 从容器内前端 nginx 移除这两个（三个）`add_header`，只由宿主机 TLS 层统一添加；
   或反过来，明确「安全头只在最外层加」这条规则；
2. **把 `verify-https.sh` 从「存在性检查」升级为「值校验」**（例如断言
   `X-Frame-Options: DENY` 精确匹配、拒绝包含逗号的值）——否则同类问题还会再漏。

### 13.4 结论：生产是证据，不是实验台

| 用途 | 本地企业栈（8003/5178） | 生产 `agentforge.ltd` |
| --- | --- | --- |
| 跑实验、改模式、比 bm25/vector/hybrid | ✅ | ❌ 会产生真实审计与配额消耗 |
| 造数据 / 播种索引 | ✅ | ❌ 污染生产租户 |
| 看 DB / 日志 / Worker / Milvus 内部 | ✅ | ❌ 无访问 |
| 验证交付声明（HTTPS/308/健康/权限边界） | 不能替代 | ✅ **只读探测即可** |
| 用真实凭据走查 UI | 用种子账号 | **由小泽本人登录**（我不持有生产凭据，也不猜测） |

**「本地 8 次 RAG 查询写了 8 条 `rag.query` 审计」这件事本身就是答案**：
在生产做同样操作等于往生产租户的审计链里插测试数据，而这个产品的核心卖点正是**可审计**。

---

## 14. 第 7 站材料：交付运维与真实边界（本次备齐）

### 14.1 交付物清单（实测文件）

| 类别 | 文件 |
| --- | --- |
| 编排 | `deploy/docker-compose.enterprise.yml`（完整栈，含 Milvus/etcd/MinIO）、`docker-compose.enterprise.apponly.yml`（低配应用-only）、`docker-compose.team.yml`，以及 5 个 override（host-tls / local-ports / multireplica / redis-nonroot / milvus-e307·e309） |
| 反向代理 | `nginx.enterprise.conf`（容器内）、`nginx.enterprise.tls.conf`（宿主机 TLS 终结）、`nginx.team.conf`、`nginx.master.conf` |
| 运维脚本 | `backup.sh`、`restore.sh`、`rollback.sh`、`upgrade.sh`、`list-backups.sh`、`monitor-health.sh`、`monitor.py`、`check-cert-expiry.sh`、`security-scan.sh`、`loadtest.sh`、`preflight.sh`、`deploy-enterprise.sh`、`verify-dns.sh`、`verify-https.sh`、`configure-host-https.sh` |
| 环境模板 | `env/enterprise.env.example`、`env/team.env.example`、`env/mvp.env.example` |
| Runner | `deploy/test-runner/Dockerfile`、`worker-healthcheck.sh` |

### 14.2 备份能力（实测读通 `backup.sh`，56 行）

四段式，**每段都产出可校验证据**：

1. **MySQL**：`mysqldump --single-transaction --hex-blob --routines --triggers` → `agent_enterprise.sql`；
   缺 `mysqldump` 或 `DATABASE_URL` 时**显式提示「生产必须配置」**（`:31`），不静默跳过；
2. **Redis**：`redis-cli SAVE` 触发快照；不可达时明确打印「SAVE 跳过」；
3. **数据卷**：对 `agent_enterprise_data` + Milvus 三卷逐个 `docker run --rm -v …:ro alpine tar`；
4. **清单**：`SHA256SUMS` + `manifest.json`（`backup_id` / `env` / `git_commit` / `database` / `created_at` / `files`）。

硬约束（`:3`）：**备份文件不含明文密钥**，且**列出/校验/恢复路径分离**。
`restore.sh` 负责还原、`rollback.sh` 负责镜像回退并输出 `ROLLBACK_IMAGE=<prev_tag>`。

### 14.3 迁移 fail-closed 状态机（实测读通 `app/db/migrate.py`，258 行）

`ensure_schema_current()` 有 **7 种判定**，全部 fail-closed：

| DB 版本关系 | 行为 |
| --- | --- |
| `current == head` | 正常启动 |
| `current` 是 head 祖先（落后） | `upgrade=True` 升级；Worker（`upgrade=False`）**抛错要求先迁移** |
| `current` 是 head 后代（领先） | 迁移层兼容启动 + warning，**不升级不降级** |
| 无祖先关系（分叉） | **BLOCKED**（永远阻断） |
| unknown（不在本应用迁移树） | **BLOCKED**；仅 `ALLOW_UNKNOWN_MIGRATION_REVISION=true` 才放行 |
| 多 head | **BLOCKED**（`target_head()` 抛） |
| `alembic_version` 非恰 1 行 | **BLOCKED**（`current_version()` 抛） |

`ALLOW_UNKNOWN` 默认 false（`:182`）。**最诚实的一段注释在 `:14-15` / `:115` / `:145`**：
DB 领先时只是「迁移层不升级不降级地共存启动」，**不等于应用全局只读**，
旧应用写新 schema 仍有风险，**本模块不提供统一写入门禁**。

`_is_ancestor`（`:210-232`）沿 `down_revision` 的**全部分支**深度搜索，
兼容多父 merge —— 避免只沿首分支误判为 `unrelated` 而误 BLOCK。

### 14.4 F10 · 「任何场景禁止 downgrade」是表述过度（文档精度）

`migrate.py:11` 写：「迁移策略**前向-only，任何场景禁止 downgrade**」。

**代码事实**：同文件 `:100 run_upgrade_to(revision)` 调用
`command.upgrade(_alembic_config(), revision)`——Alembic 的 `upgrade()` 传更低 revision
**实际会执行降级**。调用方 4 处**全在测试**，用途明确是「构造 legacy schema」：

```text
tests/test_migrations_upgrade.py:54   run_upgrade_to("c9a4e71f2b50")
tests/test_migrations_upgrade.py:184  run_upgrade_to("ad6faa347bb8")   # 降回 baseline
tests/test_migrations_schema_relationship.py:33 / :198
```

**准确表述**：**生产路径（`ensure_schema_current`）永不 downgrade；模块保留降级能力供测试构造 legacy 库。**

**为什么要改**：面试/评审被问「你说禁止 downgrade，那这个函数是干嘛的？」时，
按现文档答会自相矛盾。建议把 `:11` 改成「**生产路径前向-only；`run_upgrade_to` 仅供测试构造 legacy schema**」。

### 14.5 F11 · known-flaky 只写在文档，代码里没有标记

`docs/project-development/106:132` 与全景图 §6 都声明：
「`test_dev015_concurrency.py::test_concurrent_approve_same_plan_single_approved_event`
偶发 1 个 `None.status_code`，**已标 known-flaky**」。

**实测**：对 `backend/tests/test_dev015_concurrency.py` 搜
`flaky|known|xfail|skip|marker|偶发|None.status_code|retry` → **零命中**。

→ 「已标」**只存在于文档**。后果：CI 偶发失败时，看着测试文件的开发者**得不到任何提示**，
要么白花时间排查，要么（更糟）去放宽断言。

**建议**：在测试文件顶部加注释 + 注册一个 `known_flaky` pytest marker 并指向 `docs/.../106` §132。

### 14.6 F13 · `backup.sh` 把数据库口令暴露在进程命令行（新发现）

`deploy/scripts/backup.sh:27`：

```bash
mysqldump -h"${rhost%%:*}" -P"${rhost#*:}" -u"$muser" -p"$mpass" --single-transaction …
```

`-p"$mpass"` 会让口令出现在**进程命令行**里，同机任何能执行 `ps` 的用户都能看到；
shell history / auditd / 进程快照也可能留存。

**注意边界（别夸大）**：脚本 `:3` 声明的硬约束是「**备份文件不含明文密钥**」，
这条**仍然成立**（dump 文件与 tar 卷里确实没有密钥）。问题是**调用方式**泄漏，
不是文件泄漏。`:27` 末尾的 `2>/dev/null` 还会吞掉 mysqldump 的真实错误，
只留一句笼统的「MySQL 备份失败」，**降低可诊断性**。

**建议**：改用 `--defaults-extra-file=<600 权限的临时 cnf>`（或 `MYSQL_PWD` 环境变量），
并去掉 `2>/dev/null` 或把 stderr 落盘到日志。

### 14.7 真实边界清单（逐条可追溯，`docs/enterprise/10-pilot/06-boundaries-and-limitations.md`）

| 边界 | 声明 | 我能否独立验证 |
| --- | --- | --- |
| 生产受支持回滚点 | `agent-enterprise-backend:e6-rollback-compat-61a6752` | ✅ 生产健康 200 + 文档一致（未做回滚演练）|
| 原始 legacy 镜像跨 schema 回滚 | **不支持**（fail-closed 判 unknown→BLOCK）| ✅ `migrate.py` 代码落实 |
| `ALLOW_UNKNOWN_MIGRATION_REVISION` | **禁止生产开启** | ✅ 默认 false，代码落实 |
| 迁移前向-only | 禁止 downgrade | ⚠️ 生产路径成立；见 §14.4 表述需修 |
| DB 领先 ≠ 全局只读 | 不提供统一写入门禁 | ✅ 注释与行为一致 |
| 单区单机，无跨区容灾 | 不承诺 | ✅ 架构如此 |
| **极端承载未压测** | 容量上限为后续商业协议，**非本文档承诺** | ✅ 如实声明（未发现夸大）|
| apponly 栈无 Milvus，RAG 无向量 | 长任务走 eager，`USE_MILVUS=false` | ✅ **生产 health 实测 `milvus: skipped(use_milvus=false)`** |
| RPO ≤24h / RTO 小库 ~4s（目标 ≤30min）| 数值 | ⚠️ 未独立演练（需在生产跑 restore）|
| 未验证能力不伪装 | 「**不通过开启放行开关伪称"已验证"**」 | ✅ 这是全项目最强的质量信号 |

> **这一站的结论**：这个项目最专业的地方不是功能多，而是**它把「没做到什么」写成了和「做到了什么」
> 同等正式的交付物**。第 8 站体检时，「已知边界」这一栏你应当**主动说**，而不是等对方问。

---

## 15. 答案卡补完时的新发现（2026-10-08）

补答案卡 3 处 `[待补]` 的过程中，把 `main.py` 的**全部状态改写点**核对了一遍，发现两件事。

### 15.1 F14 · 任务失败会被误标成「上下文缺失」（新发现）

**两个代码事实**（都已核对）：

1. `main.py:2571`：
   ```python
   def mark_task_failed(task: TaskRecord, *, node: str, reason: str,
                        manual_reason: ManualReason = "context_missing") -> AuditEventRecord:
   ```
   **失败辅助函数的 `manual_reason` 默认值就是 `context_missing`**。

2. `workflow.py:330`（`node_fail`，Agent 节点异常的唯一出口）：
   ```python
   mark_task_failed(task, node=state.get("node") or "unknown",
                    reason=state.get("reason") or "Agent 节点异常")
   ```
   **没有传 `manual_reason`** → 吃默认值。

**后果**：任何因 Agent 节点异常而失败的任务，`manual_reason` 都会是
**`context_missing`（上下文缺失）**，而真实原因可能是模型调用失败、Diff 生成异常、状态机缺陷等。
用户界面与筛选器会把这类任务归到「等人工补上下文」，**指错排查方向**。

**对比**：同文件 `workflow.py:275` 的 Diff 失败就**显式传了** `manual_reason="diff_failed"`
——说明作者知道该传，只是 `node_fail` 这个通用兜底漏了。

**性质**：不是崩溃级缺陷，是**诊断误导**。严重度按「会让人追错方向」计。

**建议（未改代码）**：把 `mark_task_failed` 的 `manual_reason` 默认值改成
`"none"`（或新增一个 `"agent_failed"`），并让 `node_fail` 显式传入。
**默认值应当是「不声称任何具体原因」**，而不是「替调用方猜一个原因」。

### 15.2 测试执行的状态改写有两套实现（待定性）

`grep` 全部状态改写点后，测试执行路径出现两组高度相似的赋值：

| 组 | 行号 | 写的内容 |
| --- | --- | --- |
| A | `main.py:3310-3383` | `record.status = skipped/passed/failed` + `task.status = DIFF_GENERATED/TEST_RUNNING/TESTED/TEST_FAILED` |
| B | `main.py:8665-8718` | 同一组赋值的镜像 |

大概率是「**HTTP 内联路径**」与「**Worker 路径**」各一份。
若是，则属 `[待补]` 需要确认的**重复逻辑**：两处都改状态机，**改一处漏一处**会让两条入口行为不一致
（团队版的并发 flaky 测试 `test_dev015_concurrency` 也在这条链上，见 §14.5）。

**下一轮**：确认两处所属函数名，判断是「刻意双路径」还是「历史副本」，
再决定是否登记为发现。**在没确认之前不写成缺陷。**

### 15.3 已补完的答案卡 `[待补]` 清单

| 原 `[待补]` | 现状 |
| --- | --- |
| Q1 各状态在 `main.py` 的逐行改写点 | ✅ 15 个状态、20 个赋值点全部列出（答案卡 Q1 表格）|
| Q3 `context_missing` 落库点 | ✅ `main.py:3867-3872`；含执行步骤 `status="blocked"`（非 `failed`）与恢复接口 `:7568+` |
| Q5 `_dedupe_and_diversify` 内部实现 | ✅ `:119-143`；含 `None` 语义、`_candidate_top_k`、`_stable_sort_retrieved` |
| Q1 附带：测试执行两套实现 | ⚠️ 新增，待定性（§15.2）|

---

## 16. F15 ·「测试环境不可用」在两套实现里产出不同状态与相反的回放结论

§15.2 待定性的两套实现，本轮已定性。**结论：不是死代码，是重复逻辑已经漂移。**

### 16.1 两个入口

| 组 | 函数 | 行号 | 性质 |
| --- | --- | --- | --- |
| A | `execute_task_tests_worker(task_id)` | `main.py:3269` | **Worker 自动路径**（`workflow.py:294` 调用）|
| B | `run_task_tests(task_id, user)` | `main.py:8593` | **HTTP 手动触发**（`POST /api/tasks/{task_id}/tests/run`）|

### 16.2 同一情形（测试环境不可用）的三处差异（逐行核对）

| 维度 | A：Worker 路径 | B：HTTP 路径 |
| --- | --- | --- |
| `record.status` | `"skipped"`（`:3345`）| `"skipped"`（`:8686`）✓ 一致 |
| **`task.status`** | **无条件 `"DIFF_GENERATED"`**（`:3348`）| **`"DIFF_GENERATED" if task.status == "TEST_RUNNING" else task.status`**（`:8689`）|
| **`task.next_action`** | `"retry_test"`（`:3352`）| **`"auto_run_test"`**（`:8693`）|
| **事件类型** | **`TEST_RUN_UNAVAILABLE`**（`:3354`）| **`TEST_BLOCKED`**（`:8695`）|

**B 的注释写明了正确语义**（`:8688`）：
「环境不可用不消耗失败次数：回到执行前的可重试阶段（**DIFF_GENERATED 首轮 / TEST_FAILED 重试轮**）」。
→ 也就是说 **B 是对的，A 是错的**：A 无条件把任务推回 `DIFF_GENERATED`，
**丢掉了「这是测试失败后的重试轮」这一上下文**。

### 16.3 跨模块后果：回放摘要会给出相反结论（关键）

`append_test_run_event`（`main.py:8531-8589`）**只为三种事件类型建执行步骤**：

| `event_type` | 是否建 `execution_step` | 步骤状态 |
| --- | --- | --- |
| `TEST_RUN_PASSED` | 是（`:8556`）| `completed` |
| `TEST_RUN_FAILED` | 是（`:8567`）| `failed` |
| `TEST_BLOCKED` | 是（`:8578`）| `blocked` |
| **其他（含 `TEST_RUN_UNAVAILABLE`）** | **否 —— 直接落到 `:8588`，不建步骤** | — |

再看 `replay_service.py:91-93`（**注意 91 行在前，先判失败**）：

```python
if event_type in _FAILED_TYPES or "failed" in step_statuses:   # :91
if event_type in _SKIPPED_TYPES or "blocked" in step_statuses: # :93
```

其中 `_FAILED_TYPES`（`:54-57`）**含 `TEST_BLOCKED`**；`_SKIPPED_TYPES`（`:58`）**只有 `TEST_RUN_UNAVAILABLE`**。

**净效果**：

| | A：Worker 路径 | B：HTTP 路径 |
| --- | --- | --- |
| 时间线里有执行步骤吗 | **没有** | 有（`blocked`）|
| 回放摘要判定 | **`skipped`**（命中 `_SKIPPED_TYPES`）| **`failed`**（命中 `_FAILED_TYPES`，且先判）|

→ **同一个「测试环境不可用」，Worker 自动跑报「跳过」，用户手动跑报「失败」。**

**最讽刺的一点**：B 自己的注释写着「环境不可用**不消耗失败次数**」，
但它发的事件类型 `TEST_BLOCKED` 在回放里**被归为 failed** ——
**代码注释的意图与可观测结果自相矛盾**。

### 16.4 性质与建议

**性质**：
1. **重复逻辑漂移**（同一状态机两份实现，已出现行为差异）；
2. **诊断不一致**：同一情形的 `task.status` / `next_action` / 事件类型 / 回放结论四处分叉；
3. **时间线不完整**：A 路径只写事件不写步骤，破坏 DEV-014 建立的「事件/步骤分离」契约
   （此前我在 §4 发现的是反例：步骤引用了表里不存在的事件——两者是同一契约的两侧失守）。

**建议（未改代码，待小泽决策）**：
1. **抽一个共享核心**（如 `_finalize_test_run(task, record, outcome, *, entry)`），
   两个入口只负责取参数与返回格式，**状态机只写一份**；
2. 统一事件类型：环境不可用**只用一个**事件名，并让它落进 `_SKIPPED_TYPES` 而非 `_FAILED_TYPES`
   （因为语义确实是「没执行」而不是「执行失败」）；
3. 统一 `next_action`：`retry_test` 与 `auto_run_test` 需明确二者差异，
   否则图路由（`workflow.py:_router`）读到的信号不一致；
4. **加一条回归测试**：同一 `outcome(error=...)` 经两个入口后，
   `task.status` / `next_action` / 事件类型 / 回放判定必须一致。

### 16.5 这条发现的价值

它比 F14 更适合当面试素材，因为完整展示了**四层链路**：
`入口分歧 → 状态机漂移 → 事件类型分歧 → 回放结论相反`。
面试官追问「你怎么保证两条入口行为一致」时，这条能答出**具体后果**而不是空谈原则。
