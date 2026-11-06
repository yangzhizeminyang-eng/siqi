from sqlalchemy import create_engine, text
url=[l.split('=',1)[1].strip() for l in open('.env',encoding='utf-8') if l.startswith('DATABASE_URL')][0]
e=create_engine(url)
with e.connect() as c:
    cols=[x[0] for x in c.execute(text("SHOW COLUMNS FROM test_runs"))]
    for row in c.execute(text("SELECT * FROM test_runs WHERE task_id='TASK-8815' ORDER BY attempt")).fetchall():
        d=dict(zip(cols,row))
        print("attempt=%s exit=%s status=%s dur=%s"%(d["attempt"],d["exit_code"],d["status"],d["duration"]))
        print("  failure_reason:", (d.get("failure_reason") or "")[:300])
        so=(d.get("stdout") or ""); st=(d.get("stderr") or "")
        print("  stdout尾:", so[-400:])
        print("  stderr尾:", st[-400:])
