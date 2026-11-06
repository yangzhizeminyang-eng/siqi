from sqlalchemy import create_engine, text
url=None
for line in open(".env", encoding="utf-8"):
    line=line.strip()
    if line.startswith("DATABASE_URL") and "=" in line:
        url=line.split("=",1)[1].strip().strip("'\"")
eng=create_engine(url, pool_pre_ping=True)
with eng.connect() as c:
    print("=== 最新任务 ===")
    for r in c.execute(text("SELECT id,title,status,failure_node,failure_reason FROM tasks ORDER BY created_at DESC LIMIT 3")).fetchall():
        print(" ", r)
    latest=c.execute(text("SELECT id,status,test_command,failure_node,failure_reason FROM tasks ORDER BY created_at DESC LIMIT 1")).fetchone()
    print("latest:", latest)
    tid=latest[0]
    cols=[x[0] for x in c.execute(text("SHOW COLUMNS FROM test_runs"))]
    print("=== 该任务 test_runs ===")
    for row in c.execute(text("SELECT * FROM test_runs WHERE task_id=:t ORDER BY attempt"),{"t":tid}).fetchall():
        d=dict(zip(cols,row))
        print("  attempt=%s command=%s exit=%s status=%s"%(d.get("attempt"),d.get("command"),d.get("exit_code"),d.get("status")))
        print("     failure_reason=%s"%(d.get("failure_reason")))
        if d.get("stderr"): print("     stderr=%s"%str(d.get("stderr"))[:300])
