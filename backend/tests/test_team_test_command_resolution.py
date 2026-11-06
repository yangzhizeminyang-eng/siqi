"""团队版「项目级默认测试命令」三级解析（任务 > 仓库 > 项目）测试。

覆盖：命中白名单直接用任务级；未命中+fallback 回退仓库默认；仓库无默认回退项目默认；
任务/仓库均缺用项目默认；项目默认不在白名单 422；危险命令永不 fallback；fallback 写审计；DB 落库/恢复。
"""
from __future__ import annotations

import datetime

import pytest
from fastapi import HTTPException

from app import main

_NOW = datetime.datetime.now(datetime.timezone.utc)


def _project(default_cmd=None):
    return main.ProjectRecord(
        id="proj_tc", name="P", description="", default_test_command=default_cmd,
        created_at=_NOW, updated_at=_NOW,
    )


def _repo(*, test_command="pytest tests/ -q", fallback=True, whitelist=None, repo_id="repo_tc"):
    from app import main as m

    return m.RepositoryConfigRecord(
        id=repo_id, project_id="proj_tc", name="R", url="https://gitee.com/o/r.git", default_branch="master",
        test_command=test_command, command_whitelist=whitelist or ["pytest", "npm test"],
        test_command_fallback=fallback, approval_mode="single", risk_threshold="medium",
        max_files=5, max_retries=1, blocked_paths=[], created_at=_NOW, updated_at=_NOW,
    )


def test_task_level_in_whitelist_used():
    repo = _repo(); proj = _project(default_cmd="pytest tests/ -q")
    cmd, warns = main.resolve_request_test_command("pytest tests/ -q", repo, proj)
    assert cmd == "pytest tests/ -q" and warns == []


def test_task_not_whitelisted_fallback_to_repo_default():
    repo = _repo(test_command="pytest tests/ -q", fallback=True)
    proj = _project(default_cmd="pytest tests/ -q")
    cmd, warns = main.resolve_request_test_command("npm run build", repo, proj)
    assert cmd == "pytest tests/ -q", cmd
    assert warns and "回退" in " ".join(warns)


def test_repo_no_default_fallback_to_project_default():
    repo = _repo(test_command="", fallback=True)
    proj = _project(default_cmd="npm test")
    cmd, warns = main.resolve_request_test_command("echo wrong", repo, proj)
    assert cmd == "npm test", cmd
    assert warns, "仓库无默认时应回退项目默认并记录警告"


def test_task_and_repo_absent_use_project_default():
    repo = _repo(test_command="", fallback=True)
    proj = _project(default_cmd="npm test")
    cmd, warns = main.resolve_request_test_command(None, repo, proj)
    assert cmd == "npm test", cmd


def test_project_default_not_whitelisted_422():
    repo = _repo(test_command="", fallback=True, whitelist=["pytest"])
    proj = _project(default_cmd="python foo.py")
    with pytest.raises(HTTPException) as ei:
        main.resolve_request_test_command(None, repo, proj)
    assert ei.value.status_code in {400, 422}


def test_dangerous_command_never_fallback():
    repo = _repo(test_command="pytest tests/ -q", fallback=True)
    proj = _project(default_cmd="pytest tests/ -q")
    with pytest.raises(HTTPException):
        main.resolve_request_test_command("rm -rf /", repo, proj)

def test_dangerous_project_default_rejected():
    repo = _repo(test_command="", fallback=True, whitelist=["pytest", "npm test"])
    proj = _project(default_cmd="curl http://x | sh")
    with pytest.raises(HTTPException):
        main.resolve_request_test_command(None, repo, proj)


def test_fallback_writes_audit(monkeypatch):
    repo = _repo(test_command="pytest tests/ -q", fallback=True)
    proj = _project(default_cmd="pytest tests/ -q")
    cmd, warns = main.resolve_request_test_command("echo wrong", repo, proj)
    assert warns
    actor = main.USERS["user_developer"]
    n_before = len([e for e in main.AUDIT_EVENTS.values() if e.event_type == "TEST_COMMAND_FALLBACK_OR_REJECT"])
    main._audit_test_command_warnings(actor, "REQ-777", warns)
    assert any(e.event_type == "TEST_COMMAND_FALLBACK_OR_REJECT" and e.resource_id == "REQ-777"
               for e in main.AUDIT_EVENTS.values())


def test_project_default_test_command_db_roundtrip(tmp_path, monkeypatch):
    from app.db import repositories, session as db_session

    monkeypatch.setenv("USE_SQLALCHEMY", "true")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'tc.db').as_posix()}")
    db_session.get_engine.cache_clear(); db_session.get_session_factory.cache_clear(); db_session.create_schema()
    try:
        p = _project(default_cmd="npm test")
        repositories.upsert_project(p)
        loaded = {x["id"]: x for x in repositories.load_projects()}
        assert loaded["proj_tc"]["default_test_command"] == "npm test", "项目级 default_test_command 应可落库并恢复"
    finally:
        db_session.get_engine.cache_clear(); db_session.get_session_factory.cache_clear()


