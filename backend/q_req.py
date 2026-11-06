from sqlalchemy import create_engine, text
url=None
for line in open(".env", encoding="utf-8"):
    line=line.strip()
    if line.startswith("DATABASE_URL") and "=" in line:
        url=line.split("=",1)[1].strip().strip("'\"")
eng=create_engine(url, pool_pre_ping=True)
with eng.connect() as c:
    print("=== 平台需求列表 ===")
    try:
        cols=[x[0] for x in c.execute(text("SHOW COLUMNS FROM team_requests"))]
        for row in c.execute(text("SELECT * FROM team_requests ORDER BY created_at DESC LIMIT 10")).fetchall():
            d=dict(zip(cols,row))
            print("  %s | %-12s | %-14s | gitee=%s | task=%s | %s" % (
                d.get("request_no"), (d.get("title") or "")[:30], d.get("sync_status"),
                d.get("gitee_issue_number"), d.get("task_id"), (d.get("body") or "")[:20]))
    except Exception as e:
        print("err",e)
