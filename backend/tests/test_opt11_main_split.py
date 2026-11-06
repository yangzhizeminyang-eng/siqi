"""OPT-11 main.py 渐进拆分：行为回归锚点。

三条不变式（迁移全程必须成立）：
1. 有效路由表（含 `_IncludedRouter` 惰性包装下的全部 /api 操作）逐条不变：
   117 条 (method, path, operationId) 与拆分前一致（排序后集合比较）；
   —— OPT-14（2026-10-16）受控新增 `GET /api/audit-events/export` 后为 118 条，
   新增条目已并入下方冻结表，除此之外仍逐条比对；
2. 拆分前从 `app.routes` 扁平冻结的 83 条核心路由全部仍被服务，且处理器名未变
   （operationId = 处理器名 + 路径 + 方法，公式反推比对）；
3. 领域处理器迁出：`app.api.<domain>` 提供处理器，`app.main` 不再保留同名残留。

背景：FastAPI 0.141 起 include_router 不再即时展开路由，而是以 `_IncludedRouter`
惰性包装挂在 `app.routes` 上（拆分前 5 个企业域 router 即如此，故不在扁平扫描内），
因此路由表以 `app.openapi()`（运行时有效路由的统一投影）为准。
117 条基线于 step 1 迁移后即刻导出，并已逐条核验：83 条核心路由的
(method, path) 与处理器名和迁移前扁平冻结快照完全一致（0 丢失 / 0 改名）。

TDD：本文件先于迁移落地（红），每完成一个领域批次即收窄红区，全部迁移后转绿。
"""

from __future__ import annotations

import importlib
import re

from app.main import app

