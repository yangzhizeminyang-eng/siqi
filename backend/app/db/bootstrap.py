"""P0 数据恢复：启动时把 DB（唯一真源）灌回模块级 dict 缓存。

调用方为 main.lifespan（首请求前）。测试中 USE_SQLALCHEMY=false，本函数为空操作。
"""

from __future__ import annotations

from app.db.repositories import (
    load_audit_events,
    load_code_chunks,
    load_comments,
    load_historical_task_indexes,
    load_historical_task_summaries,
    load_index_snapshots,
    load_notifications,
    load_password_resets,
    load_projects,
    load_repositories,
    load_retrieval_records,
    load_repository_indexes,
    load_tasks,
    load_team_requests,
    load_users,
    persistence_enabled,
    seed_store,
)


def restore_from_db() -> dict[str, int]:
    """从 DB 恢复全部业务 store 到 main 的模块级 dict。返回各 store 恢复条数。"""
    if not persistence_enabled():
        return {}

    # 延迟导入 main，避免 import 期循环依赖（main 在 lifespan 内才引用本模块）
    from app import main

    counts: dict[str, int] = {}

    users = load_users()
    projects = load_projects()
    repositories = load_repositories()

    # 首次建库：DB 为空则把代码内置种子（用户/项目/仓库）写库，避免与后续持久化重复
    if not users:
        seed_store("users", main.USERS.values())
        users = load_users()
    if not projects:
        seed_store("projects", main.PROJECTS.values())
        projects = load_projects()
    if not repositories:
        seed_store("repositories", main.REPOSITORIES.values())
        repositories = load_repositories()

    _restore_users(main, users)
    _restore_projects(main, projects)
    _restore_repositories(main, repositories)
    _restore_gitee_tokens(main, repositories)

    pwd = load_password_resets()
    main.PASSWORD_RESET_REQUESTS.clear()
    for item in pwd:
        main.PASSWORD_RESET_REQUESTS[item["id"]] = main.PasswordResetRecord(**item)
    counts["password_resets"] = len(pwd)

    # 审计：DB 记录可能是任务子事件与全局事件的并集，直接以 DB 为准重建
    events = load_audit_events()
    main.AUDIT_EVENTS.clear()
    for item in events:
        main.AUDIT_EVENTS[item["id"]] = main.AuditEventRecord(**item)
    counts["audit_events"] = len(events)

    tasks = load_tasks()
    main.TASKS.clear()
    for item in tasks:
        record = main.TaskRecord.model_validate(item)
        main.TASKS[record.id] = record
    counts["tasks"] = len(tasks)

    requests = load_team_requests()
    main.TEAM_REQUESTS.clear()
    for item in requests:
        record = main.TeamRequestRecord(**item)
        main.TEAM_REQUESTS[record.id] = record
    counts["team_requests"] = len(requests)

    # DEV-019 评论与通知：DB 为真源，重启后读回
    comments = load_comments()
    main.COMMENTS.clear()
    for item in comments:
        main.COMMENTS[item["id"]] = main.CommentRecord(**item)
    counts["comments"] = len(comments)

    notifications = load_notifications()
    main.NOTIFICATIONS.clear()
    for item in notifications:
        main.NOTIFICATIONS[item["id"]] = main.NotificationRecord(**item)
    counts["notifications"] = len(notifications)

    # DEV-021 历史任务复用摘要与索引：DB 真源恢复；向量内存镜像由检索时按需构造/惰性
    summaries = load_historical_task_summaries()
    main.HISTORICAL_SUMMARIES.clear()
    for item in summaries:
        main.HISTORICAL_SUMMARIES[item["id"]] = main.HistoricalTaskSummary(**item)
    counts["historical_summaries"] = len(summaries)

    indexes = load_historical_task_indexes()
    main.HISTORICAL_INDEXES.clear()
    for item in indexes:
        main.HISTORICAL_INDEXES[item["id"]] = main.HistoricalTaskIndexRecord(**item)
    counts["historical_indexes"] = len(indexes)

    _restore_rag_stores(main, counts)

    # 企业版 E1-01：恢复租户/组织/成员关系（DB 空则种子默认租户与成员）
    try:
        from app.tenancy.store import restore_from_db as restore_tenancy

        restore_tenancy()
    except Exception:  # noqa: BLE001 租户恢复失败不影响团队版核心恢复
        pass

    # 企业版 E2-01：恢复审批申请
    try:
        from app.approvals.store import restore_from_db as restore_approvals

        restore_approvals()
    except Exception:  # noqa: BLE001 审批恢复失败不影响团队版核心恢复
        pass

    # 企业版 E4-01：恢复 Runtime 任务与 checkpoint（跨进程/重启恢复）
    try:
        from app.enterprise_runtime import store as runtime_store
        from app.enterprise_runtime.checkpoint import CheckpointStore

        runtime_store.restore_from_db()
        CheckpointStore().restore_from_db()
    except Exception:  # noqa: BLE001 Runtime 恢复失败不影响团队版核心恢复
        pass

    # 企业版 E4-02：恢复 AgentRun 与 ArtifactStore（C9：重启后产物引用链不断）+ 未完成 run stale recovery
    try:
        from app.enterprise_runtime.artifacts import runs as agent_runs_store
        from app.enterprise_runtime.artifacts import store as artifacts_store

        artifacts_store.restore_from_db()
        agent_runs_store.restore_from_db()
        agent_runs_store.mark_stale_failed()
    except Exception:  # noqa: BLE001 AgentRun/Artifact 恢复失败不影响主链路恢复
        pass

    return counts


