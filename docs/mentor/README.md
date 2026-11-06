# docs/mentor · 项目带读材料索引

> 这里是小泽熟悉「司契（Siqi）」的**带读材料**，不是项目正式交付文档
> （正式文档在 `docs/` 与 `docs/enterprise/`、`docs/team/`）。
> 生成时间：2026-09-24 ~ 2026-10-08 ｜ 记录原则：**只写实测过的事实**，推断一律标注。

> **行号漂移声明（2026-10-16）**：本目录 5 份文档生成于 2026-09-24 ~ 2026-10-08，
> 其中 41 处 `main.py:行号` 与 100 处 `文件.py:行号` 引用按**当时**代码行号书写。
> 2026-10-16 的 OPT-11 拆分后（`main.py` 9177→7651 行，抽出 12 个 `backend/app/api/*.py` 路由模块），
> 部分行号已漂移。阅读时请以**符号名**为准、用编辑器全局搜索定位，不要按行号跳转。
> （`docs/project-development` 正式开发文档的行号引用已在 OPT-12 全部改为符号锚点，
> 并纳入 `backend/scripts/opt12_doc_consistency.py` 机器校验；本历史语料按「保真」原则不改写。）

---

## 1. 五份文档与阅读顺序

| # | 文档 | 回答什么问题 | 什么时候读 |
| --- | --- | --- | --- |
| **01** | [项目全景图](./01-project-master-map.md) | 这个项目有哪 8 大块？一次任务怎么走？多 Agent 怎么编排？ | **先读这份**，配 7 张渲染 PNG |
| **02** | [带读日志](./02-guided-tour-log.md) | 每一站的真实源码证据、实测数据、**15 个发现**、导师自纠记录 | 按站查阅，是**证据总库** |
| **03** | [生产 UI 走查清单](./03-production-walkthrough-checklist.md) | 生产环境怎么走查？哪些按钮不能点？期望看到什么？ | 要动生产域名时**先读** |
| **04** | [第 8 站能力体检答案卡](./04-capability-check-answer-key.md) | 12 个必答问题的参考答案、代码证据、易错点、判分标准 | 自测或面试前 |
| **05** | [15 个发现的修复方案](./05-findings-and-remediation-plan.md) | 每个问题怎么修？优先级？要哪些新测试？ | 准备动手改代码时 |

**30 分钟速览路径**：`01` 的 §0 一句话 + §1 两张结构图 + §2 图 A 主干流程 → 再看 `04` 的 ⚠️ 易错点表。

---

## 2. 项目一句话与三个版本

> **把 AI Agent 塞进企业真实研发流程，做成一条「可解释、可审批、可回放、可追责」的任务流水线。**
> 与「AI 帮我写代码」类工具的本质区别：**约束在前，生成在后**。

| | MVP 版 | 团队版 | 企业版 |
| --- | --- | --- | --- |
| 入口 | Gitee Issue 直接建任务 | **入口反转**：平台写 `REQ-*` → 同步 Gitee → 自动建任务 | 企业身份 → Tenant/Org/Project |
| 库 | SQLite | MySQL | MySQL 独立库 |
| Agent | 单 Agent 9 节点（`agent/workflow.py`）| 同 MVP | **6-Agent Runtime**（`enterprise_runtime/`）|
| 风险模型 | `low/medium/high` | + **7 判定**自动推进门禁 | **`L0–L4`** + 7 个受治理操作码 |
| 多租户 | 无 | 无（明确排除）| **Tenant/Org/Membership 三层** |

**完成度**：`PROJECT_PROGRESS.md` **34/34 (100%)**，E1–E7 全部 DONE（2026-09-24 收口）。

---

## 3. `01` 全景图的勘误记录（2026-10-08 实测修正）

`01` 是最常读的一份，其中 5 处与代码不一致，**已就地修正**：

| # | 原写法 | 修正后 | 证据 |
| --- | --- | --- | --- |
| 1 | 任务状态机「**13 态**」 | **15 态** | `main.py:312-328 TaskStatus = Literal[...]` |
| 2 | 审批门禁「**六个条件**」 | **7 个判定**（5 常规 + 2 独立复核）| `services/approval_policy.py:82-115` |
| 3 | 思维导图「6 条件二次判断」 | 「7 判定二次门禁」 | 同上 |
| 4 | 「**高风险**默认禁止自审」 | **7 个操作全部禁止自审** | `operations.py` 无一覆盖默认 `False` |
| 5 | 对照表「6 条件自动推进」 | 「**7 判定**自动推进」 | 同上 |

另在 §4.5 补入 `runtime.execute` 的已知缺陷（F8）指引，并补入敏感文件匹配的**误报面**说明。

---

## 4. 15 个发现总览

全部为**实测 + 源码对照**，非推测。完整证据见 `02`，修复方案见 `05`。