# 迁移前扁平冻结快照（2026-10-16，迁移前 dump 自 app.routes，83 条核心路由）
FROZEN_ROUTES: list[tuple[str, str, str]] = [
    ("GET", "/api/approvals/candidates", "list_approval_candidates"),
    ("GET", "/api/approvals/pending", "list_pending_approvals"),
    ("GET", "/api/audit-events", "list_audit_events"),
    ("POST", "/api/auth/login", "login"),
    ("POST", "/api/auth/logout", "logout"),
    ("GET", "/api/auth/me", "me"),
    ("POST", "/api/auth/password/emergency-reset", "emergency_reset_password"),
    ("POST", "/api/auth/password/forgot", "forgot_password"),
    ("GET", "/api/auth/password/requests", "list_password_reset_requests"),
    ("POST", "/api/auth/password/requests/{request_id}/reset", "reset_requested_password"),
    ("GET", "/api/dashboard/automation-summary", "get_dashboard_automation_summary"),
    ("GET", "/api/events/stream", "stream_realtime_events"),
    ("GET", "/api/health", "health"),
    ("POST", "/api/history/search", "search_historical_tasks"),
    ("GET", "/api/notifications", "list_notifications"),
    ("GET", "/api/notifications/unread-count", "notifications_unread_count"),
    ("POST", "/api/notifications/{notification_id}/read", "mark_notification_read"),
    ("GET", "/api/projects", "list_projects"),
    ("POST", "/api/projects", "create_project"),
    ("GET", "/api/projects/{project_id}", "get_project"),
    ("PUT", "/api/projects/{project_id}", "update_project"),
    ("GET", "/api/repositories", "list_repositories"),
    ("POST", "/api/repositories", "create_repository"),
    ("POST", "/api/repositories/inspect", "inspect_repository"),
    ("PUT", "/api/repositories/{repository_id}", "update_repository"),
    ("POST", "/api/repositories/{repository_id}/index", "trigger_repository_index"),
    ("POST", "/api/repositories/{repository_id}/index/rebuild", "rebuild_repository_index"),
    ("GET", "/api/repositories/{repository_id}/indexes", "list_repository_indexes"),
    ("GET", "/api/repositories/{repository_id}/indexes/{index_id}", "get_repository_index"),
    ("POST", "/api/repositories/{repository_id}/indexes/{index_id}/retry", "retry_repository_index"),
    ("GET", "/api/tasks", "list_tasks"),
    ("POST", "/api/tasks", "create_task"),
    ("GET", "/api/tasks/{task_id}", "get_task"),
    ("GET", "/api/tasks/{task_id}/analysis", "get_task_analysis"),
    ("POST", "/api/tasks/{task_id}/analysis/generate", "generate_task_analysis"),
    ("GET", "/api/tasks/{task_id}/analysis/questions", "get_task_analysis_questions"),
    ("POST", "/api/tasks/{task_id}/analysis/questions/{question_id}/answer", "answer_task_analysis_question"),
    ("POST", "/api/tasks/{task_id}/approval-owner", "assign_task_approval_owner"),
    ("POST", "/api/tasks/{task_id}/cancel", "cancel_task"),
    ("GET", "/api/tasks/{task_id}/comments", "list_task_comments"),
    ("POST", "/api/tasks/{task_id}/comments", "create_task_comment"),
    ("DELETE", "/api/tasks/{task_id}/comments/{comment_id}", "delete_task_comment"),
    ("PUT", "/api/tasks/{task_id}/comments/{comment_id}", "update_task_comment"),
    ("GET", "/api/tasks/{task_id}/completed-summary", "get_task_completed_summary"),
    ("POST", "/api/tasks/{task_id}/completed-summary", "generate_task_completed_summary"),
    ("GET", "/api/tasks/{task_id}/completed-summary/index", "get_task_completed_summary_index"),
    ("POST", "/api/tasks/{task_id}/completed-summary/reindex", "reindex_task_completed_summary"),
    ("GET", "/api/tasks/{task_id}/contexts", "get_task_contexts"),
    ("POST", "/api/tasks/{task_id}/contexts/manual", "add_manual_task_contexts"),
    ("POST", "/api/tasks/{task_id}/contexts/resume", "resume_task_contexts"),
    ("GET", "/api/tasks/{task_id}/diffs", "list_task_diffs"),
    ("POST", "/api/tasks/{task_id}/diffs/generate", "generate_task_diff"),
    ("GET", "/api/tasks/{task_id}/diffs/{file_id}", "get_task_diff_file"),
    ("GET", "/api/tasks/{task_id}/events/{event_id}", "get_task_event"),
    ("GET", "/api/tasks/{task_id}/notifications", "list_task_notifications"),
    ("POST", "/api/tasks/{task_id}/nudge", "nudge_task"),
    ("GET", "/api/tasks/{task_id}/plans", "list_task_plans"),
    ("POST", "/api/tasks/{task_id}/plans", "generate_task_plan"),
    ("GET", "/api/tasks/{task_id}/plans/{plan_id}", "get_task_plan"),
    ("POST", "/api/tasks/{task_id}/plans/{plan_id}/approve", "approve_task_plan"),
    ("POST", "/api/tasks/{task_id}/plans/{plan_id}/reject", "reject_task_plan"),
    ("POST", "/api/tasks/{task_id}/plans/{plan_id}/rewrite", "rewrite_task_plan"),
    ("GET", "/api/tasks/{task_id}/pr-description", "get_task_pr_description"),
    ("POST", "/api/tasks/{task_id}/pr-description/generate", "generate_task_pr_description"),
    ("GET", "/api/tasks/{task_id}/pr-description/versions/{version_id}", "get_task_pr_description_version"),
    ("POST", "/api/tasks/{task_id}/pull-request", "create_task_pull_request_handler"),
    ("GET", "/api/tasks/{task_id}/replay", "get_task_replay"),
    ("GET", "/api/tasks/{task_id}/replay/summary", "get_task_replay_summary"),
    ("POST", "/api/tasks/{task_id}/retry", "retry_task"),
    ("GET", "/api/tasks/{task_id}/similar", "list_similar_tasks"),
    ("GET", "/api/tasks/{task_id}/tests", "list_task_test_runs"),
    ("POST", "/api/tasks/{task_id}/tests/run", "run_task_tests"),
    ("GET", "/api/tasks/{task_id}/tests/{test_id}", "get_task_test_run"),
    ("GET", "/api/tasks/{task_id}/timeline", "get_task_timeline"),
    ("GET", "/api/team/requests", "list_team_requests"),
    ("POST", "/api/team/requests", "create_team_request"),
    ("GET", "/api/team/requests/{request_id}", "get_team_request"),
    ("PATCH", "/api/team/requests/{request_id}", "update_team_request"),
    ("POST", "/api/team/requests/{request_id}/create-task", "create_task_from_team_request"),
    ("GET", "/api/team/requests/{request_id}/events", "list_team_request_events"),
    ("POST", "/api/team/requests/{request_id}/sync-gitee", "sync_team_request_to_gitee"),
    ("GET", "/api/users", "list_users"),
    ("GET", "/api/users/{user_id}", "get_user"),
]

