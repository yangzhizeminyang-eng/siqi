import urllib.request, json, http.cookiejar
base="http://127.0.0.1:8002"
cj=http.cookiejar.CookieJar(); o=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
def send(method,path,obj=None):
    data=json.dumps(obj).encode() if obj is not None else None
    r=urllib.request.Request(base+path,data=data,method=method,headers={"Content-Type":"application/json"})
    try:
        with o.open(r,timeout=30) as resp: return resp.status, resp.read().decode()
    except urllib.error.HTTPError as e: return e.code, e.read().decode()
send("POST","/api/auth/login",{"account":"admin@company.com","password":"Admin123!","remember_me":False})
s,b=send("GET","/api/repositories?include_archived=true")
repos=json.loads(b)["data"]
repo=next(r for r in repos if r.get("id")=="repo_6f46704322ec")
wl=list(repo.get("command_whitelist") or [])
for x in ("python",):
    if x not in wl: wl.append(x)
payload={
  "project_id": repo.get("project_id"),"name": repo.get("name"),"url": repo.get("url"),
  "default_branch": repo.get("default_branch"),
  "test_command": "python -m compileall -q /workspace",
  "command_whitelist": wl,
  "test_command_fallback": repo.get("test_command_fallback") or False,
  "auto_create_task_after_sync": repo.get("auto_create_task_after_sync") or False,
  "approval_owner_id": repo.get("approval_owner_id"),
  "approval_mode": repo.get("approval_mode") or "single",
  "risk_threshold": repo.get("risk_threshold") or "medium",
  "max_files": repo.get("max_files") or 20,
  "max_retries": repo.get("max_retries") or 2,
  "blocked_paths": repo.get("blocked_paths") or [],
}
s,bd=send("PUT","/api/repositories/repo_6f46704322ec",payload)
print("PUT =>", s)
if s==200:
    d=json.loads(bd).get("data",{})
    print("已保存 test_command =", d.get("test_command"))
    print("whitelist =", d.get("command_whitelist"))
else:
    print("body:", bd[:500])
