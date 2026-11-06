import base64, json, urllib.request, urllib.parse
token=None
for line in open(".env",encoding="utf-8"):
    line=line.strip()
    if line.startswith("GITEE_TOKEN") and "=" in line: token=line.split("=",1)[1].strip().strip("'\"")
def api(path,method="GET",data=None):
    url="https://gitee.com/api/v5"+path
    if method=="GET": url+="?"+urllib.parse.urlencode({"access_token":token})
    r=urllib.request.Request(url,method=method,headers={"Content-Type":"application/json"})
    if data is not None: r.data=json.dumps(data).encode()
    try:
        with urllib.request.urlopen(r,timeout=60) as resp: return resp.status,json.loads(resp.read().decode())
    except urllib.error.HTTPError as e: return e.code,e.read().decode()[:300]
st,cur=api("/repos/yang-zhizemin/health-middle/contents/backend/requirements.txt")
sha=cur.get("sha") if isinstance(cur,dict) else None
content=base64.b64encode(open("requirements.txt","rb").read()).decode()
payload={"access_token":token,"branch":"main","content":content,
         "message":"chore: platform requirements 精简为顶层直接依赖 (14行), 加速测试自动装配"}
if sha: payload["sha"]=sha
st2,b=api("/repos/yang-zhizemin/health-middle/contents/backend/requirements.txt","PUT",payload)
print("update requirements.txt ->", st2)
print("commit:", (b.get("commit",{}).get("sha") if isinstance(b,dict) and isinstance(b.get("commit"),dict) else ""))