# 有效路由表冻结（2026-10-16 step 1 迁移后即刻导出；= 83 条核心 + 34 条企业域；
# OPT-14 受控新增 GET /api/audit-events/export 后 = 83 + 35）
FROZEN_EFFECTIVE_OPERATIONS: list[tuple[str, str, str]] = [
    ("DELETE", "/api/tasks/{task_id}/comments/{comment_id}", "delete_task_comment_api_tasks__task_id__comments__comment_id__delete"),
    ("GET", "/api/approvals/candidates", "list_approval_candidates_api_approvals_candidates_get"),
    ("GET", "/api/approvals/pending", "list_pending_approvals_api_approvals_pending_get"),
    ("GET", "/api/audit-events", "list_audit_events_api_audit_events_get"),
    ("GET", "/api/audit-events/export", "export_audit_events_api_audit_events_export_get"),
    ("GET", "/api/auth/me", "me_api_auth_me_get"),
    ("GET", "/api/auth/password/requests", "list_password_reset_requests_api_auth_password_requests_get"),
    ("GET", "/api/dashboard/automation-summary", "get_dashboard_automation_summary_api_dashboard_automation_summary_get"),
    ("GET", "/api/enterprise/approvals", "list_approvals_api_enterprise_approvals_get"),
    ("GET", "/api/enterprise/approvals/{approval_id}", "get_approval_api_enterprise_approvals__approval_id__get"),
    ("GET", "/api/enterprise/governance/operations", "governance_operations_api_enterprise_governance_operations_get"),
    ("GET", "/api/enterprise/members", "list_members_api_enterprise_members_get"),
    ("GET", "/api/enterprise/ops/metrics", "ops_metrics_api_enterprise_ops_metrics_get"),
    ("GET", "/api/enterprise/ops/status", "ops_status_api_enterprise_ops_status_get"),
    ("GET", "/api/enterprise/organizations", "list_organizations_api_enterprise_organizations_get"),
    ("GET", "/api/enterprise/rag/evaluations", "rag_evaluation_list_api_enterprise_rag_evaluations_get"),
    ("GET", "/api/enterprise/rag/evaluations/{run_id}", "rag_evaluation_detail_api_enterprise_rag_evaluations__run_id__get"),
    ("GET", "/api/enterprise/rag/indexes", "rag_indexes_api_enterprise_rag_indexes_get"),
    ("GET", "/api/enterprise/rag/indexes/{index_id}", "rag_index_detail_api_enterprise_rag_indexes__index_id__get"),
    ("GET", "/api/enterprise/rag/reports", "rag_reports_api_enterprise_rag_reports_get"),
    ("GET", "/api/enterprise/rag/reports/{name}", "rag_report_detail_api_enterprise_rag_reports__name__get"),
    ("GET", "/api/enterprise/runtime/tasks", "list_tasks_api_enterprise_runtime_tasks_get"),
    ("GET", "/api/enterprise/runtime/tasks/{task_id}", "get_task_api_enterprise_runtime_tasks__task_id__get"),
    ("GET", "/api/enterprise/runtime/traces", "list_traces_api_enterprise_runtime_traces_get"),
    ("GET", "/api/enterprise/tenant-context", "get_tenant_context_api_enterprise_tenant_context_get"),
    ("GET", "/api/events/stream", "stream_realtime_events_api_events_stream_get"),
    ("GET", "/api/health", "health_api_health_get"),
    ("GET", "/api/notifications", "list_notifications_api_notifications_get"),
    ("GET", "/api/notifications/unread-count", "notifications_unread_count_api_notifications_unread_count_get"),
    ("GET", "/api/projects", "list_projects_api_projects_get"),
    ("GET", "/api/projects/{project_id}", "get_project_api_projects__project_id__get"),
    ("GET", "/api/repositories", "list_repositories_api_repositories_get"),
    ("GET", "/api/repositories/{repository_id}/indexes", "list_repository_indexes_api_repositories__repository_id__indexes_get"),
    ("GET", "/api/repositories/{repository_id}/indexes/{index_id}", "get_repository_index_api_repositories__repository_id__indexes__index_id__get"),
    ("GET", "/api/tasks", "list_tasks_api_tasks_get"),
    ("GET", "/api/tasks/{task_id}", "get_task_api_tasks__task_id__get"),
    ("GET", "/api/tasks/{task_id}/analysis", "get_task_analysis_api_tasks__task_id__analysis_get"),
    ("GET", "/api/tasks/{task_id}/analysis/questions", "get_task_analysis_questions_api_tasks__task_id__analysis_questions_get"),
    ("GET", "/api/tasks/{task_id}/comments", "list_task_comments_api_tasks__task_id__comments_get"),
    ("GET", "/api/tasks/{task_id}/completed-summary", "get_task_completed_summary_api_tasks__task_id__completed_summary_get"),
    ("GET", "/api/tasks/{task_id}/completed-summary/index", "get_task_completed_summary_index_api_tasks__task_id__completed_summary_index_get"),
    ("GET", "/api/tasks/{task_id}/contexts", "get_task_contexts_api_tasks__task_id__contexts_get"),
    ("GET", "/api/tasks/{task_id}/diffs", "list_task_diffs_api_tasks__task_id__diffs_get"),
    ("GET", "/api/tasks/{task_id}/diffs/{file_id}", "get_task_diff_file_api_tasks__task_id__diffs__file_id__get"),
    ("GET", "/api/tasks/{task_id}/events/{event_id}", "get_task_event_api_tasks__task_id__events__event_id__get"),
    ("GET", "/api/tasks/{task_id}/notifications", "list_task_notifications_api_tasks__task_id__notifications_get"),
    ("GET", "/api/tasks/{task_id}/plans", "list_task_plans_api_tasks__task_id__plans_get"),
    ("GET", "/api/tasks/{task_id}/plans/{plan_id}", "get_task_plan_api_tasks__task_id__plans__plan_id__get"),
    ("GET", "/api/tasks/{task_id}/pr-description", "get_task_pr_description_api_tasks__task_id__pr_description_get"),
    ("GET", "/api/tasks/{task_id}/pr-description/versions/{version_id}", "get_task_pr_description_version_api_tasks__task_id__pr_description_versions__version_id__get"),
    ("GET", "/api/tasks/{task_id}/replay", "get_task_replay_api_tasks__task_id__replay_get"),
    ("GET", "/api/tasks/{task_id}/replay/summary", "get_task_replay_summary_api_tasks__task_id__replay_summary_get"),
    ("GET", "/api/tasks/{task_id}/similar", "list_similar_tasks_api_tasks__task_id__similar_get"),
    ("GET", "/api/tasks/{task_id}/tests", "list_task_test_runs_api_tasks__task_id__tests_get"),
    ("GET", "/api/tasks/{task_id}/tests/{test_id}", "get_task_test_run_api_tasks__task_id__tests__test_id__get"),
    ("GET", "/api/tasks/{task_id}/timeline", "get_task_timeline_api_tasks__task_id__timeline_get"),
    ("GET", "/api/team/requests", "list_team_requests_api_team_requests_get"),
    ("GET", "/api/team/requests/{request_id}", "get_team_request_api_team_requests__request_id__get"),
    ("GET", "/api/team/requests/{request_id}/events", "list_team_request_events_api_team_requests__request_id__events_get"),
    ("GET", "/api/users", "list_users_api_users_get"),
    ("GET", "/api/users/{user_id}", "get_user_api_users__user_id__get"),
    ("PATCH", "/api/enterprise/members/{membership_id}", "update_member_api_enterprise_members__membership_id__patch"),
    ("PATCH", "/api/team/requests/{request_id}", "update_team_request_api_team_requests__request_id__patch"),
    ("POST", "/api/auth/login", "login_api_auth_login_post"),
    ("POST", "/api/auth/logout", "logout_api_auth_logout_post"),
    ("POST", "/api/auth/password/emergency-reset", "emergency_reset_password_api_auth_password_emergency_reset_post"),
    ("POST", "/api/auth/password/forgot", "forgot_password_api_auth_password_forgot_post"),
    ("POST", "/api/auth/password/requests/{request_id}/reset", "reset_requested_password_api_auth_password_requests__request_id__reset_post"),
    ("POST", "/api/enterprise/approvals", "create_approval_api_enterprise_approvals_post"),
    ("POST", "/api/enterprise/approvals/{approval_id}/approve", "approve_approval_api_enterprise_approvals__approval_id__approve_post"),
    ("POST", "/api/enterprise/approvals/{approval_id}/cancel", "cancel_approval_api_enterprise_approvals__approval_id__cancel_post"),
    ("POST", "/api/enterprise/approvals/{approval_id}/execute", "execute_approval_api_enterprise_approvals__approval_id__execute_post"),
    ("POST", "/api/enterprise/approvals/{approval_id}/reject", "reject_approval_api_enterprise_approvals__approval_id__reject_post"),
    ("POST", "/api/enterprise/governance/decide", "governance_decision_api_enterprise_governance_decide_post"),
    ("POST", "/api/enterprise/members", "create_member_api_enterprise_members_post"),
    ("POST", "/api/enterprise/organizations", "create_organization_api_enterprise_organizations_post"),
    ("POST", "/api/enterprise/rag/evaluations", "rag_evaluation_trigger_api_enterprise_rag_evaluations_post"),
    ("POST", "/api/enterprise/rag/search", "rag_search_api_enterprise_rag_search_post"),
    ("POST", "/api/enterprise/runtime/tasks", "start_task_api_enterprise_runtime_tasks_post"),
    ("POST", "/api/enterprise/runtime/tasks/{task_id}/retry", "retry_task_api_enterprise_runtime_tasks__task_id__retry_post"),
    ("POST", "/api/enterprise/runtime/tasks/{task_id}/takeover", "request_takeover_api_enterprise_runtime_tasks__task_id__takeover_post"),
    ("POST", "/api/enterprise/runtime/tasks/{task_id}/takeover/complete", "complete_takeover_api_enterprise_runtime_tasks__task_id__takeover_complete_post"),
    ("POST", "/api/enterprise/tenant-context/switch", "switch_tenant_api_enterprise_tenant_context_switch_post"),
    ("POST", "/api/enterprise/tenants", "create_tenant_api_enterprise_tenants_post"),
    ("POST", "/api/history/search", "search_historical_tasks_api_history_search_post"),
    ("POST", "/api/notifications/{notification_id}/read", "mark_notification_read_api_notifications__notification_id__read_post"),
    ("POST", "/api/projects", "create_project_api_projects_post"),
    ("POST", "/api/repositories", "create_repository_api_repositories_post"),
    ("POST", "/api/repositories/inspect", "inspect_repository_api_repositories_inspect_post"),
    ("POST", "/api/repositories/{repository_id}/index", "trigger_repository_index_api_repositories__repository_id__index_post"),
    ("POST", "/api/repositories/{repository_id}/index/rebuild", "rebuild_repository_index_api_repositories__repository_id__index_rebuild_post"),
    ("POST", "/api/repositories/{repository_id}/indexes/{index_id}/retry", "retry_repository_index_api_repositories__repository_id__indexes__index_id__retry_post"),
    ("POST", "/api/tasks", "create_task_api_tasks_post"),
    ("POST", "/api/tasks/{task_id}/analysis/generate", "generate_task_analysis_api_tasks__task_id__analysis_generate_post"),
    ("POST", "/api/tasks/{task_id}/analysis/questions/{question_id}/answer", "answer_task_analysis_question_api_tasks__task_id__analysis_questions__question_id__answer_post"),
    ("POST", "/api/tasks/{task_id}/approval-owner", "assign_task_approval_owner_api_tasks__task_id__approval_owner_post"),
    ("POST", "/api/tasks/{task_id}/cancel", "cancel_task_api_tasks__task_id__cancel_post"),
    ("POST", "/api/tasks/{task_id}/comments", "create_task_comment_api_tasks__task_id__comments_post"),
    ("POST", "/api/tasks/{task_id}/completed-summary", "generate_task_completed_summary_api_tasks__task_id__completed_summary_post"),
    ("POST", "/api/tasks/{task_id}/completed-summary/reindex", "reindex_task_completed_summary_api_tasks__task_id__completed_summary_reindex_post"),
    ("POST", "/api/tasks/{task_id}/contexts/manual", "add_manual_task_contexts_api_tasks__task_id__contexts_manual_post"),
    ("POST", "/api/tasks/{task_id}/contexts/resume", "resume_task_contexts_api_tasks__task_id__contexts_resume_post"),
    ("POST", "/api/tasks/{task_id}/diffs/generate", "generate_task_diff_api_tasks__task_id__diffs_generate_post"),
    ("POST", "/api/tasks/{task_id}/nudge", "nudge_task_api_tasks__task_id__nudge_post"),
    ("POST", "/api/tasks/{task_id}/plans", "generate_task_plan_api_tasks__task_id__plans_post"),
    ("POST", "/api/tasks/{task_id}/plans/{plan_id}/approve", "approve_task_plan_api_tasks__task_id__plans__plan_id__approve_post"),
    ("POST", "/api/tasks/{task_id}/plans/{plan_id}/reject", "reject_task_plan_api_tasks__task_id__plans__plan_id__reject_post"),
    ("POST", "/api/tasks/{task_id}/plans/{plan_id}/rewrite", "rewrite_task_plan_api_tasks__task_id__plans__plan_id__rewrite_post"),
    ("POST", "/api/tasks/{task_id}/pr-description/generate", "generate_task_pr_description_api_tasks__task_id__pr_description_generate_post"),
    ("POST", "/api/tasks/{task_id}/pull-request", "create_task_pull_request_handler_api_tasks__task_id__pull_request_post"),
    ("POST", "/api/tasks/{task_id}/retry", "retry_task_api_tasks__task_id__retry_post"),
    ("POST", "/api/tasks/{task_id}/tests/run", "run_task_tests_api_tasks__task_id__tests_run_post"),
    ("POST", "/api/team/requests", "create_team_request_api_team_requests_post"),
    ("POST", "/api/team/requests/{request_id}/create-task", "create_task_from_team_request_api_team_requests__request_id__create_task_post"),
    ("POST", "/api/team/requests/{request_id}/sync-gitee", "sync_team_request_to_gitee_api_team_requests__request_id__sync_gitee_post"),
    ("PUT", "/api/projects/{project_id}", "update_project_api_projects__project_id__put"),
    ("PUT", "/api/repositories/{repository_id}", "update_repository_api_repositories__repository_id__put"),
    ("PUT", "/api/tasks/{task_id}/comments/{comment_id}", "update_task_comment_api_tasks__task_id__comments__comment_id__put"),
]