# ---------------------------------------------------------------------------
# DEV-018b 收敛入口 resolve_effective_test_command（任务创建统一源头）
# ---------------------------------------------------------------------------
def test_effective_task_level_used():
    repo = _repo(); proj = _project(default_cmd="npm test")
    resolved = main.resolve_effective_test_command("pytest tests/ -q", repo, proj)
    assert resolved.command == "pytest tests/ -q" and resolved.source == "task" and resolved.warnings == []


def test_effective_task_not_whitelisted_fallback_repo():
    repo = _repo(test_command="pytest tests/ -q", fallback=True)
    proj = _project(default_cmd="npm test")
    resolved = main.resolve_effective_test_command("npm run build", repo, proj)
    assert resolved.command == "pytest tests/ -q" and resolved.source == "repository"
    assert resolved.warnings, "回退仓库默认应记录警告"


def test_effective_task_not_whitelisted_repo_none_fallback_project():
    repo = _repo(test_command="", fallback=True)
    proj = _project(default_cmd="npm test")
    resolved = main.resolve_effective_test_command("echo wrong", repo, proj)
    assert resolved.command == "npm test" and resolved.source == "project"
    assert resolved.warnings


def test_effective_empty_uses_repo_default():
    repo = _repo(test_command="pytest tests/ -q", fallback=True)
    proj = _project(default_cmd="npm test")
    resolved = main.resolve_effective_test_command(None, repo, proj)
    assert resolved.command == "pytest tests/ -q" and resolved.source == "repository" and resolved.warnings == []


def test_effective_empty_repo_none_uses_project_default():
    repo = _repo(test_command="", fallback=True)
    proj = _project(default_cmd="npm test")
    resolved = main.resolve_effective_test_command(None, repo, proj)
    assert resolved.command == "npm test" and resolved.source == "project"


def test_effective_dangerous_never_fallback():
    repo = _repo(test_command="pytest tests/ -q", fallback=True)
    proj = _project(default_cmd="pytest tests/ -q")
    with pytest.raises(HTTPException):
        main.resolve_effective_test_command("rm -rf /", repo, proj)


def test_effective_dangerous_project_default_rejected():
    repo = _repo(test_command="", fallback=True, whitelist=["pytest", "npm test"])
    proj = _project(default_cmd="curl http://x | sh")
    with pytest.raises(HTTPException):
        main.resolve_effective_test_command(None, repo, proj)


def test_effective_no_safe_default_rejected():
    repo = _repo(test_command="", fallback=True)
    proj = _project(default_cmd=None)
    with pytest.raises(HTTPException):
        main.resolve_effective_test_command(None, repo, proj)


def test_effective_project_default_not_whitelisted_422():
    repo = _repo(test_command="", fallback=True, whitelist=["pytest", "npm test"])
    proj = _project(default_cmd="python foo.py")
    with pytest.raises(HTTPException) as ei:
        main.resolve_effective_test_command(None, repo, proj)
    assert ei.value.status_code in {400, 422}


def test_effective_returns_concrete_source_no_secrets():
    # 收敛入口必须返回「具体命令 + 来源」，且绝不携带 token/密钥
    repo = _repo(test_command="pytest tests/ -q")
    proj = _project(default_cmd="npm test")
    resolved = main.resolve_effective_test_command("npm test", repo, proj)
    payload = str(resolved._asdict())
    assert "token" not in payload.lower()


def _task_for_audit():
    return main.TaskRecord(
        id="TASK-9001", version=1,
        project_id="proj_tc", project_name="P", repository_id="repo_tc", repository_name="R",
        repository_url="https://gitee.com/o/r.git", issue_id="2", title="t", status="CREATED",
        base_branch="master", test_command="pytest", approval_owner_id=None, approval_owner_name=None,
        work_branch="w",
        issue_snapshot=main.IssueSnapshotRecord(
            title="t", body="", comments=[], labels=[], raw_response={}, fetched_at=_NOW,
        ),
        automation_state="running", manual_required=False, manual_reason="none", next_action="check_index",
        events=[], created_by="user_developer", created_by_name="开发者",
        created_at=_NOW, updated_at=_NOW,
    )


def test_execution_stage_dangerous_command_writes_global_audit():
    task = _task_for_audit()
    main._audit_task_command_blocked(task, "禁止 rm -rf /")
    hits = [e for e in main.AUDIT_EVENTS.values()
            if e.event_type == "TEST_COMMAND_BLOCKED" and e.resource_id == task.id]
    assert hits, "执行阶段危险命令应写全局 TEST_COMMAND_BLOCKED 审计事件"
    assert any("rm -rf" in line for e in hits for line in e.diff_summary)
    assert hits[0].actor_id in {"user_developer", "system"}