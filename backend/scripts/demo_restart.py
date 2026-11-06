"""P0 重启恢复演示：两个独立进程验证「造数据 → 重启进程 → 数据仍在」。

用法（working dir = backend，注意必须先在 .venv 内执行）：
    python -m scripts.demo_restart write <db_path>
    python -m scripts.demo_restart read  <db_path>

第一个进程写入一个任务并持久化；第二个进程（模拟重启）通过 restore_from_db 恢复，
若任务标题一致即证明核心业务数据不随进程结束而丢失。
"""

from __future__ import annotations

import os
import sys

DATABASE_URL_TPL = "sqlite:///{db_path}"


def build_task(main) -> object:
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    return main.TaskRecord(
        id="TASK-restart-demo-1",
        version=1,
        project_id="proj_mvp",
        project_name="MVP 演示项目",
        repository_id="repo_payment_service",
        repository_name="payment_service",
        repository_url="https://gitee.com/acme/payment_service",
        issue_id="#7",
        title="修复支付回调幂等（重启恢复演示）",
        status="PLAN_GENERATED",
        risk_level="medium",
        risk_source="plan",
        base_branch="main",
        test_command="pytest -q",
        approval_owner_id="user_approver",
        approval_owner_name="审批人",
        work_branch="feature/demo-restart",
        issue_snapshot=main.IssueSnapshotRecord(
            title="修复支付回调幂等",
            body="回调重复投递时不产生重复入账",
            comments=[],
            labels=["bug"],
            raw_response={"id": 7},
            fetched_at=now,
        ),
        automation_state="running",
        manual_required=False,
        manual_reason="none",
        next_action="wait_approval",
        code_contexts=[],
        similar_task_refs=[],
        plans=[],
        diffs=[],
        test_runs=[],
        pr_descriptions=[],
        events=[
            main.AuditEventRecord(
                id="ev_demo",
                event_type="TASK_CREATED",
                resource_type="task",
                resource_id="TASK-restart-demo-1",
                actor_id="user_developer",
                actor_name="开发者",
                summary="创建任务（重启恢复演示）",
                diff_summary=[],
                created_at=now,
                execution_step_ids=[],
            )
        ],
        execution_steps=[],
        created_by="user_developer",
        created_by_name="开发者",
        created_at=now,
        updated_at=now,
    )


def main() -> None:
    if len(sys.argv) != 3:
        print("usage: python -m scripts.demo_restart {write|read} <db_path>")
        return
    cmd, db_path = sys.argv[1], sys.argv[2]
    os.environ["USE_SQLALCHEMY"] = "true"
    os.environ["DATABASE_URL"] = DATABASE_URL_TPL.format(db_path=os.path.abspath(db_path))

    from app import main
    from app.db.bootstrap import restore_from_db
    from app.db.repositories import upsert_team_request, upsert_task
    from app.db.session import create_schema

    create_schema()

    if cmd == "write":
        from datetime import datetime, timezone

        task = build_task(main)
        upsert_task(task)
        req = main.TeamRequestRecord(
            id="REQ-demo-1",
            request_no="REQ-1001",
            project_id="proj_mvp",
            project_name="MVP 演示项目",
            repository_id="repo_payment_service",
            repository_name="payment_service",
            type="bug",
            title="回调幂等(配套团队请求)",
            body="正文",
            acceptance_criteria=["不重复入账"],
            sync_status="SYNCED",
            content_sufficient=True,
            validation_warnings=[],
            created_by_id="user_developer",
            created_by_name="开发者",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        upsert_team_request(req)
        print(f"[write] 已写入任务 {task.id} 与团队请求 {req.id}，进程退出（模拟服务停止）")
        return

    if cmd == "read":
        counts = restore_from_db()
        task = main.TASKS.get("TASK-restart-demo-1")
        req = main.TEAM_REQUESTS.get("REQ-demo-1")
        ok = task is not None and req is not None
        print(f"[read] restore_from_db 各 store 恢复数量：{counts}")
        if task:
            print(f"[read] 恢复任务：{task.id} 状态={task.status} 标题={task.title!r}")
            print(f"[read] 恢复任务事件：{len(task.events)} 条；计划：{len(task.plans)} 份")
        if req:
            print(f"[read] 恢复团队请求：{req.id} 编号={req.request_no} 类型={req.type}")
        print("[READ-RESTORE] " + ("PASS：重启后核心数据仍在" if ok else "FAIL：数据未恢复"))
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()