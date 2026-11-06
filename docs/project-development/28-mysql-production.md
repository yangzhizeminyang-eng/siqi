# MySQL 生产接入

## 结论
本项目已真实接入 MySQL（本机 MySQL 8.0.44 验证通过）：API 创建任务 → MySQL 落库 → 重启从 MySQL 恢复 → API 读回。

## 数据库升级（Alembic，启动前必须迁移）
- **建表/升级主入口 = Alembic `upgrade head`**（对既有库安全演进，不只依赖 `create_all`）。
  - 全新库：`python -m scripts.migrate`（backend 目录）按 `DATABASE_URL` 跑 `alembic upgrade head`，baseline 创建全量表并打版本戳。
  - 旧库（早期 `create_all` 建出、无 `alembic_version`）：应用启动自动 `_stamp_existing_baseline_if_needed`（检测到 baseline 标志表存在且无版本 → `alembic stamp` baseline），再跑增量；各增量迁移均幂等守卫（`index_snapshots`/pr_*/default_test_command/comments/notifications 用 `inspect` 判存在再建/加列），不会撞 `1050 Table already exists` 或 `Duplicate column`，且保留存量数据。
- **应用启动自动迁移**：FastAPI `lifespan` 调 `app/db/migrate.py#ensure_schema_current(upgrade=True)`（`RUN_ALEMBIC_UPGRADE` 默认 true）；迁移失败抛错 → 拒绝启动，不静默继续。
- **Worker 只检查不迁移**：`app/workers/runtime.py#init_worker_runtime` 调 `ensure_schema_current(upgrade=False)`，落后即明确报错，**避免多 Worker 并发迁移抢 Alembic 版本锁**。
- **迁移命令**（部署前/升级时）：
  ```text
  cd backend
  python -m scripts.migrate          # DATABASE_URL 指 MySQL；等价 alembic upgrade head，幂等
  ```
- 版本检查：`app/db/migrate.py::current_version() / target_head() / schema_current()`；`target_head()` 从迁移脚本目录取**唯一 head**，多 head 直接报错。
- 迁移链（单一 head）：`ad6faa347bb8(baseline) → 4486135ff281(index_snapshots) → b7c3d51a9e02(tasks pr_*) → c9a4e71f2b50(projects.default_test_command) → 8f3d21c7a0b1(comments+notifications)`。
- 校验：`tests/test_migrations_upgrade.py`（4 条）覆盖 create_all 无版本旧库 / 无版本缺 pr_* 旧库升级成功、幂等、数据保留。

## team / production 必须以 MySQL 承载（不允许默认/回退 sqlite）
- `runtime_config.load_runtime_settings()`：`APP_ENV=team / production / prod` 且缺少 `DATABASE_URL` 或其为 sqlite 时**启动报错**，
  绝不静默回退到 `sqlite:///.data/app.db`；本地/local/test 才允许默认 SQLite（开发/测试降级严格分离）。
- `deploy/env/team.env.example` / `enterprise.env.example`：`DATABASE_URL` 默认即 `mysql+pymysql://…`，不再写 sqlite。
- 业务表、审计表、索引表都以 MySQL 承载（18 张 create_all；`scripts/init_mysql.py` 建库建表）。
- 实证（team 模式 + 真实 MySQL，`scripts/mysql_e2e.py`）：API 建任务 → MySQL（tasks/task_events/execution_steps/audit_events/repository_indexes）→ 重启 restore → API 读回 PASS；
  `tests/test_mysql_integration.py::test_team_production_requires_mysql_url` 断言 team/prod 缺 mysql 或给 sqlite 时抛错。

