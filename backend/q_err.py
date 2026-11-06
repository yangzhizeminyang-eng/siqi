from sqlalchemy import create_engine, text
url=None
for line in open(".env", encoding="utf-8"):
    line=line.strip()
    if line.startswith("DATABASE_URL") and "=" in line:
        url=line.split("=",1)[1].strip().strip("'\"")
eng=create_engine(url, pool_pre_ping=True)
with eng.connect() as c:
    cols=[x[0] for x in c.execute(text("SHOW COLUMNS FROM test_runs"))]
    rows=c.execute(text("SELECT * FROM test_runs WHERE status='failed' ORDER BY finished_at DESC LIMIT 1")).fetchall()
    for row in rows:
        d=dict(zip(cols,row))
        print("task=",d.get("task_id"),"cmd=",d.get("command"),"exit=",d.get("exit_code"),"duration=",d.get("duration"))
        fr=(d.get("failure_reason") or "")
        print("failure_reason:", fr[:500])
        st=(d.get("stderr") or "")
        print("=== stderr(首1800) ===")
        print(st[:1800])
