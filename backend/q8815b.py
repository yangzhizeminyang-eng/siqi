from time import sleep
from sqlalchemy import create_engine, text
url=[l.split('=',1)[1].strip() for l in open('.env',encoding='utf-8') if l.startswith('DATABASE_URL')][0]
e=create_engine(url)
for i in range(6):
    sleep(15)
    with e.connect() as c:
        r=c.execute(text("SELECT status,next_action FROM tasks WHERE id='TASK-8815'")).fetchone()
        print(i+1, "status=", r[0], "next=", r[1])
        if r[0] in ("TESTED","FAILED","COMPLETED"): break
