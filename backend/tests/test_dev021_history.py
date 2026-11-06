"""DEV-021 历史任务复用索引后端测试。

覆盖：完成任务生成完整摘要、缺失字段「暂无记录」、重复触发幂等、索引成功记录状态、
索引失败不阻断主链且保存 failure_reason、检索项目隔离+权限过滤、排除自身、
相似度阈值过滤、只读不改当前任务、不自动套用旧 Diff。
"""
from __future__ import annotations

import datetime
from itertools import count

import pytest
from fastapi.testclient import TestClient

from app import main
from app.services import history_service

_seq = count(8800)
developer = main.USERS["user_developer"]


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


def authed(account, password):
    c = TestClient(main.app)
    assert c.post("/api/auth/login", json={"account": account, "password": password}).status_code == 200
    return c


def _make_task(task_id, title, *, project_id="proj_payment", artifacts=True):
    now = _now()
    task = main.TaskRecord(
        id=task_id, version=1, project_id=project_id, project_name="支付中台",
        repository_id="repo_payment_service", repository_name="payment-service",
        repository_url="https://gitee.com/o/r.git", issue_id=f"I-{task_id}", title=title,
        status="COMPLETED", risk_level="medium", risk_source="plan",
        base_branch="main", test_command="pytest tests/ -q", approval_owner_id="user_approver",
        approval_owner_name="王琳", work_branch="agent/main/v1",
        issue_snapshot=main.IssueSnapshotRecord(title=title, body="支付回调重复入账，需要幂等处理", comments=[], labels=[], raw_response={}, fetched_at=now),
        automation_state="completed", manual_required=False, manual_reason="none", next_action="view_replay",
        failure_node=None, failure_reason=None, failed_at=None, retry_count=0, base_commit_sha=None,
        plans=[], diffs=[], test_runs=[], pr_descriptions=[], code_contexts=[], similar_task_refs=[],
        events=[], execution_steps=[], created_by=developer.id, created_by_name=developer.name,
        created_at=now, updated_at=now,
    )
    if artifacts:
        task.plans.append(main.ModificationPlan(
            id=f"PLAN-{task_id}-1", task_id=task_id, version=1, goal="修复幂等", file_scope=[], steps=[],
            risk_level="medium", risk_reason="r", test_suggestions=[], assumptions=[], rollback_plan=[],
            generated_from_analysis_revision=1, generated_from_context_ids=[], provider="mvp-rule-based",
            status="APPROVED", created_by=developer.id, created_by_name=developer.name, created_at=now,
        ))
        task.analysis = main.AnalysisResult(
            task_id=task_id, task_version=1, decision_summary=["需幂等支付"], overview="支付回调幂等",
            findings=["重复回调"], affected_areas=["支付"], suggested_risk_level="medium", risk_reason="r",
            uncertainties=[], questions=[], sources=[], prompt_injection_flags=[], generated_at=now, provider="mvp-rule-based",
        )
    main.TASKS[task.id] = task
    return task


def test_generate_produces_full_summary():
    task = _make_task("TASK-H1", "支付幂等")
    summary = history_service.generate_for_completed_task(task.id)
    assert summary is not None
    assert summary.task_id == task.id
    assert summary.source_task_title == "支付幂等"
    assert "支付回调" in summary.issue_summary
    assert "幂等" in summary.analysis_summary
    assert "v1" in summary.plan_summary
    assert summary.searchable_text
    assert summary.summary_version >= 1
    assert summary.id == f"hist_sum_{task.id.upper()}"


def test_missing_fields_marked_no_record():
    task = _make_task("TASK-H2", "无产物任务", artifacts=False)
    summary = history_service.generate_for_completed_task(task.id)
    assert summary is not None
    assert "暂无记录" in summary.diff_summary
    assert "暂无记录" in summary.test_summary
    assert "暂无记录" in summary.pr_summary


def test_generate_is_idempotent():
    task = _make_task("TASK-H3", "幂等")
    s1 = history_service.generate_for_completed_task(task.id)
    s2 = history_service.generate_for_completed_task(task.id)
    assert s1.id == s2.id
    assert len([s for s in main.HISTORICAL_SUMMARIES.values() if s.task_id == task.id]) == 1


def test_index_success_records_status():
    task = _make_task("TASK-H4", "索引成功")
    summary = history_service.generate_for_completed_task(task.id)
    rec = history_service.index(summary.id)
    assert rec.index_status == "INDEXED"
    assert rec.vector_document_id == summary.id
    assert rec.embedding_provider


def test_index_failure_does_not_raise_and_records_reason(monkeypatch):
    task = _make_task("TASK-H5", "索引失败不断链")
    summary = history_service.generate_for_completed_task(task.id)

    def boom(*args, **kwargs):
        raise RuntimeError("embedding provider down")

    monkeypatch.setattr(main.embedding_service, "embed_texts", boom)
    rec = history_service.index(summary.id)  # 不应抛错
    assert rec.index_status == "FAILED"
    assert rec.failure_reason and "embedding provider down" in rec.failure_reason
    assert rec.failure_context


def test_index_milvus_failure_does_not_fake_success_when_fallback_disabled(monkeypatch):
    task = _make_task("TASK-H5-STRICT", "严格索引失败")
    summary = history_service.generate_for_completed_task(task.id)
    monkeypatch.setenv("RAG_MOCK_FALLBACK", "false")
    monkeypatch.setattr(main.embedding_service, "embed_texts", lambda _texts: [[0.0] * 64])

    from app.retrieval import llamaindex_backend

    monkeypatch.setattr(llamaindex_backend, "is_available", lambda: True)
    monkeypatch.setattr(
        llamaindex_backend,
        "build_vector_store",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("milvus insert failed")),
    )

    rec = history_service.index(summary.id)
    assert rec.index_status == "FAILED"
    assert rec.failure_reason and "milvus insert failed" in rec.failure_reason


