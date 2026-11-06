"""DEV-010 审批模块联调演示服务器：以验收桩语义创建任务并验证「审批人绑定/指派/审批记录」。

API 走查（等价 curl，断言失败即退出）：
- 审批中心范围：approver 只见指派给自己的任务；admin 全量；lead 空数组；developer 403
- 动作归属：非 owner approver approve -> 403 且状态不变；owner approve 落库 approved_* 与风险调整字段
- admin 越权兜底：approve 非自己指派的任务 -> 200，approved_by 记录 admin
- ownerless 链路：仓库不设默认审批人 -> 任务继承空审批人 -> 生成计划 409 -> lead 指派 -> 生成成功
- 改派：admin 改派审批人 -> APPROVAL_OWNER_CHANGED 事件、审批中心队列归属切换

最终状态（供浏览器联调与真实截图）：
- TA：ownerless 仓库 ISMALL，owner 陈晓，WAITING_APPROVAL（指派链路产物）
- TB：ILOW1，owner 陈晓，WAITING_APPROVAL，计划 created_at 回拨 26h（逾期角标/催办演示）
- TC：IPAY1，owner 李静（admin 改派产物），WAITING_APPROVAL（隔离演示）
- TD：ownerless 仓库 ILOW1，ANALYZED、未指派（详情页指派控件演示）
- TE：IACC1，APPROVED（admin 越权审批 + 风险调整 高 -> 低，详情页审批记录截图）
"""
from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta, timezone

import uvicorn
from fastapi.testclient import TestClient

from acceptance_stub import app  # noqa: F401 —— 导入即注入 gitee/快照/head 桩
from app import main as m

DEVELOPER = {"account": "developer@company.com", "password": "Dev123!"}
APPROVER = {"account": "approver@company.com", "password": "Approver123!"}
APPROVER2 = {"account": "approver2@company.com", "password": "Appr210!"}
ADMIN = {"account": "admin@company.com", "password": "Admin123!"}
LEAD = {"account": "lead@company.com", "password": "Lead123!"}
REPO_OWNERLESS = "repo_no_approver"
POLL_SECONDS = 1
MAX_WAIT_SECONDS = 300


def out(message: str) -> None:
    print(message, flush=True)


def login(creds: dict[str, str]) -> TestClient:
    client = TestClient(app)
    response = client.post("/api/auth/login", json=creds)
    assert response.status_code == 200, response.text
    return client


def create_task(client: TestClient, repository_id: str, issue_id: str) -> dict:
    repo = m.REPOSITORIES[repository_id]
    response = client.post(
        "/api/tasks",
        json={"project_id": repo.project_id, "repository_id": repo.id, "issue_id": issue_id},
    )
    if response.status_code != 200:
        raise SystemExit(f"create {issue_id} failed: {response.status_code} {response.text}")
    task = response.json()["data"]
    out(
        f"[create] {task['id']} <- {repository_id} issue {issue_id}（{task['title']}）"
        f"status={task['status']} owner={task.get('approval_owner_name') or '未指派'}"
    )
    return task


def get_task(client: TestClient, task_id: str) -> dict:
    response = client.get(f"/api/tasks/{task_id}")
    assert response.status_code == 200, response.text
    return response.json()["data"]


def unanswered_blocking(task: dict) -> list[dict]:
    questions = (task.get("analysis") or {}).get("questions", [])
    return [q for q in questions if q.get("blocking") and not q.get("answered")]


def answer_question(client: TestClient, task_id: str, question: dict) -> None:
    answer = (question.get("default_answer") or "").strip() or "按默认口径处理，继续推进"
    response = client.post(
        f"/api/tasks/{task_id}/analysis/questions/{question['id']}/answer",
        json={"answer": answer},
    )
    if response.status_code not in (200, 409):
        raise SystemExit(f"answer {question['id']} failed: {response.status_code} {response.text}")


def advance_to_analyzed(client: TestClient, task_id: str) -> dict:
    deadline = time.time() + MAX_WAIT_SECONDS
    while time.time() < deadline:
        task = get_task(client, task_id)
        if task["status"] == "FAILED":
            raise SystemExit(f"{task_id} FAILED: {task.get('failure_reason')}")
        if task["status"] == "ANALYZED":
            blocking = unanswered_blocking(task)
            if blocking:
                for question in blocking:
                    answer_question(client, task_id, question)
                time.sleep(1)
                continue
            return task
        time.sleep(POLL_SECONDS)
    raise SystemExit(f"{task_id} 等待 ANALYZED 超时（{MAX_WAIT_SECONDS}s）")


