# 项目开发记录目录

这个目录专门放项目开发过程文档，后面每完成一个模块，都往这里补真实记录。

## 功能开发顺序

| 顺序 | 文件 | 模块 | 状态 |
| --- | --- | --- | --- |
| 00 | `00-project-development-log.md` | 总表、模板、记录规则 | 维护中 |
| 01 | `01-frontend-ui-baseline.md` | 前端 UI 基线 | 已完成 |
| 02 | `02-login-authentication.md` | 登录、会话恢复、退出和角色权限 | 已完成 |
| 03 | `03-password-recovery-and-legal-pages.md` | 密码恢复、管理员应急恢复、服务协议和隐私政策 | 已完成 |
| 04 | `04-project-repository-config.md` | 项目创建、Gitee 仓库配置、命令白名单、审批策略和审计事件 | 已完成 |
| 05 | `05-gitee-issue-task-creation.md` | 通过 Gitee Issue 创建 Agent 任务、Issue 快照、重复任务提示和分析队列入口 | 已完成 |
| 06 | `06-agent-task-module.md` | Agent 任务核心流转、任务筛选、需处理优先级、取消和重试 | 已完成 |
| 07 | `07-ai-issue-analysis.md` | AI Issue 分析、阻塞问题确认、分析重新生成、注入告警 | 已完成 |
| 08 | `08-code-rag-index.md` | 代码 RAG 与索引、检索上下文、过期门禁、重试与重建 | 已完成 |
| 09 | `09-modification-plan.md` | 修改计划生成、版本化、风险重算、进入待审批和历史版本查看 | 已完成 |
| 10 | `10-approval-module.md` | 审批人指派、审批中心、审批记录、风险调整与 Diff 前置门禁 | 已完成 |
| 11 | `11-diff-generation-and-display.md` | Diff 生成、演示兜底、按文件展示、Patch 复制和回放事件 | 已完成 |
| 12 | `12-test-execution.md` | 测试执行、危险命令拦截、失败重试与测试记录 | 已完成 |
| 13 | `13-pr-description.md` | PR 描述生成、版本化保留、过期重新生成与复制全文 | 已完成 |
| 14 | `14-task-replay-audit.md` | 任务回放与审计、事件/步骤分离、关联跳转与只读审计 | 已完成 |
| 16-A | `16-team-request-draft.md` | 团队版平台需求/缺陷草稿创建、`REQ-*` 编号、白名单校验和只读权限 | 已完成 |
| 16-B | `17-team-request-sync-gitee.md` | 团队版平台需求同步 Gitee Issue、映射字段、失败重试和审计回放 | 已完成 |
| 16-C | `18-team-request-create-task.md` | 团队版已同步需求一键创建 Agent 任务、自动创建开关、任务复用和来源闭环 | 已完成 |
| 017 | `19-team-context-fill.md` | 任务创建后自动拉 Issue/Commit、索引 fresh 分流、自动检索、历史相似任务、context_missing 人工补充恢复 | 已完成 |
| 7.5 | `20-team-analysis-plan.md` | 团队版 AI 分析四源输入、计划五要素与结构化门禁、版本重写上限、风险重算可见、历史任务仅参考 | 已完成 |
| 019-R | `34-realtime-sync-sse-pubsub-websocket.md` | 团队版 Redis Pub/Sub + SSE 实时同步、WebSocket 选型对比、真实联调与面试回答 | 已完成 |
| DEV-095 | `95-rag-quality-evaluation-and-rollout.md` | RAG 离线评测、真实仓库质量对比与团队/企业灰度策略 | 已完成 |
| DEV-096 | `96-team-cloud-deployment.md` | 团队版 Docker Compose、生产镜像、Runner 安全约束、云服务器部署与真实 Gitee RAG 验收 | 基础云端与 `health-middle` RAG 已验收，评测集/压测/HTTPS 待完成 |
| DEV-097 | `97-dashboard-delivery-throughput.md` | 工作台交付趋势与质量：7/30 天、双轴趋势、图例显隐、键盘详情、真实云端验收 | 已完成后端/前端测试、生产构建、云端重建、桌面与移动端交互验收 |
| DEV-098 | `98-enterprise-audit-center.md` | 企业审计中心、事件筛选、详情追踪、权限门禁与云端发布 | 已完成本地测试、生产构建、云端发布和公网页面验收 |
| E1-01 | `99-enterprise-e1-tenant-rbac-closure.md` | 多租户、企业 RBAC、项目隔离、审计权限闭环和前端权限兼容 | 云端 `<production-host>:8081` 应用-only 企业栈 auditor 真实浏览器走查已闭环；真实 MySQL 迁移 (TEXT DEFAULT) 缺陷已修复；多副本 worker 企业 Milvus 并发压测另验 |
| E2-01 | `100-enterprise-e2-01-approval-governance.md` | 企业审批与治理门禁、风险分级、审批状态机、执行前重校验、企业审批操作台、多副本执行一致性、成员特权治理边界、企业版独立 Compose 部署 | 本地验收与部署前配置已通过，可进入云端部署前准备；云端 tenant/RBAC、审批主链、多副本、HTTPS 与恢复演练尚未验收，未上线 |
| E3-01 | `101-enterprise-e3-01-rag-hybrid-retrieval.md` | 企业级 RAG 强过滤 + Hybrid 检索基线：TenantContext 边界、`rag:search`、MetadataFilters 候选受限、BM25+向量+RRF 融合去重、统一契约、stale 标记 | E3 专项 `11 passed`；本轮 E3/E1/E2/RAG 安全回归 `80 passed`；本轮后端全量 `612 passed, 24 skipped`（4 个既有 deprecation warnings）；无前端页面（本轮后端检索能力） |
| E3-02 | `102-enterprise-e3-02-query-rewrite-expansion.md` | Query Rewrite + 多路 Query Expansion：原 query 恒首位、LLM 可选确定回退、技术术语扩展（错误码/接口路径/点分↔下划线）、多路加权 RRF、`query_variants/variant_count/retrieval_trace`、安全默认配置；`security_admin` 可检索，`auditor` 仍拒绝 | `test_enterprise_e3_02_rig.py` `10 passed`；E3+E1+E2+E2-hardening+RAG `90 passed`；后端全量 `622 passed, 24 skipped, 4 warnings`；Rerank/离线评测集未落地；无前端页面 |
| E3-03 | `103-enterprise-e3-03-rag-rerank.md` | Rerank 精排基线：deterministic heuristic（符号/路径/错误码命中优先、fresh 优先、保留基础分、只重排 top_n 不删候选）、可选 LLM 摘要精排稳定回退并透传 timeout、`rerank_enabled/provider/trace` + 每结果 `original_rank/reranked_rank/rerank_score/rerank_reason` | `test_enterprise_e3_03_rerank.py` `9 passed`；E3+E1+E2+RAG `99 passed`；后端全量 `631 passed, 24 skipped, 4 warnings`；离线评测集/前端/云端 Milvus 未落地；无前端页面 |
| E3-04 | `104-enterprise-e3-04-rag-offline-evaluation.md` | 离线评测基线：Recall@K/Precision@K/MRR/NDCG@K/Freshness/Empty/Unauthorized Pass、JSON/JSONL 评估集、复用企业搜索服务、summary/per_case/failed/metrics_by_tag/config_snapshot | `test_enterprise_e3_04_evaluation.py` `11 passed`；E3+RAG+E1+E2 `110 passed`；后端全量 `650 passed, 16 skipped`；云端真实 Milvus/前端评测台/Cross-Encoder 对比未落地；dry-run 可复现 | 补 E3-04-HARDEN-01（L1–L4 收口：纵深 rag:search、真值去重、`retrieval_passed`、tag precision）：专项 `15 passed`、定向 `114 passed`、全量 `654 passed, 16 skipped`；真实生产评测集/云端 Milvus/前端评测台/Cross-Encoder 仍未完成
| E3-05 | `105-enterprise-e3-05-rag-quality-baseline.md` | 真实脱敏评测集 + 质量基线报告：32 条案例、脱敏 demo 种子、复用企业搜索、Summary/metrics_by_tag/failed/config_snapshot/dataset_size/thresholds/needs_improvement、CLI(--memory-seed/--dry-run/--top-k) | 内存态基线(K=8) recall .8750/mrr .8281/ndcg .8519/precision .1172/fresh .967/empty .067/unauthorized 1.0；`test_enterprise_e3_05_quality_baseline.py` `8 passed`；定向 `122 passed`；全量 `662 passed, 16 skipped`；云端 Milvus/前端评测台/Cross-Encoder/真实生产达标分数未完成 |
| E3-06 | `106-enterprise-e3-06-real-repository-quality.md` | 四模式评测框架（bm25/vector/hybrid/rerank，共享同一过滤）+ require-milvus 门禁 + 报告契约（validation_status/vector_backend/fallback_used/latency(p50,p95)/dataset·config hash/Precision 阈值/needs_improvement） | 框架专项 `10 passed`；真实 health-middle + 企业 Milvus verified 已闭环；质量门仍 `needs_improvement=true`，不能宣称生产质量达标 |
| E3-06-CLOUD-HEALTH-MIDDLE | 真实目标仓库 health-middle 评测 | 真实 commit 73e7e54 + 67 条真实评测集（source_provenance）+ 303 真实切片索引；企业独立 Milvus collection `enterprise_code_idx_health_middle_73e7e54`；`zhipu/embedding-3` 2048 维；四模式 verified 报告已生成 |
| E3-06-CLOUD-VERIFY | 真实 health-middle + Milvus 验证 | `--require-milvus --all-modes` exit 0；`validation_status=verified / vector_backend=milvus / milvus_verified=true / fallback_used=false`；cloud evidence collection/index/dimension/query 全真；两次非时间字段对拍 `differences=0`；主报告 precision 未过阈值，仍需质量调优 |
| E3-07 | `107-enterprise-e3-07-rag-quality-tuning.md` | 企业 RAG precision 调优（`RAG_SYMBOL_BOOST` 精确符号定义优先） | **Done（真实 Milvus verified 调优闭环）**：`test_enterprise_e3_07_precision_tuning.py` 10 passed；起企业独立 Milvus+真实 zhipu/embedding-3，`--require-milvus --all-modes` 四模式全部 verified；同环境(boost 0→1.0) bm25 recall .8731→.8881 / mrr .8072→.8632 / ndcg .8227→.8620 / prec@5 .3045→.3134，hybrid rec .8806→.8955 / prec@K .3377→.3396；生产默认 hybrid 门 prec@K≥.3 且 `needs_improvement=false`；两跑对拍差异 0；`--all-modes` bm25 主报告 prec@K .2034 受单符号 gold 1/K 上限如实 needs_improvement=true（非伪报）。报告：`e3_07_health_middle_quality_tuned.json`（+baseline_verified / verified_repro / hybrid_gate） |
| E3-08 | `108-enterprise-e3-08-rag-evaluation-ui.md` | 企业 RAG 检索与评测前端控制台 | **Done**：后端新增 `GET /api/enterprise/rag/reports` 与 `/reports/{name}`（强 rag:search + 路径穿越防护 + 脱敏投影）；前端 `/enterprise/rag` 双面板（检索 + 评测报告，summary/metrics_by_tag/mode_comparison/failed_cases + verified/local_only/blocked 徽章 + needs_improvement 提示，含 loading/empty/error/403、响应式、键盘）；AppShell 侧栏入口权限 `['rag:search']`（auditor 无入口/直达重定向/后端 403 兜底）。测试：后端专项 8 passed、前端专项 10 passed、前端全量 250 passed、后端全量 741 passed/16 skipped、vue-tsc 0 错、vite build 通过；真实浏览器截图 `assets/e308/e308_01..03*.png`；残余：在线评测触发入口未做 |
| E3-09 | `109-enterprise-e3-09-cross-encoder.md` | Cross-Encoder 模型级精排对比 | **Done（真实模型执行）**：新增 `cross_encoder.py`（本地模型优先/batch/超时/候选上限/模型缓存/不发代码正文/不可用回退 heuristic 并记录 fallback）；`RAG_CROSS_ENCODER_*` 安全默认关 + env.example/config_snapshot；`requirements-e3-09.txt` 固定 sentence-transformers==3.3.1；CLI `--rerank-compare` 同环境对比 baseline/heuristic/llm/CE。真实 Milvus + ms-marco-MiniLM-L-6-v2（cpu）实测：CE MRR .8507 / NDCG .8090（四档最优，status=executed），baseline .8172/.7981、heuristic .8116/.7934、llm .7668–.7873（随机）；recall/prec@K 各档一致；CE 缓存后 p50 331ms；两次对拍 CE/heuristic/baseline 差异 0、仅 LLM 波动。专项 17 passed、E3 组合 78、后端全量 758 passed/16 skipped、前端 250 passed；未改 E3-08 页面。不宣称企业 RAG 已彻底完成 |
| E4-01 | `110-enterprise-e4-01-runtime.md` | Multi-Agent Runtime 编排/checkpoint/重试/人工接管 | **Done（真实闭环）**：确定性编排核心 + 真实 MySQL 持久化（Alembic `e5f6071a2b3c` + repositories 写透 + bootstrap 启动恢复）+ **跨进程重启恢复**（RC2 checkpoint 落库→新进程恢复→RESTART_FINAL succeeded）+ **DB 原子租约单赢家**（条件 UPDATE + lease_expires_at）+ **真实 E2 审批接线**（runtime.execute 操作 + runtime_task 校验器 + approve/reject 回写）+ 失败→重试→waiting_human→人工 resume→succeeded + 跨租户 403+审计 + checkpoint 脱敏。`test_enterprise_e4_runtime.py` 19 passed（含真实 DB round-trip）、E4+migrations+E2 gov/hardening 71 passed、后端全量 777 passed/16 skipped、真实闭环证据 `scripts/e4_runtime_real_loop.py`。残余：Redis 已起但租约原子性在 DB、未用 Celery 字面双 worker、未做生产级多副本压测/长稳、无前端页面 |
| E5-01 | `111-enterprise-e5-01-frontend.md` | 企业前端完整化（控制台打磨） | **Done（2026-09-24 更新，与 `111` 文档 §5 状态和 `PROJECT_PROGRESS.md` 一致）**：7 个企业页面（工作台/成员/审批/审计/RAG/Runtime/企业设置运维）已统一企业后台视觉、真实数据、权限与响应式；共享组件 `components/enterprise/`（StatusBadge/PageHeader/AsyncState/DetailDrawer/ConfirmDialog/PermissionGate）；管理员、developer、reviewer、auditor、tenant_admin 真实角色走查完成，auditor 直输 Runtime 重定向且后端保留 403；运维接口补 `org:manage` 后端门禁。证据：`npm run test` **264 passed**、`vue-tsc -b`/`vite build` 通过、E5 运维专项 4 passed、后端全量 772 passed/25 skipped、Impeccable detector `[]`、桌面与 390px 七页面无整体横向溢出、真实截图 `assets/e501/`。范围外：云端生产、多副本压测与长期稳定性属 E6 |

