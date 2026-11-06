import urllib.request, json, http.cookiejar, time
from sqlalchemy import create_engine, text
base="http://127.0.0.1:8002"
cj=http.cookiejar.CookieJar(); o=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
def http(m,path,obj=None,to=90):
    data=json.dumps(obj).encode() if obj is not None else None
    r=urllib.request.Request(base+path,data=data,method=m,headers={"Content-Type":"application/json"})
    try:
        with o.open(r,timeout=to) as x: return x.status,json.loads(x.read().decode())
    except urllib.error.HTTPError as e: return e.code,e.read().decode()[:250]
http("POST","/api/auth/login",{"account":"admin@company.com","password":"Admin123!","remember_me":False})
s,b=http("POST","/api/tasks",{"project_id":"proj_payment","repository_id":"repo_4df469d07e09","issue_id":"IKEUAR"})
if s==200: tid=b["data"]["id"]; print("created:",tid)
elif s==409 and isinstance(b,dict): tid=b["data"]["existing_task"]["id"]; print("409 reuse:",tid)
else: print("create fail",s,b); raise SystemExit
url=[l.split('=',1)[1].strip() for l in open('.env',encoding='utf-8') if l.startswith('DATABASE_URL')][0]
e=create_engine(url)
for i in range(24):
    http("GET",f"/api/tasks/{tid}",to=60)
    time.sleep(12)
    st=e.connect().execute(text("SELECT status FROM tasks WHERE id=:t"),{"t":tid}).scalar()
    print(i+1, tid, st, flush=True)
    if st in ("WAITING_APPROVAL","DIFF_GENERATED","FAILED","CANCELLED"): break
print("FINAL", tid, st)
