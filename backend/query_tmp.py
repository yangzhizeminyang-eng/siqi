import urllib.request, json, http.cookiejar, time
base="http://127.0.0.1:8002"
cj=http.cookiejar.CookieJar()
o=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
def post(path,obj):
    r=urllib.request.Request(base+path,data=json.dumps(obj).encode(),headers={"Content-Type":"application/json"})
    with o.open(r,timeout=60) as resp: return json.loads(resp.read().decode())
def get(path):
    with o.open(urllib.request.Request(base+path),timeout=60) as resp: return json.loads(resp.read().decode())
post("/api/auth/login", {"account":"developer@company.com","password":"Dev123!","remember_me":False})
for i in range(6):
    try:
        r=get("/api/team/requests")
        data=r.get("data", r)
        items = data if isinstance(data,list) else data.get("items", data.get("requests", []))
        req=None
        if isinstance(items,list):
            req=next((x for x in items if x.get("request_no")=="REQ-1002"), None)
        if req:
            print("iter",i,"status=",req.get("sync_status"),"no=",req.get("request_no"))
            print("  gitee_issue:", req.get("gitee_issue_number"), req.get("gitee_issue_url"))
            print("  failure:", req.get("failure_reason"), "| manual:", req.get("manual_reason"))
            st=req.get("sync_status")
            if st in ("SYNCED","SYNC_FAILED"):
                break
        else:
            print("iter",i,"REQ-1002 not in payload structure")
    except urllib.error.HTTPError as e:
        print("iter",i,"HTTP",e.code, e.read().decode()[:200])
    import time as _t; _t.sleep(4)
