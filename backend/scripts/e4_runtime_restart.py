"""E4-01 真实重启恢复子进程：从真实 DB bootstrap 恢复 RC2 并驱动到完成（跨进程语义）。"""

import os

os.environ.setdefault("USE_SQLALCHEMY", "true")

from app.enterprise_runtime import orchestrator as rt  # noqa: E402
from app.enterprise_runtime import store as s  # noqa: E402
from app.enterprise_runtime.checkpoint import CheckpointStore  # noqa: E402

TASK_ID = os.environ.get("E4_RESTART_TASK_ID", "RC2")


def _ok(step, task, ctx):
    return {"ok": True, "output_summary": "real:" + step.name, "reason": ""}


def main() -> int:
    s.reset_store()
    CheckpointStore().reset()
    from app.db.bootstrap import restore_from_db

    restore_from_db()
    task = rt.rstore.get(TASK_ID)
    if task is None:
        print("RESTART_ERROR task-not-found", flush=True)
        return 2
    print(f"RESTART_RESTORED completed={task.completed_steps} status={task.status.value}", flush=True)
    ex = {n: _ok for n in ("context_fetch", "enterprise_rag_analysis", "plan_generation", "execution", "test_execution")}
    for _ in range(30):
        tt = rt.rstore.get(TASK_ID)
        if tt.status.value in ("succeeded", "failed", "cancelled", "waiting_approval", "waiting_human"):
            break
        rt.run_until_blocked("worker-restart", TASK_ID, ex)
    print(f"RESTART_FINAL {rt.rstore.get(TASK_ID).status.value}", flush=True)
    return 0 if rt.rstore.get(TASK_ID).status.value == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())