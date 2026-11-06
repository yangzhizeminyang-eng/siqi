# 21｜两月多 Agent 冲刺计划（单人 + Claude/Codex，商业落地到 11 月）

> 目标：单人（8 小时/天）+ 多 Agent（Claude + Codex 当"随时在线的主力工程师"），在约 8 周（到 2026-11 初）内达成——
> **里程碑 A（第 4 周）**：把地基债清零，跑通「真持久化 / 真异步 / 真安全 / 端到端可演示」。
> **里程碑 B（第 8 周）**：企业能力做成「单租户真骨架 + SSO/计费/可观测 开关化」，不牺牲产品企业属性，同时经得起 Agent 岗面试追问。
>
> 责任人：小泽（决策/设计/验收/兜底）；Claude + Codex（编码/测试/重构主力）。

---

## 0. 分工原则（决定这张表怎么读）

多 Agent 是"加速器"不是"人手翻倍"。**能交出去**：范围明确的编码、补测试、重构、CRUD、迁移脚本初稿、文档初稿。**必须亲自盯**（表里标 🔴）：技术拍板、API/DB 契约、并发/锁/迁移关键区 review、真实端到端验证、客户/统筹。

标号：
- 🔴 = 你亲自做/亲审（不能丢给 Agent）
- 🟡 = Agent 做初稿，你 review 后定稿
- 🟢 = Agent 全权做，你抽查

---

## 阶段一 · 里程碑 A：还地基债（第 1–4 周）

> 唯一目标：把「内存 + 同步 + 壳」推进到「真持久化 + 真异步 + 真安全」。面试第一问"数据怎么存、异步吗"在这里解决。

### W1 · 业务数据持久化（本轮最高价值）
| 任务 | 分工 | 验收 |
| --- | --- | --- |
| `users/projects/repositories/agent_tasks/plans/diff_files/test_runs/artifacts` 7 张表落 SQLAlchemy ORM，默认 SQLite | 🔴 定 schema；🟡 Agent 写 ORM | `orm.py` 全表齐；`create_schema()` 建表 |
| 全链路读写从内存 dict 切到 DB（读路径 > 写路径先后改） | 🔴 定迁移顺序 | 重启后任务/计划/审批/事件/审计仍在 |
| 一次性迁移脚本（内存 → SQLite，幂等） | 🟡 | 迁移后数据不丢、可重复跑 |
| 有状态恶劣路径回归：重启丢不丢、并发写冲突 | 🔴 亲验 | 通过即可锁死 |

### W2 · 真异步 + 崩溃恢复
| 任务 | 分工 | 验收 |
| --- | --- | --- |
| 索引构建/长链路挂 Celery 真派发（替代进程内搬运 `progress_*`） | 🟡 | HTTP 只触发，不阻塞请求；`use_celery=true` 时真队列 |
| 任务重试 + 幂等（`retry_count` / 幂等创建 / 审批互斥锁迁 Redis） | 🔴 review 关键区 | 双审批并发 / 重复创建不崩不重复 |
| `agent_checkpoints` 真写 + 崩溃续跑 | 🟡 | Agent 跑一半重启能续，不重头 |

### W3 · 真向量 + 多实例无状态
| 任务 | 分工 | 验收 |
| --- | --- | --- |
| 向量真写 Milvus（替换 `CODE_VECTOR_STORE` dict 兜底路径） | 🟡 | `use_milvus` 时真检索，兜底保留 |
| 会话/锁迁 Redis，两实例可并跑 | 🔴 | 无进程态假设；`threading.Lock` 收敛 |

### W4 · 端到端真跑 + 补截图（里程碑 A ✅）
| 任务 | 分工 | 验收 |
| --- | --- | --- |
| 真实 Gitee 仓库完整跑通：Issue→分析→计划→审批→Diff→测试→PR | 🔴 亲验 | 产出**真截图/录屏**（回放页、审批流、Diff），补上现欠的 7.5 截图 |
| 把「内存→DB、同步→异步、为何规则基座兜底」写进开发日志/README | 🟡 | 面试能一句讲清取舍 |

