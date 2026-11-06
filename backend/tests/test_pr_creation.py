"""真实 Gitee PR 创建测试：参数正确、token 脱敏、门禁拒绝、成功落库/审计/回放、幂等、真实 E2E 默认 skip。

不触网：git 写/提交/推送与 Gitee PR POST 均被桩掉；token 经 REPOSITORY_TOKENS 注入并断言不落日志/审计/响应。
"""
from __future__ import annotations

import datetime
import os
from datetime import timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import main

pytestmark = pytest.mark.usefixtures("_client_fixture")


# ---- 真实 Gitee PR E2E（默认 skip，配齐才跑）----
_PR_E2E = pytest.mark.skipif(
    os.getenv("GITEE_PR_E2E") != "true" or not (
        os.getenv("GITEE_TOKEN") and os.getenv("GITEE_OWNER") and os.getenv("GITEE_REPO_SCRATCH")
    ),
    reason="GITEE_PR_E2E 未开启或 GITEE_TOKEN/OWNER/REPO_SCRATCH 未配齐",
)


@pytest.fixture()
def _client_fixture():
    # 清理历史仓库（避免 409）& 注入 demo token（非真实，用于脱敏断言）
    main.REPOSITORY_TOKENS["repo_payment_service"] = "TEST-PR-TOKEN-SECRET"
    try:
        yield
    finally:
        pass


def authed() -> TestClient:
    c = TestClient(main.app)
    r = c.post("/api/auth/login", json={"account": "admin@company.com", "password": "Admin123!"})
    assert r.status_code == 200, r.text
    c.cookies = r.cookies
    return c


def _seed(repository, *, plan_approved=True, real_diff=True, with_pr=True, with_test=True):
    task_id = f"TASK-PR-{datetime.datetime.now().microsecond % 100000}"
    now = datetime.datetime.now(timezone.utc)
    task = main.TaskRecord(
        id=task_id, version=1, project_id=repository.project_id,
        project_name=main.PROJECTS[repository.project_id].name,
        repository_id=repository.id, repository_name=repository.name, repository_url=repository.url,
        issue_id="IDEVPR1", title="回调幂等修复（真实 PR）", status="TESTED",
        risk_level="high", risk_source="plan_approval", base_branch=repository.default_branch,
        test_command=repository.test_command, approval_owner_id=None, approval_owner_name=None,
        work_branch="agent/main/issue-idevpr1-v1",
        issue_snapshot=main.IssueSnapshotRecord(
            title="回调幂等修复", body="修复重复入账", comments=[], labels=[], raw_response={},
            fetched_at=now - timedelta(minutes=6),
        ),
        automation_state="completed", manual_required=False, manual_reason="none",
        next_action="watch_test_result", events=[], created_by="user_admin", created_by_name="管理员",
        created_at=now - timedelta(minutes=5), updated_at=now,
        base_commit_sha="c" * 40,
    )
    plan = main.ModificationPlan(
        id=f"PLAN-{task_id}-v1", task_id=task.id, version=1, goal="修复回调幂等",
        file_scope=[main.PlanFileScope(file_path="src/handler.py", change_type="modify", reason="修复")],
        steps=[main.PlanStep(order=1, title="校验", description="校验", target_files=["src/handler.py"])],
        test_suggestions=["pytest -q"], assumptions=[], rollback_plan=[],
        risk_level="medium", risk_reason="", generated_from_analysis_revision=1, generated_from_context_ids=[],
        provider="deepseek", status="APPROVED" if plan_approved else "WAITING_APPROVAL",
        risk_adjust_reason=None, approved_by_id=None, approved_by_name=None, approved_at=None,
        created_by="user_admin", created_by_name="管理员", created_at=now - timedelta(minutes=4),
    )
    artifact = main.DiffArtifact(
        id=f"DIFF-{task_id}-v1", task_id=task.id, plan_id=plan.id, plan_version=1,
        execution_step="步骤 1", generation_status="SUCCEEDED",
        execution_mode="REAL" if real_diff else "DEMO_FALLBACK",
        is_demo_fallback=not real_diff, fallback_reason=None if real_diff else "demo",
        real_failure_reason=None, real_change_source="e2e_scratch_real_change" if real_diff else None,
        generated_at=now - timedelta(minutes=3),
        files=[main.DiffFileRecord(
            id=f"DIFF-{task_id}-v1-1", file_path="src/handler.py", change_type="modify",
            patch="--- a/src/handler.py\n+++ b/src/handler.py", added_lines=2, deleted_lines=1,
            before=["a"], after=["a", "b", "c"],
        )], test_summary="真实", pr_description_draft=None,
    )
    test_run = main.TestRunRecord(
        id=f"TEST-{task_id}-1", task_id=task.id, diff_artifact_id=artifact.id, plan_id=plan.id,
        plan_version=1, command=task.test_command, exit_code=0, stdout="通过", stderr="",
        status="passed", duration=1.0, attempt=1,
        started_at=now - timedelta(minutes=2), finished_at=now - timedelta(minutes=1),
        failure_reason=None, blocked_reason=None, runner_mode="docker",
    )
    task.plans.append(plan); task.diffs.append(artifact)
    if with_test:
        task.test_runs.append(test_run)
    if with_pr:
        task.pr_descriptions.append(main.PrDescriptionRecord(
            id=f"PRD-{task_id}-1", task_id=task.id, plan_id=plan.id, plan_version=1,
            diff_artifact_id=artifact.id, test_run_id=test_run.id, version=1,
            background="背景", change_summary="变更", impact_scope="影响", test_result="测试通过",
            risk_notes="风险", rollback_notes="回滚", generation_status="GENERATED",
            failure_reason=None, generated_at=now - timedelta(minutes=1),
            generated_by="user_admin", generated_by_name="管理员",
        ))
    main.TASKS[task.id] = task
    return task


