"""迁移版本判定（前向-only / fail-closed）专项测试。

覆盖评审要求的状态机：
- current == head            -> 正常启动（schema_current）。
- current 为 head 祖先（DB 落后）-> 应用可 upgrade；Worker(upgrade=False) 抛错要求先迁移。
- current 为 head 后代（DB 领先/compat）-> 迁移层兼容启动 + warning，不升级、不降级（分类逻辑单测）。
- current 不在迁移树（unknown）-> 默认 BLOCKED；仅 ALLOW_UNKNOWN_MIGRATION_REVISION=true 时迁移层兼容启动。
- 任何场景禁止 downgrade（关键断言：unknown 放行后 alembic_version 不被改写/不回退）。

使用 SQLite 临时库（与 test_migrations_upgrade.py 同模式，不需真实 MySQL）。
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

from app import main
from app.db import migrate


def _enable_db(tmp_path, monkeypatch) -> None:
    url = f"sqlite:///{(tmp_path / 'rel.db').as_posix()}"
    monkeypatch.setenv("USE_SQLALCHEMY", "true")
    monkeypatch.setenv("DATABASE_URL", url)
    from app.db.session import get_engine, get_session_factory

    get_engine.cache_clear()
    get_session_factory.cache_clear()


def _setup(at_revision: str) -> None:
    """迁到指定 revision 并回填一条用户，验证数据在后续场景不被破坏。"""
    migrate.run_upgrade_to(at_revision)
    user = main.UserRecord(id="user_schema_rel", name="用户", email="rel@company.com",
                           role="developer", password_hash="x")
    from app.db.repositories import load_users, upsert_user

    upsert_user(user)
    assert any(d["id"] == "user_schema_rel" for d in load_users())


def _set_version(revision: str) -> None:
    from app.db.session import session_scope

    with session_scope() as session:
        session.execute(__import__("sqlalchemy").text(f"UPDATE alembic_version SET version_num='{revision}'"))


def _clear() -> None:
    from app.db.session import get_engine, get_session_factory

    get_engine.cache_clear()
    get_session_factory.cache_clear()


def test_unknown_revision_fail_closed_then_manual_override(tmp_path, monkeypatch):
    """unknown revision：默认 BLOCKED；显式开关下迁移层兼容启动且不降级、数据保留。

    这是旧镜像回滚到比自身更新 DB 的关键场景（DB revision 在本树不存在）。
    """
    _enable_db(tmp_path, monkeypatch)
    monkeypatch.delenv("ALLOW_UNKNOWN_MIGRATION_REVISION", raising=False)
    try:
        _setup(migrate.target_head())
        assert migrate.schema_current()

        fake = "f1f2f3f4f5f6"  # 不在本迁移树
        _set_version(fake)
        assert migrate.current_version() == fake

        # 1) 默认 fail-closed：应用与 Worker 均 BLOCKED
        with pytest.raises(RuntimeError):
            migrate.ensure_schema_current(upgrade=True)
        with pytest.raises(RuntimeError):
            migrate.ensure_schema_current(upgrade=False)
        assert migrate.current_version() == fake, "BLOCKED 时不得改写版本（不得降级）"

        # 2) 显式人工确认开关：迁移层兼容启动
        monkeypatch.setenv("ALLOW_UNKNOWN_MIGRATION_REVISION", "true")
        got = migrate.ensure_schema_current(upgrade=False)
        assert got == fake, "放行 unknown 时返回当前 DB 版本"
        # 版本/head 均未被改动 -> 未发生降级
        assert migrate.current_version() == fake
        assert migrate.target_head() != fake, "app head 不得被污染成 unknown"
        # 数据保留 + 读层可用（未破坏业务）
        from app.db.repositories import load_users

        assert any(d["id"] == "user_schema_rel" for d in load_users())
    finally:
        _clear()


def test_behind_upgrade_and_worker_raise_regression(tmp_path, monkeypatch):
    """回归：DB 落后（current 祖先）时应用仍可 upgrade、Worker 仍抛错——修复未破坏正常路径。"""
    _enable_db(tmp_path, monkeypatch)
    monkeypatch.delenv("ALLOW_UNKNOWN_MIGRATION_REVISION", raising=False)
    try:
        _setup("c9a4e71f2b50")  # 一个早于 head 的中段 revision（祖先）
        assert not migrate.schema_current()
        assert migrate._classify_schema(migrate.current_version(), migrate.target_head()) == "behind"

        # Worker：落后且不允许升级 -> 抛错，不静默、不并发迁移
        with pytest.raises(RuntimeError):
            migrate.ensure_schema_current(upgrade=False)

        # 应用：落后 -> 升到 head
        migrate.ensure_schema_current(upgrade=True)
        assert migrate.schema_current()
        assert migrate.current_version() == migrate.target_head()
        from app.db.repositories import load_users

        assert any(d["id"] == "user_schema_rel" for d in load_users())
    finally:
        _clear()


def test_schema_relationship_classification(tmp_path, monkeypatch):
    """关系分类单元（真实单链）：behind / unknown 判定正确。"""
    _enable_db(tmp_path, monkeypatch)
    try:
        from alembic.script import ScriptDirectory

        head = migrate.target_head()
        baseline = "ad6faa347bb8"  # 链根部（head 的祖先）
        script = ScriptDirectory.from_config(migrate._alembic_config())
        # behind：baseline 是 head 祖先，反之 head 不是 baseline 祖先
        assert migrate._is_ancestor(script, baseline, head) is True
        assert migrate._is_ancestor(script, head, baseline) is False
        assert migrate._classify_schema(baseline, head) == "behind"
        # unknown：不在树
        assert migrate._classify_schema("000000000000", head) == "unknown"
    finally:
        _clear()


# ---- 评审补充：真实 descendant / 多父 merge / alembic_version 多行 fail-closed ----

def _write_synth_tree(tmp_path, files):
    """构造合成 alembic 迁移树：files=[(rev, down_revision)...]。返回 (Config, ScriptDirectory)。

    down=None 表示根；down=("a","b") 表示多父 merge。用于在单链之外的场景测试分类/祖先逻辑。
    """
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    versions_dir = tmp_path / "synth" / "versions"
    versions_dir.mkdir(parents=True, exist_ok=True)
    for i, (rev, down) in enumerate(files):
        down_repr = "None" if down is None else repr(tuple(down) if isinstance(down, tuple) else down)
        (versions_dir / f"{i:04d}_{rev}.py").write_text(
            f"revision = {rev!r}\ndown_revision = {down_repr}\n", encoding="utf-8"
        )
    cfg = Config()
    cfg.set_main_option("script_location", str(tmp_path / "synth"))
    return cfg, ScriptDirectory.from_config(cfg)


def test_ahead_real_descendant_compat_boot_no_downgrade(tmp_path, monkeypatch):
    """真实 descendant 场景：DB 是 app head 的后代 → 迁移层兼容启动，不升级、不降级。

    用合成树 r1→r2(head=app)→r3(DB)，而非用 head==head 冒充 ahead。
    """
    _enable_db(tmp_path, monkeypatch)  # 使 persistence_enabled()=True
    monkeypatch.setenv("ALLOW_UNKNOWN_MIGRATION_REVISION", "false")
    cfg, script = _write_synth_tree(
        tmp_path, [("r1", None), ("r2", "r1"), ("r3", "r2")]
    )
    assert migrate._is_ancestor(script, "r2", "r3") is True  # r2(app head) 是 r3(DB) 祖先 -> 真 descendant
    # 用合成树替换模块内部 config，并注入 DB current=r3 / app head=r2
    monkeypatch.setattr(migrate, "_alembic_config", lambda: cfg)
    monkeypatch.setattr(migrate, "target_head", lambda: "r2")
    monkeypatch.setattr(migrate, "current_version", lambda: "r3")
    got = migrate.ensure_schema_current(upgrade=True)
    assert got == "r3", "descendant(ahead) 迁移层兼容启动应返回 DB 版本"
    # 未降级/未升级：app head 仍是 r2，DB 仍是 r3（二者均未被改写）
    assert migrate.target_head() == "r2"
    assert migrate.current_version() == "r3"


def test_multi_parent_merge_ancestor_walk(tmp_path, monkeypatch):
    """多父 merge：祖先判定必须覆盖全部分支，不能只沿首个父（downs[0]）走。"""
    _enable_db(tmp_path, monkeypatch)
    # 构造 r1 根；r2、r2b 两个兄弟；m9 合并(r2,r2b) —— 头部唯一为 m9
    cfg, script = _write_synth_tree(
        tmp_path, [("r1", None), ("r2", "r1"), ("r2b", "r1"), ("m9", ("r2", "r2b"))]
    )
    assert migrate._is_ancestor(script, "r2", "m9") is True
    assert migrate._is_ancestor(script, "r2b", "m9") is True  # 第二父也必须是祖先（修复前会漏判）
    assert migrate._is_ancestor(script, "r1", "m9") is True
    assert migrate._is_ancestor(script, "m9", "m9") is True
    assert migrate._is_ancestor(script, "absent", "m9") is False


def test_alembic_version_multi_row_is_blocked(tmp_path, monkeypatch):
    """alembic_version 表 >1 行 → fail-closed BLOCK，禁止启动（多版本不唯一）。"""
    _enable_db(tmp_path, monkeypatch)
    try:
        migrate.run_upgrade_to(migrate.target_head())
        assert migrate.schema_current()
        # 插入第二行版本 -> 表不再唯一
        _set_version(migrate.target_head())
        from app.db.session import session_scope

        with session_scope() as session:
            session.execute(text("INSERT INTO alembic_version (version_num) VALUES ('ffffffffffff')"))
        with pytest.raises(RuntimeError):
            migrate.current_version()
        with pytest.raises(RuntimeError):
            migrate.ensure_schema_current(upgrade=True)
        # 清理第二行，恢复单行
        with session_scope() as session:
            session.execute(text("DELETE FROM alembic_version WHERE version_num='ffffffffffff'"))
        assert migrate.current_version() == migrate.target_head()
    finally:
        _clear()