## 交付后优化池（OPT）

| 顺序 | 文件 | 模块 | 状态 |
| --- | --- | --- | --- |
| OPT-01 | `113-opt01-local-mcp-client.md` | 本地 stdio MCP Client 与受控 Semgrep 工具：双端策略校验、工具发现/取消/非法响应、类型/大小/并发/时限、审计关联 ID、真实本地冒烟证据 | **Done**：专项 21 passed；OPT-01+E4 Agent/Runtime 回归 74 passed, 1 deselected（Redis Celery 项归 OPT-05）；真实本地运行证据 `assets/opt01/mcp_semgrep_smoke.json` |
| OPT-02 | `114-opt02-agent-eval.md` | Agent 级 Eval 与版本回归：固定集 deterministic/隔离沙箱、报告净化（仅工具名）、逐 Agent 成功率/p50/p95/失败类别、同数据集 hash 对拍、`--allow-real-model` 显式 opt-in、environment 契约块 | **Done**：专项 17 passed；OPT-02+E4 Agent/Runtime 回归 70 passed, 1 deselected（Redis Celery 项归 OPT-05）；报告 `agent_eval_local_v1_verified.json` 与对拍 `agent_eval_local_v1_comparison.json`，成本 `not_collected` |
| OPT-03 | `115-opt03-e2e-demo.md` | 可复现多 Agent 端到端演示：Issue→6 Agent→真实 BM25 RAG→E2 审批门→受控变更/测试→审计回放；修复分支（repair_round）；全隔离 CLI（任意 cwd、敌对环境压制、无远端写模块） | **Done**：14/14 checks，证据 `assets/opt03/opt03_e2e_evidence.json`；专项 3 passed（跨 cwd 稳定投影对拍）；OPT-02/03+E4 回归 73 passed, 1 deselected；TDD 修复 `orchestrator._audit` 同秒 ID 覆盖 |
| OPT-04 | `116-opt04-rag-report-contract.md` | RAG 报告复现契约：`repro_contract` 固化仓库/数据集/索引/Embedding/Reranker/检索参数/依赖与配置 hash（凭据排除）；verified/local_only/blocked 三级 + 原因；`compare_rag_eval_reports` 离线对拍（回归/改善/环境漂移）；CLI `--compare-with` | **Done**：专项 15 passed；RAG 全回归（E3-02~E3-09）118 passed；双轮跨 cwd 对拍证据 `assets/opt04/`（非时间指标 delta 全 0.0，drift=false，local_only 不冒称 verified） |
| OPT-05 | `117-opt05-test-ci-stability.md` | 测试环境与 CI 稳定性：导入期隔离（ENV_FILE 哨兵，本地 .env/凭据/生产地址不进入测试；Celery 导入即 eager）、`requires_redis` 标记 + 收集期 TCP 探测统一 skip（环境缺失 vs 代码失败可区分）、固定命令 `scripts/check-all.sh`、CI 定义 `.github/workflows/ci.yml`（零机密/内存态/redis service） | **Done**：修复全量套件隐性依赖本机 Redis 缺陷（kombu 10061）；分类正/反分支子进程真跑自证；后端全量 `890 passed, 17 skipped`（全为显式环境门控）、前端 `272 passed` + 生产构建通过；证据 `assets/opt05/`；CI 未上云执行（如实标注） |
| OPT-06 | `118-opt06-agent-tool-security.md` | Agent 工具安全回归（六维度）：注入（`run_test` 危险命令+白名单双闸门；模型自述不得覆盖真实测试/证据结果）、跨租户参数篡改（`repository_id`/artifact ref/task ref 与 runtime ctx 比对，fail closed）、路径越权（工作区 `resolve+containment`）、超时重试（exit 124/异常不伪造成功）、重复副作用（`draft_pr`/`collect_evidence` 幂等 + `content_hash` 稳定指纹）、凭据脱敏（环境真实值/URL 凭据/查询串 token） | **Done（2026-10-15）**：专项 25 passed（TDD 红 15 failed → 绿）；相关回归 `143 passed, 1 skipped`；全量后端 `916 passed, 17 skipped`（853.16s）；顺带修复 OPT-03 跨进程 flake 根因（`executor._PROJECTION` set→有序元组 + 结构性回归，5 连跑绿）；证据 `assets/opt06/` |
| OPT-07 | `119-opt07-full-chain-observability.md` | Agent 全链路可观测性：叶子模块 `observability.py`（TraceSpan 内存 store + contextvars trace 绑定 + 出口脱敏 + retention 上限/dropped_total）；六维度埋点 model/tool/rag/approval/worker/artifact（真实 token 采集不到诚实记 `not_collected`；工具 span wrapper 与消息摘要兜底经 `_obs_tool_names` 去重；异常记 `error_kind` 如 `http_404`）；检索接口 `GET /api/enterprise/runtime/traces`（AUDIT_VIEW 门 + tenant 过滤 + filter/分页） | **Done（2026-10-16）**：专项 19 passed（TDD 红 16 failed → 绿）；相关回归（E4 Agents/OPT-03/OPT-06/E3-02/04/E2）`139 passed, 1 skipped`；全量后端 `935 passed, 17 skipped`（831.13s，= OPT-06 基线 916 + 19 新增）；演示 CLI 单 trace 29 spans 六类全覆盖 `checks=13/13`；证据 `assets/opt07/` |
| OPT-08 | `120-opt08-rag-online-evaluation-api.md` | RAG 在线评测入口：`POST /api/enterprise/rag/evaluations` 触发（新增 `rag:evaluate`；developer/auditor 403）+ `GET /evaluations(/{run_id})` 查询（`rag:search` + 租户过滤），报告回读复用 E3-08；作用域门（默认 `isolated` 内存态强制种子基线；`production` 需 `confirm_production=true` 否则 422 + 审计，确认后 `require_milvus` 语义 → Milvus 不可用即 blocked 不冒称 verified）；生命周期 queued→running→succeeded/blocked/failed + dispatch 诚实标注（redis/eager）；修复 `deny_and_audit` 固定 id 键冲突丢审计 | **Done（2026-10-16）**：专项 13 passed（TDD 红 12 failed → 绿）；相关回归 `102 passed`（77.38s）；全量后端 `948 passed, 17 skipped`（1162.47s，= OPT-07 基线 935 + 13 新增）；演示 CLI 16/16 checks（run=rageval_e83a40c8e922、succeeded/local_only、生产确认 blocked 诚实口径）；证据 `assets/opt08/` |
| OPT-09 | `121-opt09-embedding-index-lifecycle.md` | Embedding 与索引生命周期：构建时固化 provider/model/dimension 指纹（零网络探测）与索引版本；检索时向量相关模式（vector/hybrid/rerank）不兼容 → **409 fail-closed** + `tenant.rag.index.incompatible_rejected` 审计，bm25 放行但如实标注；无记录/维度未知 → unknown 放行不冒称兼容；同仓库+分支新索引 ready → 旧记录 superseded + 审计；失败/重试状态接真实管线（failed 阶段原因、retry_count 对齐）；新增只读接口 `GET /api/enterprise/rag/indexes(/{index_id})`（rag:search + 租户过滤） | **Done（2026-10-16）**：TDD 红 12 failed → 专项 `12 passed`（17.66s）；相关回归 `115 passed, 5 skipped`（483.61s，skip 全为 Milvus 环境门控）；全量后端 `960 passed, 17 skipped`（1253.22s，= OPT-08 基线 948 + 12 新增）；证据 `assets/opt09/` |
| OPT-10 | `122-opt10-rag-ablation.md` | 可复现 RAG 检索参数消融：12 检索配置 + 基线复跑 + 5 切片探针（每配置独立子进程 + hermetic 环境；per-config `config_sha256`；逐用例结果/失败样本/指标 delta/确定性对拍合成报告 JSON）；新增 Hybrid RRF 融合权重契约 `RAG_HYBRID_VECTOR_WEIGHT`/`RAG_HYBRID_BM25_WEIGHT`（默认 1.0/1.0 与历史公式逐位一致；0=禁用该路不查询、不产生候选）与切片参数 `RAG_CHUNK_LINES/OVERLAP_LINES/MAX_CHARS` import-time 覆盖 | **Done（2026-10-16）**：TDD 红 15 failed/2 passed → 专项 `17 passed`（290.91s）；相关回归 `188 passed, 1 skipped`（586.44s，skip 为 Milvus 门控）；全量后端 `977 passed, 17 skipped`（2104.48s，= OPT-09 基线 960 + 17 新增）；实测 `legs_small` 唯一改排名（mrr −0.0302）、`vector_only` mrr −0.1374（本地 mock 信号弱）、切片 `lines_20/80` 与基线逐位一致（如实记录当前 llama-index `chunk_lines` 不参与切分）；证据 `assets/opt10/` |
| OPT-11 | `123-opt11-main-split.md` | `main.py` 渐进拆分：六步按领域迁移 API/服务/依赖边界，83 条核心路由中 49 个处理器迁至 `app/api/` 12 个 APIRouter 模块（system/auth/users/projects/comments/notifications/repositories/repository_indexes/dashboard/audit/history/team_requests）；补丁安全导入规则（模块级仅限注册块前且未被整体替换的符号，其余函数体内 `from app import main` 惰性读取）；`app.openapi()` 117 条操作/83 路由名冻结锚点 | **Done（2026-10-16）**：锚点 `test_opt11_main_split.py` `4 passed`；六步回归链红区单调收窄（1 failed → … → `104 passed`），全链 `151 passed`（125.82s）；全量后端 `981 passed, 17 skipped`（1572.75s，= OPT-10 基线 977 + 锚点 4 新增，`EXIT=0`）；`main.py` 9177→7651 行；首轮 1 failed 假红（MCP 取消计时 3.0166s vs 3.0s）修复为 5.0s；证据 `assets/opt11/` |
| OPT-12 | `124-opt12-doc-consistency.md` | 文档状态与时间线一致性：把 OPT-11 拆分后失效的 83 处 `main.py:<行号>` 引用全量锚点化为「文件路径（符号名）」（迁出的处理函数指向 `backend/app/api/` 新址），新增引用防腐检查器 `backend/scripts/opt12_doc_consistency.py`（A 禁行号引用 / B 路径存在性 / C 符号锚点可解析；`--json`、`--corpus`、退出码门禁），并处理时间线陈旧项（00-log 截图状态、`docs/22` 复核、`docs/23` 检查点口径、`03` 截图重拍回填） | **Done（2026-10-16）**：HEAD 语料 60 份 → 77 违规（73 stale_line_ref + 4 missing_path），修订后 72 份 → **0 违规**（98 个锚点逐一校验，复现命令见 124 文档）；门禁测试 `test_opt12_doc_consistency.py` `5 passed`（2.56s）；前后对比 JSON `assets/opt12/`；03 截图 4 张 `docs/assets/*-real.png` |
| OPT-13 | `125-opt13-celery-multiworker-recovery.md` | Celery 多 Worker 恢复与长稳验证：真实双 Worker 子进程（`--pool=solo --prefetch-multiplier=1`）+ fakeredis TcpFakeServer（真实 RESP/Lua）+ 真实 SQLite + `taskkill` 真杀进程 + deterministic provider；四场景 harness（S1 恰好一次 / S2 双投递 / S3 崩溃恢复 / S4 有界长稳）+ 确定性探针 + `scripts/opt13_trace_restore.py` 残锁加固（无 Lua mutex：SET NX PX + WATCH/GET/MULTI/DEL CAS）；冷启动方差按上界设超时（子进程 240-300s，超时收敛 `rc=-998` 场景级失败不升级 fatal；S4 补投 `run_children_parallel` 共享 deadline） | **Done（2026-10-16）**：harness `36/36` checks 全绿（S1 4/4、S2 10/10、S3 15/15 kill→收敛 111.28s、S4 7/7 kill→全量收敛 104.7s，退出码 0，`final_run_20261016_1503` 双份保全）；专项 `21 passed`（16 机制 + 5 装配门禁）；全量后端 `1007 passed, 17 skipped`（1601.60s = OPT-11 基线 981 + OPT-12 门禁 5 + 本轮 21）；三轮运行对照（run1/run2 失败报告如实留档）；关键实证：S2 真实可见性重投（150s 探针持锁 > 30s 超时）与 S4 恢复由 A 首扫触发，断言不绑定重投者；证据 `assets/opt13/` |
| OPT-14 | `126-opt14-identity-audit-tenant-isolation.md` | 企业身份、审计与租户隔离增强：审计导出 `GET /api/audit-events/export`（JSONL/CSV + 事件类型/资源/时间窗过滤 + 截断诚实标注响应头 + 导出自审计 `tenant.audit.exported` + `audit:view` 门 + 租户作用域）；审计保留 `app/tenancy/audit_retention.py` + 默认 dry-run CLI（三道防误删闸：`days<=0` 拒绝 / 下限 30 天默认 / 上限截断）；`AUTH_MODE` fail-closed（默认 local；非 local 拒绝启动，不静默降级）；隔离冻结回归（ORM 30 表分类 / 7 角色权限矩阵 / 跨租户读隔离 / 伪造租户头） | **Done（2026-10-16）**：harness 17/17 checks（真实 uvicorn HTTP + 真实 SQLite 子进程 CLI + AUTH_MODE 子进程）；专项 26 passed（41.41s，OPT-14 22 + OPT-11 冻结受控 4）；全量后端 `1029 passed, 17 skipped`（1472.18s，= OPT-13 基线 1007 + 本轮受控新增 22，EXIT=0）；路由冻结受控 117→118；证据 `assets/opt14/` |
| OPT-15 | `127-opt15-git-host-provider.md` | GitHub/GitLab 代码托管适配：托管平台无关 Provider 契约 `app/services/git_host/`（`contract.py`：runtime_checkable Protocol `GitHostProvider` 8 方法 + 错误分类 `kind` + `redact` 脱敏；`gitee.py`：`GiteeProvider` 实现仓库解析/远程构造/掩码/分支 head/建 Issue/查重/快照/PR，PR 委托既有 `gitee_pr_service` 保持 monkeypatch 补丁链；`registry.py`：`get_git_host_provider`）；fail-closed 双重门（启动期 `GIT_HOST_PROVIDER` 非 gitee 拒绝启动 + 调用点 `GitHostError(kind=unsupported_provider)`，绝不静默回退）；E4 PR 采纳接缝最小化（`create_runtime_pr` 经注册表 + `provider` 字段，团队版链路冻结）；查重分页语义（6 页上限/per_page=100/state=all 命中即停）；传输对齐团队版（GET token 走 query/POST 走 body/12s 超时，修正双 `?` 拼接缺陷） | **Done（2026-10-16）**：harness 16/16 checks（本地 stub 127.0.0.1 真实 socket + 真实 git 二进制 bare 仓库全链路 + 子进程 fail-closed）；专项 26 passed（24.60s，TDD 红→绿）；采纳兼容回归 64 passed, 2 skipped（4.11s）；全量后端 `1055 passed, 17 skipped`（1338.55s，= OPT-14 基线 1029 + 26 新增，EXIT=0；首轮 3 failed 为 OPT-10 用例 OpenBLAS 内存分配环境性假红，隔离复跑 3 passed 后复跑全绿，如实留档）；真实 Gitee 远端写入 not_collected（零真实远端写入）；证据 `assets/opt15/` |
| OPT-16 | `128-opt16-resume-evidence-pack.md` | 简历成果证据包：声明 → 归档证据的机械可核对索引——`assets/opt16/opt16_evidence_index.json`（22 条声明 `C01`–`C22` + 7 条边界 `B01`–`B07`；statement/kind/metrics/evidence_files/markers/repro；kind 四档 `production_cloud`/`local_real_ext`/`local_offline`/`synthetic`；61 条字面 marker 指向真实归档证据：assets/opt01..15、`backend/evaluation/` 评测报告、云端走查截图、E6/E7 文档）+ 校验器 `backend/scripts/opt16_evidence_check.py`（A schema/ID/kind、B 证据存在性+仓库外拒绝、C marker 字面命中、D 主文档覆盖全部 ID；退出码门禁 + JSON 报告）+ 门禁测试 `backend/tests/test_opt16_evidence_pack.py` | **Done（2026-10-16）**：TDD 红 7 failed → 绿 `7 passed`（1.08s，侦测覆盖 marker 篡改/证据缺失/kind 非法/主文档缺 ID/仓库外路径）；校验器对真实索引 `violation_count=0 / ok=true`（EXIT=0，报告归档 `assets/opt16/opt16_evidence_check.json`）；相关回归 12 passed（2.37s）；文档一致性语料 76 份 0 违规；全量后端 `1062 passed, 17 skipped`（1439.00s，首轮即绿 EXIT=0，= OPT-15 基线 1055 + 本轮 7 新增，日志归档 `assets/opt16/opt16_full_pytest.txt`）；边界 7 条如实入档（零远端写入/OIDC 未接入/RLS 未启用/token·Redis·真实模型评测 not_collected/E6 回滚限制） |

