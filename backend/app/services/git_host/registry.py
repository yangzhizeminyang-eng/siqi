"""GitHost Provider 注册表：默认 gitee；显式非 gitee（GitHub/GitLab 未实现）fail-closed。

同名先例：OPT-14 `AUTH_MODE`（非 local 拒绝启动）。这里在调用点二次设防：
即使运行期以显式名称请求未实现平台，也绝不静默回退 gitee——避免把 Issue/PR
写到非预期的托管平台。见 docs/project-development/127。
"""

from __future__ import annotations

from app.services.git_host.contract import UNSUPPORTED_PROVIDER, GitHostError
from app.services.git_host.gitee import GiteeProvider


def get_git_host_provider(name: str | None = None) -> GiteeProvider:
    """按名称返回 Provider；name 为 None 时读运行时配置（GIT_HOST_PROVIDER，默认 gitee）。"""
    if name is None:
        from app.core.runtime_config import load_runtime_settings

        name = load_runtime_settings().git_host_provider
    value = (name or "gitee").strip().lower()
    if value != "gitee":
        raise GitHostError(
            f"代码托管平台 {value} 未实现（GitHub/GitLab 适配规划中，GIT_HOST_PROVIDER 配置）："
            "fail-closed 拒绝，绝不静默回退 gitee 托管。见 docs/project-development/127。",
            kind=UNSUPPORTED_PROVIDER,
        )
    return GiteeProvider()