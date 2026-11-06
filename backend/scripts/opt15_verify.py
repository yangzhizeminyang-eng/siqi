"""OPT-15 验证 harness（默认隔离环境；真实 socket HTTP + 真实 git 二进制 + 真实子进程）。

不做假验证：拉起 127.0.0.1 真实 socket stub 服务器（scripted 响应 + 请求捕获）跑通
GitHost Provider 的传输契约；用真实 git 二进制在本地 bare 仓库完成 clone/worktree/
commit/push 全链路（provider.authenticated_remote 本地路径透传）；子进程验证
GIT_HOST_PROVIDER fail-closed 与注册表显式拒绝；recorder 验证 E4 采纳链
（release_service → registry → GiteeProvider → gitee_pr_service 模块属性）。
不调用真实 Gitee/GitHub/GitLab 远端，不写任何真实仓库。

用法：.venv/Scripts/python.exe -m scripts.opt15_verify
退出码：0=全部检查通过；2=存在失败检查（报告如实落盘，不伪造成功）。
报告：backend/.data/opt15-exp/opt15_evidence.json（终稿副本入 docs/project-development/assets/opt15/）。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import urllib.parse
from collections import deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable
OUT_DIR = BACKEND_ROOT / ".data" / "opt15-exp"
HERMETIC_ENV_FILE = BACKEND_ROOT / "tests" / ".env.hermetic-nonexistent"

TOKEN = "tok15-harness-secret-0123456789"
OWNER = "example-org"
REPO = "health-middle"
REPO_URL = f"https://gitee.com/{OWNER}/{REPO}.git"
BRANCH = "main"
STUB_SHA = "f" * 40
MARKER = "来源：平台需求 REQ-OPT15-HARNESS（本平台自动同步，请勿手工编辑）"


def _isolated_env(extra: dict | None = None) -> dict:
    env = dict(os.environ)
    env.update({
        "ENV_FILE": str(HERMETIC_ENV_FILE),
        "USE_SQLALCHEMY": "false",
        "MODEL_MOCK_FALLBACK": "true",
        "USE_CELERY": "false",
        "PYTHONUNBUFFERED": "1",
        "PYTHONIOENCODING": "utf-8",
    })
    for leaky in ("GIT_HOST_PROVIDER", "E4_PR_PROVIDER", "E4_RELEASE_STATE_DIR",
                  "ALLOW_SCRATCH_REAL_CHANGE", "AUTH_MODE", "DATABASE_URL"):
        env.pop(leaky, None)
    if extra:
        env.update(extra)
    return env


def _run_py(code: str, env_extra: dict | None = None, timeout: int = 180) -> subprocess.CompletedProcess:
    return subprocess.run(
        [PY, "-c", code], cwd=str(BACKEND_ROOT), env=_isolated_env(env_extra),
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
    )


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _force_rmtree(path: Path) -> None:
    """删除可能含 git 只读对象（Windows）的目录；先清只读位再删。"""
    if not path.exists():
        return

    def _clear(func, target, exc) -> None:  # noqa: ARG001
        os.chmod(target, 0o700)
        func(target)

    try:
        shutil.rmtree(path, onexc=_clear)
    except TypeError:  # Python < 3.12
        shutil.rmtree(path, onerror=_clear)


# ---------------------------------------------------------------------------
# 真实 socket stub 服务器（本地 127.0.0.1，零外网）
# ---------------------------------------------------------------------------


class StubState:
    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.script: deque[tuple[int, object]] = deque()
        self.issues_pages: list[list[dict]] | None = None
        self.issue_detail: dict | None = None
        self.issue_comments: list[dict] | None = None


class StubHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args) -> None:
        pass

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length).decode("utf-8", errors="replace")
        return dict(urllib.parse.parse_qsl(raw, keep_blank_values=True))

    def _dispatch(self, method: str) -> None:
        state: StubState = self.server.stub_state
        parsed = urllib.parse.urlparse(self.path)
        query = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
        state.requests.append({
            "method": method, "path": parsed.path, "query": query,
            "body": self._read_body() if method == "POST" else {},
            "user_agent": self.headers.get("User-Agent") or "",
            "content_type": self.headers.get("Content-Type") or "",
        })
        if state.script:
            code, payload = state.script.popleft()
            self._respond(code, payload)
            return
        self._route(method, parsed.path, query, state.requests[-1])

    def _respond(self, code: int, payload) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _route(self, method: str, path: str, query: dict, record: dict) -> None:
        state: StubState = self.server.stub_state
        parts = [urllib.parse.unquote(p) for p in path.split("/") if p]
        if parts[:2] == ["api", "v5"]:
            parts = parts[2:]
        if method == "GET" and len(parts) == 5 and parts[0] == "repos" and parts[3] == "branches":
            self._respond(200, {"name": parts[4], "commit": {"sha": STUB_SHA, "branch": parts[4]}})
            return
        if method == "POST" and len(parts) == 3 and parts[0] == "repos" and parts[1] == OWNER and parts[2] == "issues":
            number = "I15HAR1"
            self._respond(201, {"id": "issue-harness", "number": number, "state": "open",
                                "title": record["body"].get("title", ""),
                                "html_url": f"https://gitee.com/{OWNER}/{REPO}/issues/{number}"})
            return
        if method == "GET" and len(parts) == 4 and parts[0] == "repos" and parts[3] == "issues":
            page = int(query.get("page") or 1)
            pages = state.issues_pages or []
            self._respond(200, list(pages[page - 1]) if page - 1 < len(pages) else [])
            return
        if method == "GET" and len(parts) == 5 and parts[0] == "repos" and parts[3] == "issues":
            self._respond(200, dict(state.issue_detail or {}))
            return
        if method == "GET" and len(parts) == 6 and parts[0] == "repos" and parts[3] == "issues" and parts[5] == "comments":
            self._respond(200, list(state.issue_comments or []))
            return
        if method == "POST" and len(parts) == 4 and parts[0] == "repos" and parts[3] == "pulls":
            self._respond(201, {"id": 1500, "number": "PR15HAR", "state": "OPEN",
                                "html_url": f"https://gitee.com/{OWNER}/{REPO}/pulls/PR15HAR"})
            return
        self._respond(404, {"message": "stub 未覆盖的路由: " + path})


class StubServer:
    def __init__(self) -> None:
        self.state = StubState()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), StubHandler)
        self.server.stub_state = self.state
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}/api/v5"

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def _issue_item(index: int, body: str = "") -> dict:
    number = f"I{index:05d}"
    return {"id": f"item-{index}", "number": number, "body": body,
            "html_url": f"https://gitee.com/{OWNER}/{REPO}/issues/{number}"}


def _page(start: int, count: int, *, marker: str | None = None, marker_at: int = 0) -> list[dict]:
    items = [_issue_item(start + i) for i in range(count)]
    if marker is not None:
        items[marker_at]["body"] = marker
    return items


def _git(*args: str, cwd: Path) -> str:
    env = dict(os.environ)
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_TERMINAL_PROMPT"] = "0"
    proc = subprocess.run(["git", *args], cwd=str(cwd), env=env, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=180)
    if proc.returncode != 0:
        raise RuntimeError(f"git {args[0]} 失败: {(proc.stderr or proc.stdout)[:300]}")
    return proc.stdout


def main() -> int:
    parser = argparse.ArgumentParser(description="OPT-15 验证 harness")
    parser.add_argument("--keep", action="store_true", help="保留工作目录（默认也保留）")
    args = parser.parse_args()
    del args

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    # 隔离本进程环境后再 import app（ENV_FILE 屏掉开发机 .env，避免外部配置干扰证据）
    os.environ["ENV_FILE"] = str(HERMETIC_ENV_FILE)
    os.environ.pop("GIT_HOST_PROVIDER", None)
    os.environ.pop("E4_PR_PROVIDER", None)

    from app.services import repo_mirror
    from app.services.git_host import (
        AUTH_EXPIRED, FIELD_ERROR, INVALID_REPOSITORY, NETWORK_TIMEOUT, RESPONSE_INVALID,
        UNSUPPORTED_PROVIDER, GiteeProvider, GitHostError, GitHostProvider, get_git_host_provider,
    )

    checks: list[dict] = []

    def check(cid: str, desc: str, ok: bool, evidence: dict) -> None:
        checks.append({"id": cid, "desc": desc, "status": "pass" if ok else "fail", "evidence": evidence})

    report: dict = {
        "schema": "opt15-evidence-v1",
        "harness": "opt15_verify",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "environment": {
            "python": PY,
            "backend_root": str(BACKEND_ROOT),
            "real_remote_writes": "false（不触真实 Gitee/GitHub/GitLab 远端）",
        },
        "validation_status": {},
        "checks": checks,
        "summary": {},
    }

    # ------------------------------------------------------------------
    # A. 契约 + 传输（真实 socket stub HTTP）
    # ------------------------------------------------------------------
    provider = GiteeProvider()
    conformance = {
        "isinstance_protocol": isinstance(provider, GitHostProvider),
        "name": provider.name,
        "methods": {m: callable(getattr(provider, m, None)) for m in (
            "parse_repository", "authenticated_remote", "mask_remote", "fetch_branch_head",
            "create_issue", "find_issue_by_marker", "fetch_issue_snapshot", "create_pull_request")},
    }
    check("A1-contract-conformance", "GiteeProvider 满足 GitHostProvider 契约（8 方法 + name）",
          conformance["isinstance_protocol"] and provider.name == "gitee" and all(conformance["methods"].values()),
          conformance)

    forms = {}
    for url in ("https://gitee.com/owner/repo.git", "https://gitee.com/owner/repo",
                "git@gitee.com:owner/repo.git", "ssh://git@gitee.com/owner/repo.git"):
        ref = provider.parse_repository(url)
        forms[url] = f"{ref.owner}/{ref.repo}"
    invalid_kind = ""
    try:
        provider.parse_repository("https://github.com/owner/repo.git")
    except GitHostError as err:
        invalid_kind = err.kind
    remote = provider.authenticated_remote(REPO_URL, TOKEN)
    masked = provider.mask_remote(remote)
    check("A2-repository-semantics", "仓库解析多形态 / 非 gitee 拒绝 / 鉴权远程与掩码",
          set(forms.values()) == {"owner/repo"} and invalid_kind == INVALID_REPOSITORY
          and remote == f"https://oauth2:{TOKEN}@gitee.com/{OWNER}/{REPO}.git"
          and TOKEN not in masked and "***" in masked,
          {"forms": forms, "invalid_kind": invalid_kind, "remote_shape": "https://oauth2:***@gitee.com/...",
           "masked": masked})

    stub = StubServer()
    try:
        sp = GiteeProvider(api_base=stub.base)

        head = sp.fetch_branch_head(REPO_URL, BRANCH, TOKEN)
        req = stub.state.requests[-1]
        check("A3-branch-head-via-stub", "真实 HTTP：GET 分支 head 返回 sha，token 走查询参数",
              head.commit_sha == STUB_SHA and req["path"] == f"/api/v5/repos/{OWNER}/{REPO}/branches/{BRANCH}"
              and req["query"].get("access_token") == TOKEN,
              {"sha": head.commit_sha, "path": req["path"], "token_in_query": bool(req["query"].get("access_token"))})

        ref = sp.create_issue(REPO_URL, TOKEN, title="平台需求草稿", body="正文", labels=["bug", "sync"])
        req = stub.state.requests[-1]
        token_in_body = req["body"].get("access_token") == TOKEN
        token_not_in_url = TOKEN not in req["path"] and TOKEN not in urllib.parse.urlencode(req["query"])
        check("A4-issue-create-contract", "真实 HTTP：owner 作用域建 Issue + repo 参数 + token 仅在请求体",
              ref.number == "I15HAR1" and req["path"] == f"/api/v5/repos/{OWNER}/issues"
              and req["body"].get("repo") == REPO and req["body"].get("labels") == "bug,sync"
              and token_in_body and token_not_in_url,
              {"path": req["path"], "repo_param": req["body"].get("repo"), "labels": req["body"].get("labels"),
               "token_in_body": token_in_body, "token_not_in_url": token_not_in_url})

        # A5 查重分页三态：命中即停 / 空页停 / 满 100 续翻 / 上限 6 页
        def _page_seq_seen(state: StubState) -> list[str]:
            return [r["query"]["page"] for r in state.requests if r["path"].endswith("/issues") and r["method"] == "GET"]

        stub.state.requests.clear()
        stub.state.issues_pages = [_page(1, 100), _page(101, 20, marker=MARKER, marker_at=3), _page(201, 100)]
        hit = sp.find_issue_by_marker(REPO_URL, TOKEN, MARKER)
        hit_pages = _page_seq_seen(stub.state)

        stub.state.requests.clear()
        stub.state.issues_pages = [[]]
        empty = sp.find_issue_by_marker(REPO_URL, TOKEN, MARKER)
        empty_pages = _page_seq_seen(stub.state)

        stub.state.requests.clear()
        stub.state.issues_pages = [_page(1, 100), _page(101, 100), _page(201, 5, marker=MARKER, marker_at=2)]
        cont = sp.find_issue_by_marker(REPO_URL, TOKEN, MARKER)
        cont_pages = _page_seq_seen(stub.state)

        stub.state.requests.clear()
        stub.state.issues_pages = [_page(i * 100 + 1, 100) for i in range(7)]
        capped = sp.find_issue_by_marker(REPO_URL, TOKEN, MARKER)
        cap_pages = _page_seq_seen(stub.state)
        check("A5-dedupe-pagination", "查重分页四态：命中即停 / 空页停 / 满页续翻 / 上限 6 页",
              hit is not None and hit.number == "I00104" and hit_pages == ["1", "2"]
              and empty is None and empty_pages == ["1"]
              and cont is not None and cont.number == "I00203" and cont_pages == ["1", "2", "3"]
              and capped is None and cap_pages == ["1", "2", "3", "4", "5", "6"],
              {"hit": {"number": hit.number if hit else None, "pages": hit_pages},
               "empty": {"result": empty, "pages": empty_pages},
               "continue": {"number": cont.number if cont else None, "pages": cont_pages},
               "cap": {"result": capped, "pages": cap_pages}})

        # A6 错误分类：畸形响应 / 非 auth HTTP / 网络失败 / auth 上抛
        errors: dict = {}
        stub.state.script.append((200, {"name": BRANCH, "commit": {}}))
        try:
            sp.fetch_branch_head(REPO_URL, BRANCH, TOKEN)
        except GitHostError as err:
            errors["malformed"] = err.kind
        stub.state.script.append((500, {"message": "internal"}))
        try:
            sp.fetch_branch_head(REPO_URL, BRANCH, TOKEN)
        except GitHostError as err:
            errors["http_500"] = err.kind
        stub.state.script.append((401, {"message": "token expired"}))
        try:
            sp.find_issue_by_marker(REPO_URL, TOKEN, MARKER)
        except GitHostError as err:
            errors["auth_find"] = err.kind
        stub.state.script.append((500, {"message": "boom"}))
        errors["non_auth_find"] = sp.find_issue_by_marker(REPO_URL, TOKEN, MARKER)

        dead = _free_port()
        dead_provider = GiteeProvider(api_base=f"http://127.0.0.1:{dead}/api/v5")
        try:
            dead_provider.fetch_branch_head(REPO_URL, BRANCH, TOKEN)
        except GitHostError as err:
            errors["network"] = err.kind
        check("A6-error-classification", "错误分类：畸形=response_invalid / 500=field_error / 401=auth 上抛 / 断网=network_timeout / 非 auth 降级 None",
              errors.get("malformed") == RESPONSE_INVALID and errors.get("http_500") == FIELD_ERROR
              and errors.get("auth_find") == AUTH_EXPIRED and errors.get("non_auth_find") is None
              and errors.get("network") == NETWORK_TIMEOUT,
              errors)

        # A7 脱敏：stub 故意回显 token，异常消息必须打码
        stub.state.script.append((403, {"message": f"bad {TOKEN}; access_token={TOKEN} https://oauth2:{TOKEN}@gitee.com/x.git"}))
        redacted = ""
        redact_kind = ""
        try:
            sp.create_issue(REPO_URL, TOKEN, title="t", body="b")
        except GitHostError as err:
            redacted, redact_kind = str(err), err.kind
        check("A7-error-redaction", "stub 回显 token 时异常消息仍被脱敏（不含 token，含 ***）",
              redact_kind == AUTH_EXPIRED and TOKEN not in redacted and "***" in redacted,
              {"kind": redact_kind, "message_shape": redacted[:120]})

        # A8 快照归一化：labels 取 name、comments 兼容 name/login、# 前缀去除
        stub.state.issue_detail = {"title": "回调幂等修复", "body": "修复重复入账",
                                   "labels": [{"name": "bug"}, {"id": 1}], "state": "open"}
        stub.state.issue_comments = [
            {"id": "c1", "user": {"name": "张三"}, "body": "请补充测试", "created_at": "2026-10-16T10:00:00+08:00"},
            {"id": "c2", "user": {"login": "reviewer"}, "body": "已复核", "created_at": None},
        ]
        snap = sp.fetch_issue_snapshot(REPO_URL, TOKEN, "#I15HAR1")
        snap_paths = [r["path"] for r in stub.state.requests if "I15HAR1" in r["path"]]
        check("A8-snapshot-normalization", "Issue 快照归一化：labels/评论用户名/# 前缀",
              snap.title == "回调幂等修复" and snap.labels == ["bug"]
              and [c.user_name for c in snap.comments] == ["张三", "reviewer"]
              and any(p.endswith("/issues/I15HAR1") for p in snap_paths)
              and any(p.endswith("/issues/I15HAR1/comments") for p in snap_paths),
              {"title": snap.title, "labels": snap.labels, "comment_users": [c.user_name for c in snap.comments],
               "paths": snap_paths})

        # A9 PR 契约：api_base 注入后真实走 gitee_pr_service._post → stub
        pr = sp.create_pull_request(REPO_URL, TOKEN, head_branch="agent/opt15", base_branch="main",
                                    title="OPT-15 harness PR", body="body")
        req = stub.state.requests[-1]
        check("A9-pull-request-stub", "真实 HTTP：PR 创建委托 gitee_pr_service 打到 stub（head/base/token 契约）",
              pr.number == "PR15HAR" and pr.state == "OPEN"
              and req["path"] == f"/api/v5/repos/{OWNER}/{REPO}/pulls"
              and req["body"].get("head") == "agent/opt15" and req["body"].get("base") == "main"
              and req["body"].get("access_token") == TOKEN,
              {"number": pr.number, "path": req["path"], "head": req["body"].get("head"),
               "base": req["body"].get("base"), "token_in_body": req["body"].get("access_token") == TOKEN})
    finally:
        stub.stop()

    # ------------------------------------------------------------------
    # B. 本地 git e2e（真实 git 二进制；bare 仓库作为 origin，零外网）
    # ------------------------------------------------------------------
    work = OUT_DIR / "git-e2e"
    _force_rmtree(work)
    bare = work / "origin.git"
    seed = work / "seed"
    try:
        bare.parent.mkdir(parents=True, exist_ok=True)
        _git("init", "--bare", str(bare), cwd=work)
        _git("symbolic-ref", "HEAD", "refs/heads/main", cwd=bare)  # bare 默认 HEAD=master；镜像 fetch +HEAD 需指向真实分支
        seed.mkdir(parents=True, exist_ok=True)
        _git("init", "-b", "main", str(seed), cwd=work)
        (seed / "README.md").write_text("opt15 local git e2e\n", encoding="utf-8")
        _git("add", "-A", cwd=seed)
        _git("-c", "user.name=opt15-seed", "-c", "user.email=seed@localhost", "commit", "-m", "init", cwd=seed)
        _git("remote", "add", "origin", str(bare), cwd=seed)
        _git("push", "origin", "HEAD:refs/heads/main", cwd=seed)

        passthrough = provider.authenticated_remote(str(bare), TOKEN) == str(bare)
        mirror = repo_mirror.ensure_mirror("opt15_git_e2e", str(bare), TOKEN, base_dir=work / "repos")
        base_sha = repo_mirror.resolve_commit_sha(mirror, "main")
        wt = repo_mirror.checkout_commit(mirror, base_sha, work / "wt")
        (wt / "feature.txt").write_text("opt15 provider local e2e\n", encoding="utf-8")
        repo_mirror.commit_changes(wt, "feat(opt15): local git e2e", user="opt15-bot", email="opt15@localhost")
        new_sha = _git("rev-parse", "HEAD", cwd=wt).strip()
        repo_mirror.push_head_branch(wt, "agent/opt15/local-e2e")
        pushed_sha = _git("rev-parse", "refs/heads/agent/opt15/local-e2e", cwd=bare).strip()
        check("B1-local-git-e2e", "本地 git 全链路：本地路径透传 → clone/worktree/commit/push → bare 分支 sha 一致",
              passthrough and base_sha != new_sha and pushed_sha == new_sha,
              {"local_path_passthrough": passthrough, "base_sha": base_sha[:12], "new_sha": new_sha[:12],
               "pushed_sha": pushed_sha[:12], "bare": str(bare.relative_to(BACKEND_ROOT))})
    except Exception as exc:  # noqa: BLE001 证据如实记录失败
        check("B1-local-git-e2e", "本地 git 全链路：本地路径透传 → clone/worktree/commit/push → bare 分支 sha 一致",
              False, {"error": f"{type(exc).__name__}: {exc}"})

    # ------------------------------------------------------------------
    # C. fail-closed：GIT_HOST_PROVIDER 启动期 + 注册表显式拒绝
    # ------------------------------------------------------------------
    local = _run_py("from app.core.runtime_config import load_runtime_settings as L; print(L().git_host_provider)")
    check("C1-provider-default-gitee", "GIT_HOST_PROVIDER 默认 gitee",
          local.returncode == 0 and local.stdout.strip() == "gitee",
          {"returncode": local.returncode, "stdout": local.stdout.strip()})

    gitlab = _run_py("from app.core.runtime_config import load_runtime_settings as L; L()",
                     {"GIT_HOST_PROVIDER": "gitlab"})
    check("C2-provider-fail-closed-startup", "GIT_HOST_PROVIDER=gitlab（未实现）→ 拒绝启动，报错含 GIT_HOST_PROVIDER/fail-closed",
          gitlab.returncode != 0 and "GIT_HOST_PROVIDER" in gitlab.stderr and "fail-closed" in gitlab.stderr,
          {"returncode": gitlab.returncode, "stderr_tail": gitlab.stderr.strip()[-220:]})

    registry_code = (
        "import json\n"
        "from app.services.git_host import get_git_host_provider, GitHostError\n"
        "try:\n"
        "    get_git_host_provider('github')\n"
        "    print(json.dumps({'raised': False, 'kind': ''}))\n"
        "except GitHostError as err:\n"
        "    print(json.dumps({'raised': True, 'kind': err.kind, 'message': str(err)}))\n"
    )
    registry = _run_py(registry_code)
    reg_payload = json.loads(registry.stdout.strip().splitlines()[-1]) if registry.stdout.strip() else {}
    check("C3-registry-explicit-refusal", "注册表显式请求 github → unsupported_provider（绝不静默回退 gitee）",
          registry.returncode == 0 and reg_payload.get("raised") is True
          and reg_payload.get("kind") == UNSUPPORTED_PROVIDER and "gitee" in reg_payload.get("message", ""),
          {"returncode": registry.returncode, "payload": reg_payload})

    # ------------------------------------------------------------------
    # D. 采纳链证据：release_service → registry → GiteeProvider → gitee_pr_service（模块属性）
    # ------------------------------------------------------------------
    from app.services import enterprise_release_service as release_service
    from app.services import gitee_pr_service

    state_dir = OUT_DIR / "d1-state"
    _force_rmtree(state_dir)
    os.environ["E4_RELEASE_STATE_DIR"] = str(state_dir)
    recorded: dict = {}
    originals = {
        "create": gitee_pr_service.create_gitee_pull_request,
        "push": release_service._prepare_and_push_runtime_branch,
        "repo": release_service._repository_for_task,
        "token": release_service._repository_token,
    }
    try:
        def recorder(url, token, *, head_branch, base_branch, title, body, **kwargs):
            recorded.update({"url": url, "token": token, "head_branch": head_branch,
                             "base_branch": base_branch, "title": title, "body": body, "kwargs": sorted(kwargs)})
            return {"id": "9500", "number": "PR-OPT15", "state": "OPEN",
                    "html_url": "https://gitee.com/example/opt15-probe/pulls/PR-OPT15"}

        gitee_pr_service.create_gitee_pull_request = recorder
        release_service._prepare_and_push_runtime_branch = (
            lambda task, repository, *, title, body: {
                "head_branch": "agent/opt15/probe", "base_branch": "main",
                "base_commit_sha": "a" * 40, "commit_sha": "b" * 40, "diff_hash": "d" * 64})
        release_service._repository_for_task = lambda task: SimpleNamespace(
            id="repo_opt15_probe", url="https://gitee.com/example/opt15-probe.git", default_branch="main")
        release_service._repository_token = lambda repository: TOKEN
        result = release_service.create_runtime_pr(SimpleNamespace(id="rt_opt15_probe"),
                                                   title="OPT-15 probe", body="body")
        pr_file = state_dir / "rt_opt15_probe.pr.json"
        file_text = pr_file.read_text(encoding="utf-8") if pr_file.is_file() else ""
        result_text = json.dumps(result, ensure_ascii=False)
        check("D1-adoption-chain", "E4 create_runtime_pr 采纳链：provider=gitee 结果 + recorder 命中 + token 不落结果/文件",
              result.get("provider") == "gitee" and result.get("number") == "PR-OPT15"
              and result.get("head_branch") == "agent/opt15/probe"
              and recorded.get("head_branch") == "agent/opt15/probe" and recorded.get("token") == TOKEN
              and recorded.get("kwargs") == [] and pr_file.is_file()
              and TOKEN not in result_text and TOKEN not in file_text,
              {"provider": result.get("provider"), "number": result.get("number"),
               "recorded": {k: ("***" if k == "token" else v) for k, v in recorded.items()},
               "pr_state_file": str(pr_file.relative_to(BACKEND_ROOT)),
               "token_leaked_in_result": TOKEN in result_text, "token_leaked_in_file": TOKEN in file_text})
    finally:
        gitee_pr_service.create_gitee_pull_request = originals["create"]
        release_service._prepare_and_push_runtime_branch = originals["push"]
        release_service._repository_for_task = originals["repo"]
        release_service._repository_token = originals["token"]

    # ------------------------------------------------------------------
    # E. pytest 门禁（OPT-15 契约 + 采纳兼容回归）
    # ------------------------------------------------------------------
    contract_log = OUT_DIR / "opt15_pytest_contract.txt"
    contract_run = subprocess.run(
        [PY, "-m", "pytest", "tests/test_opt15_git_provider_contract.py", "-q"],
        cwd=str(BACKEND_ROOT), env=_isolated_env(), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=1200,
    )
    contract_log.write_text(contract_run.stdout + "\n" + contract_run.stderr, encoding="utf-8")
    contract_summary = ""
    for line in reversed((contract_run.stdout + contract_run.stderr).splitlines()):
        if "passed" in line or "failed" in line or "error" in line:
            contract_summary = line.strip()
            break
    check("E1-pytest-contract-gate", "OPT-15 契约专项 pytest（26 条）",
          contract_run.returncode == 0 and "26 passed" in contract_summary,
          {"returncode": contract_run.returncode, "summary": contract_summary,
           "log": str(contract_log.relative_to(BACKEND_ROOT))})

    compat_log = OUT_DIR / "opt15_pytest_compat.txt"
    compat_run = subprocess.run(
        [PY, "-m", "pytest", "tests/test_pr_creation.py", "tests/test_opt06_agent_tool_security.py",
         "tests/test_enterprise_e4_agents.py", "-q"],
        cwd=str(BACKEND_ROOT), env=_isolated_env(), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=1800,
    )
    compat_log.write_text(compat_run.stdout + "\n" + compat_run.stderr, encoding="utf-8")
    compat_summary = ""
    for line in reversed((compat_run.stdout + compat_run.stderr).splitlines()):
        if "passed" in line or "failed" in line or "error" in line:
            compat_summary = line.strip()
            break
    check("E2-pytest-compat-gate", "采纳兼容回归（test_pr_creation + test_opt06 + test_enterprise_e4_agents）",
          compat_run.returncode == 0 and "failed" not in compat_summary and "error" not in compat_summary,
          {"returncode": compat_run.returncode, "summary": compat_summary,
           "log": str(compat_log.relative_to(BACKEND_ROOT))})

    # ------------------------------------------------------------------
    passed = sum(1 for c in checks if c["status"] == "pass")
    report["summary"] = {"total": len(checks), "passed": passed, "failed": len(checks) - passed}
    report["validation_status"] = {
        "real_model_calls": False,
        "real_gitee_write": "not_collected：真实 Gitee 远端写入需逐项确认后再执行（本轮用本地 stub + 本地 bare 仓库全链路替代）",
        "real_github_gitlab": "not_implemented：GitHub/GitLab 未实现（注册表 + 启动期双重 fail-closed 已验证）",
        "stub_http": "真实 127.0.0.1 socket HTTP（scripted 响应 + 请求捕获）",
        "local_git": "真实 git 二进制（bare 仓库作为 origin，零外网）",
    }

    report_path = OUT_DIR / "opt15_evidence.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[opt15-verify] {passed}/{len(checks)} checks passed")
    for c in checks:
        print(f"  [{'PASS' if c['status'] == 'pass' else 'FAIL'}] {c['id']}: {c['desc']}")
    print(f"[opt15-verify] report: {report_path}")
    return 0 if passed == len(checks) else 2


if __name__ == "__main__":
    sys.exit(main())