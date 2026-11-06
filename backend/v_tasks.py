import urllib.request, json, http.cookiejar
base="http://127.0.0.1:8002"
cj=http.cookiejar.CookieJar(); o=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
def post(p,obj):
    r=urllib.request.Request(base+p,data=json.dumps(obj).encode(),headers={"Content-Type":"application/json"})
    with o.open(r,timeout=30) as resp: return json.loads(resp.read().decode())
def get(p):
    with o.open(urllib.request.Request(base+p),timeout=30) as resp: return json.loads(resp.read().decode())
post("/api/auth/login", {"account":"admin@company.com","password":"Admin123!","remember_me":False})
def show(tag,path):
    d=get(path).get("data",[])
    items=d if isinstance(d,list) else d.get("items",[])
    print("=== %s (%s tasks) ==="%(tag,len(items)))
    for t in items:
        print("  %-14s %-20s %s"%(t.get("id"), (t.get("repository_name") or "")[:20], (t.get("title") or "")[:30]))
show("默认(仅当前仓库)","/api/tasks?per_page=50")
print()
show("include_archived=true","/api/tasks?include_archived=true&per_page=50")
