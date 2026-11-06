"""OPT-15 代码托管 Provider 契约（GitHub/GitLab 适配）：

1. 契约抽象：`GitHostProvider` Protocol + `GiteeProvider` 实现（仓库解析 / 鉴权远程 /
   掩码 / 分支 head / Issue 创建与查重 / Issue 快照 / PR 创建）；
2. 本地契约测试：127.0.0.1 真实 socket stub HTTP 服务器（scripted 响应 + 路由表 +
   请求捕获），零外网验证 Gitee 传输语义（owner 作用域 Issue 端点、token 仅在请求体、
   查重分页三态、错误分类 auth_expired/field_error/network_timeout/response_invalid、
   错误消息脱敏绝不回显 token）；
3. 注册表：默认 gitee；显式非 gitee（GitHub/GitLab 未实现）→ fail-closed
   unsupported_provider，绝不静默回退；
4. 启动期边界：GIT_HOST_PROVIDER 非 gitee → load_runtime_settings 直接拒绝启动
   （子进程验证，镜像 OPT-14 AUTH_MODE 先例）。

补丁兼容约束（实现必须满足）：
- PR 委托必须按模块属性读取 `gitee_pr_service.create_gitee_pull_request`
  （test_pr_creation / test_opt06 / test_enterprise_e4_agents 用 monkeypatch 替换）；
- api_base 为默认时不得向委托调用追加 api_base kwarg（既有 fake 签名无 **kwargs）。

TDD：本文件先于实现落地（红）。
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import urllib.parse
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.git_host import (
    AUTH_EXPIRED,
    FIELD_ERROR,
    INVALID_REPOSITORY,
    NETWORK_TIMEOUT,
    RESPONSE_INVALID,
    UNSUPPORTED_PROVIDER,
    GiteeProvider,
    GitHostError,
    GitHostProvider,
    IssueRef,
    PullRequestRef,
    get_git_host_provider,
)

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable

TOKEN = "tok15_secret_abcdef"
OWNER = "example-org"
REPO = "health-middle"
REPO_URL = f"https://gitee.com/{OWNER}/{REPO}.git"
BRANCH = "main"
STUB_SHA = "f" * 40
MARKER = "来源：平台需求 REQ-OPT15（本平台自动同步，请勿手工编辑）"


# ---------------------------------------------------------------------------
# 本地隔离 stub HTTP 服务器（真实 socket，零外网）
# ---------------------------------------------------------------------------


class _StubState:
    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.script: deque[tuple[int, object]] = deque()  # 优先脚本化响应（按请求顺序消费）
        self.issues_pages: list[list[dict]] | None = None
        self.issue_detail: dict | None = None
        self.issue_comments: list[dict] | None = None


class _StubHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args) -> None:  # 测试期静音
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
        ctype = (self.headers.get("Content-Type") or "").lower()
        if "json" in ctype:
            try:
                return json.loads(raw)
            except ValueError:
                return {"_raw": raw}
        return dict(urllib.parse.parse_qsl(raw, keep_blank_values=True))

    def _dispatch(self, method: str) -> None:
        state: _StubState = self.server.stub_state
        parsed = urllib.parse.urlparse(self.path)
        query = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
        state.requests.append({
            "method": method,
            "path": parsed.path,
            "query": query,
            "body": self._read_body() if method == "POST" else {},
            "headers": {k.lower(): v for k, v in self.headers.items()},
        })
        if state.script:
            code, payload = state.script.popleft()
            self._respond(code, payload)
            return
        self._route(method, parsed.path, query, state.requests[-1])

    def _respond(self, code: int, payload) -> None:
        if isinstance(payload, (bytes, bytearray)):
            raw = bytes(payload)
        elif isinstance(payload, str):
            raw = payload.encode("utf-8")
        else:
            raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _route(self, method: str, path: str, query: dict, record: dict) -> None:
        state: _StubState = self.server.stub_state
        parts = [urllib.parse.unquote(p) for p in path.split("/") if p]
        if parts[:2] == ["api", "v5"]:
            parts = parts[2:]
        # GET /repos/{owner}/{repo}/branches/{branch}
        if method == "GET" and len(parts) == 5 and parts[0] == "repos" and parts[3] == "branches":
            self._respond(200, {"name": parts[4], "commit": {"sha": STUB_SHA, "branch": parts[4]}})
            return
        # POST /repos/{owner}/issues（owner 作用域，repo 走请求体参数）
        if method == "POST" and len(parts) == 3 and parts[0] == "repos" and parts[1] == OWNER and parts[2] == "issues":
            number = "I15AAA1"
            self._respond(201, {
                "id": "issue-15", "number": number, "title": record["body"].get("title", ""),
                "state": "open", "html_url": f"https://gitee.com/{OWNER}/{REPO}/issues/{number}",
            })
            return
        # GET /repos/{owner}/{repo}/issues?page=N（查重分页）
        if method == "GET" and len(parts) == 4 and parts[0] == "repos" and parts[3] == "issues":
            page = int(query.get("page") or 1)
            pages = state.issues_pages or []
            self._respond(200, list(pages[page - 1]) if page - 1 < len(pages) else [])
            return
        # GET /repos/{owner}/{repo}/issues/{number}
        if method == "GET" and len(parts) == 5 and parts[0] == "repos" and parts[3] == "issues":
            self._respond(200, dict(state.issue_detail or {}))
            return
        # GET /repos/{owner}/{repo}/issues/{number}/comments
        if method == "GET" and len(parts) == 6 and parts[0] == "repos" and parts[3] == "issues" and parts[5] == "comments":
            self._respond(200, list(state.issue_comments or []))
            return
        # POST /repos/{owner}/{repo}/pulls
        if method == "POST" and len(parts) == 4 and parts[0] == "repos" and parts[3] == "pulls":
            self._respond(201, {
                "id": 1500, "number": "PR15", "state": "OPEN",
                "html_url": f"https://gitee.com/{OWNER}/{REPO}/pulls/PR15",
            })
            return
        self._respond(404, {"message": "stub 未覆盖的路由: " + path})


@pytest.fixture()
def stub():
    state = _StubState()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _StubHandler)
    server.stub_state = state
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}/api/v5"
        yield SimpleNamespace(base=base, state=state)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _provider(stub: SimpleNamespace) -> GiteeProvider:
    return GiteeProvider(api_base=stub.base)


def _issue_item(index: int, body: str = "") -> dict:
    number = f"I{index:05d}"
    return {
        "id": f"item-{index}", "number": number, "body": body,
        "html_url": f"https://gitee.com/{OWNER}/{REPO}/issues/{number}",
    }


def _page(start: int, count: int, *, marker: str | None = None, marker_at: int = 0) -> list[dict]:
    items = [_issue_item(start + i) for i in range(count)]
    if marker is not None:
        items[marker_at]["body"] = marker
    return items


def _fetched_pages(stub: SimpleNamespace) -> list[str]:
    return [r["query"]["page"] for r in stub.state.requests]


# ---------------------------------------------------------------------------
# 1. 契约 conformance 与仓库地址语义（无网络）
# ---------------------------------------------------------------------------


def test_gitee_provider_satisfies_contract():
    provider = GiteeProvider()
    assert isinstance(provider, GitHostProvider)
    assert provider.name == "gitee"
    for attr in ("parse_repository", "authenticated_remote", "mask_remote", "fetch_branch_head",
                 "create_issue", "find_issue_by_marker", "fetch_issue_snapshot", "create_pull_request"):
        assert callable(getattr(provider, attr)), attr


def test_parse_repository_accepts_known_url_forms():
    provider = GiteeProvider()
    for url in (
        "https://gitee.com/owner/repo.git",
        "https://gitee.com/owner/repo",
        "git@gitee.com:owner/repo.git",
        "ssh://git@gitee.com/owner/repo.git",
    ):
        ref = provider.parse_repository(url)
        assert (ref.owner, ref.repo) == ("owner", "repo"), url


def test_parse_repository_rejects_unknown_host():
    provider = GiteeProvider()
    with pytest.raises(GitHostError) as err:
        provider.parse_repository("https://github.com/owner/repo.git")
    assert err.value.kind == INVALID_REPOSITORY
    assert "owner/repo" in str(err.value)


def test_authenticated_remote_embeds_token_and_passes_local_through(tmp_path):
    provider = GiteeProvider()
    assert provider.authenticated_remote(REPO_URL, TOKEN) == f"https://oauth2:{TOKEN}@gitee.com/{OWNER}/{REPO}.git"
    assert provider.authenticated_remote(REPO_URL, None) == f"https://gitee.com/{OWNER}/{REPO}.git"
    local = str(tmp_path / "repo.git")
    assert provider.authenticated_remote(local, TOKEN) == local, "本地路径（本地测试仓库）必须原样透传"


def test_mask_remote_hides_token():
    provider = GiteeProvider()
    masked = provider.mask_remote(f"https://oauth2:{TOKEN}@gitee.com/{OWNER}/{REPO}.git")
    assert TOKEN not in masked and "***" in masked
    assert provider.mask_remote(str(Path("C:/tmp/repo.git"))).startswith("<local:")


# ---------------------------------------------------------------------------
# 2. 传输契约（stub HTTP：分支 / Issue / 分页 / 快照 / 错误分类 / 脱敏 / PR）
# ---------------------------------------------------------------------------


def test_fetch_branch_head_returns_sha_via_token_query(stub):
    provider = _provider(stub)
    head = provider.fetch_branch_head(REPO_URL, BRANCH, TOKEN)
    assert head.branch == BRANCH and head.commit_sha == STUB_SHA
    req = stub.state.requests[-1]
    assert req["method"] == "GET"
    assert req["path"] == f"/api/v5/repos/{OWNER}/{REPO}/branches/{BRANCH}"
    assert req["query"]["access_token"] == TOKEN, "GET 请求 token 应走查询参数"


def test_fetch_branch_head_malformed_response_fails(stub):
    stub.state.script.append((200, {"name": BRANCH, "commit": {}}))
    provider = _provider(stub)
    with pytest.raises(GitHostError) as err:
        provider.fetch_branch_head(REPO_URL, BRANCH, TOKEN)
    assert err.value.kind == RESPONSE_INVALID


def test_http_error_maps_to_field_error(stub):
    stub.state.script.append((500, {"message": "internal"}))
    provider = _provider(stub)
    with pytest.raises(GitHostError) as err:
        provider.fetch_branch_head(REPO_URL, BRANCH, TOKEN)
    assert err.value.kind == FIELD_ERROR


def test_create_issue_uses_owner_scoped_endpoint_with_repo_param(stub):
    provider = _provider(stub)
    ref = provider.create_issue(REPO_URL, TOKEN, title="平台需求草稿", body="正文", labels=["bug", "sync"])
    assert isinstance(ref, IssueRef)
    assert ref.number == "I15AAA1" and ref.html_url.endswith("/issues/I15AAA1")
    req = stub.state.requests[-1]
    assert req["method"] == "POST"
    assert req["path"] == f"/api/v5/repos/{OWNER}/issues", "Gitee 建 Issue 是 owner 作用域端点"
    assert req["body"]["repo"] == REPO, "仓库名须走请求体 repo 参数"
    assert req["body"]["access_token"] == TOKEN, "POST 请求 token 仅在请求体"
    assert req["body"]["title"] == "平台需求草稿"
    assert req["body"]["labels"] == "bug,sync", "labels 以逗号连接"
    assert TOKEN not in req["path"] and TOKEN not in urllib.parse.urlencode(req["query"])
    assert req["headers"]["content-type"].startswith("application/x-www-form-urlencoded")


def test_find_issue_by_marker_stops_when_hit(stub):
    stub.state.issues_pages = [
        _page(1, 100),
        _page(101, 20, marker=MARKER, marker_at=3),
        _page(201, 100, marker=MARKER),
    ]
    provider = _provider(stub)
    ref = provider.find_issue_by_marker(REPO_URL, TOKEN, MARKER)
    assert ref is not None and ref.number == "I00104"
    assert _fetched_pages(stub) == ["1", "2"], "命中即停，不得继续翻页"


def test_find_issue_by_marker_stops_on_empty_page(stub):
    stub.state.issues_pages = [[]]
    provider = _provider(stub)
    assert provider.find_issue_by_marker(REPO_URL, TOKEN, MARKER) is None
    assert _fetched_pages(stub) == ["1"]
    req = stub.state.requests[-1]
    assert req["query"]["state"] == "all"
    assert req["query"]["per_page"] == "100"
    assert req["query"]["sort"] == "created"
    assert req["query"]["direction"] == "desc"
    assert req["query"]["access_token"] == TOKEN


def test_find_issue_by_marker_continues_across_full_pages(stub):
    stub.state.issues_pages = [
        _page(1, 100),
        _page(101, 100),
        _page(201, 5, marker=MARKER, marker_at=2),
        _page(301, 100, marker=MARKER),
    ]
    provider = _provider(stub)
    ref = provider.find_issue_by_marker(REPO_URL, TOKEN, MARKER)
    assert ref is not None and ref.number == "I00203"
    assert _fetched_pages(stub) == ["1", "2", "3"], "满 100 条须继续翻页"


def test_find_issue_by_marker_caps_pages(stub):
    stub.state.issues_pages = [_page(i * 100 + 1, 100) for i in range(7)]
    provider = _provider(stub)
    assert provider.find_issue_by_marker(REPO_URL, TOKEN, MARKER) is None
    assert _fetched_pages(stub) == ["1", "2", "3", "4", "5", "6"], "翻页上限 6 页"


def test_find_issue_by_marker_reraises_auth_error(stub):
    stub.state.script.append((401, {"message": "token expired"}))
    provider = _provider(stub)
    with pytest.raises(GitHostError) as err:
        provider.find_issue_by_marker(REPO_URL, TOKEN, MARKER)
    assert err.value.kind == AUTH_EXPIRED, "auth 类错误必须上抛（调用方置 SYNC_FAILED）"


def test_find_issue_by_marker_degrades_non_auth_error(stub):
    stub.state.script.append((500, {"message": "boom"}))
    provider = _provider(stub)
    assert provider.find_issue_by_marker(REPO_URL, TOKEN, MARKER) is None, "非 auth 错误降级为 None（可重试）"


def test_fetch_issue_snapshot_normalizes_labels_and_comments(stub):
    stub.state.issue_detail = {
        "title": "回调幂等修复", "body": "修复重复入账",
        "labels": [{"name": "bug"}, {"name": "sync"}, {"id": 1}], "state": "open",
    }
    stub.state.issue_comments = [
        {"id": "c1", "user": {"name": "张三"}, "body": "请补充测试", "created_at": "2026-10-16T10:00:00+08:00"},
        {"id": "c2", "user": {"login": "reviewer"}, "body": "已复核", "created_at": None},
    ]
    provider = _provider(stub)
    snapshot = provider.fetch_issue_snapshot(REPO_URL, TOKEN, "#I15AAA1")
    assert snapshot.title == "回调幂等修复" and snapshot.body == "修复重复入账"
    assert snapshot.labels == ["bug", "sync"], "labels 归一化为名称列表，无效项过滤"
    assert [c.user_name for c in snapshot.comments] == ["张三", "reviewer"]
    assert [c.body for c in snapshot.comments] == ["请补充测试", "已复核"]
    paths = [r["path"] for r in stub.state.requests]
    assert f"/api/v5/repos/{OWNER}/{REPO}/issues/I15AAA1" in paths, "# 前缀应去除"
    assert f"/api/v5/repos/{OWNER}/{REPO}/issues/I15AAA1/comments" in paths


def test_fetch_issue_snapshot_malformed_response_fails(stub):
    stub.state.script.append((200, []))
    provider = _provider(stub)
    with pytest.raises(GitHostError) as err:
        provider.fetch_issue_snapshot(REPO_URL, TOKEN, "I15AAA1")
    assert err.value.kind == RESPONSE_INVALID


def test_error_message_redacts_token(stub):
    stub.state.script.append((403, {"message": f"bad {TOKEN}; url https://oauth2:{TOKEN}@gitee.com/x.git access_token={TOKEN}"}))
    provider = _provider(stub)
    with pytest.raises(GitHostError) as err:
        provider.create_issue(REPO_URL, TOKEN, title="t", body="b")
    assert err.value.kind == AUTH_EXPIRED
    text = str(err.value)
    assert TOKEN not in text, "异常消息绝不回显 token"
    assert "***" in text


def test_network_failure_maps_to_network_timeout():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()  # 端口随即空闲 → 连接必然失败
    provider = GiteeProvider(api_base=f"http://127.0.0.1:{port}/api/v5")
    with pytest.raises(GitHostError) as err:
        provider.fetch_branch_head(REPO_URL, BRANCH, TOKEN)
    assert err.value.kind == NETWORK_TIMEOUT


def test_create_pull_request_via_stub_api_base(stub):
    provider = _provider(stub)
    pr = provider.create_pull_request(
        REPO_URL, TOKEN, head_branch="agent/opt15", base_branch="main", title="OPT-15 PR", body="body",
    )
    assert isinstance(pr, PullRequestRef)
    assert pr.number == "PR15" and pr.state == "OPEN" and pr.html_url.endswith("/pulls/PR15")
    req = stub.state.requests[-1]
    assert req["method"] == "POST"
    assert req["path"] == f"/api/v5/repos/{OWNER}/{REPO}/pulls"
    assert req["body"]["head"] == "agent/opt15" and req["body"]["base"] == "main"
    assert req["body"]["title"] == "OPT-15 PR"
    assert req["body"]["access_token"] == TOKEN


def test_create_pull_request_delegates_without_extra_kwargs(monkeypatch):
    """补丁兼容：按模块属性读取 gitee_pr_service；默认 api_base 时不追加 kwarg。"""
    from app.services import gitee_pr_service

    calls: dict = {}

    def fake_create(url, token, *, head_branch, base_branch, title, body):  # 精确签名（无 **kwargs）
        calls.update(url=url, token=token, head=head_branch, base=base_branch, title=title, body=body)
        return {"id": "9001", "number": "PR-9", "html_url": "https://gitee.com/x/y/pulls/9", "state": "OPEN"}

    monkeypatch.setattr(gitee_pr_service, "create_gitee_pull_request", fake_create)
    provider = GiteeProvider()
    pr = provider.create_pull_request(REPO_URL, TOKEN, head_branch="h", base_branch="m", title="t", body="b")
    assert isinstance(pr, PullRequestRef) and pr.number == "PR-9" and pr.state == "OPEN"
    assert calls == {"url": REPO_URL, "token": TOKEN, "head": "h", "base": "m", "title": "t", "body": "b"}


# ---------------------------------------------------------------------------
# 3. 注册表：默认 gitee；显式非 gitee fail-closed
# ---------------------------------------------------------------------------


def test_registry_defaults_to_gitee(monkeypatch):
    monkeypatch.delenv("GIT_HOST_PROVIDER", raising=False)
    provider = get_git_host_provider()
    assert isinstance(provider, GiteeProvider)
    assert isinstance(provider, GitHostProvider)
    assert provider.name == "gitee"


def test_registry_normalizes_explicit_name():
    assert get_git_host_provider("Gitee").name == "gitee"


def test_registry_unsupported_provider_fails_closed():
    for name in ("github", "gitlab"):
        with pytest.raises(GitHostError) as err:
            get_git_host_provider(name)
        assert err.value.kind == UNSUPPORTED_PROVIDER
        assert name in str(err.value)
        assert "gitee" in str(err.value), "错误消息须点明绝不静默回退 gitee"


# ---------------------------------------------------------------------------
# 4. 启动期边界：GIT_HOST_PROVIDER 非 gitee → fail-closed（子进程）
# ---------------------------------------------------------------------------


def _run_python(code: str, env_extra: dict | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    if env_extra is not None:
        env.update(env_extra)
    return subprocess.run(
        [PY, "-c", code], cwd=str(BACKEND_ROOT), env=env,
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
    )


def test_git_host_provider_defaults_to_gitee_at_startup():
    env = {k: v for k, v in os.environ.items() if k != "GIT_HOST_PROVIDER"}
    proc = subprocess.run(
        [PY, "-c", "from app.core.runtime_config import load_runtime_settings as L; print(L().git_host_provider)"],
        cwd=str(BACKEND_ROOT), env=env,
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
    )
    assert proc.returncode == 0, proc.stderr[-1500:]
    assert proc.stdout.strip() == "gitee"


def test_git_host_provider_non_gitee_fails_closed_at_startup():
    proc = _run_python(
        "from app.core.runtime_config import load_runtime_settings as L; L()",
        env_extra={"GIT_HOST_PROVIDER": "gitlab"},
    )
    assert proc.returncode != 0, "GIT_HOST_PROVIDER=gitlab（未实现）必须 fail-closed 拒绝启动"
    assert "GIT_HOST_PROVIDER" in proc.stderr
    assert "fail-closed" in proc.stderr