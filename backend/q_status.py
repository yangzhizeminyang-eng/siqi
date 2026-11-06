import json
from sqlalchemy import create_engine, text
url=None
for line in open(".env", encoding="utf-8"):
    line=line.strip()
    if line.startswith("DATABASE_URL") and "=" in line:
        url=line.split("=",1)[1].strip().strip("'\"")
eng=create_engine(url, pool_pre_ping=True)
with eng.connect() as c:
    print("=== 最近任务 ===")
    rows=c.execute(text("SELECT id,title,status,risk,updated_at FROM tasks ORDER BY updated_at DESC LIMIT 6")).fetchall()
    for r in rows:
        print(" ", r)
    # 取最新任务 id
    latest=c.execute(text("SELECT id FROM tasks ORDER BY updated_at DESC LIMIT 1")).scalar()
    if latest:
        print("=== 最新任务", latest, "事件流 ===")
        evs=c.execute(text("SELECT event_type,created_at,payload FROM task_events WHERE task_id=:t ORDER BY created_at DESC LIMIT 20"),{"t":latest}).fetchall()
        for e in reversed(evs):
            pt = (e[2] or "")
            if isinstance(pt,str) and len(pt)>120: pt=pt[:120]+"..."
            print("  [%s] %s" % (e[1], e[0]))
        print("=== plans 状态 ===")
        for p in c.execute(text("SELECT id,status,version,risk FROM plans WHERE task_id=:t ORDER BY version DESC LIMIT 5"),{"t":latest}).fetchall():
            print("  ", p)
