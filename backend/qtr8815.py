from sqlalchemy import create_engine, text
url=[l.split('=',1)[1].strip() for l in open('.env',encoding='utf-8') if l.startswith('DATABASE_URL')][0]
e=create_engine(url)
with e.connect() as c:
    cols=[x[0] for x in c.execute(text("SHOW COLUMNS FROM test_runs"))]
    for row in c.execute(text("SELECT * FROM test_runs WHERE task_id='TASK-8815' ORDER BY attempt")).fetchall():
        d=dict(zip(cols,row))
        print("attempt=%s exit=%s status=%s dur=%s runner=%s"%(d.get("attempt"),d.get("exit_code"),d.get("status"),d.get("duration"),d.get("runner_mode")))
        print("  cmd:", d.get("command"))
        print("  failure_reason:", (d.get("failure_reason") or "")[:200])
        print("  stderr尾:", (d.get("stderr") or "")[-500:])
        print("  stdout尾:", (d.get("stdout") or "")[-500:])
