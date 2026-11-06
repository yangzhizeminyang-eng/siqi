"""真实 Gitee Pull Request 创建。

注意（重要）：Gitee 建 Issue 端点曾为非直觉的 owner 作用域（POST /repos/{owner}/issues + repo 参数）。
PR 创建端点以 Gitee SDK 文档 `POST /repos/{owner}/{repo}/pulls` 为主；若返回 404 `project or enterprise`，
按 owner 作用域回退 `POST /repos/{owner}/pulls` + repo 参数。首次真实验证确认，不假设。

token 只进入请求体 access_token；任何异常消息/日志不得含 token。
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request


class GiteePrError(Exception):
    """PR 失败；message 已脱敏（不含 token）。"""


def _post(path: str, data: dict, token: str | None, *, api_base: str | None = None) -> tuple[int, dict]:
    url = (api_base or "https://gitee.com/api/v5").rstrip("/") + path
    payload = dict(data)
    if token:
        payload["access_token"] = token
    req = urllib.request.Request(
        url,
        data=urllib.parse.urlencode(payload).encode("utf-8"),
        headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded",
                 "User-Agent": "enterprise-agent-platform-mvp"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = json.loads(resp.read().decode("utf-8") or "{}")
            return 200, raw if isinstance(raw, dict) else {}
    except urllib.error.HTTPError as err:
        try:
            body = json.loads(err.read().decode("utf-8") or "{}")
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}
        return err.code, body


def _message(body: dict) -> str:
    return str(body.get("message") or body.get("messages") or body.get("error") or "")


def create_gitee_pull_request(
    url: str,
    token: str | None,
    *,
    head_branch: str,
    base_branch: str,
    title: str,
    body: str,
    api_base: str | None = None,
) -> dict:
    """创建真实 Gitee PR，返回 {id, number, html_url, state}。失败抛 GiteePrError（message 脱敏）。

    api_base 仅用于本地契约测试注入（默认 None = 平台地址，零行为变化）：
    非默认值时才向 `_post` 透传，保持既有 monkeypatch 桩签名兼容。
    """
    owner, repo = _parse(url)
    payload = {"head": head_branch, "base": base_branch, "title": title, "body": body}
    post_kwargs = {"api_base": api_base} if api_base else {}
    code, resp = _post(f"/repos/{owner}/{repo}/pulls", payload, token, **post_kwargs)
    if code in (200, 201):
        number = resp.get("number") or ""
        html_url = resp.get("html_url") or resp.get("url") or ""
        if not (number or html_url):
            raise GiteePrError(f"Gitee PR 创建响应缺少 number/html_url：HTTP {code}")
        return {"id": str(resp.get("id", "")), "number": str(number), "html_url": str(html_url),
                "state": str(resp.get("state") or "OPEN")}
    if code == 404 and "enterprise" in _message(resp):
        # 该仓库 scope 化端点未命中 → 按 owner 作用域回退（repo 作参数），语义同建 Issue 的坑
        retry_payload = dict(payload); retry_payload["repo"] = repo
        code2, resp2 = _post(f"/repos/{owner}/pulls", retry_payload, token, **post_kwargs)
        if code2 in (200, 201):
            number = resp2.get("number") or ""
            html_url = resp2.get("html_url") or resp2.get("url") or ""
            if number or html_url:
                return {"id": str(resp2.get("id", "")), "number": str(number), "html_url": str(html_url),
                        "state": str(resp2.get("state") or "OPEN")}
        raise GiteePrError(f"Gitee PR 创建失败：HTTP {code2} {_message(resp2)}".strip())
    raise GiteePrError(f"Gitee PR 创建失败：HTTP {code} {_message(resp)}".strip())


def _parse(url: str) -> tuple[str, str]:
    from app.services.repo_mirror import parse_gitee_repository

    try:
        return parse_gitee_repository(url)
    except Exception as exc:  # noqa: BLE001
        raise GiteePrError("无法解析 Gitee 仓库 URL（owner/repo）") from exc