| ID | 问题 | 严重度 | 生产在线 |
| --- | --- | --- | --- |
| **F1** | 未认证可读运维指标端点（`/api/enterprise/ops/metrics`）| 中高 | ✅ |
| **F2** | Runtime 路由无权限门禁，文档声称的 403 不成立 | **高** | 结构同源 |
| **F3** | `/ops/status` 的 `dependencies` 恒为空（修了 metrics 漏了 routes）| 低 | 待确认 |
| F4 | Runtime 前端守卫误用 `rag:search` | 低 | — |
| F5 | E7 角色矩阵与代码不一致（auditor 审批）| 低 | — |
| F6 | `rag/reports` 泄露服务器绝对路径 | 低 | 待确认 |
| F7 | 报告投影 `generated_at`/`index_id` 为 null | 低 | — |
| **F8** | `runtime.execute` 治理注册不可执行（权限无人拥有 + 无执行器）| 中高 | 待确认 |
| F9 | `AppShell` 测试夹具路由漂移 | 极低 | — |
| F10 | 「任何场景禁止 downgrade」表述过度 | 极低 | — |
| F11 | `known-flaky` 只写在文档，代码无标记 | 低 | — |
| **F12** | 生产安全头重复（`DENY,DENY`）+ 验收脚本只查存在 | **中** | ✅ |
| **F13** | `backup.sh` 把 DB 口令放进进程命令行 | 中 | ✅ |
| **F14** | 失败任务被误标 `context_missing` | 中 | 待确认 |
| **F15** | 测试执行两套实现漂移 → 回放结论相反 | 中高 | — |

### 4.1 最该记住的一条归纳

**F12 和 F2 栽在同一件事上——验证只覆盖了「东西存在」，没覆盖「东西正确」**：

- `deploy/scripts/verify-https.sh:37-41` 查**头名**不查**值** → `DENY,DENY` 一路通过还打印「存在」；
- E5 只写了**前端**路由测试、没写**后端 403** 测试 → auditor 直连 API 能绕过，验收却是绿的。

---

## 5. 运行环境速查

### 5.1 本地企业版（可做实验，无副作用）

```powershell
# 首次（建库 + 迁移）
.\deploy\dev-enterprise-local.ps1 -InitDatabase
# 日常（后端 + Worker + 前端）
.\deploy\dev-enterprise-local.ps1
```

| 组件 | 地址 |
| --- | --- |
| 企业前端 | `http://127.0.0.1:5178` |
| 企业后端 | `http://127.0.0.1:8003`（`APP_ENV=enterprise`）|
| Redis / Milvus / MySQL | `127.0.0.1:6379` / `:19530` / `:3306`（库 `agent_enterprise`）|

登录：`admin@company.com` / `Admin123!`（团队角色 `admin` + 企业角色 `tenant_owner`）

**注意**：企业版**禁止静默降级**（`RAG_MOCK_FALLBACK=false`），且
**`MILVUS_COLLECTION_PREFIX` 必须为空**——本机 Milvus 的 collection 名是 `code_<index_id>`（无前缀）。

### 5.2 生产环境（**只读**）

`https://agentforge.ltd/` ｜ `http→308→https` ｜ `nginx/1.24.0 (Ubuntu)`

**生产是 apponly 栈**（实测 `/api/health`）：
```json
{"db":{"status":"ok"},"milvus":{"status":"skipped","reason":"use_milvus=false"},"redis":{"status":"ok"}}
```
→ **无 Milvus**，所以 `/enterprise/rag` 的**检索**面板预期会失败/降级（已声明边界），
而**评测报告**面板不依赖 Milvus，应正常出列表。

**生产上不要点**：重建索引（烧 embedding 配额）、同步 Gitee（创建真 Issue）、
启动 Runtime / 执行审批（调真实 LLM，可能创建真 PR）。详见 `03` §1。

---

## 6. 相关但不属于本目录的文档

| 需要什么 | 去哪 |
| --- | --- |
| 项目正式文档索引 | [`docs/README.md`](../README.md) |
| 企业版正式文档 | [`docs/enterprise/`](../enterprise/README.md) |
| 开发记录（每功能一档，含真实截图）| [`docs/project-development/`](../project-development/README.md) |
| 面试速记（作者本人整理）| [`docs/杨志泽民·项目笔记.md`](../杨志泽民·项目笔记.md) |
| 交付进度看板 | [`PROJECT_PROGRESS.md`](../../PROJECT_PROGRESS.md) |
| 试点交付包（含边界免责）| [`docs/enterprise/10-pilot/`](../enterprise/10-pilot/README.md) |

---

## 7. 这份材料自己的边界

- **只覆盖实测过的内容**：每条结论都标了 `[码]`（读过源码/实测）、`[推]`（机制确认，设计意图是解读）、
  `[待补]`（未逐行核对）。答案卡 `04` 的 `[待补]` 已于 2026-10-08 补完。
- **不代表项目正式验收结论**：正式结论以 `PROJECT_PROGRESS.md` 与 `docs/enterprise/` 为准。
- **15 个发现是「额外发现」，不是「项目失败」**：这个项目在**诚实性**上做得比绝大多数项目好
  （降级显式标注、未验证能力不伪装、质量门不过就如实保留 `needs_improvement=true`）。
  这些发现恰恰是**读得够深才看得见**的那一层。
