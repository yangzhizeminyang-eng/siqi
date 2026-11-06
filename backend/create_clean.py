import urllib.request, json, http.cookiejar, urllib.parse
# ---- Gitee 列健康 middle open issues ----
token=None
for line in open(".env",encoding="utf-8"):
    line=line.strip()
    if line.startswith("GITEE_TOKEN") and "=" in line: token=line.split("=",1)[1].strip().strip("'\"")
q=urllib.parse.urlencode({"access_token":token,"state":"open","per_page":10})
with urllib.request.urlopen(urllib.request.Request("https://gitee.com/api/v5/repos/yang-zhizemin/health-middle/issues?"+q),timeout=150) as r:
    iss=json.loads(r.read().decode())
print("open issues:", [(i.get('number'), i.get('title')[:20]) for i in iss])
issue = iss[0] if iss else None
issue_num = issue.get("number") if issue else None
# ---- 登录 team 并建任务 ----
base="http://127.0.0.1:8002"
cj=http.cookiejar.CookieJar(); o=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
def post(p,obj=None):
    data=json.dumps(obj).encode() if obj is not None else None
    r=urllib.request.Request(base+p,data=data,method="POST",headers={"Content-Type":"application/json"})
    try:
        with o.open(r,timeout=150) as x: return x.status,x.read().decode()
    except urllib.error.HTTPError as e: return e.code,e.read().decode()[:300]
post("/api/auth/login",{"account":"admin@company.com","password":"Admin123!","remember_me":False})
if issue_num:
    s,b=post("/api/tasks",{"project_id":"proj_payment","repository_id":"repo_4df469d07e09","issue_id":issue_num})
    d=json.loads(b).get("data",{}) if s==200 else b
    print("CREATE ->",s, "task=", (d.get("id") if isinstance(d,dict) else d))
else:
    print("无可用 open issue; 需先在 health-middle 建一个 issue")