def _stub_git(monkeypatch, tmp_path):
    import pathlib

    from app.services import repo_mirror

    wt = pathlib.Path(tmp_path) / "wt"
    wt.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(repo_mirror, "ensure_mirror", lambda *a, **k: pathlib.Path(tmp_path) / "mirror")
    monkeypatch.setattr(repo_mirror, "checkout_commit", lambda *a, **k: wt)
    monkeypatch.setattr(repo_mirror, "apply_diff_files", lambda *a, **k: None)
    monkeypatch.setattr(repo_mirror, "commit_changes", lambda *a, **k: None)
    monkeypatch.setattr(repo_mirror, "push_head_branch", lambda *a, **k: None)


def _stub_pr_post(monkeypatch):
    from app.services import gitee_pr_service

    calls = {"n": 0, "path": None, "data": None, "token": None}

    def fake_post(path, data, token=None):
        calls["n"] += 1
        calls["path"] = path
        calls["data"] = dict(data)
        calls["token"] = token
        return 201, {"id": 9001, "number": "PR-001", "html_url": "https://gitee.com/o/r/pulls/PR-001", "state": "OPEN"}

    monkeypatch.setattr(gitee_pr_service, "_post", fake_post)
    return calls


def test_create_pr_success_payload_and_persist(tmp_path, monkeypatch):
    repo = main.REPOSITORIES["repo_payment_service"]
    task = _seed(repo)
    calls = _stub_pr_post(monkeypatch)
    _stub_git(monkeypatch, tmp_path)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ALLOW_SCRATCH_REAL_CHANGE", "true")

    r = authed().post(f"/api/tasks/{task.id}/pull-request")
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert calls["n"] == 1, "应只调一次 PR 创建"
    assert calls["path"].endswith("/pulls") and "/payment-service/" in calls["path"], calls["path"]
    assert calls["data"]["head"] == task.work_branch
    assert calls["data"]["base"] == task.base_branch
    assert calls["data"]["title"] == task.title
    assert calls["data"]["body"], "PR body 应来自 PR 描述"
    assert data["pr_number"] == "PR-001"
    assert data["pr_url"] == "https://gitee.com/o/r/pulls/PR-001"
    assert data["pr_status"] == "OPEN"
    nodes = [s.get("node") for s in data["execution_steps"]]
    assert "create_pr" in nodes, nodes
    assert any(e.get("event_type") == "GITEE_PR_CREATED" for e in data["events"]), data["events"]


