from sqlalchemy import create_engine, text
url=None
for line in open(".env", encoding="utf-8"):
    line=line.strip()
    if line.startswith("DATABASE_URL") and "=" in line:
        url=line.split("=",1)[1].strip().strip("'\"")
eng=create_engine(url, pool_pre_ping=True)
with eng.connect() as c:
    print("=== 最新5任务 ===")
    for r in c.execute(text("SELECT id,title,status,created_at,updated_at FROM tasks ORDER BY created_at DESC LIMIT 5")).fetchall():
        print("  id=%s status=%-14s created=%s updated=%s | %s"%(r[0],r[1],r[2],r[3],(r[1] or '')[:30]))
    latest=c.execute(text("SELECT id FROM tasks ORDER BY created_at DESC LIMIT 1")).scalar()
    if latest:
        print("\n=== 最新 %s 事件(近15) ==="%latest)
        for e in c.execute(text("SELECT event_type,created_at FROM task_events WHERE task_id=:t ORDER BY created_at DESC LIMIT 15"),{"t":latest}).fetchall():
            print("  %-24s %s"%(e[0],e[1]))
        r=c.execute(text("SELECT status,auto_create_task_after_sync FROM tasks WHERE id=:t"),{"t":latest}).fetchone()
        print("status:", r[0])
