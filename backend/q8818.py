from sqlalchemy import create_engine, text
url=[l.split('=',1)[1].strip() for l in open('.env',encoding='utf-8') if l.startswith('DATABASE_URL')][0]
e=create_engine(url)
with e.connect() as c:
    cols=[r[0] for r in c.execute(text("SHOW COLUMNS FROM diffs"))]
    print("diffs cols:", [x for x in cols])
    r=c.execute(text("SELECT status FROM tasks WHERE id='TASK-8818'")).scalar()
    print("8818 status:", r)
    for row in c.execute(text("SELECT TOP 1 * FROM diffs WHERE task_id='TASK-8818' ORDER BY generated_at DESC" if False else "SELECT id,execution_mode,is_demo_fallback,generation_status FROM diffs WHERE task_id='TASK-8818' ORDER BY generated_at DESC LIMIT 1")).fetchall():
        print("latest diff:", row)
