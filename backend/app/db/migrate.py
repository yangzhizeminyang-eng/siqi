"""数据库 schema 版本管理与 Alembic 升级入口。

职责：
- 唯一的升级入口：`alembic upgrade head`（对既有库做安全演进，不只依赖 create_all）。
- 启动前可检查 schema 版本：应用与 Worker 都能确认当前版本是否落后于目标 head。
- 并发安全：只有显式允许的进程做迁移（默认 FastAPI 应用），Worker 只检查不迁移，
  避免多 Worker 抢 Alembic 版本锁。

约定：
- `persistence_enabled()==False`（本地/测试内存模式）时全部为空操作。
- 迁移策略**前向-only，任何场景禁止 downgrade**。
- 落后（DB 为 head 祖先）时 `upgrade=True` 执行迁移；`upgrade=False`（Worker）则抛出清晰错误要求先迁移。
- DB 领先（DB 为 head 后代）→ **迁移层兼容启动** + warning，不升级、不降级。
  注意：这只是「迁移层不升级不降级地共存启动」，**不等于应用全局只读**——旧应用对新增 schema 的写
  操作仍可能由调用方执行并存在风险；本模块不提供统一写入门禁。
- unknown / 无祖先关系（分支不一致）/ 多 head / alembic_version 多行 → **BLOCKED**，禁止自动启动
  （unknown 仅在显式开关下放行迁移层兼容启动）。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger("backend.migrate")

_BACKEND_DIR = Path(__file__).resolve().parents[2]
_ALEMBIC_INI = _BACKEND_DIR / "alembic.ini"
_BASELINE_REVISION = "ad6faa347bb8"
_BASELINE_MARKER_TABLES = {"agent_checkpoints", "tasks", "users"}


def _alembic_config():
    from alembic.config import Config

    config = Config(str(_ALEMBIC_INI))
    config.set_main_option("script_location", str(_BACKEND_DIR / "migrations"))
    return config


def _persistence_enabled() -> bool:
    from app.db.repositories import persistence_enabled

    return persistence_enabled()


def current_version() -> str | None:
    """读取库中 alembic_version 表当前已应用版本；未迁移则为 None。"""
    if not _persistence_enabled():
        return None
    from sqlalchemy import inspect, text

    from app.db.session import get_engine, session_scope

    engine = get_engine()
    try:
        if "alembic_version" not in inspect(engine).get_table_names():
            return None
    except Exception:  # noqa: BLE001 - 连不上库时按未迁移处理，调用方再决定升级还是报错
        return None
    with session_scope() as session:
        rows = session.execute(text("SELECT version_num FROM alembic_version")).fetchall()
        if len(rows) > 1:
            # fail-closed：应用/启动方必须能唯一确定迁移版本，多行说明版本不一致或迁移异常。
            raise RuntimeError(
                f"数据库 alembic_version 表存在 {len(rows)} 行（应为恰 1 行）。迁移版本不唯一，"
                "无法安全判定 schema，禁止启动（fail-closed）。请人工核对并收敛为单行后再启动。"
            )
        return rows[0][0] if rows else None


def target_head() -> str:
    """从迁移脚本目录计算唯一的 head 版本（若有多个 head 说明链路断裂，会直接暴露）。"""
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(_alembic_config())
    heads = {rev.revision if hasattr(rev, "revision") else str(rev) for rev in script.get_heads()}
    if len(heads) != 1:
        raise RuntimeError(f"迁移链存在 {len(heads)} 个 head（{sorted(heads)}），应合并为单一 head 后再升级")
    return heads.pop()


def schema_current() -> bool:
    return current_version() == target_head()


def run_upgrade() -> str:
    """执行 alembic upgrade head（幂等）；落后且迁移失败时抛异常（启动即失败，不静默）。"""
    from alembic import command

    _stamp_existing_baseline_if_needed()
    logger.info("[migrate] 执行 alembic upgrade head（target=%s）", target_head())
    command.upgrade(_alembic_config(), "head")
    version = current_version()
    logger.info("[migrate] upgrade 完成，当前版本=%s", version)
    return version or ""


def run_upgrade_to(revision: str) -> str:
    """升级/回退到指定迁移版本（供测试构造 legacy schema 或手工精确定位）。"""
    from alembic import command

    command.upgrade(_alembic_config(), revision)
    return current_version() or ""


def ensure_schema_current(*, upgrade: bool) -> str:
    """启动前 schema 检查 + 可选迁移（前向-only，任何场景禁止 downgrade）。

    判定规则（fail-closed）：
    - `current == head`                         -> 正常启动。
    - `current` 是 `head` 的祖先（DB 落后）      -> `upgrade=True` 时执行升级；`upgrade=False`(Worker) 抛错提示先迁移。
    - `current` 是 `head` 的后代（DB 领先/compat）-> **迁移层兼容启动**（不升级、不降级，仅 `warning`）。
      仅迁移层共存，**不等于应用全局只读**：旧应用写新 schema 仍可能产生风险，调用方须自行评估。
    - `current` 与 `head` 无祖先关系（分支/分叉）  -> **BLOCKED**，抛错禁止启动。
    - `current` 不在本应用迁移树（unknown）       -> **BLOCKED**（默认 fail-closed）；仅当显式
      开启 `ALLOW_UNKNOWN_MIGRATION_REVISION=true`（人工确认开关）才允许迁移层兼容启动并强 warning。
    - 多 head                                  -> **BLOCKED**（`target_head()` 已抛）。
    - alembic_version 表非恰 1 行               -> **BLOCKED**（`current_version()` 已抛）。
    - 未开启持久化（本地/测试内存模式）: 直接返回。
    """
    if not _persistence_enabled():
        return ""
    _stamp_existing_baseline_if_needed()
    current = current_version()
    head = target_head()  # 多 head 会在此抛错 -> BLOCKED
    if current is None:
        # 未版本化的库（空库或 create_all 老库被补戳为 baseline）：视为落后，走升级。
        if upgrade:
            run_upgrade()
            return current_version() or ""
        raise RuntimeError(
            f"数据库 schema 未版本化（无 alembic_version）；当前由应用负责迁移。请在部署环境运行 "
            f"`python -m scripts.migrate`（或数据库管理员执行 alembic upgrade head）后，再启动 Worker。"
        )
    if current == head:
        return current
    relation = _classify_schema(current, head)
    if relation == "ahead":
        # DB 领先于本应用 head：新 schema 对本镜像视为迁移层兼容（旧版/回滚镜像读取更新 schema）。
        # 迁移层兼容启动：不升级、不降级；注意不代表应用全局只读（旧应用写新表/列由调用方负责评估）。
        logger.warning(
            "[migrate][compat] DB revision %s 是本应用 head %s 的后代（DB 领先）。"
            "按迁移层兼容启动（不 upgrade、不 downgrade），非全局只读；若业务要求旧版本写这些新表/列，请改用匹配版本应用。",
            current,
            head,
        )
        return current
    if relation == "behind":
        message = (
            f"数据库 schema 版本落后：当前 {current}，目标 {head}。"
        )
        if upgrade:
            run_upgrade()
            return current_version() or ""
        raise RuntimeError(
            f"{message} 请先在部署环境运行 `python -m scripts.migrate`（或数据库管理员执行 alembic upgrade head）后，再启动 Worker。"
        )
    if relation == "unknown":
        if _allow_unknown_revision():
            logger.warning(
                "[migrate][compat] DB revision %s 不在本应用迁移树（unknown），且已显式开启 "
                "ALLOW_UNKNOWN_MIGRATION_REVISION 人工确认开关。按迁移层兼容启动（不升级、不降级，非全局只读）。",
                current,
            )
            return current
        raise RuntimeError(
            f"数据库 schema 版本 %s 不在本应用迁移树（unknown），无法判定与目标 {head} 的关系（fail-closed）。"
            "禁止自动启动：请人工核对 schema 兼容性；确需强制迁移层兼容启动时，显式设置环境变量 "
            "ALLOW_UNKNOWN_MIGRATION_REVISION=true 后再启动。" % current
        )
    # relation == "unrelated"：DB 版本与 app head 无任何祖先关系（分支/分叉）。
    raise RuntimeError(
        f"数据库 schema 版本 {current} 与本应用迁移 head {head} 无祖先关系（分支不一致/多 fork），"
        "无法安全升级或降级（BLOCKED）。请人工核对迁移链后处理，禁止自动启动。"
    )


def _allow_unknown_revision() -> bool:
    """人工确认开关：仅当显式开启时才放行 unknown revision 的迁移层兼容启动（默认 false = fail-closed）。"""
    return os.getenv("ALLOW_UNKNOWN_MIGRATION_REVISION", "false").strip().lower() in {"1", "true", "yes", "on"}


def _classify_schema(current: str, head: str) -> str:
    """返回 current 相对 head 的关系：'equal'|'behind'|'ahead'|'unrelated'|'unknown'。

    - equal     : current == head（已在调用方提前处理，此处不返回）。
    - behind    : current 是 head 的祖先（DB 落后）。
    - ahead     : head 是 current 的祖先（DB 领先/兼容）。
    - unrelated : 两者均在本树但无祖先关系（分支/分叉）。
    - unknown   : current 不在本应用迁移树。
    """
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(_alembic_config())
    try:
        node = script.get_revision(current)
    except Exception:  # noqa: BLE001
        node = None
    if node is None:
        return "unknown"
    if _is_ancestor(script, head, current):  # head 祖先指向 current -> DB 领先
        return "ahead"
    if _is_ancestor(script, current, head):  # current 祖先指向 head -> DB 落后
        return "behind"
    return "unrelated"


def _is_ancestor(script, ancestor: str, descendant: str) -> bool:
    """判断 `ancestor` 是否在 `descendant` 的祖先链上。

    沿 `down_revision` 的**全部分支**深度搜索（兼容多父 merge/multi-parent，避免只沿首个分支误判
    为 unrelated）。
    """
    stack = [script.get_revision(descendant)]
    seen = set()
    while stack:
        node = stack.pop()
        if node is None or node.revision in seen:
            continue
        seen.add(node.revision)
        if node.revision == ancestor:
            return True
        downs = node.down_revision
        if not downs:
            continue
        if isinstance(downs, str):
            downs = [downs]
        for parent in downs:
            stack.append(script.get_revision(parent))
    return False


def _stamp_existing_baseline_if_needed() -> None:
    """兼容早期由 `create_all()` 建出的无版本旧库。

    这类库已经有 baseline 业务表，但 `alembic_version` 为空；如果直接跑 Alembic，
    baseline 会再次 `create_table` 并失败。本函数只在检测到 baseline 标志表均存在时
    将版本戳补到 baseline，再继续执行后续幂等迁移。
    """
    if not _persistence_enabled() or current_version() is not None:
        return

    from alembic import command
    from sqlalchemy import inspect

    from app.db.session import get_engine

    table_names = set(inspect(get_engine()).get_table_names())
    if not _BASELINE_MARKER_TABLES.issubset(table_names):
        return

    logger.warning(
        "[migrate] 检测到已有 baseline 表但 alembic_version 为空，补版本戳到 %s 后继续增量迁移",
        _BASELINE_REVISION,
    )
    command.stamp(_alembic_config(), _BASELINE_REVISION)