def generate_plan(client: TestClient, task_id: str, *, expect_conflict: bool = False) -> dict:
    response = client.post(f"/api/tasks/{task_id}/plans")
    if expect_conflict:
        assert response.status_code == 409, f"{task_id} 预期 409 实际 {response.status_code}: {response.text}"
        message = response.json()["detail"]["message"]
        assert "指派" in message, f"409 文案未提示指派: {message}"
        out(f"[guard ] {task_id} 生成计划 409：{message}")
        return get_task(client, task_id)
    if response.status_code != 200:
        raise SystemExit(f"generate plan {task_id} failed: {response.status_code} {response.text}")
    data = response.json()["data"]
    assert data["status"] == "WAITING_APPROVAL", data
    latest = max(data["plans"], key=lambda plan: plan["version"])
    out(
        f"[plan  ] {task_id} v{latest['version']} status={latest['status']} "
        f"risk={latest['risk_level']} owner={data['approval_owner_name']}"
    )
    return data


def pending_ids(client: TestClient) -> set[str]:
    response = client.get("/api/approvals/pending")
    assert response.status_code == 200, response.text
    return {task["id"] for task in response.json()["data"]}


def assign_owner(client: TestClient, task_id: str, user_id: str, reason: str) -> dict:
    response = client.post(
        f"/api/tasks/{task_id}/approval-owner",
        json={"user_id": user_id, "reason": reason},
    )
    if response.status_code != 200:
        raise SystemExit(f"assign {task_id} -> {user_id} failed: {response.status_code} {response.text}")
    return response.json()["data"]