def _restore_rag_stores(main, counts: dict[str, int]) -> None:
    """恢复 RAG 侧 store：仓库索引、代码切片、检索记录（供重启后仍可检索）。"""
    indexes = load_repository_indexes()
    main.REPOSITORY_INDEXES.clear()
    main.LATEST_INDEX_BY_REPO.clear()
    for item in indexes:
        record = main.RepositoryIndexRecord(**item)
        main.REPOSITORY_INDEXES[record.id] = record
        current = main.LATEST_INDEX_BY_REPO.get(record.repository_id)
        if current is None or record.created_at >= main.REPOSITORY_INDEXES[current].created_at:
            main.LATEST_INDEX_BY_REPO[record.repository_id] = record.id
    counts["repository_indexes"] = len(indexes)

    chunks = load_code_chunks()
    main.CODE_CHUNKS.clear()
    for item in chunks:
        record = main.CodeChunkRecord(**item)
        main.CODE_CHUNKS[record.chunk_id] = record
    counts["code_chunks"] = len(chunks)

    retrieval = load_retrieval_records()
    main.RETRIEVAL_RECORDS.clear()
    for item in retrieval:
        record = main.RetrievalRecord(**item)
        main.RETRIEVAL_RECORDS[record.id] = record
    counts["retrieval_records"] = len(retrieval)

    # 单索引的仓库代码快照：PARSING 等非终态重启后可由内存快照继续，避免中断重建
    snapshots = load_index_snapshots()
    main.CODE_INDEX_SNAPSHOTS.clear()
    for item in snapshots:
        from app.main import CodeFile

        files = [CodeFile(**f) for f in item["files"] if isinstance(f, dict)]
        main.CODE_INDEX_SNAPSHOTS[item["index_id"]] = files
    counts["index_snapshots"] = len(snapshots)


def _restore_users(main, users) -> None:
    main.USERS.clear()
    for item in users:
        main.USERS[item["id"]] = main.UserRecord(**item)


def _restore_projects(main, projects) -> None:
    main.PROJECTS.clear()
    for item in projects:
        main.PROJECTS[item["id"]] = main.ProjectRecord(**item)


def _restore_repositories(main, repositories) -> None:
    main.REPOSITORIES.clear()
    for item in repositories:
        main.REPOSITORIES[item["id"]] = main.RepositoryConfigRecord(**item)


def _restore_gitee_tokens(main, repositories) -> None:
    """把部署级账号令牌（env GITEE_TOKEN）作为兜底，注入尚无按库令牌的仓库。

    Gitee token 不落明文库（DB 只存 token_alias/token_configured），重启后
    REPOSITORY_TOKENS 会清空；此兜底让部署配置生效，未按库存 token 的仓库统一
    走账号级令牌。token 值只赋给内存 dict，永不打印/记日志/落库。
    """
    import os

    env_token = os.environ.get("GITEE_TOKEN")
    token = env_token.strip() if env_token else None
    placeholder_tokens = {"demo-token-not-returned"}

    for item in repositories:
        rid = item["id"]
        current = main.REPOSITORY_TOKENS.get(rid)
        repository = main.REPOSITORIES.get(rid)

        # Seed data intentionally carries a non-secret placeholder. It must
        # never win over the deployment token or be sent to Gitee.
        if current in placeholder_tokens:
            main.REPOSITORY_TOKENS.pop(rid, None)
            current = None

        if token and not current:
            main.REPOSITORY_TOKENS[rid] = token
            current = token

        if repository is not None and current is None:
            repository.token_configured = False
            repository.token_alias = None
