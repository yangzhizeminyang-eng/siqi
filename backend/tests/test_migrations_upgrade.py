"""既有 MySQL 数据库 Alembic 迁移闭环测试。

覆盖：构造 legacy schema（缺新表 comments/notifications、缺 tasks.pr_*/default_test_command 列）
→ alembic upgrade head → 新表/新列出现、load_tasks/load_comments/load_notifications 可执行、
原业务数据仍存在、重复 upgrade head 幂等、Worker(upgrade=False) 落后时明确报错不并发迁移。
"""
from __future__ import annotations

import datetime
from pathlib import Path

import pytest
from sqlalchemy import inspect

from app import main


def _enable_db(tmp_path, monkeypatch) -> str:
    url = f"sqlite:///{(tmp_path / 'legacy.db').as_posix()}"
    monkeypatch.setenv("USE_SQLALCHEMY", "true")
    monkeypatch.setenv("DATABASE_URL", url)
    from app.db.session import get_engine, get_session_factory

    get_engine.cache_clear()
    get_session_factory.cache_clear()
    return url


def _clear_caches() -> None:
    from app.db.session import get_engine, get_session_factory

    get_engine.cache_clear()
    get_session_factory.cache_clear()


def _table_names() -> set[str]:
    from app.db.session import get_engine

    return set(inspect(get_engine()).get_table_names())


def _column_names(table: str) -> set[str]:
    from app.db.session import get_engine

    return {c["name"] for c in inspect(get_engine()).get_columns(table)}


def test_legacy_db_upgrade_closure_to_head(tmp_path, monkeypatch):
    _enable_db(tmp_path, monkeypatch)
    from app.db import migrate

    try:
        # 1) 构造 legacy：升级到「评论/通知之前的版本」（c9a4e71f2b50），此时无 comments/notifications 表
        migrate.run_upgrade_to("c9a4e71f2b50")
        assert "comments" not in _table_names()
        assert "notifications" not in _table_names()
        assert not migrate.schema_current()

        # 2) 预置 legacy 业务数据（用户），验证升级后仍存在
        now = datetime.datetime.now(datetime.timezone.utc)
        legacy_user = main.UserRecord(id="user_legacy", name="旧库用户", email="legacy@company.com",
                                      role="developer", password_hash="x")
        from app.db.repositories import load_users, upsert_user
        from app.db.session import create_schema

        upsert_user(legacy_user)
        # legacy 已含 baseline（含 users），确认能读回
        assert any(d["id"] == "user_legacy" for d in load_users())

        # 3) Worker 检查：落后且不允许升级 → 明确报错（不静默、不并发迁移）
        with pytest.raises(RuntimeError):
            migrate.ensure_schema_current(upgrade=False)

        # 4) upgrade head
        migrate.run_upgrade()

        # 5) 新表到位
        tables = _table_names()
        assert "comments" in tables
        assert "notifications" in tables
        assert "approval_applications" in tables
        assert {
            "tenant_id",
            "idempotency_key",
            "approval_status",
            "approval_mode",
            "resource_version",
        }.issubset(_column_names("approval_applications"))

        # 6) tasks 新列到位（pr_number/pr_url/pr_status + default_test_command 在 projects）
        assert {"pr_number", "pr_url", "pr_status"}.issubset(_column_names("tasks"))
        assert "default_test_command" in _column_names("projects")

        # 7) 原业务数据仍存在（load_tasks/load_users 可执行，数据未丢）
        from app.db.repositories import load_comments, load_notifications, load_tasks, upsert_comment, upsert_notification

        assert any(d["id"] == "user_legacy" for d in load_users()), "legacy 业务数据应在升级后保留"
        assert isinstance(load_tasks(), list), "load_tasks 应可执行"

        # 8) 新实体可写可读
        comment = main.CommentRecord(id="cmt_mig", task_id="TASK-MIG", project_id="p", repository_id="r",
                                     author_id="user_legacy", author_name="旧库用户", body="迁移后评论",
                                     mentioned_user_ids=[], created_at=now)
        upsert_comment(comment)
        assert any(d["id"] == "cmt_mig" for d in load_comments())

        notif = main.NotificationRecord(id="ntf_mig", recipient_id="user_legacy", task_id="TASK-MIG",
                                        type="MENTION", title="迁移后通知", body="b",
                                        actor_id="user_legacy", actor_name="旧库用户", priority="normal",
                                        read_at=None, created_at=now)
        upsert_notification(notif)
        assert any(d["id"] == "ntf_mig" and d["recipient_id"] == "user_legacy" for d in load_notifications())

        # 9) schema_current 为 True，重复 upgrade head 幂等
        assert migrate.schema_current()
        migrate.run_upgrade()
        assert migrate.schema_current()
        assert migrate.current_version() == migrate.target_head()
    finally:
        _clear_caches()


