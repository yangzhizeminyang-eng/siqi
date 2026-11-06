from sqlalchemy import create_engine, text
url=None
for line in open(".env", encoding="utf-8"):
    line=line.strip()
    if line.startswith("DATABASE_URL") and "=" in line:
        url=line.split("=",1)[1].strip().strip("'\"")
eng=create_engine(url, pool_pre_ping=True)
with eng.connect() as c:
    cols=[x[0] for x in c.execute(text("SHOW COLUMNS FROM repositories"))]
    print("=== 所有仓库 按 updated_at 倒序 ===")
    for row in c.execute(text("SELECT * FROM repositories ORDER BY updated_at DESC")).fetchall():
        d=dict(zip(cols,row))
        print("  %s | %-26s | active=%s | archived=%s | %s" % (
            d["id"], (d["name"] or "")[:26], d["is_active"], "yes" if d["archived_at"] else "no", (d.get("url") or "")[:55]))
    print("=== 任务 按仓库分组(不含CANCELLED) ===")
    for r in c.execute(text("SELECT repository_id, repository_name, COUNT(*) cnt FROM tasks WHERE status<>'CANCELLED' GROUP BY repository_id, repository_name ORDER BY cnt DESC")).fetchall():
        print("  %s | %s | %s tasks" % r)
