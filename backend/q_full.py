from sqlalchemy import create_engine, text
url=None
for line in open(".env", encoding="utf-8"):
    line=line.strip()
    if line.startswith("DATABASE_URL") and "=" in line:
        url=line.split("=",1)[1].strip().strip("'\"")
eng=create_engine(url, pool_pre_ping=True)
with eng.connect() as c:
    print("=== 最近5任务 ===")
    for r in c.execute(text("SELECT id,status,failure_node,updated_at FROM tasks ORDER BY created_at DESC LIMIT 5")).fetchall():
        print("  ", r)
    latest=c.execute(text("SELECT id FROM tasks ORDER BY created_at DESC LIMIT 1")).scalar()
    print("\n=== 最新任务 %s 完整状态 ==="%latest)
    row=c.execute(text("SELECT status,automation_state,risk_level,pr_number,pr_status,base_commit_sha,work_branch,failure_node,failure_reason FROM tasks WHERE id=:t"),{"t":latest}).fetchone()
    for k,v in zip(["status","automation_state","risk","pr_number","pr_status","base_commit","work_branch","failure_node","failure_reason"],row):
        print("  %s = %s"%(k,(str(v)[:200] if v else v)))
    print("\n=== 事件里程碑(新->旧, 去重保留关键) ===")
    seen=set()
    for e in c.execute(text("SELECT event_type,created_at FROM task_events WHERE task_id=:t ORDER BY created_at"),{"t":latest}).fetchall():
        if e[0] not in seen:
            seen.add(e[0]); print("  %-24s %s"%(e[0],e[1]))
    print("\n=== 测试执行结果 ===")
    for r in c.execute(text("SELECT command,exit_code,status,failure_reason FROM test_runs WHERE task_id=:t"),{"t":latest}).fetchall():
        print("  ", r)
    print("\n=== PR 描述 ===")
    for r in c.execute(text("SELECT id,status FROM pr_descriptions WHERE task_id=:t"),{"t":latest}).fetchall():
        print("  ", r)