def test_single_head_and_target_resolves(tmp_path, monkeypatch):
    """迁移链必须是单一 head，且 target_head 可解析（无断裂/重复）。"""
    _enable_db(tmp_path, monkeypatch)
    from app.db import migrate

    try:
        head = migrate.target_head()
        assert isinstance(head, str) and len(head) >= 8, f"head 解析异常：{head}"
    finally:
        _clear_caches()


def test_legacy_create_all_db_no_version_upgrade(tmp_path, monkeypatch):
    """既有由 create_all 建出、无 alembic_version 的旧库：run_upgrade 应平滑恢复到 head。

    覆盖评审阻断场景：旧库已有全部业务表（可能含新表/新列）、无版本戳，
    此前 alelmbic upgrade head 会因 baseline 再 create 撞 1050。当前实现：
    检测到 baseline 标志表存在且无版本戳 → 先 stamp baseline，再跑增量（增量均已幂等守卫）。
    """
    _enable_db(tmp_path, monkeypatch)
    from app.db import migrate
    from app.db.session import create_schema, get_engine

    try:
        # 用 create_all 建出「当前全量 schema」（无 alembic_version），等价旧 create_all 库
        create_schema()
        assert "alembic_version" not in set(__import__("sqlalchemy").inspect(get_engine()).get_table_names())

        now = datetime.datetime.now(datetime.timezone.utc)
        legacy_user = main.UserRecord(id="user_legacy_all", name="旧库用户", email="legacy_all@company.com",
                                      role="developer", password_hash="x")
        from app.db.repositories import load_users, upsert_user

        upsert_user(legacy_user)

        assert migrate.current_version() is None  # 无版本戳
        migrate.run_upgrade()
        assert migrate.schema_current(), "create_all 旧库应恢复到 head"
        assert migrate.current_version() == migrate.target_head()

        # 数据保留 + 重启读层可用
        assert any(d["id"] == "user_legacy_all" for d in load_users())
        migrate.run_upgrade()  # 幂等
        assert migrate.schema_current()
    finally:
        _clear_caches()


def test_legacy_no_version_missing_pr_columns_upgrade(tmp_path, monkeypatch):
    """无版本戳且 tasks 缺 pr_* 列的更旧库：run_upgrade 应补列并使 load_tasks 可用。

    覆盖评审真实现场（agent_async：无 alembic_version、tasks 缺 pr_number/pr_url/pr_status）。
    """
    _enable_db(tmp_path, monkeypatch)
    from app.db import migrate
    from sqlalchemy import inspect

    from app.db.session import get_engine, session_scope

    try:
        # 构建真实旧形态：只有 baseline（无 pr 列/无新表/无版本戳时再跑增量）
        migrate.run_upgrade_to("ad6faa347bb8")
        # 清掉版本戳，模拟「由旧 create_all 建出、无 alembic_version」的库
        with session_scope() as session:
            session.execute(__import__("sqlalchemy").text("DROP TABLE alembic_version"))

        tasks_cols = {c["name"] for c in inspect(get_engine()).get_columns("tasks")}
        assert "pr_number" not in tasks_cols, "前置：baseline 的 tasks 尚无 pr_* 列"

        migrate.run_upgrade()
        assert migrate.schema_current()

        # pr 列补齐 + 新表出现
        after = {c["name"] for c in inspect(get_engine()).get_columns("tasks")}
        assert {"pr_number", "pr_url", "pr_status"}.issubset(after), "旧库应补齐 pr_* 列"
        tables = set(inspect(get_engine()).get_table_names())
        assert {"comments", "notifications"}.issubset(tables)

        from app.db.repositories import load_tasks

        assert isinstance(load_tasks(), list), "load_tasks 升级后应可执行"
    finally:
        _clear_caches()


def test_migrations_do_not_use_text_server_defaults():
    """MySQL 不允许 BLOB/TEXT/JSON 带 DEFAULT；迁移脚本必须避免 Text server_default。

    SQLite 会接受 `TEXT NOT NULL DEFAULT ''`，但 MySQL 8 报 1101。
    该回归扫描全部 Alembic 迁移，防止再次引入 MySQL 不可执行的 DDL。
    """
    import ast

    violations: list[str] = []
    versions = Path(__file__).resolve().parents[1] / "migrations" / "versions"

    def _is_sa_text(node: ast.AST) -> bool:
        return (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "Text"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "sa"
        )

    for path in sorted(versions.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "Column"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "sa"
            ):
                continue
            if len(node.args) >= 2 and _is_sa_text(node.args[1]):
                if any(kw.arg == "server_default" for kw in node.keywords):
                    violations.append(f"{path.name}: {ast.unparse(node)[:180]}")
    assert violations == [], "迁移中的 Text server_default 会导致 MySQL 1101：\n" + "\n".join(violations)
