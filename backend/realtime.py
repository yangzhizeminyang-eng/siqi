import urllib.request, json, http.cookiejar, time, urllib.parse
base="http://127.0.0.1:8002"
cj=http.cookiejar.CookieJar(); o=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
def http(method,path,obj=None,timeout=90):
    data=json.dumps(obj).encode() if obj is not None else None
    r=urllib.request.Request(base+path,data=data,method=method,headers={"Content-Type":"application/json"})
    try:
        with o.open(r,timeout=timeout) as x: return x.status, json.loads(x.read().decode())
    except urllib.error.HTTPError as e: return e.code, e.read().decode()[:300]
http("POST","/api/auth/login",{"account":"admin@company.com","password":"Admin123!","remember_me":False})
s,b=http("POST","/api/tasks",{"project_id":"proj_payment","repository_id":"repo_4df469d07e09","issue_id":"IKETXJ"})
if s==200:
    tid=b["data"]["id"]; print("created task:", tid)
elif s==409:
    tid=b["data"]["existing_task"]["id"]; print("reuse task:", tid)
else:
    print("create fail",s,b); raise SystemExit
# advance until WAITING_APPROVAL
for i in range(24):
    time.sleep(12)
    s,d=http("GET",f"/api/tasks/{tid}",timeout=60)
    st=d.get("data",{}).get("status") if isinstance(d,dict) else None
    print(i+1, tid, st, flush=True)
    if st in ("WAITING_APPROVAL","PLAN_GENERATED","APPROVED","DIFF_GENERATED","FAILED","CANCELLED"):
        break
print("FINAL", tid, st)