# 迁移目标：领域模块 -> 处理器名（app.main 中不再保留）
MIGRATED_HANDLERS: dict[str, list[str]] = {
    "app.api.system": ["health"],
    "app.api.auth": [
        "login", "logout", "me", "forgot_password", "emergency_reset_password",
        "list_password_reset_requests", "reset_requested_password",
    ],
    "app.api.users": ["list_users", "get_user"],
    "app.api.projects": ["list_projects", "create_project", "get_project", "update_project"],
    "app.api.comments": [
        "list_task_comments", "create_task_comment", "update_task_comment", "delete_task_comment",
    ],
    "app.api.notifications": [
        "list_notifications", "notifications_unread_count", "stream_realtime_events",
        "list_task_notifications", "mark_notification_read", "nudge_task",
    ],
    "app.api.repositories": [
        "list_repositories", "inspect_repository", "create_repository", "update_repository",
    ],
    "app.api.repository_indexes": [
        "trigger_repository_index", "rebuild_repository_index", "list_repository_indexes",
        "get_repository_index", "retry_repository_index",
    ],
    "app.api.dashboard": ["get_dashboard_automation_summary"],
    "app.api.audit": ["list_audit_events", "list_pending_approvals", "list_approval_candidates"],
    "app.api.history": [
        "search_historical_tasks", "generate_task_completed_summary", "get_task_completed_summary",
        "get_task_completed_summary_index", "reindex_task_completed_summary",
    ],
    "app.api.team_requests": [
        "create_team_request", "list_team_requests", "get_team_request", "update_team_request",
        "sync_team_request_to_gitee", "list_team_request_events", "create_task_from_team_request",
    ],
}


