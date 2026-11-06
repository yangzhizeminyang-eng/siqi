from time import sleep
from sqlalchemy import create_engine, text
url=[l.split('=',1)[1].strip() for l in open('.env',encoding='utf-8') if l.startswith('DATABASE_URL')][0]
e=create_engine(url)
for n in range(3):
    sleep(20)
    rows=e.connect().execute(text("SELECT id,status FROM tasks WHERE id IN ('TASK-8810','TASK-8811','TASK-8813','TASK-8814')")).fetchall()
    print("===", n+1, "===")
    for r in rows: print("  %s -> %s"%(r[0], r[1]))
