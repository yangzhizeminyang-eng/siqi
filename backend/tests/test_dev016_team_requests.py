"""DEV-016-A 平台需求/缺陷草稿模块测试套件。

覆盖：DRAFT 草稿创建成功（REQ-* 编号、系统字段、创建人/项目/仓库）、
type 枚举校验（feature/task 拒绝）、缺必填字段中文 422、正文过短可保存但
content_sufficient=false、测试命令白名单命中/未命中 422/回退默认命令、
观察者与审批人 403、开发者不能修改他人草稿、SYNCED/TASK_CREATED/CLOSED
状态 409 不可修改、列表过滤、审计事件写入与失败场景不写审计。
"""
import pytest
from fastapi.testclient import TestClient

from app import main

REPOSITORY = main.REPOSITORIES["repo_payment_service"]

LONG_BODY = (
    "登录页在刷新后会误跳转到登录页：用户已登录状态下刷新 /dashboard，"
    "页面先渲染工作台内容再跳回 /login，期望刷新后保持当前页面，"
    "仅当登录态失效时才跳转登录，复现概率约 100%。"
)
SHORT_BODY = "刷新后误跳转登录页，需要修复。"

CREATE_PAYLOAD = {
    "project_id": REPOSITORY.project_id,
    "repository_id": REPOSITORY.id,
    "type": "bug",
    "title": "登录页刷新后误跳转",
    "body": LONG_BODY,
    "reproduction_steps": "1. 登录进入工作台 2. 按 F5 刷新 3. 观察跳转",
    "acceptance_criteria": ["刷新后保持当前页面", "登录态失效时才跳转登录"],
    "priority": "high",
    "labels": ["bug", "frontend", "frontend"],
    "target_branch": "main",
    "test_command": "pytest tests/ -q",
}


def authed_client(account: str, password: str) -> TestClient:
    test_client = TestClient(main.app)
    login = test_client.post(
        "/api/auth/login",
        json={"account": account, "password": password},
    )
    assert login.status_code == 200
    return test_client


@pytest.fixture()
def developer_client() -> TestClient:
    return authed_client("developer@company.com", "Dev123!")


@pytest.fixture()
def admin_client() -> TestClient:
    return authed_client("admin@company.com", "Admin123!")


@pytest.fixture()
def lead_client() -> TestClient:
    return authed_client("lead@company.com", "Lead123!")


@pytest.fixture()
def approver_client() -> TestClient:
    return authed_client("approver@company.com", "Approver123!")


@pytest.fixture()
def observer_client() -> TestClient:
    return authed_client("observer@company.com", "Observer123!")


@pytest.fixture()
def second_developer_client() -> TestClient:
    extra = main.UserRecord(
        id="user_developer2",
        name="李冲",
        email="developer2@company.com",
        role="developer",
        password_hash=main.hash_password("Dev123!"),
    )
    main.USERS[extra.id] = extra
    main.USERS_BY_EMAIL[extra.email] = extra
    client = authed_client(extra.email, "Dev123!")
    yield client
    main.USERS.pop(extra.id, None)
    main.USERS_BY_EMAIL.pop(extra.email, None)


def create_request(client: TestClient, **overrides) -> dict:
    payload = {**CREATE_PAYLOAD, **overrides}
    response = client.post("/api/team/requests", json=payload)
    assert response.status_code == 200, response.text
    return response.json()["data"]


def request_type_values(client: TestClient) -> list[str]:
    response = client.get("/api/team/requests")
    assert response.status_code == 200
    return [item["type"] for item in response.json()["data"]]