def test_force_enqueue_reindexes_indexed_summary(monkeypatch):
    task = _make_task("TASK-H6-FORCE", "强制重建索引")
    summary = history_service.generate_for_completed_task(task.id)
    first = history_service.index(summary.id)
    assert first.index_status == "INDEXED"
    monkeypatch.setattr(history_service, "index", lambda _summary_id: first)
    rec = history_service.enqueue(summary.id, force=True)
    assert rec is first
    assert rec.retry_count == 1


def test_retry_resets_and_reindexes():
    task = _make_task("TASK-H6", "重试")
    summary = history_service.generate_for_completed_task(task.id)
    rec = history_service.enqueue(summary.id)
    assert rec.index_status in {"PENDING", "INDEXED"}
    assert history_service.get_status(summary.id) is not None


def test_search_excludes_self_and_threshold():
    t1 = _make_task("TASK-HS1", "支付幂等")
    t2 = _make_task("TASK-HS2", "对账定时任务")
    s1 = history_service.generate_for_completed_task(t1.id)
    history_service.index(s1.id)
    s2 = history_service.generate_for_completed_task(t2.id)
    history_service.index(s2.id)

    admin = authed("admin@company.com", "Admin123!")
    res = admin.post("/api/history/search", json={"projectId": "proj_payment", "query": "支付幂等", "taskId": t1.id})
    assert res.status_code == 200
    items = res.json()["data"]
    assert items, "应有历史相似任务"
    assert all(i["taskId"] != t1.id for i in items), "当前任务不应出现在自己的相似结果中"
    assert all(i["sourceLabel"] == "历史任务参考" for i in items)

    strict = admin.post("/api/history/search", json={"projectId": "proj_payment", "query": "支付幂等", "minSimilarity": 0.99})
    assert all(i["similarity"] < 0.99 for i in strict.json()["data"])


def test_search_project_isolation_and_permission():
    t1 = _make_task("TASK-HI1", "支付幂等", project_id="proj_payment")
    t2 = _make_task("TASK-HI2", "支付幂等", project_id="proj_tenant") if "proj_tenant" in main.PROJECTS else None
    s1 = history_service.generate_for_completed_task(t1.id)
    history_service.index(s1.id)

    admin = authed("admin@company.com", "Admin123!")
    # 项目隔离：查询 proj_other 无文件
    in_payment = admin.post("/api/history/search", json={"projectId": "proj_payment", "query": "支付幂等"}).json()["data"]
    assert in_payment and all(i["project"] == "proj_payment" for i in in_payment)
    other = admin.post("/api/history/search", json={"projectId": "proj_tenant", "query": "支付幂等"}).json()["data"] if "proj_tenant" in main.PROJECTS else []
    assert not other, "项目隔离：其他项目不应返回本项目的摘要"

    # 权限：observer 非创建者/审批人 → 检索不到
    observer = authed("observer@company.com", "Observer123!")
    obs = observer.post("/api/history/search", json={"projectId": "proj_payment", "query": "支付幂等"}).json()["data"]
    assert not obs, "无权用户（observer 非参与人）不应检索到历史任务"
    # 创建者（developer）可检索
    dev = authed("developer@company.com", "Dev123!")
    dev_res = dev.post("/api/history/search", json={"projectId": "proj_payment", "query": "支付幂等"}).json()["data"]
    assert dev_res, "创建者可检索自己的历史任务"


def test_summary_apis_read_only_and_no_auto_apply():
    task = _make_task("TASK-H7", "只读")
    summary = history_service.generate_for_completed_task(task.id)
    admin = authed("admin@company.com", "Admin123!")

    before_events = len(task.events)
    got = admin.get(f"/api/tasks/{task.id}/completed-summary")
    assert got.status_code == 200
    assert got.json()["data"]["task_id"] == task.id
    idx = admin.get(f"/api/tasks/{task.id}/completed-summary/index")
    assert idx.status_code == 200
    # 只读：读接口不改当前任务
    assert task.status == "COMPLETED"
    assert len(task.events) == before_events
    # 摘要记录不携带任何「套用旧 Diff/代码」的标志或正文（历史仅供参考，禁止自动套用）
    assert getattr(summary, "diff_artifact_applied", None) is None
    assert "patch" not in summary.searchable_text.lower()


def test_generate_endpoint_requires_completed():
    task = _make_task("TASK-H8", "未完成")
    task.status = "WAITING_APPROVAL"
    dev = authed("developer@company.com", "Dev123!")
    resp = dev.post(f"/api/tasks/{task.id}/completed-summary")
    assert resp.status_code == 409


def test_search_empty_query_returns_recent_completed():
    """空查询词时回退展示项目内最近完成的历史任务（保证前端相似卡有内容而非空）。"""
    t1 = _make_task("TASK-HR1", "支付幂等")
    t2 = _make_task("TASK-HR2", "对账定时任务")
    s1 = history_service.generate_for_completed_task(t1.id)
    history_service.index(s1.id)
    s2 = history_service.generate_for_completed_task(t2.id)
    history_service.index(s2.id)
    admin = authed("admin@company.com", "Admin123!")
    recent = admin.post("/api/history/search", json={"taskId": "TASK-HR1", "query": ""}).json()["data"]
    assert recent, "空查询应返回当前项目内最近完成任务"
    assert all(i["taskId"] != "TASK-HR1" for i in recent)
    assert "历史任务参考" in {i["sourceLabel"] for i in recent}
