from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app import main


def authed_client(account: str, password: str) -> TestClient:
    client = TestClient(main.app)
    login = client.post("/api/auth/login", json={"account": account, "password": password})
    assert login.status_code == 200, login.text
    return client


def make_snapshot(title: str, body: str) -> main.IssueSnapshotRecord:
    now = datetime.now(timezone.utc)
    return main.IssueSnapshotRecord(
        title=title,
        body=body,
        comments=[],
        labels=["ui"],
        raw_response={},
        fetched_at=now,
    )


def make_event(task_id: str, event_id: str, event_type: str, summary: str, created_at: datetime) -> main.AuditEventRecord:
    return main.AuditEventRecord(
        id=event_id,
        event_type=event_type,
        resource_type="task",
        resource_id=task_id,
        actor_id="user_developer",
        actor_name="周航",
        summary=summary,
        diff_summary=[],
        created_at=created_at,
        execution_step_ids=[],
    )


def make_waiting_plan(task_id: str, created_at: datetime) -> main.ModificationPlan:
    return main.ModificationPlan(
        id=f"PLAN-{task_id}",
        task_id=task_id,
        version=1,
        goal="优化项目健康度统计",
        file_scope=[],
        steps=[],
        risk_level="medium",
        risk_reason="统计链路需要联调",
        generated_from_analysis_revision=1,
        provider="mvp-rule-based",
        status="WAITING_APPROVAL",
        created_by="user_developer",
        created_by_name="周航",
        created_at=created_at,
    )


def make_task(
    task_id: str,
    *,
    project_id: str,
    project_name: str,
    status: main.TaskStatus,
    automation_state: main.AutomationState,
    manual_required: bool,
    created_at: datetime,
    updated_at: datetime,
    approval_owner_id: str | None = None,
    approval_owner_name: str | None = None,
    plans: list[main.ModificationPlan] | None = None,
    events: list[main.AuditEventRecord] | None = None,
) -> main.TaskRecord:
    repo = main.REPOSITORIES["repo_payment_service"]
    now = datetime.now(timezone.utc)
    return main.TaskRecord(
        id=task_id,
        version=1,
        project_id=project_id,
        project_name=project_name,
        repository_id=repo.id,
        repository_name=repo.name,
        repository_url=repo.url,
        issue_id=f"#{task_id[-4:]}",
        title=f"任务 {task_id}",
        status=status,
        risk_level="medium",
        risk_source="pending_ai_analysis",
        base_branch=repo.default_branch,
        test_command=repo.test_command,
        approval_owner_id=approval_owner_id,
        approval_owner_name=approval_owner_name,
        work_branch=f"agent/main/{task_id.lower()}",
        issue_snapshot=make_snapshot(f"任务 {task_id}", "内容足够长，满足测试种子需要。"),
        automation_state=automation_state,
        manual_required=manual_required,
        manual_reason="approval_required" if manual_required else "none",
        next_action="auto_generate_plan",
        plans=plans or [],
        diffs=[],
        test_runs=[],
        pr_descriptions=[],
        events=events or [],
        created_by="user_developer",
        created_by_name="周航",
        created_at=created_at,
        updated_at=updated_at,
    )


