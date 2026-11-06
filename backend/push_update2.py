import base64, json, urllib.request, urllib.parse
token=None
for line in open(".env",encoding="utf-8"):
    if line.startswith("GITEE_TOKEN") and "=" in line: token=line.split("=",1)[1].strip().strip("'\"")
def api(path,method="GET",data=None):
    url="https://gitee.com/api/v5"+path
    if method=="GET": url+="?"+urllib.parse.urlencode({"access_token":token})
    r=urllib.request.Request(url,method=method,headers={"Content-Type":"application/json"})
    if data is not None: r.data=json.dumps(data).encode()
    try:
        with urllib.request.urlopen(r,timeout=60) as resp: return resp.status,json.loads(resp.read().decode())
    except urllib.error.HTTPError as e: return e.code,e.read().decode()[:200]
st,cur=api("/repos/yang-zhizemin/health-middle/contents/backend/requirements.txt")
sha=cur.get("sha") if isinstance(cur,dict) else None
content=base64.b64encode(open("requirements.txt","rb").read()).decode()
payload={"access_token":token,"branch":"main","content":content,"message":"chore: requirements 补 alibabacloud 短信依赖(main 源码 sms_service 需要)"}
if sha: payload["sha"]=sha
st2,b=api("/repos/yang-zhizemin/health-middle/contents/backend/requirements.txt","PUT",payload)
print("update ->",st2, "commit:", (b.get("commit",{}).get("sha") if isinstance(b,dict) and isinstance(b.get("commit"),dict) else b[:80]))
