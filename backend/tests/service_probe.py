"""OPT-05 测试服务探测：把「服务缺失」与「代码失败」分成两类。

- 默认测试进程不依赖任何外部服务（conftest 已隔离 .env / broker）；
- 标了 @pytest.mark.requires_redis 的用例，只在 broker 可达时运行；
- 不可达时由 conftest 统一 skip，理由里显式标注「环境缺失（非代码失败）」。
"""

from __future__ import annotations

import os
import socket
from urllib.parse import urlparse

DEFAULT_REDIS_URL = "redis://127.0.0.1:6379/0"
PROBE_TIMEOUT_SECONDS = 0.6


def redis_broker_url() -> str:
    """requires_* 用例与探测共用地址：显式 TEST_REDIS_URL 优先，否则本地默认。"""
    return (os.getenv("TEST_REDIS_URL") or DEFAULT_REDIS_URL).strip()


def url_target(url: str) -> tuple[str, int]:
    parsed = urlparse(url)
    return parsed.hostname or "127.0.0.1", parsed.port or 6379


def tcp_reachable(host: str, port: int, timeout: float = PROBE_TIMEOUT_SECONDS) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def redis_reachable(url: str | None = None, timeout: float = PROBE_TIMEOUT_SECONDS) -> bool:
    host, port = url_target(url or redis_broker_url())
    return tcp_reachable(host, port, timeout)


def target_label(url: str | None = None) -> str:
    """仅输出 host:port（脱敏：不输出 URL 中的凭据/库号）。"""
    host, port = url_target(url or redis_broker_url())
    return f"{host}:{port}"
