from sqlalchemy import create_engine, text
url=None
for line in open(".env", encoding="utf-8"):
    line=line.strip()
    if line.startswith("DATABASE_URL") and "=" in line:
        url=line.split("=",1)[1].strip().strip("'\"")
eng=create_engine(url, pool_pre_ping=True)
with eng.connect() as c:
    print(c.execute(text("SELECT id,repository_id,repository_name,test_command,base_commit_sha FROM tasks WHERE id='TASK-8811'")).fetchone())
    # 该仓库 requirements 是否存在(看 worktree)
