"""数据库迁移入口（Alembic）。

用法（working dir = backend）：
    python -m scripts.migrate                    # 按 DATABASE_URL 执行 alembic upgrade head（幂等）
    DATABASE_URL=mysql+pymysql://u:p@host:3306/db python -m scripts.migrate

说明：
- 对既有库安全演进（加新表/新列），不只依赖 create_all 建表；
- 幂等：重复执行 upgrade head 无副作用；
- 失败即非零退出并输出明确错误，不静默继续；
- 多进程部署时：只由应用进程（RUN_ALEMBIC_UPGRADE 默认 true）执行迁移，
  Worker 启动只检查 schema 版本（见 app/db/migrate.py#ensure_schema_current），避免并发迁移。
"""

from __future__ import annotations

import os
import sys

_PROD_ENVS = {"team", "production", "prod", "enterprise"}


def _require_authorization() -> None:
    """生产迁移必须先通过逐项授权门禁；本地/测试环境不受影响。"""
    if os.getenv("APP_ENV", "").strip().lower() not in _PROD_ENVS:
        return
    operation = os.getenv("AUTHORIZATION_OPERATION", "").strip()
    if not operation:
        print(
            "[migrate] 拒绝执行：生产迁移必须设置 AUTHORIZATION_OPERATION，"
            "并通过 authorization_gate 的逐项授权", file=sys.stderr)
        raise SystemExit(2)
    from scripts.authorization_gate import main as gate_main

    args = ["--operation", operation, "--preflight", "--json"]
    log = os.getenv("AUTHORIZATION_LOG", "").strip()
    if log:
        args.extend(["--log", log])
    if gate_main(args) != 0:
        print("[migrate] 拒绝执行：授权门禁未通过", file=sys.stderr)
        raise SystemExit(2)


def main() -> None:
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass
    _require_authorization()
    from app.db.migrate import run_upgrade, schema_current

    try:
        if schema_current():
            print("[migrate] 数据库 schema 已是最新，跳过（幂等）")
            return
        version = run_upgrade()
        print(f"[migrate] alembic upgrade head 完成，当前版本={version}")
        if not schema_current():
            raise RuntimeError("迁移后版本仍与目标 head 不一致，请检查迁移链")
    except RuntimeError as error:
        print(f"[migrate] FAIL: {error}", file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()