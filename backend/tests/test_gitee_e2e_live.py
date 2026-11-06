"""真实 Gitee → Issue 端到端测试（默认 skip）。

必须显式开启且配齐环境才跑：
    GITEE_E2E=true
    GITEE_TOKEN=<账号级令牌>
    GITEE_OWNER=<owner>
    GITEE_REPO=<仓库真实 path>

作用：调用真实 `main.create_gitee_issue` 在目标仓库创建一条 Issue，
返回里必须带 id/number/html_url。任何情况下不得打印/断言 token 值。

状态（2026-09-08）：Gitee 平台对 `POST /repos/{o}/{r}/issues` 返回
`404 {"message":"project or enterprise"}`（同令牌 milestone 建 201、GET 列表 200），
此测试在默认环境保持 skip；token 配齐且平台放开写时才作为真实通过证据。
"""
import datetime
import os

import pytest

from app import main

_ET = os.environ.get
skip = _ET("GITEE_E2E") != "true" or not (
    _ET("GITEE_TOKEN") and _ET("GITEE_OWNER") and _ET("GITEE_REPO")
)
pytestmark = pytest.mark.skipif(skip, reason="GITEE_E2E 未开启或 GITEE_TOKEN/OWNER/REPO 未配齐")


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def test_create_gitee_issue_live() -> None:
    token = os.environ["GITEE_TOKEN"]
    owner = os.environ["GITEE_OWNER"]
    repo = os.environ["GITEE_REPO"]
    cur = _now()

    rep = main.RepositoryConfigRecord(
        id="repo_gitee_live_e2e", project_id="proj_live_e2e", name=repo,
        url=f"https://gitee.com/{owner}/{repo}.git", default_branch="master",
        test_command="pytest -q", command_whitelist=["pytest -q"], test_command_fallback=True,
        auto_create_task_after_sync=False, approval_mode="single", risk_threshold="medium",
        max_files=50, max_retries=2, blocked_paths=[], created_at=cur, updated_at=cur,
    )
    req = main.TeamRequestRecord(
        id="REQ-GITEE-LIVE", request_no="REQ-GITEE-LIVE", project_id=rep.project_id,
        project_name="E2E", repository_id=rep.id, repository_name=repo,
        type="bug", title=f"真实Gitee E2E {cur.strftime('%m%d%H%M%S')}",
        body="真实 Issue 创建 E2E，内容足够长通过校验。",
        acceptance_criteria=["Gitee Issue 真实创建"], priority="high", labels=["bug", "e2e"],
        target_branch="master", test_command="pytest -q", sync_status="DRAFT",
        content_sufficient=True, validation_warnings=[], created_by_id="user_developer",
        created_by_name="开发", created_at=cur, updated_at=cur,
    )

    main.REPOSITORIES[rep.id] = rep
    main.TEAM_REQUESTS[req.id] = req
    # 真实链路注入令牌（绝不打印/断言其值）
    main.REPOSITORY_TOKENS[rep.id] = token
    try:
        issue = main.create_gitee_issue(req)
        assert isinstance(issue, dict), f"期望 dict，得到 {type(issue)}"
        assert issue.get("id") and issue.get("number") and issue.get("html_url"), (
            "Gitee Issue 响应缺少 id/number/html_url"
        )
    finally:
        main.REPOSITORIES.pop(rep.id, None)
        main.TEAM_REQUESTS.pop(req.id, None)
        main.REPOSITORY_TOKENS.pop(rep.id, None)