## 阶段二 · 里程碑 B：企业版「单租户真骨架 + 开关化」（第 5–8 周）

> 原则：企业能力 = **开关 + 清晰方案**，不是"没做"。真接客户再逐项开（SSO→多租户并发→计费→沙箱安全→可观测→eval）。

### W5–6 · 单租户骨架 + SSO/令牌/安全
| 任务 | 分工 | 验收 |
| --- | --- | --- |
| 租户表 + 逐层 tenant 过滤骨架（检索/缓存/审计/向量 collection） | 🔴 定隔离边界；🟡 实现 | 单租户正确；方案文档写清多租户并发代价 |
| SSO/OIDC 开关 + 接入方案 | 🟡 | 开关可切；文档讲透代价 |
| Gitee token vault 化（不再明文内存/库） | 🟡 | token 不落明文 |
| Docker 沙箱真实安全（CPU/内存/网络限额、隔离、镜像供应链） | 🔴 review | 危险命令拦截 + 资源限额生效 |

### W7 · 可观测 + 部署
| 任务 | 分工 | 验收 |
| --- | --- | --- |
| 结构化日志 + trace/metrics + 简单 Grafana | 🟡 | 线上可定位"审批卡住"这类问题 |
| CI/CD + Helm + TLS + 备份恢复 + schema 迁移 | 🟡 | 升级不丢数据；有备份演练 |

### W8 · 商业化开关 + 简历收口（里程碑 B ✅）
| 任务 | 分工 | 验收 |
| --- | --- | --- |
| 计费/配额/席位做成开关 + 方案（真收费再开） | 🟡 | 方案清晰，不阻塞 PoC |
| Agent eval 黄金集 + 回归骨架（先建，不深做） | 🟡 | 模型升级有量化基线 |
| 简历三行收口：架构(内存→真持久/异步/编排) + 闭环(全链路+测试+门禁) + 工程(安全/RAG/审计) | 🔴 | 能扛追问，不虚报 |

---

## 单人 + 多 Agent 的三个特有风险（比工时更要盯）

1. **多 Agent 架构越写越漂**（各自生成、互不 review）→ 🔴 高危区（锁/迁移/异步竞态）**你亲自 review**，别交给 Agent 互审。
2. **"测试跑绿" ≠ "质量对"**（Agent 常测到边界窄）→ 🔴 你抽查并发重写、迁移脏数据、审批竞态这几条。
3. **决策瓶颈 = 你一个人**（选型/契约/架构要你拍板）→ 全丢给 Agent 会失控，全自己拍又拖慢；**按表里 🔴/🟡/🟢 分流**。

---

## 当前累计
- 里程碑 A（我们的相：真持久化 / 真异步 / LlamaIndex 全链 / MySQL / Milvus / 多 Worker）**已按源码+测试+真实运行推进**，见下方「里程碑 A 落地验收」。

---

## 里程碑 A 落地验收（2026-09-08，按源码/测试/真实运行/阻断四层）

