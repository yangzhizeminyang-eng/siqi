import urllib.request, json, http.cookiejar, time
from sqlalchemy import create_engine, text
base="http://127.0.0.1:8002"
cj=http.cookiejar.CookieJar(); o=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
def get(p):
    with o.open(urllib.request.Request(base+p),timeout=60) as x: return json.loads(x.read().decode())
o.open(urllib.request.Request(base+"/api/auth/login",data=json.dumps({"account":"admin@company.com","password":"Admin123!","remember_me":False}).encode(),headers={"Content-Type":"application/json"}))
try:
    d=get("/api/tasks/TASK-8815").get("data",{})
    print("advance ->", d.get("status"))
except Exception as e:
    print("GET err", e)
url=[l.split('=',1)[1].strip() for l in open('.env',encoding='utf-8') if l.startswith('DATABASE_URL')][0]
e=create_engine(url)
for _ in range(4):
    time.sleep(12)
    s=e.connect().execute(text("SELECT status FROM tasks WHERE id='TASK-8815'")).scalar()
    print("8815:", s)
    if s not in ("ANALYZED","PLAN_GENERATED"): pass
    if s=="PLAN_GENERATED": break