class TestCreateDraft:
    def test_create_draft_success(self, developer_client):
        record = create_request(developer_client)

        assert record["request_no"] == "REQ-1001"
        assert record["sync_status"] == "DRAFT"
        assert record["type"] == "bug"
        assert record["title"] == "登录页刷新后误跳转"
        assert record["project_id"] == REPOSITORY.project_id
        assert record["project_name"] == "支付中台"
        assert record["repository_id"] == REPOSITORY.id
        assert record["repository_name"] == REPOSITORY.name
        assert record["created_by_id"] == "user_developer"
        assert record["created_by_name"] == "周航"
        assert record["content_sufficient"] is True
        assert record["validation_warnings"] == []
        assert record["test_command"] == "pytest tests/ -q"
        assert record["labels"] == ["bug", "frontend"]
        assert record["gitee_issue_id"] is None
        assert record["gitee_issue_number"] is None
        assert record["gitee_issue_url"] is None
        assert record["task_id"] is None
        assert record["created_at"]
        assert record["updated_at"]

    def test_create_request_no_increments(self, developer_client):
        first = create_request(developer_client)
        second = create_request(developer_client, title="第二份草稿")
        assert first["request_no"] == "REQ-1001"
        assert second["request_no"] == "REQ-1002"

    @pytest.mark.parametrize("bad_type", ["feature", "task", "", "Bug"])
    def test_create_with_invalid_type_returns_422(self, developer_client, bad_type):
        response = developer_client.post("/api/team/requests", json={**CREATE_PAYLOAD, "type": bad_type})
        assert response.status_code == 422
        assert "需求类型" in response.json()["detail"]["message"]

    def test_create_without_type_returns_422_chinese(self, developer_client):
        payload = {key: value for key, value in CREATE_PAYLOAD.items() if key != "type"}
        response = developer_client.post("/api/team/requests", json=payload)
        assert response.status_code == 422
        assert "需求类型不能为空" in response.json()["detail"]["message"]

    @pytest.mark.parametrize("missing", ["project_id", "repository_id", "title", "body"])
    def test_create_missing_required_field_returns_422_chinese(self, developer_client, missing):
        payload = {key: value for key, value in CREATE_PAYLOAD.items() if key != missing}
        response = developer_client.post("/api/team/requests", json=payload)
        assert response.status_code == 422
        message = response.json()["detail"]["message"]
        assert message and self._is_chinese_message(missing, message)

    @staticmethod
    def _is_chinese_message(missing: str, message: str) -> bool:
        expected_keywords = {
            "project_id": "项目不能为空",
            "repository_id": "仓库不能为空",
            "title": "标题不能为空",
            "body": "正文不能为空",
        }
        return expected_keywords[missing] in message

    def test_create_with_blank_title_returns_422(self, developer_client):
        response = developer_client.post("/api/team/requests", json={**CREATE_PAYLOAD, "title": "   "})
        assert response.status_code == 422
        assert "标题不能为空" in response.json()["detail"]["message"]

    def test_create_with_unknown_project_returns_404(self, developer_client):
        response = developer_client.post("/api/team/requests", json={**CREATE_PAYLOAD, "project_id": "proj_none"})
        assert response.status_code == 404

    def test_create_with_mismatched_repository_returns_404(self, developer_client):
        response = developer_client.post("/api/team/requests", json={**CREATE_PAYLOAD, "repository_id": "repo_other"})
        assert response.status_code == 404


class TestContentSufficiency:
    def test_short_body_still_saves_draft_with_warning(self, developer_client):
        record = create_request(developer_client, body=SHORT_BODY)
        assert record["sync_status"] == "DRAFT"
        assert record["content_sufficient"] is False
        assert record["validation_warnings"]
        assert "同步 Gitee 或创建 Agent 任务前" in record["validation_warnings"][0]

    def test_short_body_warning_persists_in_detail(self, developer_client):
        record = create_request(developer_client, body=SHORT_BODY)
        detail = developer_client.get(f"/api/team/requests/{record['id']}")
        assert detail.status_code == 200
        assert detail.json()["data"]["content_sufficient"] is False


class TestTestCommandWhitelist:
    def test_command_in_whitelist_saved(self, developer_client):
        record = create_request(developer_client, test_command="pytest -q")
        assert record["test_command"] == "pytest -q"
        assert record["validation_warnings"] == []

    def test_command_not_in_whitelist_returns_422(self, developer_client):
        response = developer_client.post("/api/team/requests", json={**CREATE_PAYLOAD, "test_command": "rm -rf /"})
        assert response.status_code == 422
        assert "白名单" in response.json()["detail"]["message"]

    def test_command_fallback_saves_default_with_warning(self, developer_client):
        REPOSITORY.test_command_fallback = True
        try:
            record = create_request(developer_client, test_command="npx jest")
        finally:
            REPOSITORY.test_command_fallback = False
        assert record["test_command"] == REPOSITORY.test_command
        assert record["validation_warnings"]
        assert "回退为默认测试命令" in record["validation_warnings"][0]

    def test_empty_command_saved_as_none(self, developer_client):
        record = create_request(developer_client, test_command="   ")
        assert record["test_command"] is None