def test_dashboard_summary_uses_real_tasks_and_project_scope():
    admin_client = authed_client("admin@company.com", "Admin123!")
    approver_client = authed_client("approver@company.com", "Approver123!")
    developer_client = authed_client("developer@company.com", "Dev123!")

    now = datetime.now(timezone.utc)
    current_project = main.PROJECTS["proj_payment"]
    other_project = main.ProjectRecord(
        id="proj_dashboard_extra",
        name="运营平台",
        description="临时测试项目",
        created_at=now - timedelta(days=4),
        updated_at=now - timedelta(days=1),
    )
    main.PROJECTS[other_project.id] = other_project
    try:
        main.TASKS["TASK-7001"] = make_task(
            "TASK-7001",
            project_id=current_project.id,
            project_name=current_project.name,
            status="ANALYZED",
            automation_state="running",
            manual_required=False,
            created_at=now - timedelta(days=1),
            updated_at=now - timedelta(hours=2),
            events=[make_event("TASK-7001", "ev-7001", "PLAN_GENERATED", "新的分析计划已生成", now - timedelta(hours=2))],
        )
        main.TASKS["TASK-7002"] = make_task(
            "TASK-7002",
            project_id=current_project.id,
            project_name=current_project.name,
            status="WAITING_APPROVAL",
            automation_state="waiting_approval",
            manual_required=True,
            approval_owner_id="user_approver",
            approval_owner_name="陈晓",
            created_at=now - timedelta(days=3),
            updated_at=now - timedelta(hours=26),
            plans=[make_waiting_plan("TASK-7002", now - timedelta(hours=26))],
            events=[make_event("TASK-7002", "ev-7002", "PLAN_GENERATED", "修改计划已生成", now - timedelta(hours=3))],
        )
        main.TASKS["TASK-7003"] = make_task(
            "TASK-7003",
            project_id=current_project.id,
            project_name=current_project.name,
            status="WAITING_APPROVAL",
            automation_state="waiting_approval",
            manual_required=True,
            approval_owner_id="user_lead",
            approval_owner_name="王琳",
            created_at=now - timedelta(days=2),
            updated_at=now - timedelta(hours=2),
            plans=[make_waiting_plan("TASK-7003", now - timedelta(hours=2))],
            events=[make_event("TASK-7003", "ev-7003", "TASK_CREATED", "任务已创建", now - timedelta(hours=4))],
        )
        main.TASKS["TASK-7004"] = make_task(
            "TASK-7004",
            project_id=current_project.id,
            project_name=current_project.name,
            status="COMPLETED",
            automation_state="completed",
            manual_required=False,
            created_at=now - timedelta(days=5),
            updated_at=now - timedelta(hours=1),
            events=[make_event("TASK-7004", "ev-7004", "TEST_RUN_PASSED", "测试通过", now - timedelta(minutes=30))],
        )
        main.TASKS["TASK-7005"] = make_task(
            "TASK-7005",
            project_id=other_project.id,
            project_name=other_project.name,
            status="COMPLETED",
            automation_state="completed",
            manual_required=False,
            created_at=now - timedelta(days=4),
            updated_at=now - timedelta(hours=2),
            events=[make_event("TASK-7005", "ev-7005", "TEST_RUN_PASSED", "外部项目测试通过", now - timedelta(days=1))],
        )

        scoped = admin_client.get("/api/dashboard/automation-summary", params={"project_name": current_project.name})
        assert scoped.status_code == 200, scoped.text
        scoped_data = scoped.json()["data"]
        assert scoped_data["runningTasks"] == 1
        assert scoped_data["pendingApprovals"] == 2
        assert scoped_data["completedThisWeek"] == 1
        assert scoped_data["overdueApprovals"] == 1
        assert scoped_data["trend"][-1]["day"] == "今天"
        assert scoped_data["events"][0]["title"] == "测试通过"
        assert scoped_data["events"][0]["level"] == "success"
        assert scoped_data["automationRateLabel"].endswith("%")
        assert scoped_data["savedHours"] != 126
        assert scoped_data["weeklyGrowthLabel"].endswith("%")

        global_summary = admin_client.get("/api/dashboard/automation-summary")
        assert global_summary.status_code == 200, global_summary.text
        global_data = global_summary.json()["data"]
        assert global_data["completedThisWeek"] == 2
        assert global_data["pendingApprovals"] == 2

        approver_data = approver_client.get("/api/dashboard/automation-summary", params={"project_name": current_project.name}).json()["data"]
        developer_data = developer_client.get("/api/dashboard/automation-summary", params={"project_name": current_project.name}).json()["data"]
        assert approver_data["pendingApprovals"] == 1
        assert developer_data["pendingApprovals"] == 0
    finally:
        main.TASKS.clear()
        main.PROJECTS.pop(other_project.id, None)


def test_dashboard_summary_exposes_30_day_delivery_metrics_with_failure_and_rate():
    admin_client = authed_client("admin@company.com", "Admin123!")
    now = datetime.now(timezone.utc)
    project = main.PROJECTS["proj_payment"]

    main.TASKS["TASK-7030"] = make_task(
        "TASK-7030",
        project_id=project.id,
        project_name=project.name,
        status="FAILED",
        automation_state="blocked",
        manual_required=True,
        created_at=now - timedelta(days=29),
        updated_at=now - timedelta(days=28),
    )
    main.TASKS["TASK-7031"] = make_task(
        "TASK-7031",
        project_id=project.id,
        project_name=project.name,
        status="COMPLETED",
        automation_state="completed",
        manual_required=False,
        created_at=now - timedelta(days=1),
        updated_at=now - timedelta(days=1),
    )
    try:
        response = admin_client.get(
            "/api/dashboard/automation-summary",
            params={"project_name": project.name, "days": 30},
        )

        assert response.status_code == 200, response.text
        payload = response.json()["data"]
        assert payload["trendDays"] == 30
        assert len(payload["trend"]) == 30
        assert payload["trend"][-1]["date"] == now.date().isoformat()
        assert {"date", "created", "done", "failed", "successRate"} <= payload["trend"][-1].keys()
        assert any(point["failed"] == 1 for point in payload["trend"])
        assert any(point["done"] == 1 and point["successRate"] == 100 for point in payload["trend"])
    finally:
        main.TASKS.pop("TASK-7030", None)
        main.TASKS.pop("TASK-7031", None)
