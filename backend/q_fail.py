from sqlalchemy import create_engine, text
url=None
for line in open(".env", encoding="utf-8"):
    line=line.strip()
    if line.startswith("DATABASE_URL") and "=" in line:
        url=line.split("=",1)[1].strip().strip("'\"")
eng=create_engine(url, pool_pre_ping=True)
with eng.connect() as c:
    print("=== 最近失败/测试中任务 ===")
    for r in c.execute(text("SELECT id,status,test_command,failure_reason FROM tasks WHERE status IN ('TEST_FAILED','FAILED') ORDER BY updated_at DESC LIMIT 3")).fetchall():
        print("  ", r)
    print("=== 最新 test_runs(近3) ===")
    cols=[x[0] for x in c.execute(text("SHOW COLUMNS FROM test_runs"))]
    for row in c.execute(text("SELECT * FROM test_runs ORDER BY finished_at DESC LIMIT 3")).fetchall():
        d=dict(zip(cols,row))
        print("  %s cmd=%s exit=%s status=%s failure=%s | runner=%s" % (d.get("task_id"), d.get("command"), d.get("exit_code"), d.get("status"), (d.get("failure_reason") or "")[:40], d.get("runner_mode")))
