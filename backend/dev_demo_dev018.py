"""DEV-018 团队版 7.5 AI 分析与修改计划增强（四源 / 历史参考 / 风险重算）演示种子。

目的：在不改产品代码的前提下，起一份可重复的本地联调实例，用 mock 兜底（无真实 LLM Key）
把一条「四源分析 + 含历史参考与风险变化行的修改计划」的真实任务种出来，供浏览器截图与文档同步。

策略：
- 先种一条同仓库 COMPLETED 历史任务，让目标任务在上下文注入时命中「历史相似任务」来源；
- 目标 Issue 只命中中风险关键词（修复/失败），不命中高风险与阻塞触发词，且受影响面命中安全区域「回调链路」，
  从而让「分析初判=medium → 计划重算=low」的差异自然出现（前端显示风险变化行）；
- 代码快照内容重述 Issue 领域词汇以命中检索，但不含任何计划风险词，保证计划重算为 low。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import uvicorn
from fastapi.testclient import TestClient

from app import main as m

TASK_ID_FILE = Path(".data/dev018-demo-task.json")
HEAD_SHA = "d18d018d018d018d018d018d018d018d018d018d18"

# 代码快照：重述 Issue 领域词汇（保证检索命中），但不含计划风险词（支付/资金/删除/安全/迁移/审批/密钥等）。
CODE_CONTENT = '''\
"""
回调链路超时兜底处理模块。
第三方回调偶发失败、需要强可靠时，在回调服务内部记录状态并触发有限次重试，
保证关键回调不被漏处理，达到幂等，避免重复回调造成重复结果。
处理路径：回调进来 -> 记录状态 -> 触发重试 -> 返回结果。
"""
def handle_callback_timeout(payload):
    record = normalize(payload)
    state = build_retry_state(record)
    attempt = 0
    while attempt < max_retries and state.needs_retry:
        state = trigger_once(state)
        attempt += 1
    persist(state)
    return ok(record)
'''

HISTORICAL_TITLE = "修复回调链路偶发超时失败"
HISTORICAL_BODY = (
    "第三方回调在弱网超时场景下会偶发处理失败，需要在回调服务增加超时兜底与重试，"
    "目标是对失败的回调做幂等重试并记录状态，改动集中在回调服务内部。"
)

TARGET_TITLE = "修复回调链路偶发超时失败"
TARGET_BODY = (
    "第三方回调在弱网超时场景下会偶发处理失败，需要在回调服务增加超时兜底与重试，"
    "避免重复回调未处理。目标是对失败回调做幂等重试并记录状态。"
    "当前改动集中在回调服务内部，范围明确。"
)
TARGET_LABELS = ["bug", "稳定性"]


def snapshot_of(title: str, body: str, labels: list[str]) -> m.IssueSnapshotRecord:
    return m.IssueSnapshotRecord(
        title=title,
        body=body,
        comments=[],
        labels=labels,
        raw_response={"demo": True, "scenario": "dev018"},
        fetched_at=datetime.now(timezone.utc),
    )


def seed_completed_history() -> str:
    repo = m.REPOSITORIES["repo_payment_service"]
    developer = m.USERS["user_developer"]
    now = datetime.now(timezone.utc)
    task = m.TaskRecord(
        id="TASK-HIST-CALLBACK-001",
        version=1,
        project_id=repo.project_id,
        project_name=m.PROJECTS[repo.project_id].name,
        repository_id=repo.id,
        repository_name=repo.name,
        repository_url=repo.url,
        issue_id="IHIST018",
        title=HISTORICAL_TITLE,
        status="COMPLETED",
        base_branch=repo.default_branch,
        test_command=repo.test_command,
        approval_owner_id=repo.approval_owner_id,
        approval_owner_name=repo.approval_owner_name,
        work_branch="agent/main/issue-IHIST018-v1",
        issue_snapshot=snapshot_of(HISTORICAL_TITLE, HISTORICAL_BODY, ["bug", "稳定性"]),
        automation_state="completed",
        manual_required=False,
        manual_reason="none",
        next_action="none",
        events=[],
        created_by=developer.id,
        created_by_name=developer.name,
        created_at=now - timedelta(days=1),
        updated_at=now,
    )
    m.TASKS[task.id] = task
    return task.id


def build_index() -> None:
    repo = m.REPOSITORIES["repo_payment_service"]
    user = m.USERS["user_developer"]
    index = m.new_repository_index(
        repository=repo,
        commit_sha=HEAD_SHA,
        base_branch=repo.default_branch,
        requested_by=user,
        requested_at=datetime.now(timezone.utc),
    )
    while index.status in m.INDEX_RUNNING_STAGES:
        m.progress_single_repository_index(index)


def seed_demo_task() -> str:
    repo = m.REPOSITORIES["repo_payment_service"]
    developer = m.USERS["user_developer"]

    # 1) 固定仓库 head 与代码快照：主题与 Issue 重叠（命中检索），但不含计划风险词。
    m.INDEX_STAGE_SECONDS = 0
    m.fetch_gitee_branch_head = lambda repository, base_branch=None: HEAD_SHA
    m.sync_repository_snapshot = lambda repository, commit_sha: [
        m.CodeFile(path="services/callback/timeout_handler.py", language="python", content=CODE_CONTENT)
    ]

    # 2) 建索引
    build_index()

    # 3) 种已完成历史任务（提供「历史相似任务」来源）
    seed_completed_history()

    # 4) 校验演示预期：上下文检索命中 + 历史相似命中 + 分析中风险 + 计划低风险
    query_text = f"{TARGET_TITLE} {TARGET_BODY} {' '.join(TARGET_LABELS)}"
    code_score = m.retrieval_score(query_text, CODE_CONTENT)
    hist_score = m.retrieval_score(query_text, f"{HISTORICAL_TITLE} {HISTORICAL_BODY}")
    print(f"[dev018] 检索命中预演：代码 {code_score:.2f}（阈值 0.3）；历史相似 {hist_score:.2f}（阈值 0.2）")

    # 5) 创建目标任务，经真实 /api 端点推进到「分析→计划」
    m.fetch_gitee_issue_snapshot = lambda repository, issue_id: snapshot_of(TARGET_TITLE, TARGET_BODY, TARGET_LABELS)
    client = TestClient(m.app)
    login = client.post("/api/auth/login", json={"account": "developer@company.com", "password": "Dev123!"})
    login.raise_for_status()
    response = client.post(
        "/api/tasks",
        json={"project_id": repo.project_id, "repository_id": repo.id, "issue_id": "IDEV018"},
    )
    response.raise_for_status()
    task_id = response.json()["data"]["id"]
    m.progress_task_automation(m.TASKS[task_id])  # CONTEXT_INDEXED -> ANALYSIS_QUEUED

    analysis = client.post(f"/api/tasks/{task_id}/analysis/generate")
    analysis.raise_for_status()
    plans = client.post(f"/api/tasks/{task_id}/plans")
    plans.raise_for_status()

    task = m.TASKS[task_id]
    plan = max(task.plans, key=lambda p: p.version)
    print(
        f"[dev018] task={task_id} status={task.status} "
        f"analysis_risk={task.analysis.suggested_risk_level} "
        f"plan_risk={plan.risk_level} (初判->重算) "
        f"similar_refs={len(task.similar_task_refs)} "
        f"referenced={len(plan.referenced_historical_tasks)} "
        f"approval_owner={task.approval_owner_id}"
    )
    TASK_ID_FILE.parent.mkdir(parents=True, exist_ok=True)
    TASK_ID_FILE.write_text(
        f'{{"task_id":"{task_id}",'
        f'"analysis_risk":"{task.analysis.suggested_risk_level}",'
        f'"plan_risk":"{plan.risk_level}",'
        f'"similar_refs":{len(task.similar_task_refs)}}}',
        encoding="utf-8",
    )
    return task_id


seed_demo_task()
app = m.app

if __name__ == "__main__":
    uvicorn.run("dev_demo_dev018:app", host="127.0.0.1", port=8001, reload=False)