"""DEV-017 自动上下文补齐演示服务器。

目的：在不改产品代码的前提下，起一份可重复的本地联调实例，
把一条真实的 context_missing 任务种出来，供浏览器截图和文档同步。
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import uvicorn
from fastapi.testclient import TestClient

from app import main as m


TASK_ID_FILE = Path(".data/dev017-demo-task.json")
HEAD_SHA = "d17d170017d170017d170017d170017d170017d1"


def unrelated_snapshot(title: str, body: str, labels: list[str] | None = None) -> m.IssueSnapshotRecord:
    return m.IssueSnapshotRecord(
        title=title,
        body=body,
        comments=[],
        labels=labels or ["bug", "ui"],
        raw_response={"demo": True, "scenario": "dev017"},
        fetched_at=datetime.now(timezone.utc),
    )


def seed_demo_task() -> str:
    repo = m.REPOSITORIES["repo_payment_service"]
    user = m.USERS["user_developer"]
    now = datetime.now(timezone.utc)

    # 1) 固定仓库 head 与无关代码快照，确保后续检索不会命中。
    m.fetch_gitee_branch_head = lambda repository, base_branch=None: HEAD_SHA
    m.sync_repository_snapshot = lambda repository, commit_sha: [
        m.CodeFile(
            path="src/theme.ts",
            language="typescript",
            content=(
                "export const theme = {\n"
                "  primary: '#1f6feb',\n"
                "  radius: 8,\n"
                "}\n"
            ),
        )
    ]

    index = m.new_repository_index(
        repository=repo,
        commit_sha=HEAD_SHA,
        base_branch=repo.default_branch,
        requested_by=user,
        requested_at=now,
    )
    while index.status in m.INDEX_RUNNING_STAGES:
        m.progress_single_repository_index(index)

    # 2) Issue 快照与索引内容主题不相关，任务创建后应自动转 context_missing。
    m.fetch_gitee_issue_snapshot = lambda repository, issue_id: unrelated_snapshot(
        "修复登录页刷新后误跳转",
        "登录页刷新后误跳转到登录页，当前问题与仓库核心代码无明显直接关联，需人工补充上下文。",
        ["ui", "bug"],
    )

    client = TestClient(m.app)
    login = client.post("/api/auth/login", json={"account": "developer@company.com", "password": "Dev123!"})
    login.raise_for_status()
    response = client.post(
        "/api/tasks",
        json={
            "project_id": repo.project_id,
            "repository_id": repo.id,
            "issue_id": "IDEV017",
        },
    )
    response.raise_for_status()
    task = response.json()["data"]
    task_id = task["id"]
    TASK_ID_FILE.parent.mkdir(parents=True, exist_ok=True)
    TASK_ID_FILE.write_text(
        f'{{"task_id":"{task_id}","request_no":null,"status":"{task["status"]}"}}',
        encoding="utf-8",
    )
    print(f"[dev017] seeded task {task_id} status={task['status']} manual_reason={task.get('manual_reason')}")
    return task_id


task_id = seed_demo_task()
app = m.app


if __name__ == "__main__":
    uvicorn.run("dev_demo_dev017:app", host="127.0.0.1", port=8001, reload=False)
