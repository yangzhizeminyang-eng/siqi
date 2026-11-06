import urllib.request, json, http.cookiejar
base="http://127.0.0.1:8002"
cj=http.cookiejar.CookieJar(); o=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
def post(p,obj=None):
    data=json.dumps(obj).encode() if obj is not None else None
    r=urllib.request.Request(base+p,data=data,method="POST",headers={"Content-Type":"application/json"})
    try:
        with o.open(r,timeout=30) as x: return x.status, x.read().decode()
    except urllib.error.HTTPError as e: return e.code, e.read().decode()[:200]
def get(p):
    with o.open(urllib.request.Request(base+p),timeout=30) as x: return json.loads(x.read().decode())
post("/api/auth/login",{"account":"admin@company.com","password":"Admin123!","remember_me":False})
for t in ("TASK-8813","TASK-8814"):
    s,b=post(f"/api/tasks/{t}/cancel")
    try:
        st=json.loads(b).get("data",{}).get("status") if s==200 else b
    except: st=b
    print("cancel", t, s, st)
