import urllib.request, json, http.cookiejar
base="http://127.0.0.1:8002"
cj=http.cookiejar.CookieJar(); o=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
def post(p,obj=None):
    data=json.dumps(obj).encode() if obj is not None else None
    r=urllib.request.Request(base+p,data=data,method="POST",headers={"Content-Type":"application/json"})
    try:
        with o.open(r,timeout=60) as x: return x.status,x.read().decode()
    except urllib.error.HTTPError as e: return e.code,e.read().decode()[:200]
post("/api/auth/login",{"account":"admin@company.com","password":"Admin123!","remember_me":False})
s,b=post("/api/tasks/TASK-8815/tests/run")
print("run_test:", s, b[:200])
