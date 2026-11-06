from sqlalchemy import create_engine, text
url=None
for line in open(".env", encoding="utf-8"):
    line=line.strip()
    if line.startswith("DATABASE_URL") and "=" in line:
        url=line.split("=",1)[1].strip().strip("'\"")
eng=create_engine(url, pool_pre_ping=True)
with eng.connect() as c:
    r=c.execute(text("SELECT repository_id,repository_name,test_command,base_commit_sha,created_at FROM tasks WHERE id='TASK-8810'")).fetchone()
    print("TASK-8810:", r)
    rid=r[0]
    cols=[x[0] for x in c.execute(text("SHOW COLUMNS FROM repositories"))]
    row=c.execute(text("SELECT * FROM repositories WHERE id=:id"),{"id":rid}).fetchone()
    d=dict(zip(cols,row))
    print("仓库 %s: name=%s test_command=%s whitelist=%s active=%s" % (rid, d.get("name"), d.get("test_command"), d.get("command_whitelist"), d.get("is_active")))
    print("创建该任务时仓库当时命令无存日期列，以下是当前仓库命令。")
