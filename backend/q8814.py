from sqlalchemy import create_engine, text
url=None
for line in open(".env", encoding="utf-8"):
    line=line.strip()
    if line.startswith("DATABASE_URL") and "=" in line:
        url=line.split("=",1)[1].strip().strip("'\"")
eng=create_engine(url, pool_pre_ping=True)
T="TASK-8814"
with eng.connect() as c:
    print("=== 最近5任务 ===")
    for r in c.execute(text("SELECT id,status,created_at,updated_at FROM tasks ORDER BY created_at DESC LIMIT 5")).fetchall():
        print("  %-14s %-14s %s -> %s"%(r[0],r[1],r[2],r[3]))
    r=c.execute(text("SELECT status,failure_reason FROM tasks WHERE id=:t"),{"t":T}).fetchone()
    print("TASK-8814 status=%s failure=%s"%(r[0], (r[1] or "")[:60]))
    print("=== 事件(时间正序) ===")
    for e in c.execute(text("SELECT event_type,created_at,summary FROM task_events WHERE task_id=:t ORDER BY created_at LIMIT 25"),{"t":T}).fetchall():
        print("  %-22s %-19s %s"%(e[0],e[1],(e[2] or "")[:50]))
