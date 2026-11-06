"""OPT-15 GitHost Provider 契约：托管平台无关的仓库/Issue/PR 操作抽象。

设计边界（与 docs/project-development/127 配套）：

* `GitHostProvider` 是 runtime_checkable Protocol，任何托管平台实现（当前仅 Gitee）
  必须满足同一份方法签名；注册表拒绝未实现平台的配置（fail-closed，绝不静默回退）；
* `GitHostError.kind` 与既有 HTTPException 语义对齐（auth_expired / field_error /
  network_timeout），并补充 response_invalid / invalid_repository / unsupported_provider；
* 任何异常消息都不得包含 token：`redact()` 同时做结构掩码（URL userinfo / access_token 参数）
  与字面量擦除（调用方传入的 token 值），第三方回显 token 也必须被打码。
"""

from __future__ import annotations

import dataclasses
import re
from typing import Protocol, runtime_checkable

AUTH_EXPIRED = "auth_expired"
FIELD_ERROR = "field_error"
NETWORK_TIMEOUT = "network_timeout"
RESPONSE_INVALID = "response_invalid"
INVALID_REPOSITORY = "invalid_repository"
UNSUPPORTED_PROVIDER = "unsupported_provider"

_TOKEN_IN_URL = re.compile(r"//oauth2:[^@/\s]+@")
_TOKEN_IN_QUERY = re.compile(r"(access_token=)[^&\s]+")


class GitHostError(RuntimeError):
    """托管平台操作失败；message 已脱敏（绝不包含 token）。"""

    def __init__(self, message: str, *, kind: str = FIELD_ERROR) -> None:
        super().__init__(message)
        self.kind = kind


def redact(text: str, token: str | None = None) -> str:
    """抹掉消息中可能的 token：先擦除已知字面量，再按 URL/查询参数形态结构掩码。"""
    out = str(text or "")
    if token:
        out = out.replace(token, "***")
    out = _TOKEN_IN_URL.sub("//oauth2:***@", out)
    return _TOKEN_IN_QUERY.sub(r"\1***", out)


@dataclasses.dataclass(frozen=True)
class RepositoryRef:
    owner: str
    repo: str


@dataclasses.dataclass(frozen=True)
class BranchHeadRef:
    branch: str
    commit_sha: str


@dataclasses.dataclass(frozen=True)
class IssueRef:
    id: str
    number: str
    html_url: str


@dataclasses.dataclass(frozen=True)
class IssueCommentRef:
    id: str
    user_name: str
    body: str
    created_at: str | None = None


@dataclasses.dataclass(frozen=True)
class IssueSnapshotRef:
    title: str
    body: str
    labels: list[str]
    comments: list[IssueCommentRef]


@dataclasses.dataclass(frozen=True)
class PullRequestRef:
    id: str
    number: str
    html_url: str
    state: str = "OPEN"


@runtime_checkable
class GitHostProvider(Protocol):
    """代码托管平台 Provider 契约（当前唯一实现：GiteeProvider）。"""

    name: str

    def parse_repository(self, url: str) -> RepositoryRef: ...

    def authenticated_remote(self, url: str, token: str | None) -> str: ...

    def mask_remote(self, url: str) -> str: ...

    def fetch_branch_head(self, repository_url: str, branch: str, token: str | None) -> BranchHeadRef: ...

    def create_issue(
        self, repository_url: str, token: str | None, *, title: str, body: str,
        labels: list[str] | None = None,
    ) -> IssueRef: ...

    def find_issue_by_marker(self, repository_url: str, token: str | None, marker: str) -> IssueRef | None: ...

    def fetch_issue_snapshot(self, repository_url: str, token: str | None, issue_id: str) -> IssueSnapshotRef: ...

    def create_pull_request(
        self, repository_url: str, token: str | None, *, head_branch: str, base_branch: str,
        title: str, body: str,
    ) -> PullRequestRef: ...