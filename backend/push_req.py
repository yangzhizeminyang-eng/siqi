import base64, json, urllib.request, urllib.parse
token=None
for line in open(".env",encoding="utf-8"):
    line=line.strip()
    if line.startswith("GITEE_TOKEN") and "=" in line:
        token=line.split("=",1)[1].strip().strip("'\"")
def api(path, method="GET", data=None):
    url="https://gitee.com/api/v5"+path
    if method=="GET": url+="?"+urllib.parse.urlencode({"access_token":token})
    r=urllib.request.Request(url, method=method, headers={"Content-Type":"application/json"})
    if data is not None: r.data=json.dumps(data).encode()
    try:
        with urllib.request.urlopen(r,timeout=30) as resp: return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e: return e.code, e.read().decode()[:300]
st,d=api("/repos/yang-zhizemin/health-middle")
db = d.get("default_branch") if isinstance(d,dict) else "main"
print("default_branch =", db)
content=open("requirements.txt","rb").read()
payload={"access_token":token,"branch":db,"content":base64.b64encode(content).decode(),
         "message":"chore: 补充平台后端 requirements.txt，支持测试执行自动装配依赖"}
for path in ("backend/requirements.txt","requirements.txt"):
    st2,b2=api(f"/repos/yang-zhizemin/health-middle/contents/{path}","GET")
    sha=b2.get("sha") if isinstance(b2,dict) else None
    if st2==200:
        payload["sha"]=sha
        upload_path=path; break
else:
    upload_path="backend/requirements.txt"
st3,b3=api(f"/repos/yang-zhizemin/health-middle/contents/{upload_path}","PUT",payload)
print("PUT", upload_path, "=>", st3)
if isinstance(b3,dict):
    print("  commit:", b3.get("commit",{}).get("sha") if isinstance(b3.get("commit"),dict) else "")
    print("  path:", b3.get("content",{}).get("path") if isinstance(b3.get("content"),dict) else "")
else:
    print("  body:", b3[:300])
