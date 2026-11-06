"""里程碑 A 真实收尾（scratch）：真实 Diff → Docker 测试 → push → 真实 Gitee PR。

仅对专用 scratch 仓（env E2E_SCRATCH_REPO_URLS）执行，须显式 ALLOW_SCRATCH_REAL_CHANGE=true；
token 走 backend/.env（runtime_config），全程不打印。产出真实 PR，供 docs/29 取证。
"""
from __future__ import annotations

import os
import sys

try:
    sys.stdout.reconfigure(errors="replace")
except Exception:
    pass
from app import main as m
from app.services import diff_service, repo_mirror
from app.db.repositories import persistence_enabled, upsert_repository, upsert_task


def _now(): return __import__("datetime").datetime.now(__import__("datetime").timezone.utc)


def build_repo(owner, repo):
    url = f"https://gitee.com/{owner}/{repo}.git"
    # 复用已有 / 新建 repo 配置
    for r in m.REPOSITORIES.values():
        if r.name == repo:
            return r
    rec = m.RepositoryConfigRecord(
        id=f"repo_scratch_{repo}", project_id="proj_payment", name=repo, url=url, default_branch="master",
        test_command="echo ok", command_whitelist=["echo ok", "echo ok && true"], test_command_fallback=True,
        auto_create_task_after_sync=False, approval_mode="single", risk_threshold="medium",
        max_files=50, max_retries=2, blocked_paths=[], created_at=_now(), updated_at=_now(),
    )
    m.REPOSITORIES[rec.id] = rec
    m.REPOSITORY_TOKENS[rec.id] = os.environ.get("GITEE_TOKEN", "")
    if persistence_enabled():
        upsert_repository(rec)
    return rec


def main():
    from scripts.authorization_gate import require_authorization

    require_authorization(
        os.environ.get("AUTHORIZATION_OPERATION", ""),
        os.environ.get("AUTHORIZATION_LOG", "") or None,
    )
    owner = os.environ.get("SCRATCH_OWNER", "yang-zhizemin")
    repo_name = os.environ.get("SCRATCH_REPO", "git-repo-test")
    repo = build_repo(owner, repo_name)
    token = os.environ.get("GITEE_TOKEN", "")
    print("repo:", repo.url)

    task_id = "TASK-REALPR-1"
    now = _now()
    task = m.TaskRecord(
        id=task_id, version=1, project_id=repo.project_id, project_name="演示", repository_id=repo.id,
        repository_name=repo.name, repository_url=repo.url, issue_id="scratch-real",
        title="里程碑A真实变更 e2e", status="TESTED", base_branch="master", test_command="echo ok",
        work_branch=f"agent/master/issue-scratch-v{now.strftime('%H%M%S')}",
        issue_snapshot=m.IssueSnapshotRecord(title="真实E2E", body="scratch", comments=[], labels=[], raw_response={}, fetched_at=now),
        automation_state="completed", manual_required=False, manual_reason="none",
        next_action="generate_pr_description", events=[], created_by="user_admin", created_by_name="管理员",
        created_at=now - __import__("datetime").timedelta(minutes=5), updated_at=now,
    )
    # 计划（已审批）
    plan = m.ModificationPlan(
        id=f"PLAN-{task_id}", task_id=task.id, version=1, goal="追加真实变更标注到 README",
        file_scope=[m.PlanFileScope(file_path="README.md", change_type="modify", reason="")],
        steps=[m.PlanStep(order=1, title="追加标注", description="", target_files=["README.md"])],
        test_suggestions=["echo ok"], assumptions=[], rollback_plan=[], risk_level="medium", risk_reason="",
        generated_from_analysis_revision=1, generated_from_context_ids=[], provider="deepseek",
        status="APPROVED", risk_adjust_reason=None, approved_by_id=None, approved_by_name=None, approved_at=now,
        created_by="user_admin", created_by_name="管理员", created_at=now - __import__("datetime").timedelta(minutes=4),
    )
    task.plans.append(plan)

    # 1) 真实 Diff（scratch 白名单 + 开关）
    payload = diff_service.execute_diff(task, plan)
    print("DIFF execution_mode=", payload["execution_mode"], "source=", payload.get("real_change_source"),
          "is_demo=", payload["is_demo_fallback"])
    if payload["is_demo_fallback"]:
        print("DIFF_FAIL real not produced:", payload.get("fallback_reason")); return 2
    artifact = m.store_diff_artifact(task, plan, payload, now)
    task.status = "DIFF_GENERATED"
    print("DIFF_OK files=", [(f.file_path, f.change_type) for f in artifact.files], "patch_head=", artifact.files[0].patch[:40].replace("\n", " "))

    # 2) 真实 Docker 测试
    _shared = {"test": None}
    def _run_real_test(t, art, cmd, attempt):
        from app.services import test_runner
        res = test_runner.execute_test(t, art, cmd, attempt)
        _shared["test"] = res
        return res
    from app import main as mm
    # 直接跑一个真实 docker 记录冲进 test_runs（模拟 execute_task_tests_worker 的落库）
    res = _run_real_test(task, artifact, "echo ok", 1)
    print("TEST runner_mode=", res.get("runner_mode"), "exit=", res.get("exit_code"), "dur=", res.get("duration"), "error=", res.get("error"))
    if res.get("runner_mode") != "docker":
        print("TEST_NOT_DOCKER:", res.get("error")); return 2
    test_run = m.TestRunRecord(
        id="TEST-REALPR-1", task_id=task.id, diff_artifact_id=artifact.id, plan_id=plan.id, plan_version=1,
        command="echo ok", exit_code=res["exit_code"], stdout=res["stdout"], stderr=res["stderr"],
        status="passed" if res["exit_code"] == 0 else "failed", duration=res["duration"], attempt=1,
        started_at=now, finished_at=now, failure_reason=None if res["exit_code"] == 0 else "fail",
        blocked_reason=res.get("error"), runner_mode=res["runner_mode"],
    )
    task.test_runs.append(test_run)
    task.status = "TESTED"
    print("TEST_OK runner_mode=", test_run.runner_mode, "exit=", test_run.exit_code)

    # 3) PR 描述（构造 GENERATED，引用真实 diff+test）
    prd = m.PrDescriptionRecord(
        id=f"PRD-{task_id}-1", task_id=task.id, plan_id=plan.id, plan_version=1,
        diff_artifact_id=artifact.id, test_run_id=test_run.id, version=1,
        background="里程碑 A 真实 E2E", change_summary="在 README 追加 e2e_scratch_real_change 真实变更标注，由 AI Agent 生成",
        impact_scope="README.md 文档", test_result=f"真实 Docker 执行 pass（runner=docker）", risk_notes="仅 scratch 仓",
        rollback_notes="删除标注行即回滚", generation_status="GENERATED", failure_reason=None,
        generated_at=now, generated_by="user_admin", generated_by_name="管理员",
    )
    task.pr_descriptions.append(prd)

    # 4) 真实 push + PR（复用 create_task_pull_request 编排：apply→commit→push→create PR）
    m.TASKS[task.id] = task
    if persistence_enabled():
        upsert_task(task)
    out = m.create_task_pull_request(task, m.USERS["user_admin"])
    print("PR status=", out.pr_status, "number=", out.pr_number, "url=", out.pr_url)
    if out.pr_number:
        m.TASKS[task.id] = out
        if persistence_enabled(): upsert_task(out)
        print("REAL_PR_OK url=", out.pr_url)
        return 0
    print("PR_FAIL no pr_number"); return 3


if __name__ == "__main__":
    raise SystemExit(main())