# 生产环境 UI 走查清单（agentforge.ltd）

> 对象：小泽本人用凭据登录走查。 ｜ 生成：2026-10-08
> 目的：**用真实生产页面验证 E6/E7 的交付声明**，并顺手对照我们本地发现的问题。
> 原则：**只读观察**。任何会写数据/花钱/动外部系统的按钮都不要点（见 §1）。

---

## 1. 安全边界（先读这段）

| 可以做 | 不要做 |
| --- | --- |
| 登录、浏览所有页面 | ❌ 点「重建索引」——会真实克隆仓库 + 调真实 embedding（有配额成本）|
| 打开任务/审批/审计详情 | ❌ 点「同步 Gitee」——会在真实 Gitee 创建 Issue |
| 在企业 RAG 面板里**查一次**（会产生 1 条 `rag.query` 审计，见 §6 说明）| ❌ 点「执行」任何审批——`repository.delete` / `audit.export` 等是真实副作用 |
| 看 DevTools 的响应头与网络请求 | ❌ 启动 Runtime 任务——会调真实 LLM，且可能创建真实 Gitee PR |
| 切换账号比对权限 | ❌ 改任何配置 / 成员角色 |

> 关于 RAG 查询：如果你想看真实检索效果，**查一次**是可接受的代价（1 条审计），
> 但不要连续刷模式（多模式对比请在本地 8003 做，那里无副作用）。

---

## 2. 我已验证的生产基线（供你对照，无需重复）

2026-10-08 只读探测结果：

| 项 | 实测 |
| --- | --- |
| `http://agentforge.ltd/` | **308** → `https://agentforge.ltd/` |
| `https://agentforge.ltd/` | 200，`Server: nginx/1.24.0 (Ubuntu)` |
| `/health` | 200 `ok` |
| `/api/health` | 200，`db.status=ok`，`latency_ms≈1.1` |
| `/login` | 200 |
| `/api/enterprise/ops/status` | **401**（未登录被拒 ✓）|
| `/api/enterprise/rag/reports` | **401** ✓ |
| `/api/audit-events` | **401** ✓ |
| `/api/enterprise/ops/metrics` | **200 —— 未登录可读**（F1，已在线）|
| 累计请求 | `agent_http_requests_total = 221978` |

### 2.1 生产跑的是哪个栈（已确认，直接影响你看到什么）

`https://agentforge.ltd/api/health` 全量返回：

```json
{"status":"ok","dependencies":{
  "db":{"status":"ok","latency_ms":1.1,"pool":{"size":10,"checkedin":6,"checkedout":0}},
  "milvus":{"status":"skipped","reason":"use_milvus=false"},
  "redis":{"status":"ok","latency_ms":4.3}}}
```

| 依赖 | 生产状态 | 结论 |
| --- | --- | --- |
| MySQL | `ok`（1.1ms）| 真实业务库 |
| Redis | `ok`（4.3ms）| 真实队列，`celery_required()` 为真 |
| **Milvus** | **`skipped`（`use_milvus=false`）** | **生产没有向量库** |

→ 生产是 **apponly 栈**（backend + redis + frontend），
**与 `docs/enterprise/10-pilot/06-boundaries-and-limitations.md` §4 的声明逐字一致**：
「apponly 栈无 worker/Milvus：长任务走 eager、RAG 无向量（`USE_MILVUS=false`）」。

**所以你走查时的两点预期**（否则会误判成故障）：

1. **`/enterprise/rag` 的「检索」面板**：因无 Milvus 且企业版 `RAG_MOCK_FALLBACK=false`，
   **检索会失败或仅返回词法结果**——这是**已声明的边界，不是新缺陷**。
   请把实际表现（报错文案 / 降级提示 / BM25 结果）如实记下来。
2. **`/enterprise/rag` 的「评测报告」面板**：读的是服务端报告文件，**不依赖 Milvus**，
   **应当照常列出报告**。若这一栏也空 → 那才是异常。

> 顺带：`pool.checkedin=6`（本机是 1）说明生产有真实并发连接。

**浏览器里请确认**：地址栏是锁形图标 + `https://`，且输入 `http://agentforge.ltd` 会自动跳到 `https://`。

---

## 3. 逐页走查（路由名取自 `frontend/src/router/index.ts`，均为真实路径）

打开 DevTools（F12）→ Console 保持可见，随时记录红色报错。

### 3.1 通用页面

| # | 路由 | 页面 | 期望 | 请记录 |
| --- | --- | --- | --- | --- |
| 1 | `/dashboard` | 工作台 | 吞吐/趋势图渲染，无报错 | 有没有数据？图表是空白还是真实曲线？ |
| 2 | `/tasks` | 任务列表 | 列表 + 筛选项 | 任务数是多少？状态分布？ |
| 3 | `/requests` | 平台需求 | 需求列表 | 条数？ |
| 4 | `/approvals` | 审批中心 | 队列或空状态 | 空队列是否显示为**正常空状态**而非报错？ |
| 5 | `/indexes` | 索引管理 | 仓库索引状态 | 有几个索引？状态？ |
| 6 | `/audit` | **审计中心** | 事件列表 | **条数 + 事件类型**（E7 文档称生产只有 2 条：`PROJECT_CREATED` + 越权被拒）。**与我 8 次只读探测无关，我只读了 metrics** |
| 7 | `/settings` | 项目配置 | 仓库/白名单/审批阈值 | 仓库名、`risk_threshold`、`command_whitelist` |

### 3.2 企业页面

