import urllib.request, json, http.cookiejar, time
from sqlalchemy import create_engine, text
base="http://127.0.0.1:8002"
cj=http.cookiejar.CookieJar(); o=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
def http(m,p,obj=None,to=200):
    data=json.dumps(obj).encode() if obj is not None else None
    r=urllib.request.Request(base+p,data=data,method=m,headers={"Content-Type":"application/json"})
    try:
        with o.open(r,timeout=to) as x: return x.status,x.read().decode()
    except urllib.error.HTTPError as e: return e.code,e.read().decode()[:600]
http("POST","/api/auth/login",{"account":"admin@company.com","password":"Admin123!","remember_me":False})
T="TASK-8818"
s,b=http("POST",f"/api/tasks/{T}/pr-description/generate")
print("pr-description generate ->",s, b[:260]); time.sleep(3)
url=[l.split('=',1)[1].strip() for l in open('.env',encoding='utf-8') if l.startswith('DATABASE_URL')][0]
e=create_engine(url)
n=e.connect().execute(text("SELECT COUNT(*) FROM pr_descriptions WHERE task_id=:t"),{"t":T}).scalar()
print("pr_descriptions rows:", n)
s2,b2=http("POST",f"/api/tasks/{T}/pull-request",{"base_branch":"main"})
print("pull-request ->", s2, b2[:500])
with e.connect() as c:
    r=c.execute(text("SELECT pr_number,pr_url,pr_status FROM tasks WHERE id=:t"),{"t":T}).fetchone()
    print("DB pr:", tuple(r))
