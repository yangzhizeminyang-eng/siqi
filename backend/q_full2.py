from sqlalchemy import create_engine, text
url=None
for line in open(".env", encoding="utf-8"):
    line=line.strip()
    if line.startswith("DATABASE_URL") and "=" in line:
        url=line.split("=",1)[1].strip().strip("'\"")
eng=create_engine(url, pool_pre_ping=True)
T="TASK-8809"
with eng.connect() as c:
    row=c.execute(text("SELECT status,automation_state,risk_level,pr_number,pr_status,test_command,failure_node,failure_reason FROM tasks WHERE id=:t"),{"t":T}).fetchone()
    print("=== TASK-8809 ===")
    for k,v in zip(["status","automation_state","risk","pr_number","pr_status","test_command","failure_node","failure_reason"],row):
        print("  %s = %s"%(k,(str(v)[:240] if v else v)))
    print("\n=== 事件里程碑(时间正序) ===")
    for e in c.execute(text("SELECT event_type,created_at,summary FROM task_events WHERE task_id=:t ORDER BY created_at"),{"t":T}).fetchall():
        s=(e[2] or "")[:60]
        print("  %-25s %-19s %s"%(e[0], e[1], s))
    print("\n=== test_runs ===")
    for r in c.execute(text("SELECT command,exit_code,status,failure_reason FROM test_runs WHERE task_id=:t"),{"t":T}).fetchall():
        print("  ", r)
    print("\n=== diffs / pr_descriptions 计数 ===")
    print("  diffs:", c.execute(text("SELECT COUNT(*) FROM diffs WHERE task_id=:t"),{"t":T}).scalar())
    cols=[x[0] for x in c.execute(text("SHOW COLUMNS FROM pr_descriptions"))]
    print("  pr_descriptions cols:", cols[:8])
    print("  pr_descriptions rows:", c.execute(text("SELECT COUNT(*) FROM pr_descriptions WHERE task_id=:t"),{"t":T}).scalar())
