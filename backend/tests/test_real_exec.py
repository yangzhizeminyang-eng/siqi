"""里程碑 A 最后收尾的真实执行测试：
- 真实 default 变更（scratch 仓）产出 execution_mode=REAL + real_change_source=e2e_scratch_real_change；
- DEMO_FALLBACK 不进入 pull-request（复用 test_pr_creation）；
- 真实 diff 可 apply/commit 到 git worktree；
- docker runner 记录 exit_code/stdout/stderr/duration（runner_mode=docker / unavailable 不可伪装）。
"""
from __future__ import annotations

import datetime
import pathlib
import subprocess

import pytest

from app import main
from app.services import diff_service, repo_mirror, test_runner


def _sh(args, cwd):
    r = subprocess.run(args, cwd=str(cwd), capture_output=True, text=True)
    assert r.returncode == 0, (args, r.stderr)


@pytest.fixture()
def origin(tmp_path: pathlib.Path) -> pathlib.Path:
    repo = tmp_path / "origin"
    repo.mkdir()
    _sh(["git", "init", "-q", "-b", "master"], repo)
    (repo / "README.md").write_text("# demo\n", encoding="utf8")
    (repo / "app.py").write_text("print('x')\n", encoding="utf8")
    _sh(["git", "-C", str(repo), "add", "-A"], repo)
    _sh(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "c1"], repo)
    return repo


def _head(repo) -> str:
    return subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()


def _task(origin, repo_id="repo_scratch", base_commit=None):
    now = datetime.datetime.now(datetime.timezone.utc)
    return main.TaskRecord(
        id="TASK-R1", version=1, project_id="p", project_name="P", repository_id=repo_id,
        repository_name="scratch", repository_url=str(origin),
        issue_id="I1", title="scratch real change", status="APPROVED",
        base_branch="master", test_command="echo ok", work_branch="agent/master/issue-i1-v1",
        issue_snapshot=main.IssueSnapshotRecord(title="t", body="b", comments=[], labels=[], raw_response={},
                                                fetched_at=now - datetime.timedelta(minutes=2)),
        automation_state="blocked", manual_required=False, manual_reason="none",
        next_action="auto_generate_diff", events=[], created_by="u", created_by_name="u",
        created_at=now, updated_at=now, base_commit_sha=base_commit or _head(origin),
    )


def _dummy_task():
    now = datetime.datetime.now(datetime.timezone.utc)
    return main.TaskRecord(
        id="TASK-R2", version=1, project_id="p", project_name="P", repository_id="repo_x",
        repository_name="x", repository_url="https://gitee.com/o/r.git", issue_id="I2",
        title="docker test", status="APPROVED", base_branch="master", test_command="echo ok",
        work_branch="agent/master/issue-i2-v1",
        issue_snapshot=main.IssueSnapshotRecord(title="t", body="b", comments=[], labels=[], raw_response={},
                                                fetched_at=now - datetime.timedelta(minutes=2)),
        automation_state="blocked", manual_required=False, manual_reason="none",
        next_action="auto_run_test", events=[], created_by="u", created_by_name="u",
        created_at=now, updated_at=now, base_commit_sha=None,
    )


def _plan(task_id="TASK-R1", goal="在 README 追加真实变更标注"):
    now = datetime.datetime.now(datetime.timezone.utc)
    return main.ModificationPlan(
        id=f"PLAN-{task_id}", task_id=task_id, version=1, goal=goal,
        file_scope=[main.PlanFileScope(file_path="README.md", change_type="modify", reason="")],
        steps=[main.PlanStep(order=1, title="追加标注", description="", target_files=["README.md"])],
        test_suggestions=["echo ok"], assumptions=[], rollback_plan=[],
        risk_level="medium", risk_reason="", generated_from_analysis_revision=1, generated_from_context_ids=[],
        provider="deepseek", status="APPROVED", risk_adjust_reason=None,
        approved_by_id=None, approved_by_name=None, approved_at=None,
        created_by="u", created_by_name="u", created_at=now,
    )


class _Art:
    files = []
    execution_step = ""