## 修复记录

修复类文档不插入主线功能顺序，统一放在 `90+`，避免干扰 MVP 功能链路。

| 顺序 | 文件 | 说明 |
| --- | --- | --- |
| 90 | `90-fix-refresh-redirect-to-login.md` | 修复刷新页面误跳登录 |
| 91 | `91-fix-refresh-badge-flicker.md` | 修复刷新后审批角标闪烁 |
| 94 | `94-repository-lifecycle-isolation.md` | 修复仓库地址切换后旧任务混入，增加仓库生命周期与项目任务隔离 |

## 面试复盘文档

| 文件 | 说明 |
| --- | --- |
| `92-rag-llamaindex-interview-review.md` | RAG、LlamaIndex、Milvus 专题问题与面试问答 |
| `93-project-problem-interview-review.md` | 项目问题、根因、解决方案、证据和面试回答总表 |

## 资产目录

| 路径 | 作用 |
| --- | --- |
| `assets/` | 模块文档使用的真实运行截图 |
| `assets/screenshots/` | 早期前端基线截图 |

## 记录规则

- 每个模块都要有真实截图。
- 每条记录都要写清功能、问题、解决方案和验收结果。
- 功能变更后，要同步更新对应的设计文档。
- 截图只放真实页面、接口或控制台结果，不放概念图。

## 推荐写法

每完成一个模块就新增一条记录：

1. 模块名称
2. 关联文档
3. 真实截图
4. 功能说明
5. 遇到的问题
6. 解决方案
7. 测试和验收
8. 复盘

## 全量代码审查整改（FIX）

| 顺序 | 文件 | 模块 | 状态 |
| --- | --- | --- | --- |
| FIX-01..05 / FIX-AUDIT-2 | `129-fix-runtime-shared-state-closure.md` | 评测 run / 索引生命周期 / Trace 跨进程持久化；审计时间源；`max_delete=0` 防误删；第二轮补跨租户 supersede、tokens 脱敏与证据时点 | **Done（2026-10-18）**：第二轮 FIX 专项 19 passed、相关回归 43 passed、E2E 20/20、本机隔离 MySQL 8 迁移+集成 8 passed、全量后端 1118 passed/13 skipped/0 failed、前端 272 passed + 构建通过；历史 1108/14 与外部审核 1100/22 分开记录；证据 `assets/fix/` |
