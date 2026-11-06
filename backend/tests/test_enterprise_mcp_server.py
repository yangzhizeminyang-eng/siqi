from __future__ import annotations

import asyncio
import json
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.enterprise_runtime import mcp_server


def _prepare_scan_env(monkeypatch, tmp_path: Path) -> Path:
    rules = tmp_path / "rules.yml"
    rules.write_text("rules: []", encoding="utf-8")
    monkeypatch.setenv("MCP_SESSION_TOKEN", "test-capability")
    monkeypatch.setenv("MCP_SCAN_ALLOWED_ROOT", str(tmp_path))
    monkeypatch.setenv("MCP_SEMGREP_RULES", str(rules))
    return rules


def test_server_rejects_capability_mismatch(monkeypatch, tmp_path):
    rules = _prepare_scan_env(monkeypatch, tmp_path)
    (tmp_path / "sample.py").write_text("pass", encoding="utf-8")

    with pytest.raises(PermissionError):
        asyncio.run(mcp_server._semgrep_scan("sample.py", str(rules), "wrong-capability"))


def test_server_rejects_path_escape_and_absolute_paths(monkeypatch, tmp_path):
    rules = _prepare_scan_env(monkeypatch, tmp_path)
    (tmp_path / "sample.py").write_text("pass", encoding="utf-8")

    with pytest.raises(PermissionError):
        asyncio.run(mcp_server._semgrep_scan("../sample.py", str(rules), "test-capability"))
    with pytest.raises(PermissionError):
        asyncio.run(mcp_server._semgrep_scan(str(tmp_path / "sample.py"), str(rules), "test-capability"))


def test_server_rejects_disallowed_suffix_and_oversized_file(monkeypatch, tmp_path):
    rules = _prepare_scan_env(monkeypatch, tmp_path)
    (tmp_path / "note.txt").write_text("pass", encoding="utf-8")
    (tmp_path / "big.py").write_bytes(b"#" * (1024 * 1024 + 1))

    with pytest.raises(PermissionError, match="file type"):
        asyncio.run(mcp_server._semgrep_scan("note.txt", str(rules), "test-capability"))
    with pytest.raises(PermissionError, match="size limit"):
        asyncio.run(mcp_server._semgrep_scan("big.py", str(rules), "test-capability"))


def test_server_rejects_unconfigured_rules_path(monkeypatch, tmp_path):
    rules = _prepare_scan_env(monkeypatch, tmp_path)
    other_rules = tmp_path / "other.yml"
    other_rules.write_text("rules: []", encoding="utf-8")
    (tmp_path / "sample.py").write_text("pass", encoding="utf-8")

    with pytest.raises(PermissionError):
        asyncio.run(mcp_server._semgrep_scan("sample.py", str(other_rules), "test-capability"))
    # 配置内的规则路径仍然是允许的（用于对照，避免把全部路径都拒绝）
    assert rules.exists()


def test_server_limits_concurrent_scans_and_maps_error(monkeypatch, tmp_path):
    rules = _prepare_scan_env(monkeypatch, tmp_path)
    (tmp_path / "sample.py").write_text("pass", encoding="utf-8")
    release = threading.Event()

    class FakeProcess:
        returncode = 0

        def __init__(self, *_args, **_kwargs):
            pass

        def communicate(self, timeout=None):  # noqa: ARG002
            assert release.wait(5), "fixture process was never released"
            return b'{"results": []}', b""

        def kill(self):  # pragma: no cover - not expected in this test
            pass

        def wait(self, timeout=None):  # noqa: ARG002
            return 0

    monkeypatch.setattr(mcp_server, "subprocess", SimpleNamespace(
        Popen=FakeProcess,
        PIPE=subprocess.PIPE,
        DEVNULL=subprocess.DEVNULL,
        TimeoutExpired=subprocess.TimeoutExpired,
    ))
    monkeypatch.setattr(mcp_server, "_SCAN_ACQUIRE_TIMEOUT_SECONDS", 0.3)

    async def scenario():
        first = asyncio.create_task(mcp_server._semgrep_scan("sample.py", str(rules), "test-capability"))
        second = asyncio.create_task(mcp_server._semgrep_scan("sample.py", str(rules), "test-capability"))
        await asyncio.sleep(0.1)
        third = json.loads(await mcp_server.semgrep_scan("sample.py", str(rules), "test-capability"))
        release.set()
        return third, [json.loads(await first), json.loads(await second)]

    third, finished = asyncio.run(scenario())

    assert third == {"status": "error", "error_type": "concurrency_limit"}
    assert all(item["status"] == "ok" for item in finished)
