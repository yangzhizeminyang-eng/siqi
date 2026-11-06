"""MySQL 生产形态冒烟。

用法（working dir = backend）：
    python -m scripts.mysql_smoke                     # 只校验形态 + pymysql 依赖
    $env:DATABASE_URL="mysql+pymysql://u:p@host:3306/db?charset=utf8mb4"
    $env:MYSQL_LIVE="1" python -m scripts.mysql_smoke  # 尝试真实连接 + SELECT 1 + 表清单

说明：本机无 MySQL 时常走形态校验；MYSQL_LIVE=1 且存在可达 MySQL 时才做真实连接。
"""

from __future__ import annotations

import os

from app.core.mysql_config import is_mysql_url, mysql_ready


def main() -> None:
    from sqlalchemy import create_engine, inspect, text

    ok, reason = mysql_ready()
    print(f"[mysql] 形态校验: {'PASS' if ok else 'FAIL'} - {reason}")
    if not ok:
        return

    url = os.environ.get("DATABASE_URL", "")
    print(f"[mysql] DATABASE_URL dialect=mysql+pymysql（已配置）")
    if os.environ.get("MYSQL_LIVE", "").strip().lower() not in {"1", "true", "yes"}:
        print("[mysql] 未启用 MYSQL_LIVE：跳过真实连接冒烟（生产形态已就绪）")
        return

    engine = create_engine(url, connect_args={"connect_timeout": 5})
    try:
        with engine.connect() as conn:
            one = conn.execute(text("SELECT 1")).scalar()
            print(f"[mysql] 真实连接 OK：SELECT 1 = {one}")
            print(f"[mysql] 表清单：{inspect(engine).get_table_names()}")
        print("[MYSQL-SMOKE] PASS：真实 MySQL 连接可用")
    except Exception as error:  # noqa: BLE001
        print(f"[MYSQL-SMOKE] FAIL：{type(error).__name__}: {error}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()