import urllib.request, json, http.cookiejar, time
from sqlalchemy import create_engine, text
base="http://127.0.0.1:8002"
cj=http.cookiejar.CookieJar(); o=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
def http(m,p,obj=None,to=120):
    data=json.dumps(obj).encode() if obj is not None else None
    r=urllib.request.Request(base+p,data=data,method=m,headers={"Content-Type":"application/json"})
    try:
        with o.open(r,timeout=to) as x: return x.status,json.loads(x.read().decode())
    except urllib.error.HTTPError as e: return e.code,e.read().decode()[:300]
http("POST","/api/auth/login",{"account":"admin@company.com","password":"Admin123!","remember_me":False})
url=[l.split('=',1)[1].strip() for l in open('.env',encoding='utf-8') if l.startswith('DATABASE_URL')][0]
e=create_engine(url)
T="TASK-8818"
# 找 plan id
with e.connect() as c:
    pid=c.execute(text("SELECT id FROM plans WHERE task_id=:t ORDER BY version"),{"t":T}).scalar() or f"PLAN-{T.replace('TASK-','')}-1"
print("plan:", pid)
# 审批通过
s,b=http("POST",f"/api/tasks/{T}/plans/{pid}/approve",{"comment":"真实链验收代批"})
print("approve ->", s, (b.get("data",{}).get("status") if s==200 and isinstance(b,dict) else b[:120]))
# advance 直到 diff 生成
for i in range(12):
    http("GET",f"/api/tasks/{T}",to=90)
    time.sleep(12)
    with e.connect() as c:
        st=c.execute(text("SELECT status FROM tasks WHERE id=:t"),{"t":T}).scalar()
        d= c.execute(text("SELECT id,execution_mode,is_demo_fallback,generation_status,real_change_source FROM diffs WHERE task_id=:t ORDER BY generated_at DESC LIMIT 1"),{"t":T}).fetchone()
    print(i+1, st, "diff:", d, flush=True)
    if st in ("TESTED","FAILED","CANCELLED"): break
    if d and d[1]=="REAL": break
print("FINAL", st)
