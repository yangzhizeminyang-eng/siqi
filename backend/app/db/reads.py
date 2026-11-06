"""统一读层门面（路径 A 核心读 DB 化；本地/测试保留 dict 读视图）。

架构契约：
- **持久化开启（USE_SQLALCHEMY=true，生产）**：核心可变业务读（任务/项目/仓库/团队需求/审批）从 **DB 读真源**，
  经 `load_*` 重建 pydantic 记录返回——清空 dict 后核心 API 仍能读到 DB 数据（"dict 退位"成立）。
- **持久化关闭（USE_SQLALCHEMY=false，本地/测试）**：读 dict 运行时视图（请求内一致），保既有 369 测试。
- 读主源唯一：本层内部按 `persistence_enabled()` 二选一（DB or dict），main.py 不直接碰 dict。
- 说明：DB 模式下读不做有副作用 advance（推进交由 Worker / 轮询详情端点），避免"读即改"。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.tenancy.models import DEFAULT_TENANT_ID


def persistence_enabled() -> bool:
    from app.db.repositories import persistence_enabled as _pe

    return _pe()


def _load_model(cls, d: dict[str, Any]):
    return cls.model_validate(d)


def _record_tenant(record: Any) -> str:
    """记录归属租户：缺失/None 按默认租户（历史数据兼容）。"""
    value = getattr(record, "tenant_id", None)
    return value or DEFAULT_TENANT_ID


def _scoped(values: list[Any], tenant_id: str | None) -> list[Any]:
    """读取层租户过滤（集中式：读真源后按 tenant_id 过滤，不在 Vue/路由层做）。"""
    if not tenant_id:
        return values
    return [v for v in values if _record_tenant(v) == tenant_id]


# ---------- 任务 ----------

def get_task(task_id: str) -> Any | None:
    from app import main
    from app.db.repositories import load_tasks

    if persistence_enabled():
        for d in load_tasks():
            if d.get("id") == task_id:
                return main.TaskRecord.model_validate(d)
        return None
    return main.TASKS.get(task_id)


def list_tasks(tenant_id: str | None = None) -> list[Any]:
    from app import main
    from app.db.repositories import load_tasks

    if persistence_enabled():
        tasks = [main.TaskRecord.model_validate(d) for d in load_tasks()]
    else:
        tasks = list(main.TASKS.values())
    tasks = _scoped(tasks, tenant_id)
    return sorted(tasks, key=lambda task: task.updated_at, reverse=True)


# ---------- 项目 / 仓库 ----------

def list_projects(tenant_id: str | None = None) -> list[Any]:
    from app import main
    from app.db.repositories import load_projects

    if persistence_enabled():
        projects = [main.ProjectRecord(**d) for d in load_projects()]
    else:
        projects = list(main.PROJECTS.values())
    projects = _scoped(projects, tenant_id)
    return sorted(projects, key=lambda p: p.updated_at, reverse=True)


def get_project(project_id: str) -> Any | None:
    from app import main
    from app.db.repositories import load_projects

    if persistence_enabled():
        for d in load_projects():
            if d.get("id") == project_id:
                return main.ProjectRecord(**d)
        return None
    return main.PROJECTS.get(project_id)


def list_repositories(project_id: str | None = None, *, include_archived: bool = False, tenant_id: str | None = None) -> list[Any]:
    from app import main
    from app.db.repositories import load_repositories

    if persistence_enabled():
        repositories = []
        for data in load_repositories():
            repo = main.RepositoryConfigRecord(**data)
            # Token plaintext is intentionally not persisted. The DB flag can
            # therefore outlive the runtime secret after a restart.
            repo.token_configured = bool(main.REPOSITORY_TOKENS.get(repo.id))
            repositories.append(repo)
        base = sorted(repositories, key=lambda r: r.updated_at, reverse=True)
    else:
        base = sorted(main.REPOSITORIES.values(), key=lambda r: r.updated_at, reverse=True)
    base = _scoped(base, tenant_id)
    return [
        repo for repo in base
        if (include_archived or repo.is_active) and (not project_id or repo.project_id == project_id)
    ]

def current_repository(project_id: str) -> Any | None:
    """返回项目当前仓库。

    旧数据可能在生命周期字段加入前已经存在多个有效仓库；当前仓库按最近更新时间确定。
    其他仓库不删除，仍由 include_archived=true 的历史查询保留。
    """
    repositories = list_repositories(project_id)
    return max(repositories, key=lambda repo: (repo.updated_at, repo.id)) if repositories else None


def current_repository_ids(project_id: str | None = None) -> set[str]:
    if project_id:
        repository = current_repository(project_id)
        return {repository.id} if repository else set()
    project_ids = {repository.project_id for repository in list_repositories()}
    return {
        repository.id
        for project in project_ids
        for repository in [current_repository(project)]
        if repository
    }


def get_repository(repository_id: str) -> Any | None:
    from app import main
    from app.db.repositories import load_repositories

    if persistence_enabled():
        for d in load_repositories():
            if d.get("id") == repository_id:
                return main.RepositoryConfigRecord(**d)
        return None
    return main.REPOSITORIES.get(repository_id)


# ---------- 用户 ----------

def list_users() -> list[Any]:
    from app import main
    from app.db.repositories import load_users

    if persistence_enabled():
        return sorted((main.UserRecord(**d) for d in load_users()), key=lambda u: u.id)
    return sorted(main.USERS.values(), key=lambda u: u.id)


def get_user(user_id: str) -> Any | None:
    from app import main
    from app.db.repositories import load_users

    if persistence_enabled():
        for d in load_users():
            if d.get("id") == user_id:
                return main.UserRecord(**d)
        return None
    return main.USERS.get(user_id)


def user_by_email(email: str) -> Any | None:
    from app import main

    return main.USERS_BY_EMAIL.get(email)


# ---------- 团队需求 ----------

def get_team_request(request_id: str) -> Any | None:
    return find_team_request(request_id)


def find_team_request(request_id: str) -> Any | None:
    from app import main
    from app.db.repositories import load_team_requests

    if persistence_enabled():
        for d in load_team_requests():
            if d.get("id") == request_id or d.get("request_no") == request_id:
                return main.TeamRequestRecord(**d)
        return None
    record = main.TEAM_REQUESTS.get(request_id)
    if record is None:
        record = next((item for item in main.TEAM_REQUESTS.values() if item.request_no == request_id), None)
    return record


def list_team_requests(tenant_id: str | None = None) -> list[Any]:
    from app import main
    from app.db.repositories import load_team_requests

    if persistence_enabled():
        requests = [main.TeamRequestRecord(**d) for d in load_team_requests()]
    else:
        requests = list(main.TEAM_REQUESTS.values())
    requests = _scoped(requests, tenant_id)
    return sorted(requests, key=lambda item: item.created_at, reverse=True)


# ---------- 审计 ----------

def list_audit_events(resource_type: str | None = None, resource_id: str | None = None, tenant_id: str | None = None) -> list[Any]:
    from app import main
    from app.db.repositories import load_audit_events, shared_persistence_enabled

    if shared_persistence_enabled():
        events = [main.AuditEventRecord(**d) for d in load_audit_events()]
    else:
        events = list(main.AUDIT_EVENTS.values())
    events = _scoped(events, tenant_id)
    events.sort(key=lambda item: item.created_at, reverse=True)
    return [
        event
        for event in events
        if (not resource_type or event.resource_type == resource_type)
        and (not resource_id or event.resource_id == resource_id)
    ]


# ---------- 评论 / 通知（DEV-019）----------
# 读主源唯一：持久化开启从 DB 读真源，否则读运行时 dict；不在 main.py 新增 TASKS.get 主读。

def list_task_comments(task_id: str) -> list[Any]:
    from app import main
    from app.db.repositories import load_comments, persistence_enabled

    if persistence_enabled():
        items = [main.CommentRecord(**d) for d in load_comments()]
    else:
        items = list(main.COMMENTS.values())
    items = [c for c in items if c.task_id == task_id]
    return sorted(items, key=lambda c: c.created_at)


def list_user_notifications(user_id: str, *, unread_only: bool = False) -> list[Any]:
    from app import main
    from app.db.repositories import load_notifications, persistence_enabled

    if persistence_enabled():
        items = [main.NotificationRecord(**d) for d in load_notifications()]
    else:
        items = list(main.NOTIFICATIONS.values())
    items = [n for n in items if n.recipient_id == user_id]
    if unread_only:
        items = [n for n in items if n.read_at is None]
    return sorted(items, key=lambda n: n.created_at, reverse=True)


def list_task_notifications(task_id: str) -> list[Any]:
    from app import main
    from app.db.repositories import load_notifications, persistence_enabled

    if persistence_enabled():
        items = [main.NotificationRecord(**d) for d in load_notifications()]
    else:
        items = list(main.NOTIFICATIONS.values())
    return sorted((n for n in items if n.task_id == task_id), key=lambda n: n.created_at, reverse=True)


def get_notification(notification_id: str) -> Any | None:
    from app import main
    from app.db.repositories import load_notifications, persistence_enabled

    if persistence_enabled():
        for d in load_notifications():
            if d.get("id") == notification_id:
                return main.NotificationRecord(**d)
        return None
    return main.NOTIFICATIONS.get(notification_id)


def unread_notification_count(user_id: str) -> int:
    return len(list_user_notifications(user_id, unread_only=True))


# ---------- 历史任务复用索引（DEV-021）----------

def list_historical_summaries(project_id: str | None = None) -> list[Any]:
    from app import main
    from app.db.repositories import load_historical_task_summaries, persistence_enabled

    if persistence_enabled():
        items = [main.HistoricalTaskSummary(**d) for d in load_historical_task_summaries()]
    else:
        items = list(main.HISTORICAL_SUMMARIES.values())
    if project_id:
        items = [s for s in items if s.project_id == project_id]
    return items


def get_historical_summary_by_task(task_id: str) -> Any | None:
    from app import main
    from app.db.repositories import load_historical_task_summaries, persistence_enabled

    if persistence_enabled():
        for d in load_historical_task_summaries():
            if d.get("task_id") == task_id:
                return main.HistoricalTaskSummary(**d)
        return None
    existing = [s for s in main.HISTORICAL_SUMMARIES.values() if s.task_id == task_id]
    return max(existing, key=lambda s: s.summary_version) if existing else None


def list_historical_indexes(summary_id: str | None = None) -> list[Any]:
    from app import main
    from app.db.repositories import load_historical_task_indexes, persistence_enabled

    if persistence_enabled():
        items = [main.HistoricalTaskIndexRecord(**d) for d in load_historical_task_indexes()]
    else:
        items = list(main.HISTORICAL_INDEXES.values())
    if summary_id:
        items = [i for i in items if i.summary_id == summary_id]
    return sorted(items, key=lambda i: i.created_at)


def get_historical_index(summary_id: str) -> Any | None:
    for rec in list_historical_indexes(summary_id):
        return rec
    return None


# ---------- 索引 / 检索 ----------
def list_repository_indexes(repository_id: str, tenant_id: str | None = None) -> list[Any]:
    from app import main
    from app.db.repositories import load_repository_indexes, persistence_enabled

    if persistence_enabled():
        records = [main.RepositoryIndexRecord(**d) for d in load_repository_indexes()]
    else:
        records = list(main.REPOSITORY_INDEXES.values())
    records = _scoped(records, tenant_id)
    scoped = [record for record in records if record.repository_id == repository_id]
    # 兼容历史上已经落库的重复运行记录：列表只展示一个 canonical 任务，原记录仍保留，
    # 由后台启动时的数据库唯一键和 active_key 防止新重复继续产生。
    seen_running: set[tuple[str, str]] = set()
    visible: list[Any] = []
    for record in sorted(scoped, key=lambda item: item.created_at, reverse=True):
        if record.status in {"PENDING", "SYNCING", "PARSING", "EMBEDDING", "STORING"}:
            key = (record.repository_id, record.commit_sha)
            if key in seen_running:
                continue
            seen_running.add(key)
        visible.append(record)
    return visible


def get_index(index_id: str) -> Any | None:
    from app import main
    from app.db.repositories import load_repository_indexes, persistence_enabled

    if persistence_enabled():
        for d in load_repository_indexes():
            if d.get("id") == index_id:
                return main.RepositoryIndexRecord(**d)
        return None
    return main.REPOSITORY_INDEXES.get(index_id)


def latest_completed_index(repository_id: str) -> Any | None:
    for record in list_repository_indexes(repository_id):
        if record.status == "COMPLETED":
            return record
    return None


def list_retrieval_records(task_id: str | None = None, tenant_id: str | None = None) -> list[Any]:
    from app import main
    from app.db.repositories import load_retrieval_records, persistence_enabled

    if persistence_enabled():
        records = [main.RetrievalRecord(**d) for d in load_retrieval_records()]
    else:
        records = list(main.RETRIEVAL_RECORDS.values())
    records = _scoped(records, tenant_id)
    records = [r for r in records if not task_id or r.task_id == task_id]
    return sorted(records, key=lambda r: r.created_at, reverse=True)


def code_chunks_of_index(index_id: str) -> list[Any]:
    from app import main
    from app.db.repositories import load_code_chunks, persistence_enabled

    if persistence_enabled():
        chunks = [main.CodeChunkRecord(**d) for d in load_code_chunks()]
    else:
        chunks = list(main.CODE_CHUNKS.values())
    return sorted(
        (chunk for chunk in chunks if chunk.index_id == index_id),
        key=lambda chunk: (chunk.file_path, chunk.chunk_index),
    )


# ---------- 审批队列 ----------

def _lead_owned_project_ids(user_id: str) -> set[str]:
    """项目负责人（lead）可见项目集：以其作为默认审批人的仓库所属项目；DB 真源。"""
    return {repo.project_id for repo in list_repositories() if repo.approval_owner_id == user_id}


def list_pending_approvals(user: Any) -> list[Any]:
    """审批中心队列（DB 真源）：
    - admin：全量可见；
    - lead（项目负责人）：可见自己负责项目下的 WAITING_APPROVAL（即使未指派给自己）+ 指派给自己的；
    - 其余（approver/developer）：仅指派给自己的任务。
    """
    base = list_tasks()
    base = [t for t in base if t.status == "WAITING_APPROVAL"]
    if user.role != "admin":
        if user.role == "lead":
            owned = _lead_owned_project_ids(user.id)
            base = [t for t in base if t.approval_owner_id == user.id or t.project_id in owned]
        else:
            base = [t for t in base if t.approval_owner_id == user.id]
    return sorted(base, key=lambda t: t.updated_at, reverse=True)


# ---------- 从 DB 恢复运行时读视图 ----------

def reload_from_db() -> dict[str, int]:
    """从 DB（持久真源）把运行时读视图（dict）重灌为最新状态；返回各 store 恢复条数。

    供重启 / 手工恢复 / 测试使用；生产启动由 bootstrap.restore_from_db 自动执行。
    """
    from app.db.bootstrap import restore_from_db

    return restore_from_db()


# ---------- DB 直查工具（对账 / 验证，不作为 handler 主读源）----------

def audit_events_from_db() -> list[dict[str, Any]]:
    from app.db.repositories import load_audit_events, persistence_enabled

    return load_audit_events() if persistence_enabled() else []


def repository_indexes_from_db(repository_id: str | None = None) -> list[dict[str, Any]]:
    from app.db.repositories import load_repository_indexes, persistence_enabled

    if not persistence_enabled():
        return []
    records = load_repository_indexes()
    if repository_id:
        records = [r for r in records if r.get("repository_id") == repository_id]
    return records


# ---------- 企业版 E1-01 租户读门面（真源在 tenancy.store，含 DB 写透/恢复） ----------

def list_memberships(tenant_id: str | None = None) -> list[Any]:
    from app.tenancy import store

    members = store.members_of_tenant(tenant_id) if tenant_id else list(store.MEMBERSHIPS.values())
    return members


def get_membership(membership_id: str) -> Any | None:
    from app.tenancy import store

    return store.get_membership(membership_id)


def active_membership_for_user(user_id: str) -> Any | None:
    from app.tenancy import store

    return store.active_membership_for_user(user_id)


def joined_tenants(user_id: str) -> list[dict]:
    from app.tenancy import store

    return store.joined_tenants(user_id)
