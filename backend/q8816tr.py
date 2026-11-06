from sqlalchemy import create_engine, text
url=[l.split('=',1)[1].strip() for l in open('.env',encoding='utf-8') if l.startswith('DATABASE_URL')][0]
e=create_engine(url)
with e.connect() as c:
    cols=[x[0] for x in c.execute(text("SHOW COLUMNS FROM test_runs"))]
    for row in c.execute(text("SELECT * FROM test_runs WHERE task_id='TASK-8816' ORDER BY attempt")).fetchall():
        d=dict(zip(cols,row))
        print("attempt=%s exit=%s dur=%s cmd=%s"%(d["attempt"],d["exit_code"],d["duration"],d["command"]))
        print("  failure_reason:", (d.get("failure_reason") or "")[:260])
        print("  stdout尾:", (d.get("stdout") or "")[-400:])
        print("  stderr尾:", (d.get("stderr") or "")[-400:])
