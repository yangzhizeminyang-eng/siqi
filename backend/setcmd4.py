import urllib.request, json, http.cookiejar
base="http://127.0.0.1:8002"
cj=http.cookiejar.CookieJar(); o=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
def send(m,p,o2=None):
    d=json.dumps(o2).encode() if o2 is not None else None
    r=urllib.request.Request(base+p,data=d,method=m,headers={"Content-Type":"application/json"})
    try:
        with o.open(r,timeout=30) as x: return x.status, x.read().decode()
    except urllib.error.HTTPError as e: return e.code, e.read().decode()
send("POST","/api/auth/login",{"account":"admin@company.com","password":"Admin123!","remember_me":False})
for rid in ("repo_4df469d07e09","repo_6f46704322ec"):
    s,b=send("GET","/api/repositories?include_archived=true")
    repo=next(r for r in json.loads(b)["data"] if r.get("id")==rid)
    wl=[x for x in ("cd","python","pytest") if x]
    payload={"project_id":repo["project_id"],"name":repo["name"],"url":repo["url"],"default_branch":repo["default_branch"],
     "test_command":"cd backend && python -m pytest tests/ -q","command_whitelist":wl,
     "test_command_fallback":repo.get("test_command_fallback") or False,"auto_create_task_after_sync":repo.get("auto_create_task_after_sync") or False,
     "approval_owner_id":repo.get("approval_owner_id"),"approval_mode":repo.get("approval_mode") or "single",
     "risk_threshold":repo.get("risk_threshold") or "medium","max_files":repo.get("max_files") or 20,
     "max_retries":repo.get("max_retries") or 2,"blocked_paths":repo.get("blocked_paths") or []}
    s,bd=send("PUT","/api/repositories/"+rid,payload)
    d=json.loads(bd).get("data",{}) if s==200 else {}
    print(rid, s, d.get("test_command") if s==200 else bd[:140])
