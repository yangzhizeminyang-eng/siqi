"""DEV-009 联调演示服务器：以验收桩语义创建任务并推进到「修改计划/审批」前置状态。

准备状态（供浏览器联调与真实截图）：
- 任务 A（IPAY1 支付回调重复记账）：ANALYZED、无计划 —— 详情页演示「生成修改计划」按钮
- 任务 B（IACC1 支付回调权限校验缺失）：ANALYZED + 计划 v1 —— 审批驳回演示
- 任务 C（ILOW1 首页文案）：ANALYZED + 计划 v1 —— 低风险一键通过演示
- 任务 D（IKCTH7 身份证校验）：ANALYZED、存在未答阻塞问题 —— 问题确认面板截图

推进方式全部走真实 API（TestClient + Cookie 会话），等价于 curl 联调。
"""
from __future__ import annotations

import sys
import time

import uvicorn
from fastapi.testclient import TestClient

from acceptance_stub import app  # noqa: F401 —— 导入即注入 gitee/快照/head 桩
from app import main as m

DEVELOPER = {"account": "developer@company.com", "password": "Dev123!"}
APPROVER = {"account": "approver@company.com", "password": "Approver123!"}
REPO_ID = "repo_payment_service"
POLL_SECONDS = 1
MAX_WAIT_SECONDS = 240


def out(message: str) -> None:
    print(message, flush=True)


def login(client: TestClient, creds: dict[str, str]) -> dict[str, str]:
    response = client.post("/api/auth/login", json=creds)
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    return {"cookies": dict(client.cookies), "data": data}


def create_task(client: TestClient, issue_id: str) -> dict:
    repo = m.REPOSITORIES[REPO_ID]
    response = client.post(
        "/api/tasks",
        json={"project_id": repo.project_id, "repository_id": repo.id, "issue_id": issue_id},
    )
    if response.status_code != 200:
        raise SystemExit(f"create {issue_id} failed: {response.status_code} {response.text}")
    task = response.json()["data"]
    out(f"[create] {task['id']} <- issue {issue_id}（{task['title']}）status={task['status']}")
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
    out(f"[answer ] {task_id} q={question['id']} answered（blocking={question.get('blocking')}）")


def advance_to_analyzed(
    client: TestClient,
    task_id: str,
    *,
    answer_blocking: bool,
) -> dict:
    deadline = time.time() + MAX_WAIT_SECONDS
    while time.time() < deadline:
        task = get_task(client, task_id)
        if task["status"] == "FAILED":
            raise SystemExit(f"{task_id} FAILED: {task.get('failure_reason')}")
        if task["status"] == "ANALYZED":
            blocking = unanswered_blocking(task)
            if answer_blocking and blocking:
                for question in blocking:
                    answer_question(client, task_id, question)
                time.sleep(1)
                continue
            if not answer_blocking and blocking:
                out(f"[state ] {task_id} ANALYZED，保留 {len(blocking)} 个未答阻塞问题（供问题面板截图）")
                return task
            if not blocking:
                return task
        time.sleep(POLL_SECONDS)
    raise SystemExit(f"{task_id} 等待 ANALYZED 超时（{MAX_WAIT_SECONDS}s）")


def generate_plan(client: TestClient, task_id: str) -> dict:
    response = client.post(f"/api/tasks/{task_id}/plans")
    if response.status_code != 200:
        raise SystemExit(f"generate plan {task_id} failed: {response.status_code} {response.text}")
    data = response.json()["data"]
    assert data["status"] == "WAITING_APPROVAL"
    assert data["next_action"] == "review_plan"
    latest = max(data["plans"], key=lambda plan: plan["version"])
    out(
        f"[plan  ] {task_id} v{latest['version']} status={latest['status']} "
        f"risk={latest['risk_level']} provider={latest['provider']} files={len(latest['file_scope'])}"
    )
    return data


def prepare() -> None:
    client = TestClient(app)
    login(client, DEVELOPER)
    out(f"[boot  ] 服务端就绪：tasks={len(m.TASKS)}，开始推进演示任务")

    # D 先建：保留未答阻塞问题（不回答、不生成计划）
    task_d = create_task(client, "IKCTH7")
    advance_to_analyzed(client, task_d["id"], answer_blocking=False)

    # A：阻塞问题全部按默认口径回答，停在 ANALYZED，计划由浏览器「生成修改计划」按钮触发
    task_a = create_task(client, "IPAY1")
    state_a = advance_to_analyzed(client, task_a["id"], answer_blocking=True)
    assert state_a["manual_required"] is False, state_a

    # B：推进 + 计划 v1（审批驳回演示）
    task_b = create_task(client, "IACC1")
    advance_to_analyzed(client, task_b["id"], answer_blocking=True)
    generate_plan(client, task_b["id"])

    # C：推进 + 计划 v1（低风险一键通过演示）
    task_c = create_task(client, "ILOW1")
    state_c = advance_to_analyzed(client, task_c["id"], answer_blocking=True)
    generate_plan(client, task_c["id"])

    out(f"[done  ] 状态准备完成：tasks={len(m.TASKS)}")
    for task in sorted(m.TASKS.values(), key=lambda t: t.id):
        latest_plan = max(task.plans, key=lambda plan: plan.version) if task.plans else None
        out(
            f"    {task.id} [{task.status}] next={task.next_action} manual={task.manual_reason} "
            f"risk={task.risk_level} plans={len(task.plans)}"
            f"{'（v' + str(latest_plan.version) + ' ' + latest_plan.status + '）' if latest_plan else ''}"
        )


if __name__ == "__main__":
    prepare()
    out("[serve ] 启动 uvicorn http://127.0.0.1:8000")
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")
    sys.exit(0)
