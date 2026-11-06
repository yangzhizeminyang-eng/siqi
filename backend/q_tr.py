from sqlalchemy import create_engine, text
url=None
for line in open(".env", encoding="utf-8"):
    line=line.strip()
    if line.startswith("DATABASE_URL") and "=" in line:
        url=line.split("=",1)[1].strip().strip("'\"")
eng=create_engine(url, pool_pre_ping=True)
with eng.connect() as c:
    print("=== test_runs columns ===")
    print([r[0] for r in c.execute(text("SHOW COLUMNS FROM test_runs"))])
    print("=== TASK-8808 test_runs ===")
    cols=[r[0] for r in c.execute(text("SHOW COLUMNS FROM test_runs"))]
    for row in c.execute(text("SELECT * FROM test_runs WHERE task_id='TASK-8808' ORDER BY id DESC LIMIT 3")).fetchall():
        d=dict(zip(cols,row))
        keep={k:d.get(k) for k in cols if k not in ("stdout","stderr")}
        print(keep)
