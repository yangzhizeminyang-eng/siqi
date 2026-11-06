from sqlalchemy import create_engine, text
url=None
for line in open(".env", encoding="utf-8"):
    line=line.strip()
    if line.startswith("DATABASE_URL") and "=" in line:
        url=line.split("=",1)[1].strip().strip("'\"")
eng=create_engine(url, pool_pre_ping=True)
with eng.connect() as c:
    r=c.execute(text("SELECT status,next_action,automation_state,manual_required,manual_reason,related_index_id,base_commit_sha FROM tasks WHERE id='TASK-8814'")).fetchone()
    print("8814:", r)
