from sqlalchemy import create_engine, text
url=None
for line in open(".env", encoding="utf-8"):
    line=line.strip()
    if line.startswith("DATABASE_URL") and "=" in line:
        url=line.split("=",1)[1].strip().strip("'\"")
eng=create_engine(url, pool_pre_ping=True)
with eng.connect() as c:
    for T in ("TASK-8810","TASK-8811","TASK-8813","TASK-8814"):
        s=c.execute(text("SELECT status FROM tasks WHERE id=:t"),{"t":T}).scalar()
        print(T, "->", s)
    print("=== 8814 最近事件 ===")
    for e in c.execute(text("SELECT event_type,created_at FROM task_events WHERE task_id='TASK-8814' ORDER BY created_at DESC LIMIT 8")).fetchall():
        print("  %-22s %s"%(e[0],e[1]))