class TestPermissions:
    def test_observer_cannot_create(self, observer_client):
        response = observer_client.post("/api/team/requests", json=CREATE_PAYLOAD)
        assert response.status_code == 403
        assert not main.AUDIT_EVENTS

    def test_approver_cannot_create(self, approver_client):
        response = approver_client.post("/api/team/requests", json=CREATE_PAYLOAD)
        assert response.status_code == 403
        assert not main.AUDIT_EVENTS

    def test_observer_cannot_update(self, developer_client, observer_client):
        record = create_request(developer_client)
        response = observer_client.patch(
            f"/api/team/requests/{record['id']}",
            json={"title": "观察者试图修改"},
        )
        assert response.status_code == 403

    def test_approver_cannot_update(self, developer_client, approver_client):
        record = create_request(developer_client)
        response = approver_client.patch(
            f"/api/team/requests/{record['id']}",
            json={"title": "审批人试图修改"},
        )
        assert response.status_code == 403

    def test_observer_can_view_list_and_detail(self, developer_client, observer_client):
        record = create_request(developer_client)
        listed = observer_client.get("/api/team/requests")
        assert listed.status_code == 200
        assert [item["id"] for item in listed.json()["data"]] == [record["id"]]
        detail = observer_client.get(f"/api/team/requests/{record['id']}")
        assert detail.status_code == 200

    def test_lead_can_update_any_draft_in_project(self, developer_client, lead_client):
        record = create_request(developer_client)
        response = lead_client.patch(
            f"/api/team/requests/{record['id']}",
            json={"title": "负责人修订标题"},
        )
        assert response.status_code == 200
        assert response.json()["data"]["title"] == "负责人修订标题"

    def test_developer_cannot_update_other_developer_draft(self, developer_client, second_developer_client):
        record = create_request(developer_client)
        response = second_developer_client.patch(
            f"/api/team/requests/{record['id']}",
            json={"title": "越权修改"},
        )
        assert response.status_code == 403
        assert "本人创建" in response.json()["detail"]["message"]

    def test_developer_cannot_view_other_developer_draft(self, developer_client, second_developer_client):
        record = create_request(developer_client)

        listed = second_developer_client.get("/api/team/requests")
        assert listed.status_code == 200
        assert listed.json()["data"] == []

        detail = second_developer_client.get(f"/api/team/requests/{record['id']}")
        assert detail.status_code == 403
        assert "暂无权限查看" in detail.json()["detail"]["message"]


class TestUpdateDraft:
    def test_update_draft_success_writes_updated_at(self, developer_client):
        record = create_request(developer_client)
        response = developer_client.patch(
            f"/api/team/requests/{record['id']}",
            json={
                "title": "登录页刷新后误跳转（修订）",
                "body": LONG_BODY + "补充：移动端同样复现。",
                "priority": "medium",
                "labels": ["bug"],
                "test_command": "pytest -q",
            },
        )
        assert response.status_code == 200
        updated = response.json()["data"]
        assert updated["title"] == "登录页刷新后误跳转（修订）"
        assert updated["priority"] == "medium"
        assert updated["labels"] == ["bug"]
        assert updated["updated_at"] >= record["updated_at"]
        assert updated["sync_status"] == "DRAFT"

    def test_update_short_body_recomputes_warning(self, developer_client):
        record = create_request(developer_client)
        response = developer_client.patch(f"/api/team/requests/{record['id']}", json={"body": SHORT_BODY})
        updated = response.json()["data"]
        assert updated["content_sufficient"] is False
        assert updated["validation_warnings"]

    def test_update_full_body_clears_warning(self, developer_client):
        record = create_request(developer_client, body=SHORT_BODY)
        response = developer_client.patch(f"/api/team/requests/{record['id']}", json={"body": LONG_BODY})
        updated = response.json()["data"]
        assert updated["content_sufficient"] is True
        assert updated["validation_warnings"] == []

    def test_update_invalid_type_returns_422(self, developer_client):
        record = create_request(developer_client)
        response = developer_client.patch(f"/api/team/requests/{record['id']}", json={"type": "feature"})
        assert response.status_code == 422

    def test_update_sync_failed_draft_allowed(self, developer_client):
        record = create_request(developer_client)
        main.TEAM_REQUESTS[record["id"]] = main.TEAM_REQUESTS[record["id"]].model_copy(
            update={"sync_status": "SYNC_FAILED", "failure_reason": "Gitee Token 已过期"}
        )
        response = developer_client.patch(f"/api/team/requests/{record['id']}", json={"title": "修复后重改"})
        assert response.status_code == 200

    @pytest.mark.parametrize("status", ["SYNCED", "TASK_CREATED", "CLOSED"])
    def test_update_locked_status_returns_409(self, developer_client, status):
        record = create_request(developer_client)
        main.TEAM_REQUESTS[record["id"]] = main.TEAM_REQUESTS[record["id"]].model_copy(update={"sync_status": status})
        response = developer_client.patch(f"/api/team/requests/{record['id']}", json={"title": "不应生效"})
        assert response.status_code == 409
        assert "仅 DRAFT（草稿）或 SYNC_FAILED（同步失败）状态允许修改" in response.json()["detail"]["message"]

    def test_update_unknown_request_returns_404(self, developer_client):
        response = developer_client.patch("/api/team/requests/team_req_none", json={"title": "X"})
        assert response.status_code == 404

    def test_detail_accepts_request_no(self, developer_client):
        record = create_request(developer_client)
        detail = developer_client.get(f"/api/team/requests/{record['request_no']}")
        assert detail.status_code == 200
        assert detail.json()["data"]["id"] == record["id"]


