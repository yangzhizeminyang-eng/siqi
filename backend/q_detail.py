from sqlalchemy import create_engine, text
url=None
for line in open(".env", encoding="utf-8"):
    line=line.strip()
    if line.startswith("DATABASE_URL") and "=" in line:
        url=line.split("=",1)[1].strip().strip("'\"")
eng=create_engine(url, pool_pre_ping=True)
T="TASK-8808"
with eng.connect() as c:
    r=c.execute(text("SELECT status,automation_state,manual_required,manual_reason,next_action,failure_node,failure_reason,failed_at,retry_count,work_branch,base_commit_sha FROM tasks WHERE id=:t"),{"t":T}).fetchone()
    print("=== TASK-8808 任务状态 ===")
    for k,v in zip(["status","automation_state","manual_required","manual_reason","next_action","failure_node","failure_reason","failed_at","retry_count","work_branch","base_commit_sha"],r):
        print("  %s = %s"%(k,(str(v)[:300] if v else v)))
    print("=== plans ===")
    for p in c.execute(text("SELECT id,version,status,provider,created_by_name,approved_by_name,approved_at FROM plans WHERE task_id=:t ORDER BY version"),{"t":T}).fetchall():
        print("  ", p)
    print("=== 事件流(新->旧) ===")
    for e in c.execute(text("SELECT event_type,summary,created_at FROM task_events WHERE task_id=:t ORDER BY created_at DESC LIMIT 25"),{"t":T}).fetchall():
        s=(e[1] or "")
        if len(s)>140: s=s[:140]+"..."
        print("  [%s] %s | %s"%(e[2], e[0], s))
