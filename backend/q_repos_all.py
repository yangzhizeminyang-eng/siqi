from sqlalchemy import create_engine, text
url=None
for line in open(".env", encoding="utf-8"):
    line=line.strip()
    if line.startswith("DATABASE_URL") and "=" in line:
        url=line.split("=",1)[1].strip().strip("'\"")
eng=create_engine(url, pool_pre_ping=True)
with eng.connect() as c:
    print("=== 所有仓库(有效/归档) ===")
    cols=[x[0] for x in c.execute(text("SHOW COLUMNS FROM repositories"))]
    for row in c.execute(text("SELECT * FROM repositories ORDER BY updated_at DESC")).fetchall():
        d=dict(zip(cols,row))
        print("  %s | %-28s | active=%s | archived=%s | url=%s" % (
            d["id"], (d["name"] or "")[:28], d["is_active"], d["archived_at"] is not None,
            (d.get("url") or "")[:60]))
    print("=== 任务归属(近10, 按仓库分组统计) ===")
    for r in c.execute(text("""
        SELECT repository_id, repository_name, COUNT(*) cnt,
               MAX(SELECT COUNT(*) FROM (SELECT 1)) 
        FROM tasks
        WHERE status NOT IN ('CANCELLED')
        GROUP BY repository_id, repository_name ORDER BY cnt DESC""")).fetchall():
        print("  ", r)
