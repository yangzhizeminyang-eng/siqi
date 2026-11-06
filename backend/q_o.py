from sqlalchemy import create_engine, text
url=None
for line in open(".env", encoding="utf-8"):
    line=line.strip()
    if line.startswith("DATABASE_URL") and "=" in line:
        url=line.split("=",1)[1].strip().strip("'\"")
eng=create_engine(url, pool_pre_ping=True)
with eng.connect() as c:
    cols=[x[0] for x in c.execute(text("SHOW COLUMNS FROM test_runs"))]
    row=c.execute(text("SELECT * FROM test_runs WHERE status='failed' ORDER BY finished_at DESC LIMIT 1")).fetchone()
    if not row: print("无 failed 记录"); raise SystemExit
    d=dict(zip(cols,row))
    print("task=",d.get("task_id"),"cmd=",d.get("command"),"exit=",d.get("exit_code"),"dur=",d.get("duration"))
    st=(d.get("stderr") or ""); so=(d.get("stdout") or "")
    print("=== stderr(尾1200)==="); print(st[-1200:] if st else "(stderr空)")
    print("=== stdout(尾2000)==="); print(so[-2000:] if so else "(stdout空)")
