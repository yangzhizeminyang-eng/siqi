from sqlalchemy import create_engine, text
url=None
for line in open(".env", encoding="utf-8"):
    line=line.strip()
    if line.startswith("DATABASE_URL") and "=" in line:
        url=line.split("=",1)[1].strip().strip("'\"")
eng=create_engine(url, pool_pre_ping=True)
with eng.connect() as c:
    print("=== 任务状态总览 ===")
    ids=[r[0] for r in c.execute(text("SELECT id FROM tasks WHERE id IN ('TASK-8810','TASK-8811','TASK-8813','TASK-8814') ORDER BY created_at"))]
    st={}
    for T in ids:
        r=c.execute(text("SELECT status,next_action FROM tasks WHERE id=:t"),{"t":T}).fetchone()
        st[T]=r
        print("  %-9s %-20s %s"%(T, r[0], r[1]))
    # 找最新 progressed 任务(审批后)
    print("=== 所有非终态任务事件最新(找 DIFF/APPROVED) ===")
    for T in ids:
        ev=c.execute(text("SELECT event_type FROM task_events WHERE task_id=:t ORDER BY created_at DESC LIMIT 1"),{"t":T}).scalar()
        if ev in ("DIFF_GENERATED","APPROVED","WAITING_APPROVAL"):
            print("  %s latest_event=%s"%(T,ev))