def test_create_pr_token_not_leaked(tmp_path, monkeypatch):
    repo = main.REPOSITORIES["repo_payment_service"]
    task = _seed(repo)
    calls = _stub_pr_post(monkeypatch)
    _stub_git(monkeypatch, tmp_path)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ALLOW_SCRATCH_REAL_CHANGE", "true")

    r = authed().post(f"/api/tasks/{task.id}/pull-request")
    assert r.status_code == 200
    body_text = r.text
    assert calls["token"] == "TEST-PR-TOKEN-SECRET", "token 应传给 Gitee PR 层（用于真实推送/PR）"
    assert "TEST-PR-TOKEN-SECRET" not in body_text, "token 不得出现在响应"
    assert "TEST-PR-TOKEN-SECRET" not in str(r.json()["data"]["execution_steps"])
    assert "TEST-PR-TOKEN-SECRET" not in str([e for e in task.events if e.event_type == "GITEE_PR_CREATED"])


def test_create_pr_rejections(tmp_path, monkeypatch):
    repo = main.REPOSITORIES["repo_payment_service"]
    _stub_pr_post(monkeypatch)
    _stub_git(monkeypatch, tmp_path)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ALLOW_SCRATCH_REAL_CHANGE", "true")
    c = authed()
    # 1) 未审批计划
    t1 = _seed(repo, plan_approved=False)
    assert c.post(f"/api/tasks/{t1.id}/pull-request").status_code == 409
    # 2) demo diff
    t2 = _seed(repo, real_diff=False)
    assert c.post(f"/api/tasks/{t2.id}/pull-request").status_code == 422
    # 3) 无 PR 描述
    t3 = _seed(repo, with_pr=False)
    assert c.post(f"/api/tasks/{t3.id}/pull-request").status_code == 409
    # 4) 任务不存在
    assert c.post("/api/tasks/TASK-NOPE/pull-request").status_code == 404


def test_create_pr_idempotent(tmp_path, monkeypatch):
    repo = main.REPOSITORIES["repo_payment_service"]
    task = _seed(repo)
    calls = _stub_pr_post(monkeypatch)
    _stub_git(monkeypatch, tmp_path)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ALLOW_SCRATCH_REAL_CHANGE", "true")
    c = authed()
    assert c.post(f"/api/tasks/{task.id}/pull-request").status_code == 200
    assert calls["n"] == 1
    assert c.post(f"/api/tasks/{task.id}/pull-request").status_code == 200
    assert calls["n"] == 1, "已有 open PR 应幂等，不重复创建"
    assert task.pr_number == "PR-001"


@_PR_E2E
def test_create_pr_real_gitee_e2e(tmp_path, monkeypatch):
    """真实 Gitee PR E2E：仅 GITEE_PR_E2E=true 且 env 配齐才跑；配齐后需对 scratch 仓真实 push+建 PR。"""
    from app.services import gitee_pr_service

    url = f"https://gitee.com/{os.environ['GITEE_OWNER']}/{os.environ['GITEE_REPO_SCRATCH']}.git"
    pr = gitee_pr_service.create_gitee_pull_request(
        url, os.environ["GITEE_TOKEN"], head_branch="refs/heads/demo", base_branch="master",
        title="E2E PR", body="真实 PR 验收",
    )
    assert pr["number"] and pr["html_url"]
    print("REAL_PR:", pr["html_url"])
