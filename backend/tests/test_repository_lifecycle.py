"""仓库地址切换后的生命周期、任务范围与历史追溯验收。"""

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app import main


def admin_client() -> TestClient:
    client = TestClient(main.app)
    response = client.post(
        "/api/auth/login",
        json={"account": "admin@company.com", "password": "Admin123!"},
    )
    assert response.status_code == 200
    return client


def repository_payload(url: str, name: str) -> dict:
    return {
        "project_id": "proj_payment",
        "name": name,
        "url": url,
        "default_branch": "main",
        "test_command": "pytest tests/ -q",
        "command_whitelist": ["pytest"],
        "approval_owner_id": "",
        "approval_mode": "single",
        "risk_threshold": "medium",
        "max_files": 12,
        "max_retries": 2,
        "blocked_paths": [],
    }


def make_task(task_id: str, repository: main.RepositoryConfigRecord) -> main.TaskRecord:
    now = datetime.now(timezone.utc)
    return main.TaskRecord(
        id=task_id,
        version=1,
        project_id=repository.project_id,
        project_name=main.PROJECTS[repository.project_id].name,
        repository_id=repository.id,
        repository_name=repository.name,
        repository_url=repository.url,
        issue_id=f"ISSUE-{task_id}",
        title=task_id,
        status="CREATED",
        base_branch=repository.default_branch,
        test_command=repository.test_command,
        work_branch=f"agent/main/{task_id.lower()}",
        issue_snapshot=main.IssueSnapshotRecord(
            title=task_id,
            body="历史范围隔离测试任务",
            comments=[],
            labels=[],
            raw_response={},
            fetched_at=now,
        ),
        automation_state="running",
        manual_required=False,
        manual_reason="none",
        next_action="check_index",
        events=[],
        created_by="user_admin",
        created_by_name="管理员",
        created_at=now,
        updated_at=now,
    )


def test_repository_url_change_archives_old_space_and_scopes_tasks(client):
    admin = admin_client()
    old_url = "https://gitee.com/company/lifecycle-old.git"
    new_url = "https://gitee.com/company/lifecycle-new.git"
    created_ids: list[str] = []
    try:
        created = admin.post("/api/repositories", json=repository_payload(old_url, "lifecycle-old"))
        assert created.status_code == 200, created.text
        old = main.RepositoryConfigRecord.model_validate(created.json()["data"])
        created_ids.append(old.id)

        updated = admin.put(
            f"/api/repositories/{old.id}",
            json=repository_payload(new_url, "lifecycle-new"),
        )
        assert updated.status_code == 200, updated.text
        new = main.RepositoryConfigRecord.model_validate(updated.json()["data"])
        created_ids.append(new.id)

        assert new.id != old.id
        assert main.REPOSITORIES[old.id].is_active is False
        assert main.REPOSITORIES[old.id].superseded_by_id == new.id
        assert main.REPOSITORIES[new.id].is_active is True
        assert any(event.event_type == "REPOSITORY_REPLACED" for event in main.AUDIT_EVENTS.values())

        current = admin.get("/api/repositories?project_id=proj_payment")
        assert current.status_code == 200
        assert [item["id"] for item in current.json()["data"] if item["id"] in created_ids] == [new.id]

        history = admin.get("/api/repositories?project_id=proj_payment&include_archived=true")
        assert history.status_code == 200
        assert {item["id"] for item in history.json()["data"]}.issuperset({old.id, new.id})

        old_task = make_task("TASK-LIFECYCLE-OLD", old)
        new_task = make_task("TASK-LIFECYCLE-NEW", new)
        main.TASKS[old_task.id] = old_task
        main.TASKS[new_task.id] = new_task

        current_tasks = admin.get("/api/tasks?project_id=proj_payment")
        assert current_tasks.status_code == 200
        assert {item["id"] for item in current_tasks.json()["data"]}.issuperset({new_task.id})
        assert old_task.id not in {item["id"] for item in current_tasks.json()["data"]}

        history_tasks = admin.get("/api/tasks?project_id=proj_payment&include_archived=true")
        assert history_tasks.status_code == 200
        assert {item["id"] for item in history_tasks.json()["data"]}.issuperset({old_task.id, new_task.id})
    finally:
        for repository_id in created_ids:
            main.REPOSITORIES.pop(repository_id, None)
            main.REPOSITORY_TOKENS.pop(repository_id, None)
        main.TASKS.pop("TASK-LIFECYCLE-OLD", None)
        main.TASKS.pop("TASK-LIFECYCLE-NEW", None)


def test_task_list_uses_latest_active_repository_when_legacy_project_has_duplicates(client):
    admin = admin_client()
    first_url = "https://gitee.com/company/legacy-first.git"
    second_url = "https://gitee.com/company/legacy-second.git"
    created_ids: list[str] = []
    try:
        first = admin.post("/api/repositories", json=repository_payload(first_url, "legacy-first"))
        second = admin.post("/api/repositories", json=repository_payload(second_url, "legacy-second"))
        assert first.status_code == 200, first.text
        assert second.status_code == 200, second.text
        first_repo = main.RepositoryConfigRecord.model_validate(first.json()["data"])
        second_repo = main.RepositoryConfigRecord.model_validate(second.json()["data"])
        created_ids.extend([first_repo.id, second_repo.id])

        first_task = make_task("TASK-LEGACY-FIRST", first_repo)
        second_task = make_task("TASK-LEGACY-SECOND", second_repo)
        main.TASKS[first_task.id] = first_task
        main.TASKS[second_task.id] = second_task

        current = admin.get("/api/tasks?project_id=proj_payment")
        assert current.status_code == 200, current.text
        current_ids = {item["id"] for item in current.json()["data"]}
        assert second_task.id in current_ids
        assert first_task.id not in current_ids

        history = admin.get("/api/tasks?project_id=proj_payment&include_archived=true")
        assert history.status_code == 200, history.text
        history_ids = {item["id"] for item in history.json()["data"]}
        assert {first_task.id, second_task.id}.issubset(history_ids)
    finally:
        for repository_id in created_ids:
            main.REPOSITORIES.pop(repository_id, None)
            main.REPOSITORY_TOKENS.pop(repository_id, None)
        main.TASKS.pop("TASK-LEGACY-FIRST", None)
        main.TASKS.pop("TASK-LEGACY-SECOND", None)
