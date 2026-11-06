# DEV-P0 业务数据持久化

## 1. 基本信息

| 项目 | 内容 |
| --- | --- |
| 模块名称 | 业务数据持久化（SQLAlchemy 写透缓存 + 重启恢复） |
| 开发阶段 | MVP / 团队版（为生产铺路） |
| 开始日期 | 2026-09-07 |
| 完成日期 | 2026-09-07 |
| 关联文档 | `../04-technical-architecture.md`、`../09-database-design.md` |
| 关联接口 | 全部业务读写接口（接口契约不变） |
| 关联数据表 | `users / projects / repositories / tasks / plans / diffs / test_runs / pr_descriptions / team_requests / audit_events / password_resets` 及既有 RAG/审计表 |

## 2. 背景与结论

验收审查发现：用户/项目/仓库/任务（含计划、审批、Diff、测试结果、PR 描述）/团队请求/审计事件
此前**全部只存在模块级内存 dict**（`backend/app/main.py`），服务一旦重启即丢失；SQLAlchemy 只对
RAG/审计做了零散"旁路只写"。本文档记录本轮把**业务数据全部落库**并打通重启恢复。

## 3. 落地方案

- **架构**：DB（SQLAlchemy）= 唯一真源；模块级 dict 降级为「启动时从 DB 加载的缓存 + 运行时写透」，
  由此保持既有 API 与前端行为不变（不改 90+ 端点契约、不动 166+ 既有测试）。
- **ORM 表**（`backend/app/models/orm.py`）：用户/项目/仓库/任务/团队请求/审计/密码重置 + 任务子表
  （计划/审批、Diff、测试、PR 描述）；深层嵌套内容（Issue 快照、分析、上下文、相似任务引用、事件、步骤）
  以 JSON 列落库保证 pydantic 精确往返。
- **仓储层**（`backend/app/db/repositories.py`）：每类对象 `upsert_* / load_*`，统一 `USE_SQLALCHEMY` 开关
  （`persistence_enabled()`），测试关闭时为空操作。
- **启动恢复**（新增 `backend/app/db/bootstrap.py::restore_from_db()`）：首启建库写入代码种子；
  之后启动把 DB 全部业务行灌回 dict 缓存。
- **迁移**（新增 `backend/scripts/migrate.py`）：`Base.metadata.create_all` 幂等建表，SQLite/MySQL 均可用。

## 4. 遇到的问题与解决

| 问题 | 原因 | 解决方式 |
| --- | --- | --- |
| 新增持久化测试触发既有测试 401（顺序依赖） | 重启恢复测试清空并重建 `main.USERS/PROJECTS...` 全局 dict，泄漏到后续用例 | `fresh_db` fixture 快照/恢复全部业务 store，测试完全自包含 |
| TaskRecord 往返后 `created_at` 丢失时区 | SQLite DateTime 列读出为 naive | load 时把 naive 时间统一视为 UTC（`_row_dict`），保证 pydantic 往返精确 |
| TaskRecord 嵌套过深，全量主从表归一化风险大 | 计划/分析/快照等是深层 pydantic 图 | 顶层标量入列，深层嵌套以 JSON 列 + pydantic `model_dump/model_validate` 精确往返 |

## 5. 测试与验收

| 验收项 | 操作 | 结果 |
| --- | --- | --- |
| 全量后端测试 | `pytest tests -q` | **302 passed**（含 4 个新持久化用例 + 既有 298，全部通过） |
| TaskRecord 往返一致性 | 落库→重载→逐字段对比 | 通过（含嵌套计划/Diff/测试/PR描述/分析/快照/事件/步骤） |
| 重启数据恢复 | 清空 dict + 重跑 `restore_from_db` | 通过（用户/项目/仓库/任务/团队请求/审计均恢复） |
| 建表幂等 | `create_schema()` 重复调用 | 通过 |
| 真实两进程重启 | `scripts/demo_restart.py write` → 新进程 `read` | **PASS**：任务/团队请求重启后仍在 |
| 持久化开关隔离 | `USE_SQLALCHEMY=false` 下读写为空操作 | 通过（既有用例保持纯内存隔离） |

## 6. 尚未解决

- 无 alembic：`create_all` 只建表不做列演进；数据库结构演进届时引入 alembic（见风险清单）。
- 生产默认仍为 SQLite；MySQL/Redis/Milvus 配置对齐与 Worker 编排属后续 P1/P2。