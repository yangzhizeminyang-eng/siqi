from sqlalchemy import create_engine, text
url=[l.split('=',1)[1].strip() for l in open('.env',encoding='utf-8') if l.startswith('DATABASE_URL')][0]
e=create_engine(url)
with e.connect() as c:
    r=c.execute(text("SELECT status,pr_number,pr_status FROM tasks WHERE id='TASK-8817'")).fetchone()
    print("status/pr:", r)
    print("=== test_runs ===")
    for rr in c.execute(text("SELECT attempt,exit_code,status,duration,command FROM test_runs WHERE task_id='TASK-8817'")).fetchall():
        print("  ", rr)
    print("=== 事件(最后10) ===")
    for ev in c.execute(text("SELECT event_type,summary FROM task_events WHERE task_id='TASK-8817' ORDER BY created_at DESC LIMIT 10")).fetchall():
        print("  %-24s %s"%(ev[0],(ev[1] or "")[:55]))
    pr=c.execute(text("SELECT COUNT(*) FROM pr_descriptions WHERE task_id='TASK-8817'")).scalar()
    print("pr_descriptions:", pr)