def prepare() -> None:
    # 注入第二个审批人「李静」与 ownerless 仓库（仅本进程生效，不动种子）
    m.USERS["user_approver2"] = m.UserRecord(
        id="user_approver2",
        name="李静",
        email="approver2@company.com",
        role="approver",
        password_hash=m.hash_password("Appr210!"),
    )
    m.USERS_BY_EMAIL = {user.email: user for user in m.USERS.values()}
    now = datetime.now(timezone.utc)
    m.REPOSITORIES[REPO_OWNERLESS] = m.RepositoryConfigRecord(
        id=REPO_OWNERLESS,
        project_id="proj_payment",
        name="no-approver-demo",
        url="https://gitee.com/company/no-approver-demo.git",
        default_branch="main",
        token_alias="gitee-demo-token",
        token_configured=True,
        token_updated_at=now - timedelta(days=1),
        test_command="pytest tests/ -q",
        command_whitelist=["pytest", "npm test", "pnpm test"],
        approval_owner_id=None,
        approval_owner_name=None,
        approval_mode="single",
        risk_threshold="medium",
        max_files=12,
        max_retries=2,
        blocked_paths=[".env", "infra/production/", "secrets/"],
        created_at=now - timedelta(days=5),
        updated_at=now - timedelta(days=1),
    )
    m.REPOSITORY_TOKENS[REPO_OWNERLESS] = "demo-token-not-returned"

    dev = login(DEVELOPER)
    approver = login(APPROVER)
    approver2 = login(APPROVER2)
    admin = login(ADMIN)
    lead = login(LEAD)
    out(f"[boot  ] 服务端就绪：tasks={len(m.TASKS)}，开始准备 DEV-010 演示状态")

    # TD：ownerless + 未指派（详情页指派控件演示），先生成计划验证 409 守卫
    task_d = create_task(dev, REPO_OWNERLESS, "ILOW1")
    advance_to_analyzed(dev, task_d["id"])
    task_d = generate_plan(dev, task_d["id"], expect_conflict=True)
    assert task_d["approval_owner_id"] is None, task_d

    # TA：ownerless 指派链路 —— lead 指派陈晓 -> 生成计划成功
    task_a = create_task(dev, REPO_OWNERLESS, "IKCTH7")
    advance_to_analyzed(dev, task_a["id"])
    assert task_a["approval_owner_id"] is None, task_a
    task_a = assign_owner(lead, task_a["id"], "user_approver", "仓库未配置默认审批人，指派陈晓处理身份证校验修复")
    assert task_a["approval_owner_name"] == "陈晓", task_a
    event_types = {event["event_type"] for event in task_a["events"]}
    assert "APPROVAL_OWNER_CHANGED" in event_types, task_a["events"]
    out("[assign] lead 指派 TA -> 陈晓（APPROVAL_OWNER_CHANGED 事件落库）")
    task_a = generate_plan(dev, task_a["id"])

    # TB：普通仓库默认审批人继承（陈晓），计划回拨 26h 制造逾期
    task_b = create_task(dev, "repo_payment_service", "ILOW1")
    advance_to_analyzed(dev, task_b["id"])
    task_b = generate_plan(dev, task_b["id"])
    assert task_b["approval_owner_id"] == "user_approver", task_b

    # TC：改派链路 —— admin 把默认继承的陈晓改派为李静（轮值切换）
    task_c = create_task(dev, "repo_payment_service", "IPAY1")
    advance_to_analyzed(dev, task_c["id"])
    task_c = generate_plan(dev, task_c["id"])
    task_c = assign_owner(admin, task_c["id"], "user_approver2", "本周审批轮值由李静处理")
    assert task_c["approval_owner_name"] == "李静", task_c
    event_types = {event["event_type"] for event in task_c["events"]}
    assert "APPROVAL_OWNER_CHANGED" in event_types, task_c["events"]
    out("[assign] admin 改派 TC：陈晓 -> 李静（APPROVAL_OWNER_CHANGED 事件落库）")

    # TE：admin 越权兜底 + 风险调整落库（owner 为陈晓，先验证非 owner 403）
    task_e = create_task(dev, "repo_payment_service", "IACC1")
    advance_to_analyzed(dev, task_e["id"])
    task_e = generate_plan(dev, task_e["id"])
    latest_e = max(task_e["plans"], key=lambda plan: plan["version"])
    response = approver2.post(
        f"/api/tasks/{task_e['id']}/plans/{latest_e['id']}/approve", json={"comment": "非本人任务，应被拒绝"}
    )
    assert response.status_code == 403, f"非 owner 预期 403 实际 {response.status_code}: {response.text}"
    assert get_task(dev, task_e["id"])["status"] == "WAITING_APPROVAL"
    out("[guard ] 非 owner approver（李静）approve TE -> 403，任务状态不变")
    response = admin.post(
        f"/api/tasks/{task_e['id']}/plans/{latest_e['id']}/approve",
        json={
            "comment": "安全漏洞已确认修复范围，越权兜底通过；按低风险跟进。",
            "risk_level": "low",
            "risk_adjust_reason": "修复仅限回调鉴权入口，计划与测试建议充分，降为低风险",
        },
    )
    if response.status_code != 200:
        raise SystemExit(f"admin 越权 approve 失败: {response.status_code} {response.text}")
    task_e = response.json()["data"]
    assert task_e["status"] == "APPROVED", task_e
    assert task_e["risk_level"] == "low" and task_e["risk_source"] == "plan_approval", task_e
    approved_e = max(task_e["plans"], key=lambda plan: plan["version"])
    assert approved_e["approved_by_id"] == "user_admin"
    assert approved_e["approved_by_name"] == "林岚"
    assert approved_e["approved_at"] is not None
    assert "越权兜底通过" in approved_e["approval_comment"]
    assert approved_e["risk_adjust_from"] == "high"
    assert approved_e["risk_level"] == "low"
    assert "降为低风险" in approved_e["risk_adjust_reason"]
    out("[approve] admin 越权 approve TE：approved_by=林岚，风险 high -> low（risk_adjust_from 落库）")

    # 审批中心范围走查
    approver_ids = pending_ids(approver)
    assert {task_a["id"], task_b["id"]} <= approver_ids, approver_ids
    assert task_c["id"] not in approver_ids and task_e["id"] not in approver_ids, approver_ids
    out(f"[queue ] 陈晓（owner 范围）pending={sorted(approver_ids)}（含 TA/TB，不含 TC/TE）")
    approver2_ids = pending_ids(approver2)
    assert approver2_ids == {task_c["id"]}, approver2_ids
    out(f"[queue ] 李静（owner 范围）pending={sorted(approver2_ids)}（仅 TC）")
    admin_ids = pending_ids(admin)
    assert admin_ids == {task_a["id"], task_b["id"], task_c["id"]}, admin_ids
    out(f"[queue ] 林岚（admin 全量）pending={sorted(admin_ids)}（TE 已审批不在列）")
    lead_pending = pending_ids(lead)
    assert lead_pending == set(), lead_pending
    out("[queue ] 王琳（lead 只读）pending=[]")
    response = login(DEVELOPER).get("/api/approvals/pending")
    assert response.status_code == 403, f"developer 预期 403 实际 {response.status_code}"
    out("[guard ] developer 访问审批中心 -> 403")

    # 逾期制造：TB 最新 WAITING 计划回拨 26h（前端按计划 created_at 计算逾期）
    record = m.TASKS[task_b["id"]]
    latest_b = max(record.plans, key=lambda plan: plan.version)
    assert latest_b.status == "WAITING_APPROVAL"
    latest_b.created_at = now - timedelta(hours=26)
    out(f"[overdue] TB 最新计划 created_at 回拨 26h（{latest_b.created_at.isoformat()}）")

    out("[done  ] DEV-010 演示状态准备完成：tasks=" + str(len(m.TASKS)))
    for task in sorted(m.TASKS.values(), key=lambda t: t.id):
        latest_plan = max(task.plans, key=lambda plan: plan.version) if task.plans else None
        out(
            f"    {task.id} [{task.status}] owner={task.approval_owner_name or '未指派'} "
            f"risk={task.risk_level} plans={len(task.plans)}"
            f"{'（v' + str(latest_plan.version) + ' ' + latest_plan.status + '）' if latest_plan else ''}"
        )


if __name__ == "__main__":
    prepare()
    out("[serve ] 启动 uvicorn http://127.0.0.1:8000")
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")
    sys.exit(0)