class TestListFilters:
    def test_list_filters(self, developer_client, second_developer_client, lead_client):
        bug = create_request(developer_client, title="缺陷草稿")
        demand = create_request(developer_client, type="demand", title="需求草稿")
        other = create_request(second_developer_client, title="他人草稿")
        main.TEAM_REQUESTS[other["id"]] = main.TEAM_REQUESTS[other["id"]].model_copy(update={"sync_status": "SYNCED"})

        by_project = lead_client.get(f"/api/team/requests?project_id={REPOSITORY.project_id}")
        assert len(by_project.json()["data"]) == 3
        by_type = lead_client.get("/api/team/requests?type=demand")
        assert [item["id"] for item in by_type.json()["data"]] == [demand["id"]]
        by_status = lead_client.get("/api/team/requests?sync_status=SYNCED")
        assert [item["id"] for item in by_status.json()["data"]] == [other["id"]]
        by_creator = lead_client.get("/api/team/requests?created_by=user_developer")
        assert {item["id"] for item in by_creator.json()["data"]} == {bug["id"], demand["id"]}
        by_repo = lead_client.get(f"/api/team/requests?repository_id={REPOSITORY.id}")
        assert len(by_repo.json()["data"]) == 3
        unknown_creator = lead_client.get("/api/team/requests?created_by=user_none")
        assert unknown_creator.json()["data"] == []


class TestAuditEvents:
    def test_create_and_update_write_audit_events(self, developer_client):
        record = create_request(developer_client)
        created_events = [
            event
            for event in main.AUDIT_EVENTS.values()
            if event.event_type == "TEAM_REQUEST_CREATED" and event.resource_id == record["id"]
        ]
        assert len(created_events) == 1
        assert created_events[0].actor_id == "user_developer"

        developer_client.patch(f"/api/team/requests/{record['id']}", json={"title": "修订标题"})
        updated_events = [
            event
            for event in main.AUDIT_EVENTS.values()
            if event.event_type == "TEAM_REQUEST_UPDATED" and event.resource_id == record["id"]
        ]
        assert len(updated_events) == 1
        assert "标题" in updated_events[0].diff_summary[0]

    def test_validation_failure_writes_no_audit(self, developer_client):
        response = developer_client.post("/api/team/requests", json={**CREATE_PAYLOAD, "type": "feature"})
        assert response.status_code == 422
        assert not main.AUDIT_EVENTS

    def test_permission_denied_writes_no_business_audit(self, observer_client):
        response = observer_client.post("/api/team/requests", json=CREATE_PAYLOAD)
        assert response.status_code == 403
        business_events = [
            event
            for event in main.AUDIT_EVENTS.values()
            if event.event_type.startswith("TEAM_REQUEST")
        ]
        assert business_events == []
