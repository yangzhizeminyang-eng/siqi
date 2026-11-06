# 文档索引

> 项目：**司契（Siqi）**——企业级 AI Agent 研发协作平台。名称释义见根目录 [`README.md`](../README.md)。

## 版本化目录

| 目录 | 说明 |
| --- | --- |
| `mvp/` | MVP v0.1 内测基线索引，指向已验收的 MVP 主文档与开发记录 |
| `team/` | 团队版增量文档，覆盖平台内创建 Issue、团队协作、审批分级、真实执行、持久化和验收 |
| `enterprise/` | 企业版正式文档，覆盖多租户、Multi-Agent、企业级 RAG、安全治理、部署交付和商业化 |
| `project-development/` | MVP 功能开发记录与真实截图索引 |
| `mvp-source-review/` | MVP 源码审查材料 |

## 使用入口

| 文档 | 说明 |
| --- | --- |
| [`USER_GUIDE.md`](./USER_GUIDE.md) | 本地启动、登录、MVP 与团队版实际操作、验收清单和常见问题 |

## 项目文档清单

| 编号 | 文档 | 路径 | 说明 |
| --- | --- | --- | --- |
| 01 | 项目立项文档 | `01-project-initiation.md` | 项目背景、定位、价值、MVP、技术路线、风险与企业化发展 |
| 02 | PRD 产品需求文档 | `02-prd.md` | 角色、用户故事、页面需求、状态、验收标准 |
| 03 | MVP 流程与 Mermaid 图 | `03-mvp-workflow.md` | 核心业务流程、状态机、时序图、审批流、数据关系草图 |
| 04 | 技术架构方案 | `04-technical-architecture.md` | 前后端架构、模块划分、API 草案、安全与审计原则 |
| 05 | MVP 里程碑计划 | `05-mvp-roadmap.md` | 阶段拆分、交付物、开发顺序与 Demo 成功画面 |
| 06 | RAG 知识库设计文档 | `06-rag-design.md` | LlamaIndex、Milvus、代码切片、向量检索、增量索引与评估 |
| 07 | UI 线框与高保真设计文档 | `07-ui-high-fidelity.md` | 页面线框图、视觉规范、组件说明、状态与交互细节 |
| 07-A | Stitch 中文界面生成提示词 | `07-stitch-prompts.md` | 总控提示词、分页面提示词和统一检查提示词 |
| 07-B | Stitch UI 评审与二次优化提示词 | `07-stitch-ui-review-prompts.md` | Stitch 生成稿问题清单和分页面返工提示词 |
| 08 | API 设计文档 | `08-api-design.md` | 接口边界、请求响应、错误码、状态流转 |
| 09 | 数据库设计文档 | `09-database-design.md` | 核心实体、ER 图、表结构、索引与审计数据 |
| 10 | AI 安全与边界文档 | `10-ai-safety.md` | 风险分级、命令控制、审批边界、RAG 安全与兜底策略 |
| 11 | 测试与答辩演示文档 | `11-test-and-demo.md` | 测试策略、RAG 测试、演示流程、兜底方案、验收清单 |
| 12 | 开发计划文档 | `12-development-plan.md` | 迭代顺序、RAG 阶段、开发节奏与同步规则 |
| 13 | 团队版文档索引 | `team/README.md` | 团队版目标、流程、接口、权限、部署和验收入口 |
| 13-0 | 团队版开发计划 | `team/00-team-development-plan.md` | 团队版 DEV-015 起步路线、阶段拆分和 Qoder/Claude 协作边界 |
| 13-A | 团队版 PRD | `team/01-team-prd.md` | 团队版详细 PRD，覆盖平台内创建 Issue、自动拉上下文、审批分级、自动产物、失败恢复和复用沉淀 |
| 13-B | 团队版业务流程 | `team/02-team-workflow.md` | 平台创建、Gitee 同步、自动分流、异常恢复和复用沉淀流程 |
| 13-C | 团队版 API 设计 | `team/03-team-api-design.md` | 需求草稿、Gitee 同步、审批分级、通知评论和复用索引接口 |
| 13-D | 团队版数据、权限与审计 | `team/04-team-data-permission.md` | 团队成员、Issue 映射、通知、复用摘要、权限矩阵和审计 |
| 13-E | 团队版部署与验收 | `team/05-team-deployment-acceptance.md` | 团队版部署配置、验收场景、测试清单和进入企业版标准 |
| 13-F | 企业版文档索引 | `enterprise/README.md` | 企业版正式文档入口、阅读顺序、总链路和维护规则 |
| 13-G | 企业版开发计划 | `enterprise/00-enterprise-development-plan.md` | 企业版阶段拆分、模块边界、接口签名和完成定义 |
| 13-H | 企业版 PRD | `enterprise/01-enterprise-prd.md` | 企业版完整产品需求、角色、模块、API、数据、部署和验收 |
| 13-I | 企业版业务流程 | `enterprise/02-enterprise-workflow.md` | 租户、任务、审批、Agent 状态和异常分流 |
| 13-J | 企业版 Multi-Agent 设计 | `enterprise/03-multi-agent-design.md` | Orchestrator、Supervisor、专业 Agent、工具边界和恢复 |
| 13-K | 企业版 RAG 设计 | `enterprise/04-enterprise-rag-design.md` | Hybrid RAG、MetadataFilters、RRF、Rerank、评测和索引治理 |
| 13-L | 企业版租户权限与安全 | `enterprise/05-tenant-permission-security.md` | 组织、成员生命周期、RBAC、隔离验收和权限矩阵 |
| 13-M | 企业版安全治理与审计 | `enterprise/06-security-governance-audit.md` | 风险分级、治理门禁、密钥、审计和可观测性 |
| 13-N | 企业版 API、数据库与迁移 | `enterprise/07-api-database-migration.md` | 接口响应、错误码、数据模型、迁移顺序和兼容策略 |
| 13-O | 企业版部署与运维 | `enterprise/08-deployment-ops.md` | 生产拓扑、HTTPS、备份、监控、发布和回滚 |
| 13-P | 企业版测试与交付 | `enterprise/09-test-delivery.md` | 测试矩阵、端到端验收、RAG 评测和交付证据 |
| 13-Q | 企业版商业化与 SLA | `enterprise/10-commercialization-and-sla.md` | 企业试点、配额、SLA、客户交付包和后续商业化 |
| 13-R | 企业版商业试点交付包 | `enterprise/10-pilot/README.md` | 演示数据/走查、客户手册、SLA、交付清单、试点验收脚本、边界与免责声明 |
| 14 | 团队版权限与协作文档（旧入口） | `14-team-permission-and-collaboration.md` | 旧草稿入口，后续以 `team/` 目录为准 |
| 15 | 团队版 API 增量文档（旧入口） | `15-team-api-increment.md` | 旧草稿入口，后续以 `team/` 目录为准 |
| 16 | 团队版部署与验收文档（旧入口） | `16-team-deployment-and-acceptance.md` | 旧草稿入口，后续以 `team/` 目录为准 |
| 17 | 企业版 PRD（旧入口） | `17-enterprise-prd.md` | 与 `enterprise/01-enterprise-prd.md` 内容一致；请以 `enterprise/` 为准 |
| 18 | 企业版多租户、权限与安全合规（旧入口） | `18-enterprise-tenant-permission-security.md` | 早期草稿，已被 `enterprise/05-tenant-permission-security.md` 取代 |
| 19 | 企业版部署架构与运维（旧入口） | `19-enterprise-deployment-ops.md` | 早期草稿，已被 `enterprise/08-deployment-ops.md` 取代（端口 8003 已作废） |
| 20 | 企业版 API、数据库与迁移（旧入口） | `20-enterprise-api-database-migration.md` | 早期草稿，已被 `enterprise/07-api-database-migration.md` 取代 |
| 21 | 企业版测试、验收与交付（旧入口） | `21-enterprise-test-and-delivery.md` | 早期草稿，已被 `enterprise/09-test-delivery.md` 取代 |
| 22 | 项目开发记录目录 | `project-development/README.md` | 每个功能模块的截图、功能、问题、解决方案和验收记录 |
| 22-A | 前端 UI 基线开发记录 | `project-development/01-frontend-ui-baseline.md` | 前端基线页面、真实截图、构建问题和验收记录 |
| 23 | 框架对齐落地记录 | `project-development/19-framework-alignment-rag-agent-db.md` | SQLAlchemy、Celery、LangGraph、DeepAgents、LlamaIndex、Milvus 落地记录 |
| 24 | 前端开发与接口对接文档 | `24-frontend-development.md` | 前端目录、设计系统、页面路由、Mock 数据、接口替换和验收清单 |
| 25 | 项目面试参考手册 | `25-interview-preparation-guide.md` | 项目介绍、核心流程、技术栈、RAG、Agent、Python 八股和面试边界 |
| 26 | 实时同步与通信选型记录 | `project-development/34-realtime-sync-sse-pubsub-websocket.md` | Redis Pub/Sub、SSE、WebSocket 的职责区别、团队版实时链路、故障边界和面试回答 |
| 27 | 团队版云部署开发记录 | `project-development/96-team-cloud-deployment.md` | Docker Compose、生产镜像、Runner 隔离、腾讯云单机部署、健康检查、备份和回滚 |
| 28 | 品牌定名方案 · 司契（Siqi） | `品牌定名方案-司契.md` | 定名结论、名称释义与出处、标语体系、使用规范、更名历史与 CNIPA 第 9 / 42 类商标检索清单 |
| 29 | 项目介绍（对外讲述版） | `项目介绍-司契.md` | 名称由来、项目背景、动因、主业务流程、技术栈、验证证据、边界与 5 分钟讲述顺序 |

## 推荐阅读顺序

1. 先看 `01`，确认项目定位和边界。
2. 再看 `02` 和 `03`，理解产品需求与流程闭环。
3. 接着看 `04`，确认技术路线与安全边界。
4. 之后看 `06`，理解代码 RAG 的数据链路和检索边界。
5. 再看 `07`，把页面线框和高保真设计一次定下来。
6. 接着看 `08` 到 `11`，把接口、数据库、安全和演示补齐。
7. 最后看 `05` 和 `12`，确认开发节奏和里程碑。
8. MVP 完成后，优先看 `mvp/README.md` 确认冻结基线，再看 `team/README.md` 规划团队版增量。
9. 团队版稳定后，先看 `enterprise/README.md`，再按 00 到 10 的顺序规划企业版能力。
10. `17` 到 `21` 是企业版历史兼容入口，后续以 `enterprise/` 目录为准。
11. 每完成一个功能模块，都更新 `project-development/` 目录里的记录文件，保留真实截图、问题和验收结果。
