"""OPT-05：测试环境隔离与「服务缺失 vs 代码失败」分类的回归测试。

覆盖：
- 默认测试进程与本地 .env / broker 隔离（ENV_FILE 哨兵、celery_app 导入即 eager）；
- 探测函数正/反分支（真实 TCP：监听端口可达、关闭端口不可达）；
- requires_redis 标记：broker 缺失 → 子进程真跑 pytest 验证被归类为 skip（环境缺失）；
- 同一标记：broker 可达（本地监听端口模拟）→ 用例被真正执行。
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
from pathlib import Path

from service_probe import target_label, tcp_reachable, url_target

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _run_marked_demo(test_redis_url: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["TEST_REDIS_URL"] = test_redis_url
    return subprocess.run(
        [sys.executable, "-m", "pytest", "tests/opt05_marked_demo.py", "-q", "-rs", "-s",
         "-p", "no:cacheprovider"],
        cwd=BACKEND_ROOT, env=env, capture_output=True, text=True, timeout=180,
    )


def _listening_socket() -> socket.socket:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    return srv


def test_default_test_process_is_isolated_from_local_env_and_broker():
    """默认测试进程不读本地 .env；celery_app 导入即 eager、未绑定任何 redis broker。"""
    from app.core.runtime_config import load_runtime_settings
    from app.workers.celery_app import celery_app

    assert os.environ.get("ENV_FILE", "").endswith(".env.hermetic-nonexistent")
    assert os.environ.get("CELERY_BROKER_URL") in (None, "")
    assert load_runtime_settings().celery_broker_url == ""
    assert celery_app.conf.task_always_eager is True
    assert not str(celery_app.conf.broker_url or "").startswith("redis")


def test_probe_reports_closed_port_unreachable():
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    assert tcp_reachable("127.0.0.1", port, timeout=0.6) is False


def test_probe_reports_listening_port_reachable():
    srv = _listening_socket()
    try:
        host, port = srv.getsockname()[:2]
        assert tcp_reachable(host, port, timeout=0.6) is True
    finally:
        srv.close()


def test_url_target_parses_broker_url_and_redacts_credentials():
    assert url_target("redis://127.0.0.1:6379/0") == ("127.0.0.1", 6379)
    assert url_target("redis://user:secret@10.0.0.5:6380/3") == ("10.0.0.5", 6380)
    assert url_target("redis://10.0.0.5") == ("10.0.0.5", 6379)
    label = target_label("redis://user:secret@10.0.0.5:6380/3")
    assert label == "10.0.0.5:6380" and "secret" not in label


def test_requires_redis_marked_is_skipped_and_classified_when_broker_missing():
    """服务缺失 → skip（环境缺失分类），整体退出码仍为 0，绝不计为代码失败。"""
    proc = _run_marked_demo("redis://127.0.0.1:9/0")
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    assert "1 skipped" in out, out
    assert "requires_redis" in out and "环境缺失" in out, out


def test_requires_redis_marked_runs_when_broker_reachable():
    """服务可达（本地监听端口模拟）→ 同一标记用例被真正执行。"""
    srv = _listening_socket()
    try:
        port = srv.getsockname()[1]
        proc = _run_marked_demo(f"redis://127.0.0.1:{port}/0")
        out = proc.stdout + proc.stderr
        assert proc.returncode == 0, out
        assert "1 passed" in out, out
        assert "OPT05_MARKED_DEMO_EXECUTED" in out, out
    finally:
        srv.close()
