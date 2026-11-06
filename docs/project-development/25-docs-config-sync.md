# P2b 文档 / 配置 / 运行同步

## 目标
让代码、测试、文档、运行方式一致；修正技术架构文档中的夸大表述；补齐启动方式与环境变量说明。

## 改动清单
- `docs/04-technical-architecture.md`：修正为 SQLite 默认（生产可切 MySQL）、Redis+Celery 可选（无 Redis 仅 eager 降级标注）、
  LangGraph 节点已实现、Milvus 首选 + `RAG_MOCK_FALLBACK` 显式降级；补「落地状态」说明。
- `docs/06-rag-design.md`：`实现口径` 更新为「Milvus 首选 + 显式降级标注，不再谎报 milvus」。
- `deploy/env/{mvp,team,enterprise}.env.example`：补齐 `DATA_DIR / DATABASE_URL / USE_SQLALCHEMY / USE_MILVUS /
  MILVUS_URI / MILVUS_TOKEN / RAG_MOCK_FALLBACK / USE_CELERY / CELERY_BROKER_URL / CELERY_RESULT_BACKEND`，
  与 `backend/app/core/runtime_config.py` 对齐。
- `start-dev.ps1`：导出后端运行时 env（SQLite + 持久化开 + Milvus/降级开），本地开箱即持久化。
- 新增 `deploy/dev-worker.ps1`：Celery Worker 启动（Windows 建议 `--pool=solo`）。

## MVP / 团队版 / 生产差异
| 维度 | MVP / 本地 | 团队版 | 生产 |
| --- | --- | --- | --- |
| 数据库 | SQLite（`sqlite:///.data/app.db`） | SQLite 或 MySQL | MySQL（`DATABASE_URL`） |
| 持久化 | `USE_SQLALCHEMY=true` 开启 | 开启 | 开启 |
| 队列 | 可选；无 Redis=eager 降级 | Redis + Celery | Redis + Celery |
| 向量库 | Milvus Lite / 显式降级 | Milvus | Milvus（`RAG_MOCK_FALLBACK=false`，失败即抛错） |
| AI 模型 | 无 Key 回退规则/本地 | DeepSeek/智谱 | DeepSeek/智谱（必配 Key） |

## 真实运行证据
- 全量后端测试：`314 passed`。
- 两进程重启恢复演示：`python -m scripts.demo_restart write|read` → `PASS`（任务/团队请求/审计/索引/切片重启后仍在）。
- Celery eager：`tests/test_celery_path.py` 5 条（eager 真实任务体、并发、降级标记）。
- LangGraph：`tests/test_workflow_graph.py` 5 条（节点路由、审批暂停/恢复、失败分流）。