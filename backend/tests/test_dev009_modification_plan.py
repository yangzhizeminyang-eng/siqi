"""DEV-009 修改计划模块测试套件。

覆盖：生成门禁（状态/分析缺失/阻塞问题未答/fresh 上下文）、规则兜底与 LLM 草稿合并、
计划风险重算三档、版本化与 SUPERSEDED、审批（风险调整 422/200、非最新版本、
非待审批 409）、驳回（原因必填、任务 FAILED 事件序）、驳回后 retry 清空计划、权限矩阵。
"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import main

HEAD_A = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0"
NEUTRAL_BODY = "列表页在窄屏下布局错乱，需要调整样式并补充回归。"
README_CONTENT = "project documentation overview\n"
NEUTRAL_CODE = "def handle():\n    return 1\n"

repository = main.REPOSITORIES["repo_payment_service"]
developer = main.USERS["user_developer"]


@pytest.fixture(autouse=True)
def fast_index_and_snapshot_seams(monkeypatch):
    """索引阶段零等待 + head/快照 seam，供 retry 复用已建索引场景。"""
    monkeypatch.setattr(main, "INDEX_STAGE_SECONDS", 0)
    monkeypatch.setattr(main, "fetch_gitee_branch_head", lambda repo, base_branch=None: HEAD_A)
    monkeypatch.setattr(
        main,
        "sync_repository_snapshot",
        lambda repo, commit_sha: [
            main.CodeFile(path="README.md", language="markdown", content=README_CONTENT),
            # DEV-017：retry 自动检索需要命中与 Issue（列表页样式/布局）相关的代码片段
            main.CodeFile(
                path="src/list_page.css",
                language="css",
                content="/* 列表页窄屏布局与样式修复，补充回归样式规则 */\n.list-page { display: flex; }\n",
            ),
        ],
    )
    yield


def snapshot_of(title: str, body: str, labels: list[str] | None = None) -> main.IssueSnapshotRecord:
    return main.IssueSnapshotRecord(
        title=title,
        body=body,
        comments=[],
        labels=labels or [],
        raw_response={},
        fetched_at=datetime.now(timezone.utc),
    )


def context_of(
    path: str = "services/payment/callback.py",
    content: str = NEUTRAL_CODE,
    *,
    fresh: bool = True,
    chunk_index: int = 0,
) -> main.RetrievalContext:
    return main.RetrievalContext(
        chunk_id=f"chunk_dev009_{chunk_index}",
        file_path=path,
        start_line=1,
        end_line=8,
        language="python",
        content=content,
        score=0.9,
        commit_sha=HEAD_A,
        index_id="idx_dev009_completed",
        fresh=fresh,
        source="milvus",
    )


def seed_analyzed_task(
    *,
    issue_id: str = "IPLAN901",
    title: str = "优化列表页样式",
    body: str = NEUTRAL_BODY,
    labels: list[str] | None = None,
    contexts: list[main.RetrievalContext] | None = None,
    answered_blocking: bool = True,
) -> main.TaskRecord:
    now = datetime.now(timezone.utc)
    task = main.TaskRecord(
        id=f"TASK-{9100 + len(main.TASKS)}",
        version=1,
        project_id=repository.project_id,
        project_name=main.PROJECTS[repository.project_id].name,
        repository_id=repository.id,
        repository_name=repository.name,
        repository_url=repository.url,
        issue_id=issue_id,
        title=title,
        status="CREATED",
        base_branch=repository.default_branch,
        test_command=repository.test_command,
        approval_owner_id=repository.approval_owner_id,
        approval_owner_name=repository.approval_owner_name,
        work_branch=f"agent/main/issue-{issue_id}-v1",
        issue_snapshot=snapshot_of(title, body, labels or ["ui"]),
        automation_state="running",
        manual_required=False,
        manual_reason="none",
        next_action="check_index",
        events=[],
        created_by=developer.id,
        created_by_name=developer.name,
        created_at=now - timedelta(seconds=30),
        updated_at=now,
    )
    main.TASKS[task.id] = task
    if body or True:
        analysis = main.generate_mock_analysis(task)
        if answered_blocking:
            for question in analysis.questions:
                question.answered = True
                question.answer = question.default_answer or "按默认口径处理"
        main.attach_analysis(task, analysis)
    if contexts:
        task.code_contexts = contexts
    return task


def analyzed_without_analysis(issue_id: str = "IPLAN910") -> main.TaskRecord:
    now = datetime.now(timezone.utc)
    task = main.TaskRecord(
        id=f"TASK-{9300 + len(main.TASKS)}",
        version=1,
        project_id=repository.project_id,
        project_name=main.PROJECTS[repository.project_id].name,
        repository_id=repository.id,
        repository_name=repository.name,
        repository_url=repository.url,
        issue_id=issue_id,
        title="缺少分析的任务",
        status="ANALYZED",
        base_branch=repository.default_branch,
        test_command=repository.test_command,
        approval_owner_id=repository.approval_owner_id,
        approval_owner_name=repository.approval_owner_name,
        work_branch=f"agent/main/issue-{issue_id}-v1",
        issue_snapshot=snapshot_of("缺少分析的任务", "占位正文。", ["ui"]),
        automation_state="blocked",
        manual_required=True,
        manual_reason="answer_required",
        next_action="answer_analysis_question",
        events=[],
        created_by=developer.id,
        created_by_name=developer.name,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    main.TASKS[task.id] = task
    return task


def approver_client() -> TestClient:
    test_client = TestClient(main.app)
    login = test_client.post(
        "/api/auth/login",
        json={"account": "approver@company.com", "password": "Approver123!"},
    )
    assert login.status_code == 200
    return test_client


def latest_plan_of(task: main.TaskRecord) -> main.ModificationPlan:
    return max(task.plans, key=lambda plan: plan.version)


def make_completed_index() -> main.RepositoryIndexRecord:
    record = main.new_repository_index(
        repository=repository,
        commit_sha=HEAD_A,
        base_branch=repository.default_branch,
        requested_by=developer,
        requested_at=datetime.now(timezone.utc),
    )
    while record.status in main.INDEX_RUNNING_STAGES:
        main.progress_single_repository_index(record)
    assert record.status == "COMPLETED"
    return record


# ---------- 生成门禁 ----------

def test_plan_generation_requires_analysis(client):
    task = analyzed_without_analysis()
    response = client.post(f"/api/tasks/{task.id}/plans")
    assert response.status_code == 409
    assert "AI 分析缺失" in response.json()["detail"]["message"]


def test_plan_generation_blocked_before_analyzed(client):
    task = seed_analyzed_task(issue_id="IPLAN902")
    task.status = "CONTEXT_INDEXED"
    response = client.post(f"/api/tasks/{task.id}/plans")
    assert response.status_code == 409
    assert "尚未完成 AI 分析" in response.json()["detail"]["message"]


def test_plan_generation_blocked_by_unanswered_blocking_question(client):
    task = seed_analyzed_task(
        issue_id="IPLAN903",
        title="修复支付回调重复记账",
        body="支付回调网络重试时重复入账，需要考虑是否兼容历史无交易号的回调。",
        labels=["bug", "支付"],
        answered_blocking=False,
    )
    response = client.post(f"/api/tasks/{task.id}/plans")
    assert response.status_code == 409
    assert "阻塞问题待确认" in response.json()["detail"]["message"]


def test_plan_generation_blocked_by_stale_context(client):
    task = seed_analyzed_task(
        issue_id="IPLAN904",
        contexts=[context_of(fresh=False, chunk_index=0)],
    )
    response = client.post(f"/api/tasks/{task.id}/plans")
    assert response.status_code == 409
    assert "过期代码上下文" in response.json()["detail"]["message"]


# ---------- 规则兜底生成 ----------

def test_generate_plan_success_without_context(client):
    task = seed_analyzed_task(issue_id="IPLAN905")
    response = client.post(f"/api/tasks/{task.id}/plans")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "WAITING_APPROVAL"
    assert data["automation_state"] == "waiting_approval"
    assert data["manual_required"] is True
    assert data["manual_reason"] == "approval_required"
    assert data["next_action"] == "review_plan"
    assert data["risk_source"] == "plan"
    assert data["risk_level"] == "low"
    plans = data["plans"]
    assert len(plans) == 1
    plan = plans[0]
    assert plan["id"] == f"PLAN-{task.id.removeprefix('TASK-')}-1"
    assert plan["version"] == 1
    assert plan["status"] == "WAITING_APPROVAL"
    assert plan["provider"] == "mvp-rule-based"
    assert plan["goal"]
    assert plan["file_scope"][0]["file_path"] == "unknown"
    assert plan["file_scope"][0]["change_type"] == "unknown"
    assert any("缺少代码上下文" in item for item in plan["assumptions"])
    assert plan["risk_reason"]
    assert len(plan["steps"]) >= 2
    event_types = [event["event_type"] for event in data["events"]]
    assert "PLAN_GENERATED" in event_types


def test_plan_risk_delete_scope_is_high(client):
    task = seed_analyzed_task(
        issue_id="IPLAN906",
        title="废弃旧回调入口",
        body="移除历史无交易号回调的兼容逻辑，删除 legacy 模块。",
        labels=["bug"],
        contexts=[context_of(chunk_index=0)],
    )
    response = client.post(f"/api/tasks/{task.id}/plans")
    assert response.status_code == 200
    plan = response.json()["data"]["plans"][0]
    assert plan["risk_level"] == "high"
    assert plan["file_scope"][0]["change_type"] == "delete"
    assert "删除" in plan["risk_reason"]


def test_plan_risk_many_files_is_medium(client):
    task = seed_analyzed_task(
        issue_id="IPLAN907",
        title="调整设置页样式",
        body="设置页多个区块样式不一致，需要统一。",
        contexts=[
            context_of(path=f"src/pages/settings/section_{index}.vue", content=NEUTRAL_CODE, chunk_index=index)
            for index in range(4)
        ],
    )
    response = client.post(f"/api/tasks/{task.id}/plans")
    assert response.status_code == 200
    plan = response.json()["data"]["plans"][0]
    assert plan["risk_level"] == "medium"
    assert "4 个文件" in plan["risk_reason"]


def test_plan_risk_readme_content_is_low(client):
    task = seed_analyzed_task(
        issue_id="IPLAN908",
        title="完善项目说明文档",
        body="README 缺少安装与接口说明，需要补充。",
        contexts=[context_of(path="README.md", content=README_CONTENT, chunk_index=0)],
    )
    response = client.post(f"/api/tasks/{task.id}/plans")
    assert response.status_code == 200
    plan = response.json()["data"]["plans"][0]
    assert plan["risk_level"] == "low"
    assert plan["risk_reason"]


def test_plan_risk_does_not_overreact_to_issue_text_when_plan_is_neutral(client, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setattr(
        main.plan_service,
        "generate_plan_fields",
        lambda prompt: {
            "goal": "优化帮助页文案提示",
            "file_scope": [
                {
                    "file_path": "src/views/help.vue",
                    "change_type": "modify",
                    "reason": "只调整帮助页静态文案",
                }
            ],
            "steps": [
                {
                    "order": 1,
                    "title": "调整文案",
                    "description": "更新帮助页提示语并保持交互不变",
                    "target_files": ["src/views/help.vue"],
                }
            ],
            "test_suggestions": ["运行前端构建"],
            "assumptions": [],
            "rollback_plan": ["回滚帮助页文案修改"],
        },
    )
    task = seed_analyzed_task(
        issue_id="IPLAN909",
        title="优化文案提示",
        body="Issue 里故意包含 删除、支付、权限、审批 等敏感词，但实际只需要调整文案。",
        contexts=[context_of(path="src/views/help.vue", content="const text = 'hello world'\n", chunk_index=0)],
    )
    response = client.post(f"/api/tasks/{task.id}/plans")
    assert response.status_code == 200
    plan = response.json()["data"]["plans"][0]
    assert plan["risk_level"] == "low"
    assert "删除" not in plan["risk_reason"]
    assert "支付" not in plan["risk_reason"]


# ---------- LLM 草稿合并与兜底 ----------

def test_plan_generation_uses_rule_base_without_llm_key(client):
    task = seed_analyzed_task(issue_id="IPLAN915")
    response = client.post(f"/api/tasks/{task.id}/plans")
    assert response.status_code == 200
    assert response.json()["data"]["plans"][0]["provider"] == "mvp-rule-based"


def test_plan_generation_falls_back_on_invalid_llm_json(client, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setattr(main.plan_service, "generate_plan_fields", lambda prompt: None)
    task = seed_analyzed_task(
        issue_id="IPLAN916",
        contexts=[context_of(path="README.md", content=README_CONTENT, chunk_index=0)],
    )
    response = client.post(f"/api/tasks/{task.id}/plans")
    assert response.status_code == 200
    plan = response.json()["data"]["plans"][0]
    assert plan["provider"] == "mvp-rule-based"
    assert plan["file_scope"][0]["file_path"] == "README.md"
    assert not any("缺少代码上下文" in item for item in plan["assumptions"])


def test_plan_generation_merges_valid_llm_draft_and_recalculates_risk(client, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    llm_fields = {
        "goal": "LLM 起草的修改目标",
        "file_scope": [
            {"file_path": "services/payment/callback.py", "change_type": "modify", "reason": "按 LLM 分析调整幂等分支"},
            {"file_path": "services/payment/validator.py", "change_type": "delete", "reason": "废弃旧校验入口"},
        ],
        "steps": [{"order": 1, "title": "重构入口", "description": "合并回调校验逻辑", "target_files": ["services/payment/callback.py"]}],
        "test_suggestions": ["补充幂等回归"],
        "assumptions": [],
        "rollback_plan": [],
    }
    monkeypatch.setattr(main.plan_service, "generate_plan_fields", lambda prompt: llm_fields)
    task = seed_analyzed_task(issue_id="IPLAN917")
    response = client.post(f"/api/tasks/{task.id}/plans")
    assert response.status_code == 200
    plan = response.json()["data"]["plans"][0]
    assert plan["provider"] == "deepseek"
    assert plan["goal"] == "LLM 起草的修改目标"
    assert len(plan["file_scope"]) == 2
    # 模型只提供草稿，风险重算仍是规则：含 delete 文件 → high
    assert plan["risk_level"] == "high"
    assert response.json()["data"]["risk_level"] == "high"
    assert response.json()["data"]["risk_source"] == "plan"
    assert plan["test_suggestions"] == ["补充幂等回归"]


# ---------- 列表 / 详情 / 版本化 ----------

def test_plan_list_desc_order_and_get_single(client):
    task = seed_analyzed_task(issue_id="IPLAN920")
    response = client.post(f"/api/tasks/{task.id}/plans")
    assert response.status_code == 200
    first = max(response.json()["data"]["plans"], key=lambda plan: plan["version"])
    assert first["version"] == 1
    approver = approver_client()
    rewrite = approver.post(
        f"/api/tasks/{task.id}/plans/{first['id']}/rewrite",
        json={"reason": "文件范围不够具体，请细化"},
    )
    assert rewrite.status_code == 200
    rewrite_data = rewrite.json()["data"]
    by_version = {plan["version"]: plan for plan in rewrite_data["plans"]}
    assert set(by_version) == {1, 2}
    assert by_version[2]["status"] == "WAITING_APPROVAL"
    assert by_version[1]["status"] == "SUPERSEDED"
    assert by_version[1]["id"] == first["id"]
    listing = client.get(f"/api/tasks/{task.id}/plans")
    assert listing.status_code == 200
    assert [plan["version"] for plan in listing.json()["data"]] == [2, 1]
    detail = client.get(f"/api/tasks/{task.id}/plans/{first['id']}")
    assert detail.status_code == 200
    assert detail.json()["data"]["version"] == 1
    assert detail.json()["data"]["status"] == "SUPERSEDED"
    missing = client.get(f"/api/tasks/{task.id}/plans/PLAN-9999-9")
    assert missing.status_code == 404
    other_task = seed_analyzed_task(issue_id="IPLAN921")
    foreign = client.get(f"/api/tasks/{other_task.id}/plans/{first['id']}")
    assert foreign.status_code == 404


def test_rewrite_requires_reason_and_waiting_state(client):
    task = seed_analyzed_task(issue_id="IPLAN922")
    response = client.post(f"/api/tasks/{task.id}/plans")
    plan_id = response.json()["data"]["plans"][0]["id"]
    approver = approver_client()
    empty = approver.post(f"/api/tasks/{task.id}/plans/{plan_id}/rewrite", json={"reason": "   "})
    assert empty.status_code == 422
    bad_state = approver.post(
        f"/api/tasks/{task.id}/plans/{plan_id}/approve",
        json={},
    )
    assert bad_state.status_code == 200
    after_approve = approver.post(f"/api/tasks/{task.id}/plans/{plan_id}/rewrite", json={"reason": "想重写"})
    assert after_approve.status_code == 409
    assert "仅待审批" in after_approve.json()["detail"]["message"]


def test_rewrite_records_user_event_with_reason(client):
    task = seed_analyzed_task(issue_id="IPLAN923")
    response = client.post(f"/api/tasks/{task.id}/plans")
    plan_id = response.json()["data"]["plans"][0]["id"]
    approver = approver_client()
    rewrite = approver.post(
        f"/api/tasks/{task.id}/plans/{plan_id}/rewrite",
        json={"reason": "补充数据库兼容说明后再审"},
    )
    assert rewrite.status_code == 200
    data = rewrite.json()["data"]
    assert data["status"] == "WAITING_APPROVAL"
    rewrite_events = [event for event in data["events"] if event["event_type"] == "PLAN_REWRITTEN"]
    assert len(rewrite_events) == 1
    assert rewrite_events[0]["actor_name"] == data["approval_owner_name"]
    assert any("补充数据库兼容说明" in item for item in rewrite_events[0]["diff_summary"])
    assert rewrite_events[0]["diff_summary"][0] == "计划版本：v2"
    assert rewrite_events[0]["actor_id"] != "agent-system"


# ---------- 审批 ----------

def test_approve_low_risk_without_comment(client):
    task = seed_analyzed_task(issue_id="IPLAN924")
    response = client.post(f"/api/tasks/{task.id}/plans")
    plan_id = response.json()["data"]["plans"][0]["id"]
    approver = approver_client()
    approve = approver.post(f"/api/tasks/{task.id}/plans/{plan_id}/approve", json={})
    assert approve.status_code == 200
    data = approve.json()["data"]
    assert data["status"] == "APPROVED"
    assert data["automation_state"] == "blocked"
    assert data["manual_required"] is False
    assert data["manual_reason"] == "none"
    assert data["next_action"] == "auto_generate_diff"
    assert data["plans"][0]["status"] == "APPROVED"
    assert data["plans"][0]["risk_level"] == "low"
    approve_events = [event for event in data["events"] if event["event_type"] == "PLAN_APPROVED"]
    assert len(approve_events) == 1
    assert approve_events[0]["actor_name"] == data["approval_owner_name"]
    assert approve_events[0]["diff_summary"][1] == "风险等级：low"
    assert "审批意见：（无）" in approve_events[0]["diff_summary"]


def test_approve_risk_adjustment_requires_reason(client):
    task = seed_analyzed_task(issue_id="IPLAN925")
    response = client.post(f"/api/tasks/{task.id}/plans")
    plan_id = response.json()["data"]["plans"][0]["id"]
    approver = approver_client()
    no_reason = approver.post(
        f"/api/tasks/{task.id}/plans/{plan_id}/approve",
        json={"risk_level": "medium"},
    )
    assert no_reason.status_code == 422
    assert "调整原因" in no_reason.json()["detail"]["message"]
    with_reason = approver.post(
        f"/api/tasks/{task.id}/plans/{plan_id}/approve",
        json={"risk_level": "medium", "risk_adjust_reason": "涉及线上数据兜底，建议升级中风险", "comment": "按中风险跟进"},
    )
    assert with_reason.status_code == 200
    data = with_reason.json()["data"]
    assert data["risk_level"] == "medium"
    assert data["risk_source"] == "plan_approval"
    plan = data["plans"][0]
    assert plan["status"] == "APPROVED"
    assert plan["risk_level"] == "medium"
    assert plan["risk_adjust_reason"] == "涉及线上数据兜底，建议升级中风险"
    approve_events = [event for event in data["events"] if event["event_type"] == "PLAN_APPROVED"]
    assert any("low → medium" in item for item in approve_events[0]["diff_summary"])
    assert any("按中风险跟进" in item for item in approve_events[0]["diff_summary"])


def test_approve_rejects_non_latest_version(client):
    task = seed_analyzed_task(issue_id="IPLAN926")
    response = client.post(f"/api/tasks/{task.id}/plans")
    first_id = response.json()["data"]["plans"][0]["id"]
    approver = approver_client()
    rewrite = approver.post(f"/api/tasks/{task.id}/plans/{first_id}/rewrite", json={"reason": "细化范围"})
    assert rewrite.status_code == 200
    old_approve = approver.post(f"/api/tasks/{task.id}/plans/{first_id}/approve", json={})
    assert old_approve.status_code == 409
    assert "只能审批最新版本" in old_approve.json()["detail"]["message"]
    latest = max(rewrite.json()["data"]["plans"], key=lambda plan: plan["version"])
    new_approve = approver.post(f"/api/tasks/{task.id}/plans/{latest['id']}/approve", json={})
    assert new_approve.status_code == 200


def test_approve_rejects_already_finalized_plan(client):
    task = seed_analyzed_task(issue_id="IPLAN927")
    response = client.post(f"/api/tasks/{task.id}/plans")
    plan_id = response.json()["data"]["plans"][0]["id"]
    approver = approver_client()
    first = approver.post(f"/api/tasks/{task.id}/plans/{plan_id}/approve", json={})
    assert first.status_code == 200
    again = approver.post(f"/api/tasks/{task.id}/plans/{plan_id}/approve", json={})
    assert again.status_code == 409
    assert "不在待审批状态" in again.json()["detail"]["message"]


# ---------- 驳回与任务流转 ----------

def test_reject_requires_reason(client):
    task = seed_analyzed_task(issue_id="IPLAN928")
    response = client.post(f"/api/tasks/{task.id}/plans")
    plan_id = response.json()["data"]["plans"][0]["id"]
    approver = approver_client()
    empty = approver.post(f"/api/tasks/{task.id}/plans/{plan_id}/reject", json={"reason": "   "})
    assert empty.status_code == 422
    assert "必须填写原因" in empty.json()["detail"]["message"]


def test_reject_marks_plan_rejected_and_task_failed(client):
    task = seed_analyzed_task(issue_id="IPLAN929")
    response = client.post(f"/api/tasks/{task.id}/plans")
    plan_id = response.json()["data"]["plans"][0]["id"]
    approver = approver_client()
    reject = approver.post(
        f"/api/tasks/{task.id}/plans/{plan_id}/reject",
        json={"reason": "改动范围超出计划目标，先收敛再提"},
    )
    assert reject.status_code == 200
    data = reject.json()["data"]
    assert data["status"] == "FAILED"
    assert data["failure_node"] == "审批驳回"
    assert "先收敛再提" in (data["failure_reason"] or "")
    assert data["manual_required"] is True
    assert data["manual_reason"] == "plan_rejected"
    assert data["next_action"] == "retry_or_cancel"
    assert data["plans"][0]["status"] == "REJECTED"
    tail_events = [event["event_type"] for event in data["events"]][-2:]
    assert tail_events == ["PLAN_REJECTED", "TASK_FAILED"]
    rejected_event = [event for event in data["events"] if event["event_type"] == "PLAN_REJECTED"][0]
    assert any("先收敛再提" in item for item in rejected_event["diff_summary"])


def test_retry_after_reject_clears_plans_and_restarts(client):
    task = seed_analyzed_task(issue_id="IPLAN930")
    response = client.post(f"/api/tasks/{task.id}/plans")
    plan_id = response.json()["data"]["plans"][0]["id"]
    approver = approver_client()
    reject = approver.post(f"/api/tasks/{task.id}/plans/{plan_id}/reject", json={"reason": "方案不满足需求"})
    assert reject.status_code == 200
    make_completed_index()
    retry = client.post(f"/api/tasks/{task.id}/retry")
    assert retry.status_code == 200
    data = retry.json()["data"]
    assert data["status"] == "CONTEXT_INDEXED"
    assert data["plans"] == []
    assert data["analysis"] is None
    # DEV-017：retry 清空旧上下文后由自动检索重新注入（旧手工片段不残留）
    assert data["code_contexts"], "retry 后应自动检索并重新注入代码上下文"
    assert all(context["source"] in {"milvus", "manual", "fallback"} for context in data["code_contexts"])  # 诚实标注：milvus/fallback(显式降级)/manual(人工指定)
    assert data["risk_level"] is None
    assert data["risk_source"] == "pending_ai_analysis"
    retried_events = [event for event in data["events"] if event["event_type"] == "TASK_RETRIED"]
    assert retried_events
    assert any("修改计划" in item for item in retried_events[0]["diff_summary"])


def test_plan_generation_after_retry_works(client):
    task = seed_analyzed_task(issue_id="IPLAN931")
    response = client.post(f"/api/tasks/{task.id}/plans")
    plan_id = response.json()["data"]["plans"][0]["id"]
    approver = approver_client()
    reject = approver.post(f"/api/tasks/{task.id}/plans/{plan_id}/reject", json={"reason": "重新评估"})
    assert reject.status_code == 200
    make_completed_index()
    retry = client.post(f"/api/tasks/{task.id}/retry")
    assert retry.status_code == 200
    data = retry.json()["data"]
    assert data["plans"] == []
    new_analysis = main.generate_mock_analysis(main.TASKS[task.id])
    main.attach_analysis(main.TASKS[task.id], new_analysis)
    regenerate = client.post(f"/api/tasks/{task.id}/plans")
    assert regenerate.status_code == 200
    regenerated = regenerate.json()["data"]
    assert regenerated["plans"][0]["version"] == 1, "retry 后版本号重新从 1 开始"
    assert regenerated["status"] == "WAITING_APPROVAL"


# ---------- 权限矩阵 ----------

def test_permissions_matrix(client):
    task = seed_analyzed_task(issue_id="IPLAN932")
    response = client.post(f"/api/tasks/{task.id}/plans")
    plan_id = response.json()["data"]["plans"][0]["id"]
    approver = approver_client()
    assert approver.get(f"/api/tasks/{task.id}/plans").status_code == 200
    assert approver.post(f"/api/tasks/{task.id}/plans").status_code == 403, "approver 无 task:operate 不能生成计划"
    assert client.post(f"/api/tasks/{task.id}/plans/{plan_id}/approve", json={}).status_code == 403, "developer 无审批权限"
    assert client.post(f"/api/tasks/{task.id}/plans/{plan_id}/reject", json={"reason": "x"}).status_code == 403
    assert client.post(f"/api/tasks/{task.id}/plans/{plan_id}/rewrite", json={"reason": "x"}).status_code == 403
    assert approver.post(f"/api/tasks/{task.id}/plans/{plan_id}/approve", json={}).status_code == 200
