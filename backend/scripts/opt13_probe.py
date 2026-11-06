"""OPT-13 harness：慢执行探针（仅 harness worker 加载，生产代码零改动）。

侧效应模块：被 `scripts.opt13_worker_boot` 导入时，若 `OPT13_PROBE_SLEEP_SECONDS > 0`，
把 `app.enterprise_runtime.agents.executor.build_agent` 包一层：命中 `OPT13_PROBE_AGENTS`
（默认 requirement_agent）的后端在 `invoke` 前 sleep 指定秒数。

用于 S3 崩溃恢复：AgentRun 已写入 running + 步骤已 RUNNING 后仍处于执行中，
给 harness 留出「taskkill 真实杀 Worker」的窗口，并复现「崩溃遗留 running AgentRun」。

不改变任何执行结果：sleep 后委托真实后端，确定性 provider 输出不变。
"""

from __future__ import annotations

import os
import time

_SLEEP = float(os.getenv("OPT13_PROBE_SLEEP_SECONDS", "0") or 0)
_AGENTS = {a.strip() for a in (os.getenv("OPT13_PROBE_AGENTS", "requirement_agent") or "").split(",") if a.strip()}


class _SlowBackend:
    """透传代理：仅 invoke 前 sleep；其余属性（invoke_with_trace 等）原样转发。"""

    def __init__(self, inner, sleep_seconds: float) -> None:
        self._inner = inner
        self._sleep_seconds = sleep_seconds

    def invoke(self, structured_input: dict) -> dict:
        agent_type = getattr(self._inner, "agent_type", "?")
        print(f"[opt13-probe] hold {self._sleep_seconds}s before invoke agent={agent_type}", flush=True)
        time.sleep(self._sleep_seconds)
        return self._inner.invoke(structured_input)

    def __getattr__(self, name):  # noqa: ANN001
        return getattr(self._inner, name)


if _SLEEP > 0:
    from app.enterprise_runtime.agents import executor as executor_mod

    _orig_build_agent = executor_mod.build_agent

    def _slow_build_agent(agent_type: str):  # noqa: ANN001
        backend = _orig_build_agent(agent_type)
        if agent_type in _AGENTS:
            return _SlowBackend(backend, _SLEEP)
        return backend

    executor_mod.build_agent = _slow_build_agent
    print(f"[opt13-probe] active sleep={_SLEEP}s agents={sorted(_AGENTS)}", flush=True)
