# P1 持久化加固：写透可观测 + 索引中间态恢复

## 结论
1. 写透不再静默吞错：所有业务 `_persist_*` 与索引侧持久化统一走 `persistence_status.measure`，
   记录日志/错误码/统计指标，可用 `PERSIST_STRICT=true` 升级为失败即抛错。
2. 索引中间态（PARSING 前的 SYNCING 快照）落库：新增 `index_snapshots` 表持久化 `CODE_INDEX_SNAPSHOTS`，
   重启后可恢复，PARSING 中断不再因缺快照失败。

## 改动
- 新增 `backend/app/core/persistence_status.py`：`measure(op, id, work)` 统一执行写透并记录
  `{op}:{ok|fail|skip}` 计数 + `last_errors`；持久化关闭时记 skip；失败记日志；`PERSIST_STRICT=true` 上抛。
- `backend/app/main.py`：7 个 `_persist_*` 包装改为走 `measure`（不再 `except: pass`）；索引侧
  `code_chunks / repository_index / index_snapshot` 三处持久化同样走 `measure`。
- `backend/app/models/orm.py` + `backend/app/db/repositories.py`：新增 `IndexSnapshotORM` 表
  + `upsert_index_snapshot / load_index_snapshots`。
- `backend/app/main.py::run_index_stage_work`：SYNCING 后持久化快照；PARSING 内存快照缺失时从 DB 重建后继续。
- `backend/app/db/bootstrap.py`：重启把 `CODE_INDEX_SNAPSHOTS` 从 DB 恢复。
- alembic 迁移：`migrations/versions/4486135ff281_add_index_snapshots.py`。
- 环境变量：`deploy/env/*.example` 补 `PERSIST_STRICT`。

## 测试（`tests/test_persistence_observability.py`，7 条）
| 项 | 结果 |
| --- | --- |
| 写库失败 → 返回失败码 + 指标计数 + last_errors | 通过 |
| 写库成功 → `project:ok` 计数 | 通过 |
| 持久化关闭 → 记 skip，不执行工作、不当失败 | 通过 |
| `PERSIST_STRICT=true` → 失败上抛 | 通过 |
| `main._persist_task` 落库 + `task:ok` 指标 | 通过 |
| 索引快照写入 → 清内存 → restore 恢复 | 通过 |
| PARSING 中断 → 内存快照缺失 → 从 DB 重建继续解析 | 通过 |

全量后端测试：**326 passed**（既有 319 + 新增 7）。

## 验收口径变化
- 「写透失败静默吞错」不再存在：任何写库异常都有 ERROR 日志 + `persistenceStatus` 计数可查。
- 索引「中途重启」不再丢失：SYNCING→PARSING 之间的快照落库，重启后可续跑。
- 生产可设 `PERSIST_STRICT=true` 让写库失败直接反映到请求（拒绝静默丢数据）；默认 false 保持 API 兼容——失败仍被记录而非吞掉。