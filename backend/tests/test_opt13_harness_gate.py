"""OPT-13 harness 装配门禁（快速、零外部依赖；重型 E2E 由 harness 手动/后台执行产出证据）。

只验证「装配不被改坏」：
- harness CLI 可解析（--help）；
- 探针默认关闭、开启时才 patch executor.build_agent（子进程隔离，不污染测试进程模块态）；
- worker_boot 的 visibility_timeout 覆盖接线生效；
- harness 主模块 import 不得引入任何 app.* 模块（保持「主进程不碰 app」的隔离设计）。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable


def _run(code_args: list[str], env_extra: dict | None = None, timeout: int = 120):
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [PY, *code_args], cwd=str(BACKEND_ROOT), env=env,
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
    )


def test_harness_cli_help():
    proc = _run(["-m", "scripts.opt13_multiworker_verify", "--help"])
    assert proc.returncode == 0, proc.stderr[-1500:]
    assert "--scenarios" in proc.stdout
    assert "--report" in proc.stdout


def test_harness_import_does_not_pull_app_modules():
    code = (
        "import sys; import scripts.opt13_multiworker_verify; "
        "print('DIRTY' if any(m == 'app' or m.startswith('app.') for m in sys.modules) else 'CLEAN')"
    )
    proc = _run(["-c", code])
    assert proc.returncode == 0, proc.stderr[-1500:]
    assert "CLEAN" in proc.stdout, "harness 主模块必须与 app 运行时零耦合（子进程隔离设计）"


def test_probe_disabled_by_default():
    code = (
        "import sys; import scripts.opt13_probe; "
        "from app.enterprise_runtime.agents import executor as ex; "
        "print('PATCHED' if ex.build_agent.__module__ == 'scripts.opt13_probe' else 'UNPATCHED')"
    )
    proc = _run(["-c", code], env_extra={"OPT13_PROBE_SLEEP_SECONDS": "0"})
    assert proc.returncode == 0, proc.stderr[-1500:]
    assert "UNPATCHED" in proc.stdout


def test_probe_patches_executor_when_enabled():
    code = (
        "import scripts.opt13_probe; "
        "from app.enterprise_runtime.agents import executor as ex; "
        "print('PATCHED' if ex.build_agent.__module__ == 'scripts.opt13_probe' else 'UNPATCHED')"
    )
    proc = _run(["-c", code], env_extra={"OPT13_PROBE_SLEEP_SECONDS": "0.01"})
    assert proc.returncode == 0, proc.stderr[-1500:]
    assert "PATCHED" in proc.stdout


def test_worker_boot_applies_visibility_timeout_override():
    code = (
        "import scripts.opt13_worker_boot as boot; "
        "print('VIS=' + str(boot.celery_app.conf.broker_transport_options.get('visibility_timeout')))"
    )
    proc = _run(["-c", code], env_extra={"OPT13_VISIBILITY_TIMEOUT": "30"})
    assert proc.returncode == 0, proc.stderr[-1500:]
    assert "VIS=30" in proc.stdout
