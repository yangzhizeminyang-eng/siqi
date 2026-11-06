from sqlalchemy import create_engine, text
url=None
for line in open(".env", encoding="utf-8"):
    line=line.strip()
    if line.startswith("DATABASE_URL") and "=" in line:
        url=line.split("=",1)[1].strip().strip("'\"")
eng=create_engine(url, pool_pre_ping=True)
with eng.connect() as c:
    for t in ("tasks","task_events","plans"):
        print("=== %s columns ==="%t)
        cols=[r[0] for r in c.execute(text("SHOW COLUMNS FROM %s"%t))]
        print(cols)
    print("=== 最近任务 ===")
    for r in c.execute(text("SELECT id,title,status,updated_at FROM tasks ORDER BY updated_at DESC LIMIT 8")):
        print("  ", r)