| 项 | 状态 | 源码证据 | 测试证据 | 真实运行证据 / 阻断 |
| --- | --- | --- | --- | --- |
| 一 · LlamaIndex 全链（解析/切片/Embedding/Retriever） | **通过**（tree-sitter 网络受限走 NodeParser 回退=边界） | `retrieval/parsing.py`：CodeFile→`llama_index.core.Document`→`CodeSplitter`/`SentenceSplitter`/`TokenTextSplitter(NodeParser)`→`CodeMetadataAdapter`；主链不用 `chunk_text_by_lines`（已移除） | `test_rag_parse_chain`(7)：Document 创建+metadata、NodeParser 调用、fallback 诚实、行号/符号保留 | 真实 Milvus 完整链路通过。tree-sitter CodeSplitter 语法二进制需运行时下载，本机网络受限 → 回退 `TokenTextSplitter`（**仍属 LlamaIndex NodeParser 链路**，功能等价，非阻断） |
| 二 · MySQL 生产承载 | 通过 | `runtime_config` team/prod 强制 mysql 禁 sqlite；`scripts/init_mysql.py` 建库建表（18 表） | `test_mysql_integration` 真实 MySQL **6 passed** | 真实 MySQL 8.0.44：`scripts/mysql_e2e`（team 模式）= API 建任务→MySQL 落库→重启 restore→API 读回 **PASS**；agent_mvp_test 18 表 + 业务/审计落 MySQL |
| 三 · Milvus 真实召回 | **通过（Standalone 已验证）** | `query_index_from_chunks` + `ensure_milvus_collection_loaded`(跨进程 load) | `test_rag_milvus_realtime` **3 passed + 1 skip**（skip=关键词兜底未命中，非 Milvus 阻断）；source=fallback/milvus/raise 全验；`test_milvus_standalone`（门禁 skip） | **Milvus Standalone v2.3.10（19530，Attu 可见）真实写→查 source=milvus；跨进程 load 后不 released**；Lite 为开发默认、Standalone 为生产已验证推荐（边界，已还原对称引用，非阻断） |
| 四 · 多 Worker / Redis 幂等 | 通过 | `advance_task`/`run_agent` 幂等 + `RedisLockBackend`（`REDIS_URL` 时）+ `_AGENT_ENQUEUED` 清理 | `test_celery_worker_runtime`/`test_celery_dispatch`/`test_celery_retry_and_config` | 真实 Redis 7.4.11 + **2 个 Celery worker（mw-w1/w2）**：同一任务重复派发 run_agent×2 → 最终 DB `WAITING_APPROVAL / plans=1 / provider=deepseek`（不双推进）；worker 日志 `received → Worker 消费 retry=0/3 开始 → 完成 → succeeded`；`get_lock_backend()`=RedisLockBackend |
| 五 · Gitee→Issue/自动Task→索引 E2E | **通过（Issue+自动Task+git-clone 索引+wait_index 恢复+真实 PR 全真实验证）** | `create_gitee_issue` 用 Gitee SDK 端点 `POST /repos/{owner}/issues`+`repo`；`sync_team_request_to_gitee` 投递+worker 自动建 Task；索引用 `repo_mirror`(git clone/fetch/worktree)+`resume_tasks_waiting_for_index` | 单测：`test_dev016b_sync_gitee` 端点；`test_gitee_e2e_live.py`/`test_repo_mirror.py`/`test_pr_creation.py`/`test_wait_index_resume.py`（真实或默认 skip）；异步链/自动建任务单测 | 真实 team 态：sync→`queued=true,mode=redis`；Gitee Issue **IKE43I**；自动建 **TASK-8801**；**git-clone 索引→COMPLETED**（不再 403）；**wait_index 自动恢复**→ run_agent(29.4s/36.8s DeepSeek) → context/analysis/plan/diff/test/pr_desc；**scratch 仓真实 push+建 PR**（`git-repo-test` PR #1，见 docs/29） |
| 六 · 文档同步 | 通过 | 04/06/22/23/24/27/28 + 本表已按现状改口径 | — | 文档不再把 fallback/mock 写成真实；可复验命令与阻断原因已列 |

**是否允许宣称「里程碑 A 全盘通过」：是**（真实闭环 + 全部真实证据；唯一保留为可选增强）。已通过=MySQL、多 Worker/Redis 幂等、LlamaIndex 主链、读层（含审计/索引/检索/代码块 DB 真源）、审计、Gitee→Issue/自动Task（真实 IKE43I+TASK-8801）、**git-clone 索引真实 COMPLETED**、**wait_index 自动恢复闭环真实**、**真实 Diff + Docker 测试 + 真实 Gitee PR（scratch 仓 git-repo-test PR #1）**、**Milvus Standalone 真实写入+跨进程召回 source=milvus**、**API fresh-load**、**文档/配置/读层一致性已收敛**（docs/08 读接口无副作用、docs/04 物理表名、frontend/.env=real 对齐 docs/24、PERSIST_STRICT 写可观测）。可选增强=tree-sitter 网络受限走 NodeParser 兜底（功能等价）；Milvus Lite 为开发缺省。全量 402 passed。详见 docs/29、docs/24。