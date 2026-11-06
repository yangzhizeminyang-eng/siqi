import urllib.request, json, http.cookiejar
base="http://127.0.0.1:8002"
cj=http.cookiejar.CookieJar(); o=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
def http(m,p,obj=None,to=90):
    data=json.dumps(obj).encode() if obj is not None else None
    r=urllib.request.Request(base+p,data=data,method=m,headers={"Content-Type":"application/json"})
    try:
        with o.open(r,timeout=to) as x: return x.status,json.loads(x.read().decode())
    except urllib.error.HTTPError as e: return e.code,e.read().decode()[:300]
http("POST","/api/auth/login",{"account":"admin@company.com","password":"Admin123!","remember_me":False})
s,d=http("GET","/api/tasks/TASK-8818")
data=d.get("data",{}) if isinstance(d,dict) else {}
diffs=data.get("diffs") or []
for x in diffs:
    print("diff:", x.get("id"), "mode=", x.get("execution_mode"), "rsc=", x.get("real_change_source"), "demo=", x.get("is_demo_fallback"))
print("task status:", data.get("status"))