def test_scratch_real_diff_is_real(origin, tmp_path, monkeypatch):
    """要求：scratch 仓 + 开关下真实变更产 execution_mode=REAL + e2e_scratch_real_change，非 demo。"""
    monkeypatch.setenv("ALLOW_SCRATCH_REAL_CHANGE", "true")
    monkeypatch.setenv("E2E_REAL_CHANGE_REPO_IDS", "repo_scratch")
    monkeypatch.setenv("GITEE_TOKEN", "")  # 本地仓无需 token
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    task = _task(origin)
    plan = _plan()
    payload = diff_service.execute_diff(task, plan)
    assert payload["execution_mode"] == "REAL", payload
    assert payload["is_demo_fallback"] is False
    assert payload["real_change_source"] == "e2e_scratch_real_change"
    assert payload["generation_status"] == "SUCCEEDED"
    assert payload["files"], "真实变更应产出文件"
    assert payload["files"][0]["file_path"] == "README.md"
    assert payload["files"][0]["change_type"] == "modify"
    assert "e2e_scratch_real_change" in "\n".join(payload["files"][0]["after"])


def test_scratch_real_diff_disabled_by_default(origin, tmp_path, monkeypatch):
    """未开开关/非白名单 → 走真实不可用（非 scratch 不产真实补丁）。"""
    monkeypatch.setenv("ALLOW_SCRATCH_REAL_CHANGE", "")
    task = _task(origin)
    files, err = diff_service.attempt_real_diff(task, _plan())
    assert files is None
    assert err == diff_service.REAL_EXECUTION_UNAVAILABLE_REASON


def test_real_diff_applies_and_commits(origin, tmp_path):
    """要求：真实 diff 可 apply 到 git worktree 并 commit。"""
    head = _head(origin)
    mirror = repo_mirror.ensure_mirror("repo_scratch2", str(origin), token=None, base_dir=tmp_path / "m")
    wt = repo_mirror.checkout_commit(mirror, head, (tmp_path / "wt") / head[:8])
    df = type("DF", (), {"file_path": "README.md", "change_type": "modify", "after": ["# demo", "", "## real"]})()
    repo_mirror.apply_diff_files(wt, [df])
    assert (wt / "README.md").read_text(encoding="utf8") == "# demo\n\n## real"
    repo_mirror.commit_changes(wt, "real e2e change", user="agent", email="a@e")
    clean = subprocess.run(["git", "-C", str(wt), "status", "--porcelain"], capture_output=True, text=True).stdout.strip()
    assert clean == "", f"apply+commit 后工作区应干净，实际：{clean}"
    log = subprocess.run(["git", "-C", str(wt), "log", "-1", "--pretty=%s"], capture_output=True, text=True).stdout.strip()
    assert log == "real e2e change"


def test_docker_runner_records_fields(monkeypatch):
    """要求：TEST_RUNNER_MODE=docker 时记录 exit_code/stdout/stderr/duration，runner_mode=docker。"""
    monkeypatch.setenv("TEST_RUNNER_MODE", "docker")
    calls = {"run": 0}
    fake = {"exit_code": 0, "stdout": "1 passed", "stderr": "", "duration": 1.25}
    monkeypatch.setattr(test_runner.DockerRunner, "run",
                        lambda self, command, files: (calls.update(run=calls["run"] + 1) or (dict(fake), None)))
    result = test_runner.execute_test(_dummy_task(), _Art(), "echo ok", attempt=1)
    assert result["runner_mode"] == "docker"
    assert calls["run"] == 1
    assert result["exit_code"] == 0 and result["duration"] == 1.25
    assert result["stdout"] == "1 passed" and result["stderr"] == ""


def test_docker_runner_unavailable_not_passed(monkeypatch):
    """要求：Docker 不可用时记录 unavailable+error，绝不写 passed/mocked_runner。"""
    monkeypatch.setenv("TEST_RUNNER_MODE", "docker")
    monkeypatch.setattr(test_runner.DockerRunner, "run",
                        lambda self, command, files: (None, "Docker daemon 未运行，无法执行容器测试"))
    result = test_runner.execute_test(_dummy_task(), _Art(), "echo ok", attempt=1)
    assert result["runner_mode"] == "unavailable"
    assert result["error"]
    assert result["exit_code"] is None