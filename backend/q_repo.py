from sqlalchemy import create_engine, text
url=None
for line in open(".env", encoding="utf-8"):
    line=line.strip()
    if line.startswith("DATABASE_URL") and "=" in line:
        url=line.split("=",1)[1].strip().strip("'\"")
eng=create_engine(url, pool_pre_ping=True)
with eng.connect() as c:
    r=c.execute(text("SELECT repository_id,repository_name,test_command FROM tasks WHERE id='TASK-8808'")).fetchone()
    print("TASK-8808 -> repository:", r)
    rid=r[0]
    cols=[x[0] for x in c.execute(text("SHOW COLUMNS FROM repositories"))]
    row=c.execute(text("SELECT * FROM repositories WHERE id=:id"),{"id":rid}).fetchone()
    d=dict(zip(cols,row))
    print("=== 仓库 %s ==="%rid)
    for k in ("name","url","default_branch","test_command","test_command_fallback","command_whitelist","approval_owner_name","auto_create_task_after_sync","max_retries"):
        print("  %s = %s"%(k, d.get(k)))
