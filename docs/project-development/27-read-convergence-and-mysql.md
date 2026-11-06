# 读路径收敛 + MySQL 生产形态

## 结论（明确选型：路径 A —— 持久化开启时核心读走 DB 真源）
- **持久化开启（USE_SQLALCHEMY=true，生产）：核心可变业务读从 DB 读真源**——`reads.get_task/list_tasks/list_projects/
  get_project/list_repositories/get_repository/list_users/get_user/find_team_request/list_team_requests/info_pending_approvals`
  在 `persistence_enabled()` 时经 `load_*` 重建 pydantic 记录返回。**清空 dict 后核心 API 仍从 DB 读出数据（dict 退位成立）**。
- **持久化关闭（本地/测试）：读 dict 运行时视图**，保既有测试；`main.py` 读 handler 一律经 `reads.py`，不直接碰 dict。
- 说明：DB 模式下 `list_tasks` 读不做有副作用 advance（推进交 Worker / 轮询详情），避免「读即改」。
- `*_from_db()` 保留为 DB 对账工具；`reads.reload_from_db()` 供重启/恢复重灌。

## MySQL 生产形态
- `app/core/mysql_config.py`：校验 `DATABASE_URL` 是否为 `mysql+pymysql` 形态、pymysql 依赖、组装连接串。
- `scripts/mysql_smoke.py`：`python -m scripts.mysql_smoke` 做形态校验；设 `MYSQL_LIVE=1` 且有可达 MySQL 时做真实 `SELECT 1` + 表清单。
- 生产连接 `DATABASE_URL=mysql+pymysql://u:p@host:3306/db?charset=utf8mb4`，建表走 alembic（SQLite 已开 batch）或 `scripts/migrate.py`（create_all）。

## 改动文件
- 新增 `backend/app/db/reads.py`（统一门面 + `reload_from_db`）、`tests/test_reads_layer.py`
- 改 `backend/app/main.py`：`get_task / list_tasks / get_project / get_repository / list_users / get_user / list_team_requests / find_team_request / list_audit_events / list_repository_indexes / get_repository_index / list_pending_approvals` 改走读层
- 文档：`docs/project-development/00` + `21` + `27`

## 测试
| 项 | 结果 |
| --- | --- |
| 读层任务/项目/仓库/用户/团队需求/审批队列与视图一致 | 通过 |
| 清空运行时视图→读层返回 0（读视图语义）；`reload_from_db`→DB 恢复后可读回 | 通过 |
| **路径 A 决定性验收：清空 TASKS/PROJECTS/TEAM_REQUESTS dict 后，GET /api/tasks、/projects、/team/requests 仍从 DB 读到（dict 退位）** | **通过**（`test_core_reads_are_db_backed_after_dict_clear`） |
| 全量后端测试 | **369 passed（+5 MySQL skipped）** |

## 剩余风险 / 边界
- **任务等可变对象读仍是缓存视图**：要做到「API 读直连 DB 主源」需引入 CQRS 或把每次读前强制写透（改架构），超出「保持 API 兼容」范围，有意保留。
- 真实 MySQL 连接需有可达 MySQL 服务 + `MYSQL_LIVE=1` 冒烟；本机未验证真连接。
- 读层暂为「缓存门面」，对账 `*_from_db()` 为追加型存储专用。