"""Gitee Provider：`GitHostProvider` 契约的 Gitee 实现（当前唯一支持）。

传输面（分支 / Issue）为本模块独立 urllib 实现，语义对齐 app/main.py 的
`gitee_api_get/post`（token：GET 走查询参数、POST 走请求体；401/403 → auth_expired；
其余 HTTP → field_error；网络失败/超时 → network_timeout）；`api_base` 可注入，
供本地契约测试用 127.0.0.1 stub 服务器真实走一遍 HTTP。

PR 创建委托 `gitee_pr_service.create_gitee_pull_request`：按模块属性在调用时读取，
保持既有测试的 monkeypatch 补丁链生效；`api_base` 为平台默认时不得追加该 kwarg，
兼容既有 fake 的精确签名（无 **kwargs）。token 绝不进入任何异常消息。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from app.services import repo_mirror
from app.services.git_host.contract import (
    AUTH_EXPIRED,
    FIELD_ERROR,
    INVALID_REPOSITORY,
    NETWORK_TIMEOUT,
    RESPONSE_INVALID,
    BranchHeadRef,
    GitHostError,
    IssueCommentRef,
    IssueRef,
    IssueSnapshotRef,
    PullRequestRef,
    RepositoryRef,
    redact,
)

DEFAULT_API_BASE = "https://gitee.com/api/v5"
_MAX_DEDUPE_PAGES = 6
_PAGE_SIZE = 100
_USER_AGENT = "enterprise-agent-platform-mvp"
_TIMEOUT_SECONDS = 12


class GiteeProvider:
    name = "gitee"

    def __init__(self, *, api_base: str | None = None) -> None:
        self._api_base = (api_base or DEFAULT_API_BASE).rstrip("/")

    # ---- 仓库地址语义（委托 repo_mirror，保持镜像/推送行为一致） ----

    def parse_repository(self, url: str) -> RepositoryRef:
        try:
            owner, repo = repo_mirror.parse_gitee_repository(url)
        except repo_mirror.RepoMirrorError as err:
            raise GitHostError(str(err), kind=INVALID_REPOSITORY) from None
        return RepositoryRef(owner=owner, repo=repo)

    def authenticated_remote(self, url: str, token: str | None) -> str:
        return repo_mirror.build_authenticated_remote(url, token)

    def mask_remote(self, url: str) -> str:
        return repo_mirror.mask_remote(url)

    # ---- 分支 / Issue（独立传输，可注入 api_base 做本地契约验证） ----

    def fetch_branch_head(self, repository_url: str, branch: str, token: str | None) -> BranchHeadRef:
        ref = self.parse_repository(repository_url)
        raw = self._get_json(
            f"/repos/{_quote(ref.owner)}/{_quote(ref.repo)}/branches/{_quote(branch)}", token,
        )
        if not isinstance(raw, dict) or not isinstance(raw.get("commit"), dict) or not raw["commit"].get("sha"):
            raise GitHostError("Gitee 分支接口响应格式异常，无法获取最新 Commit", kind=RESPONSE_INVALID)
        return BranchHeadRef(branch=branch, commit_sha=str(raw["commit"]["sha"]))

    def create_issue(
        self, repository_url: str, token: str | None, *, title: str, body: str,
        labels: list[str] | None = None,
    ) -> IssueRef:
        """建 Issue：Gitee 为 owner 作用域端点 `POST /repos/{owner}/issues`，仓库名走请求体 `repo`。"""
        ref = self.parse_repository(repository_url)
        payload: dict[str, str] = {"title": title, "body": body, "repo": ref.repo}
        if labels:
            payload["labels"] = ",".join(labels)
        raw = self._post_json(f"/repos/{_quote(ref.owner)}/issues", token, payload)
        if not isinstance(raw, dict):
            raise GitHostError("Gitee Issue 响应格式异常", kind=RESPONSE_INVALID)
        return _issue_ref(raw)

    def find_issue_by_marker(self, repository_url: str, token: str | None, marker: str) -> IssueRef | None:
        """按来源标识在仓库 Issue 列表翻页查重；auth 错误上抛，其余异常/未命中返回 None。

        翻页上限 `_MAX_DEDUPE_PAGES`；某页为空或不足 `_PAGE_SIZE` 条时提前停止
        （与 main.py 查重语义一致：超时重试前定位已建 Issue 用）。
        """
        ref = self.parse_repository(repository_url)
        path = f"/repos/{_quote(ref.owner)}/{_quote(ref.repo)}/issues"
        for page in range(1, _MAX_DEDUPE_PAGES + 1):
            try:
                items = self._get_json(path, token, query={
                    "state": "all", "page": page, "per_page": _PAGE_SIZE,
                    "sort": "created", "direction": "desc",
                })
            except GitHostError as err:
                if err.kind == AUTH_EXPIRED:
                    raise
                return None
            if not isinstance(items, list) or not items:
                return None
            for item in items:
                if isinstance(item, dict) and marker in (item.get("body") or ""):
                    return _issue_ref(item)
            if len(items) < _PAGE_SIZE:
                return None
        return None

    def fetch_issue_snapshot(self, repository_url: str, token: str | None, issue_id: str) -> IssueSnapshotRef:
        ref = self.parse_repository(repository_url)
        safe_issue = _quote(issue_id.strip().lstrip("#"))
        base = f"/repos/{_quote(ref.owner)}/{_quote(ref.repo)}/issues/{safe_issue}"
        raw = self._get_json(base, token)
        comments_raw = self._get_json(f"{base}/comments", token)
        if not isinstance(raw, dict):
            raise GitHostError("Gitee Issue 响应格式异常", kind=RESPONSE_INVALID)
        if not isinstance(comments_raw, list):
            comments_raw = []
        labels = [
            label.get("name", "")
            for label in (raw.get("labels") or [])
            if isinstance(label, dict) and label.get("name")
        ]
        comments = [
            IssueCommentRef(
                id=str(comment.get("id", "")),
                user_name=_comment_user_name(comment),
                body=comment.get("body") or "",
                created_at=comment.get("created_at"),
            )
            for comment in comments_raw
            if isinstance(comment, dict)
        ]
        return IssueSnapshotRef(
            title=raw.get("title") or f"Issue #{safe_issue}",
            body=raw.get("body") or "",
            labels=labels,
            comments=comments,
        )

    # ---- PR：委托 gitee_pr_service（模块属性读取，保持补丁兼容） ----

    def create_pull_request(
        self, repository_url: str, token: str | None, *, head_branch: str, base_branch: str,
        title: str, body: str,
    ) -> PullRequestRef:
        from app.services import gitee_pr_service

        kwargs: dict[str, str] = {}
        if self._api_base != DEFAULT_API_BASE:
            kwargs["api_base"] = self._api_base
        pr = gitee_pr_service.create_gitee_pull_request(
            repository_url, token, head_branch=head_branch, base_branch=base_branch,
            title=title, body=body, **kwargs,
        )
        return PullRequestRef(
            id=str(pr.get("id") or ""),
            number=str(pr.get("number") or ""),
            html_url=str(pr.get("html_url") or ""),
            state=str(pr.get("state") or "OPEN"),
        )

    # ---- 传输核心 ----

    def _get_json(self, path: str, token: str | None, *, query: dict | None = None):
        return self._send("GET", path, token, data=None, query=query)

    def _post_json(self, path: str, token: str | None, data: dict):
        return self._send("POST", path, token, data=data, query=None)

    def _send(self, method: str, path: str, token: str | None, *, data: dict | None, query: dict | None):
        url = self._api_base + path
        headers = {"Accept": "application/json", "User-Agent": _USER_AGENT}
        body: bytes | None = None
        if method == "POST":
            payload = dict(data or {})
            if token:
                payload["access_token"] = token  # POST token 仅在请求体，不进 URL
            body = urllib.parse.urlencode(payload).encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            params = dict(query or {})
            if token:
                params["access_token"] = token
            if params:
                url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            message = f"Gitee 接口请求失败：HTTP {error.code}"
            try:
                parsed = json.loads(error.read().decode("utf-8"))
                message = parsed.get("message") or parsed.get("error") or message
            except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
                pass
            kind = AUTH_EXPIRED if error.code in (401, 403) else FIELD_ERROR
            raise GitHostError(redact(message, token), kind=kind) from None
        except urllib.error.URLError as error:
            raise GitHostError(f"Gitee 网络连接失败：{redact(str(error.reason), token)}", kind=NETWORK_TIMEOUT) from None
        except TimeoutError:
            raise GitHostError("Gitee 请求超时，请稍后重试", kind=NETWORK_TIMEOUT) from None
        except json.JSONDecodeError:
            raise GitHostError("Gitee 响应不是合法 JSON", kind=RESPONSE_INVALID) from None


def _quote(value: str) -> str:
    return urllib.parse.quote(value, safe="")


def _issue_ref(raw: dict) -> IssueRef:
    return IssueRef(
        id=str(raw.get("id") or ""),
        number=str(raw.get("number") or ""),
        html_url=str(raw.get("html_url") or ""),
    )


def _comment_user_name(comment: dict) -> str:
    user = comment.get("user") or {}
    return user.get("name") or user.get("login") or "未知用户"