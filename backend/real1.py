import urllib.request, json, http.cookiejar, urllib.parse
# health
s=urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:8002/api/health"),timeout=6).status
print("health:", s)
# commercial open issues
token=None
for line in open(".env",encoding="utf-8"):
    if line.startswith("GITEE_TOKEN") and "=" in line: token=line.split("=",1)[1].strip().strip("'\"")
q=urllib.parse.urlencode({"access_token":token,"state":"open","per_page":10})
with urllib.request.urlopen(urllib.request.Request("https://gitee.com/api/v5/repos/yang-zhizemin/health-middle-commercial/issues?"+q),timeout=30) as r:
    iss=json.loads(r.read().decode())
print("commercial open issues:", [(i.get('number'),(i.get('title') or '')[:22]) for i in iss])
issue=iss[0]['number'] if iss else 'IKEUAR'
base="http://127.0.0.1:8002"
cj=http.cookiejar.CookieJar(); o=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
def post(p,obj=None):
    data=json.dumps(obj).encode() if obj is not None else None
    r=urllib.request.Request(base+p,data=data,method="POST",headers={"Content-Type":"application/json"})
    try:
        with o.open(r,timeout=90) as x: return x.status,json.loads(x.read().decode())
    except urllib.error.HTTPError as e: return e.code,e.read().decode()[:250]
post("/api/auth/login",{"account":"admin@company.com","password":"Admin123!","remember_me":False})
s2,b=post("/api/tasks",{"project_id":"proj_payment","repository_id":"repo_6f46704322ec","issue_id":issue})
if s2==200: tid=b["data"]["id"]; print("created:",tid,"issue:",issue)
elif s2==409 and isinstance(b,dict): 
    d=b.get("data",{}).get("existing_task",{}); print("409 reuse:",d.get("id"),"issue:",issue)
else: print("create fail",s2,b)