| # | 路由 | 页面 | 期望 | 请记录 |
| --- | --- | --- | --- | --- |
| 8 | `/enterprise/dashboard` | 企业工作台 | 企业视图 | 有无整体横向溢出？ |
| 9 | `/enterprise/members` | 成员管理 | 成员列表 | 人数？角色分布？（E7 称 5 人）|
| 10 | `/enterprise/approvals` | 企业审批 | 队列/空状态 | 空状态是正常的吗？ |
| 11 | `/enterprise/rag` | **企业 RAG** | 双面板：检索 + 评测报告。**检索面板因生产无 Milvus（见 §2.1）预期会失败/降级；评测报告面板应正常出列表** | **报告数 __ ；徽章 __ ；检索面板的实际表现（报错文案 or BM25 结果）__** |
| 12 | `/enterprise/runtime` | 企业 Runtime | 任务/checkpoint | 空状态？（E7 称 0 个 Runtime 任务）|
| 13 | `/enterprise/ops` | **企业设置** | 运维状态 | **重点看：`dependencies` 那一栏是不是空的？** 见 §5-F3 |

### 3.3 空状态判定（这是本轮的重点）

我们本地实测过：**「空」有两种，一种是正确，一种是缺陷**。生产上请对每一页判一次：

- **空但正确**：`/approvals`、`/enterprise/approvals`、`/enterprise/runtime` 无数据 → 正常
- **空得可疑**：`/enterprise/ops` 的 `dependencies` → 本地恒为 `{}`，是缺陷（`routes.py:75`）
- **不应为空**：`/enterprise/members`（≥1）、`/enterprise/rag` 报告列表（本地 15 份）

---

## 4. DevTools 三项（生产专属验证）

### 4.1 响应头重复（F12）

Network → 刷新 → 点第一个 `agentforge.ltd` 文档请求 → Response Headers，找这三个：

| 头 | 生产实测值 | 期望 |
| --- | --- | --- |
| `X-Frame-Options` | **`DENY,DENY`** | 应为单个 `DENY` |
| `X-Content-Type-Options` | **`nosniff,nosniff`** | 应为单个 `nosniff` |
| `Referrer-Policy` | `strict-origin-when-cross-origin,...` | 列表合法，无害 |

**请确认你看到的和上表一致**——这是 F12 的独立复核。

### 4.2 未认证的指标端点（F1）

浏览器**新开一个无痕窗口**（确保不带任何会话），直接访问：

```text
https://agentforge.ltd/api/enterprise/ops/metrics
```

期望：**能看到一串 Prometheus 指标文本**（含 `agent_http_requests_total`、
`agent_dependency_db/redis/milvus/worker`）。恭喜——你正在演示 F1。

### 4.3 SSE 实时连接

Network → 筛选 `EventSource` 或找 `/api/events/stream`：

- 有这条请求 → 生产实时同步**已启用**
- 没有 → 生产未启用实时（`REALTIME_ENABLED` 未开），属配置事实，记录即可

---

## 5. 权限边界走查（关键，用第二个账号）

E5/E7 声称的角色差异，需要你实测。若有 observer/auditor 类账号，登录后依次验证：

| 检查 | 期望（按文档） | 实际（请记录）|
| --- | --- | --- |
| 侧栏是否还显示「企业 Runtime」 | 应**消失**（无 `rag:search`）| |
| 地址栏直输 `/enterprise/runtime` | 应**跳回工作台** + 提示无权限 | |
| 地址栏直输 `/enterprise/rag` | 应**跳回工作台** | |
| `/audit` | 应**可读**（有 `audit:view`）| |
| `/enterprise/ops` | 应**跳回工作台**（需 `org:manage`）| |

> **我要提醒你一件事**（我们本地实测发现的）：后端 `/api/enterprise/runtime/*` 路由
> **没有权限门禁**，只有前端守卫。所以如果你有办法直连 API（或看 DevTools），
> 会发现 **401 只在未登录时出现；一旦登录，任何租户成员都能列 Runtime 任务**。
> 而 E5 文档写的是「后端保留 403」。**这条在生产上同样成立，值得你亲手确认一次**——
> 它是 F2，属于面试时的高价值素材（能证明你读到实现层）。

---

## 6. 回报模板（照这个填就行）

```text
【生产走查回报 · <日期>】

0. 入口：https 锁标 ✔/✘ ；http 自动跳转 ✔/✘
1. 登录账号角色：<团队角色> / <企业角色>
2. 逐页结果：
   /dashboard            -> <有数据/空/报错>
   /tasks                -> 任务数 __ ，状态分布 __
   /requests             -> 条数 __
   /approvals            -> 空状态正常？ __
   /indexes              -> 索引数 __
   /audit                -> 事件数 __ ；类型 __
   /settings             -> 仓库 __ ；risk_threshold __
   /enterprise/dashboard -> 溢出？ __
   /enterprise/members   -> 人数 __
   /enterprise/approvals -> 空状态正常？ __
   /enterprise/rag       -> 报告数 __ ；徽章 __
   /enterprise/runtime   -> 空状态？ __
   /enterprise/ops       -> dependencies 是否为空？ __
3. DevTools：
   X-Frame-Options = ___ ；X-Content-Type-Options = ___
   无痕打开 /api/enterprise/ops/metrics -> HTTP ___
   /api/events/stream 是否存在 -> ___
4. Console 有无红色报错：___
5. 与你预期不符的地方：___
```

---

## 7. 走查后我们对什么

你回报后我做三件事：

1. **把「文档声明 vs 生产实际」逐条对账**，产出差异清单（E7 的数据面写于 2026-09-24，
   生产已服务 22 万次请求，**数字很可能已经变了**，这不是缺陷但要如实更新）；
2. 把 F1 / F2 / F12 的生产证据补进带读日志，**从「本地可复现」升级为「生产已确认」**；
3. 进第 7 站复盘：**生产环境证明了什么、又证明了不了什么**。
