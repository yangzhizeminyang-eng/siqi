"""OPT-15 GitHost Provider 包：托管平台无关契约 + 注册表（当前唯一实现：Gitee）。"""

from app.services.git_host.contract import (
    AUTH_EXPIRED,
    FIELD_ERROR,
    INVALID_REPOSITORY,
    NETWORK_TIMEOUT,
    RESPONSE_INVALID,
    UNSUPPORTED_PROVIDER,
    BranchHeadRef,
    GitHostError,
    GitHostProvider,
    IssueCommentRef,
    IssueRef,
    IssueSnapshotRef,
    PullRequestRef,
    RepositoryRef,
    redact,
)
from app.services.git_host.gitee import DEFAULT_API_BASE, GiteeProvider
from app.services.git_host.registry import get_git_host_provider

__all__ = [
    "AUTH_EXPIRED",
    "FIELD_ERROR",
    "INVALID_REPOSITORY",
    "NETWORK_TIMEOUT",
    "RESPONSE_INVALID",
    "UNSUPPORTED_PROVIDER",
    "BranchHeadRef",
    "DEFAULT_API_BASE",
    "GitHostError",
    "GitHostProvider",
    "GiteeProvider",
    "IssueCommentRef",
    "IssueRef",
    "IssueSnapshotRef",
    "PullRequestRef",
    "RepositoryRef",
    "get_git_host_provider",
    "redact",
]