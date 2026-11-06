import urllib.request, json, http.cookiejar
base="http://127.0.0.1:8002"
cj=http.cookiejar.CookieJar()
o=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
def post(path,obj):
    r=urllib.request.Request(base+path,data=json.dumps(obj).encode(),headers={"Content-Type":"application/json"})
    with o.open(r,timeout=60) as resp: return json.loads(resp.read().decode())
body=("当前平台主链路仅适配 Gitee（gitee.com/api/v5 硬编码于 backend/app/main.py 的 gitee_api_get / "
"fetch_gitee_branch_head / sync_repository_snapshot，仓库地址校验仅允许 gitee.com，PR 走 services/gitee_pr_service.py，"
"git 镜像 URL 写死 gitee.com 域，凭证按 GITEE_TOKEN 注入）。导致 GitHub 仓库无法纳入从 Issue 到代码 RAG、分析、计划、"
"审批、Diff、测试、PR 的研发闭环。\n\n目标：引入托管平台(provider)概念，仓库新增 platform(gitee/github)字段，后端按平台分发"
"API 基址与凭证，前端按 platform 展示；既有 Gitee 链路不得回归。\n\n范围：1)仓库配置加 platform 并支持 github.com 地址校验；"
"2)抽象平台 API 层(Issue 拉取/分支 head/仓库快照/PR 创建与 Gitee 并存)；3)git 镜像地址按平台拼接、凭证扩展按平台注入(GITHUB_TOKEN)；"
"4)前端仓库表单/创建任务按 platform 展示；5)Gitee 回归通过。")
post("/api/auth/login", {"account":"developer@company.com","password":"Dev123!","remember_me":False})
payload={"project_id":"proj_payment","repository_id":"repo_5b5b9a2dd10d",
 "type":"demand","title":"新增 GitHub 代码托管平台适配，使 GitHub 仓库进入同一研发闭环",
 "body":body,
 "acceptance_criteria":["可新增 platform=github 的仓库并保存","从 GitHub Issue 创建任务且标题/正文/评论/标签完整落库","代码 RAG 能 clone GitHub 仓库并索引、检索","分析->计划->审批->Diff->测试在 GitHub 仓库正常推进","能生成 PR 描述并调用 GitHub API 创建 PR","既有 Gitee 任务回归通过"],
 "priority":"high","labels":["feature"],"target_branch":"main","test_command":"pytest tests/ -q"}
for attempt in ("create","sync"):
    pass
try:
    r=post("/api/team/requests", payload)
    d=r.get("data",r)
    rid=d.get("id"); print("CREATE request_id=", rid, "no=", d.get("request_no"), "status=", d.get("sync_status"))
    s=post("/api/team/requests/%s/sync-gitee"%rid, {})
    print("SYNC resp:", json.dumps(s, ensure_ascii=False)[:500])
except urllib.error.HTTPError as e:
    print("HTTP", e.code, e.read().decode()[:1000])