def _effective_operations() -> list[tuple[str, str, str]]:
    """运行时有效路由表：以 OpenAPI 投影为准（含惰性包装 router 内的全部操作）。"""
    schema = app.openapi()
    rows: list[tuple[str, str, str]] = []
    for path, operations in schema.get("paths", {}).items():
        if not path.startswith("/api"):
            continue
        for method, operation in operations.items():
            if method.upper() in ("HEAD", "OPTIONS"):
                continue
            rows.append((method.upper(), path, operation.get("operationId", "")))
    return sorted(rows)


def test_frozen_effective_table_matches():
    """迁移不得增删/改名任何对外路由操作（118 条逐条一致，含企业域与 OPT-14 受控新增）。"""
    rows = _effective_operations()
    assert len(rows) == 118
    assert rows == sorted(FROZEN_EFFECTIVE_OPERATIONS)


def test_frozen_core_routes_all_still_served():
    """拆分前 83 条核心路由全部仍被服务且处理器名未变（operationId 公式反推比对）。"""
    effective = {(method, path): oid for method, path, oid in _effective_operations()}
    problems: list[str] = []
    for method, path, name in FROZEN_ROUTES:
        oid = effective.get((method, path))
        if oid is None:
            problems.append(f"路由丢失：{method} {path}")
            continue
        expected = re.sub(r"\W", "_", f"{name}{path}") + f"_{method.lower()}"
        if oid != expected:
            problems.append(f"处理器改名或路由变化：{method} {path} -> {oid}（期望 {expected}）")
    assert problems == [], problems


def test_migrated_handlers_live_in_domain_modules():
    """领域处理器定义在 app.api.<domain>，app.main 不再保留同名残留。"""
    import app.main as main

    missing: list[str] = []
    leftovers: list[str] = []
    for mod_name, handlers in MIGRATED_HANDLERS.items():
        try:
            module = importlib.import_module(mod_name)
        except ImportError:
            missing.append(f"{mod_name}（模块未创建）")
            continue
        for handler in handlers:
            if not callable(getattr(module, handler, None)):
                missing.append(f"{mod_name}.{handler}")
            if hasattr(main, handler):
                leftovers.append(handler)
    assert missing == [], f"未在领域模块找到处理器: {missing}"
    assert leftovers == [], f"app.main 仍残留处理器: {leftovers}"


def test_health_smoke_after_split(client):
    """拆分后健康检查可用（冒烟）。"""
    response = client.get("/api/health")
    assert response.status_code == 200
