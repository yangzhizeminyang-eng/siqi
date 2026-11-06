from sqlalchemy import create_engine, text
url=[l.split('=',1)[1].strip() for l in open('.env',encoding='utf-8') if l.startswith('DATABASE_URL')][0]
e=create_engine(url)
with e.connect() as c:
    r=c.execute(text("SELECT id,status,next_action,test_command,base_commit_sha,repository_name FROM tasks WHERE id='TASK-8815'")).fetchone()
    print("8815:", r)
    print("=== 事件 ===")
    for ev in c.execute(text("SELECT event_type,created_at,summary FROM task_events WHERE task_id='TASK-8815' ORDER BY created_at")).fetchall():
        print("  %-22s %-19s %s"%(ev[0],ev[1],(ev[2] or "")[:45]))
