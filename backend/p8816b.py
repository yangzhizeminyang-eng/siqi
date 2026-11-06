import urllib.request, json, http.cookiejar, time
from sqlalchemy import create_engine, text
base="http://127.0.0.1:8002"
cj=http.cookiejar.CookieJar(); o=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
def http(m,path,obj=None,to=90):
    data=json.dumps(obj).encode() if obj is not None else None
    r=urllib.request.Request(base+path,data=data,method=m,headers={"Content-Type":"application/json"})
    try:
        with o.open(r,timeout=to) as x: return x.status,json.loads(x.read().decode())
    except urllib.error.HTTPError as e: return e.code,e.read().decode()[:200]
http("POST","/api/auth/login",{"account":"admin@company.com","password":"Admin123!","remember_me":False})
url=[l.split('=',1)[1].strip() for l in open('.env',encoding='utf-8') if l.startswith('DATABASE_URL')][0]
e=create_engine(url)
for i in range(20):
    http("GET","/api/tasks/TASK-8816",to=60)  # advance → 生成计划
    time.sleep(12)
    st=e.connect().execute(text("SELECT status FROM tasks WHERE id='TASK-8816'")).scalar()
    print(i+1, st, flush=True)
    if st in ("WAITING_APPROVAL","APPROVED","DIFF_GENERATED","FAILED","CANCELLED"): break
print("FINAL", st)