## 运行三步（production）
```powershell
# ① 初始化库（需 CREATE DATABASE 权限账号；密码走环境变量，不落盘）
cd backend
$env:MYSQL_HOST="127.0.0.1"; $env:MYSQL_PORT="3306"; $env:MYSQL_ADMIN_USER="root"
$env:MYSQL_ROOT_PASSWORD="<密码>"   # 仅存于当前 shell/密管，勿提交
$env:DATABASE_NAME="agent"
.\.venv\Scripts\python.exe -m scripts.init_mysql     # 建库(如缺)+ utf8mb4 + create_all 建全量表(幂等)

# ② 配置应用连接并启动（密码在生产走密管注入环境变量）
$env:DATABASE_URL="mysql+pymysql://root:<密码>@127.0.0.1:3306/agent?charset=utf8mb4"; $env:USE_SQLALCHEMY="true"
.\.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000

# ③ 可选：真实连接冒烟 / 端到端复验
$env:MYSQL_LIVE="1"; .\.venv\Scripts\python.exe -m scripts.mysql_smoke   # 真实 SELECT 1
.\.venv\Scripts\python.exe -m scripts.mysql_e2e                          # API写入→MySQL可查→重启恢复
```
数据库若已由 DBA 创建、应用账号无 CREATE DATABASE，则跳过步骤①的建库（脚本会据此明确报错或提示），直接配应用 DATABASE_URL。

## 关键文件
- `scripts/init_mysql.py`：建库（缺失时，utf8mb4）+ 全量建表（create_all 幂等）。凭据只读环境变量，报错清晰不把“库没建”留给用户猜。
- `scripts/mysql_smoke.py`：形态校验 +（`MYSQL_LIVE=1`）真实连接。
- `scripts/mysql_e2e.py`：API→MySQL→重启→API 全链路。
- `app/core/mysql_config.py`：形态校验 / 连接串组装。
- 建表引擎：`Base.metadata.create_all`（等价 alembic baseline + `index_snapshots`），SQLite 开 batch。

## SQLite 与 MySQL 边界
| 维度 | SQLite（开发/测试降级） | MySQL（生产） |
| --- | --- | --- |
| DATABASE_URL | `sqlite:///.data/app.db` / `sqlite:///:memory:` | `mysql+pymysql://user:pass@host:3306/db?charset=utf8mb4` |
| 建库 | 无需建库（文件即库） | 需有 CREATE DATABASE 权限；init_mysql 处理 |
| 建表 | create_all 幂等 | create_all 幂等（同模型） |
| 表结构 | 18 张（含 index_snapshots） | 18 张（已验证） |
| 重启恢复 | restore_from_db（SQLite） | 同一套 restore_from_db（MySQL），已验证 |

业务模型 / API 契约 / 写透 / 审计 / 读层在两种引擎下完全一致；引擎差异只由 `DATABASE_URL` 决定。

## 测试
`tests/test_mysql_integration.py`（6 条，无 `MYSQL_TEST_URL` 时自动跳过）：MySQL 可达；schema 含全部业务表；HTTP 写入后 MySQL 可查；写入→清内存→restore 恢复；写 MySQL 失败不计静默（指标/last_errors）；team/prod 拒绝 sqlite 回退。

### 让核心回归在 MySQL 引擎上跑（闭合「只跑 sqlite」盲区）
```powershell
# 设置 MYSQL_TEST_URL 指向真实 MySQL（可复用 CI/部署库或临时库），再跑 MySQL 集成 + 全量回归
$env:MYSQL_TEST_URL="mysql+pymysql://user:pass@host:3306/agent_ci?charset=utf8mb4"
.\.venv\Scripts\python.exe -m pytest tests/test_mysql_integration.py -q      # 6 passed（不再 skip）
.\.venv\Scripts\python.exe -m pytest tests -q                               # 全量在本机 SQLite + 该 MySQL 上都跑
```
CI/部署若配置 `MYSQL_TEST_URL`，MySQL 集成即纳入主回归，业务逻辑既在 SQLite（快速）也在 MySQL（真引擎）验证，避免覆盖盲区。

## 安全
- 密码仅经环境变量/密管注入运行，**不写入任何提交文件**；docs/env 仅给占位说明。
- `init_mysql`/测试均不打印连接串详情。