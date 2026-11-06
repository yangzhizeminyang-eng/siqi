from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

from app.enterprise_runtime.mcp_client import LocalMcpClient, McpIdentity


def _client(root: Path, events: list[dict], timeout: float = 10.0) -> LocalMcpClient:
    return LocalMcpClient(
        server_command=(sys.executable, "tests/mcp_stub_server.py"),
        allowed_root=root,
        timeout_seconds=timeout,
        audit=events.append,
    )


def _identity(root: Path, **overrides) -> McpIdentity:
    values = {
        "tenant_id": "tenant-test",
        "actor_id": "user-test",
        "role": "reviewer",
        "agent_type": "review_security_agent",
        "task_id": "task-test",
        "workspace_root": root,
    }
    values.update(overrides)
    return McpIdentity(**values)


def test_stdio_client_authenticates_and_calls_local_semgrep_tool(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "sample.py").write_text("print('local')", encoding="utf-8")
    events: list[dict] = []

    result = _client(tmp_path, events).call_tool(
        "semgrep_scan", {"relative_path": "src/sample.py"}, _identity(tmp_path)
    )

    assert result == {"status": "ok", "relative_path": "src/sample.py"}
    assert events[-1]["outcome"] == "success"
    assert events[-1]["tenant_id"] == "tenant-test"
    assert "capability" not in str(events[-1])


def test_default_client_deadline_exceeds_server_scan_limit():
    assert LocalMcpClient().timeout_seconds > 30


def test_client_denies_untrusted_agent_before_spawning_server(tmp_path):
    (tmp_path / "sample.py").write_text("pass", encoding="utf-8")
    events: list[dict] = []

    result = _client(tmp_path, events).call_tool(
        "semgrep_scan", {"relative_path": "sample.py"},
        _identity(tmp_path, agent_type="repository_research_agent"),
    )

    assert result["status"] == "denied"
    assert events[-1]["outcome"] == "denied"


def test_client_denies_role_without_scan_permission(tmp_path):
    (tmp_path / "sample.py").write_text("pass", encoding="utf-8")
    events: list[dict] = []

    result = _client(tmp_path, events).call_tool(
        "semgrep_scan", {"relative_path": "sample.py"},
        _identity(tmp_path, role="developer"),
    )

    assert result["status"] == "denied"
    assert events[-1]["outcome"] == "denied"


def test_client_denies_missing_role_instead_of_assuming_reviewer(tmp_path):
    (tmp_path / "sample.py").write_text("pass", encoding="utf-8")
    events: list[dict] = []

    result = _client(tmp_path, events).call_tool(
        "semgrep_scan", {"relative_path": "sample.py"}, _identity(tmp_path, role="")
    )

    assert result["status"] == "denied"
    assert events[-1]["outcome"] == "denied"


def test_agent_tool_preserves_missing_role_for_client_authorization(monkeypatch, tmp_path):
    import json

    from app.enterprise_runtime.agents.definition import TOOL_SEMGREP_SCAN
    from app.enterprise_runtime.agents.tools import _TOOL_MAP, runtime_tool_context
    from app.enterprise_runtime.mcp_client import LocalMcpClient

    identities = []

    def deny_missing_role(self, tool_name, arguments, identity):
        identities.append(identity)
        return {"status": "denied", "error_type": "permission_denied"}

    monkeypatch.setattr(LocalMcpClient, "call_tool", deny_missing_role)
    with runtime_tool_context(
        task_id="task-test", run_id="run-test", tenant_id="tenant-test",
        actor_id="user-test", role="", workspace_root=str(tmp_path),
    ):
        result = _TOOL_MAP[TOOL_SEMGREP_SCAN].invoke({"relative_path": "sample.py"})

    assert json.loads(result)["status"] == "denied"
    assert identities[0].role == ""


def test_client_rejects_paths_outside_workspace_and_symlink_escape(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("pass", encoding="utf-8")
    events: list[dict] = []
    client = _client(workspace, events)

    traversal = client.call_tool(
        "semgrep_scan", {"relative_path": "../outside.py"}, _identity(workspace)
    )
    absolute = client.call_tool(
        "semgrep_scan", {"relative_path": str(outside)}, _identity(workspace)
    )

    assert traversal["status"] == "denied"
    assert absolute["status"] == "denied"
    assert [event["outcome"] for event in events] == ["denied", "denied"]


def test_client_times_out_and_audits_server_errors(tmp_path):
    (tmp_path / "slow.py").write_text("pass", encoding="utf-8")
    (tmp_path / "error.py").write_text("pass", encoding="utf-8")
    events: list[dict] = []
    client = _client(tmp_path, events, timeout=1.5)

    timeout_result = client.call_tool(
        "semgrep_scan", {"relative_path": "slow.py"}, _identity(tmp_path)
    )
    error_result = _client(tmp_path, events).call_tool(
        "semgrep_scan", {"relative_path": "error.py"}, _identity(tmp_path)
    )

    assert timeout_result["status"] == "error"
    assert timeout_result["error_type"] == "timeout"
    assert error_result["status"] == "error"
    assert error_result["error_type"] == "server_error"
    assert [event["outcome"] for event in events] == ["timeout", "server_error"]


def test_client_reuses_stdio_session_for_repeated_calls(tmp_path):
    (tmp_path / "sample.py").write_text("pass", encoding="utf-8")
    events: list[dict] = []
    client = _client(tmp_path, events)
    identity = _identity(tmp_path)

    first = client.call_tool("semgrep_scan", {"relative_path": "sample.py"}, identity)
    second = client.call_tool("semgrep_scan", {"relative_path": "sample.py"}, identity)

    assert first["status"] == second["status"] == "ok"
    assert [event["outcome"] for event in events] == ["success", "success"]


def test_security_scan_tool_is_only_granted_to_review_agent():
    from app.enterprise_runtime.agents.agent_definitions import AGENT_DEFINITIONS
    from app.enterprise_runtime.agents.definition import TOOL_SEMGREP_SCAN
    from app.enterprise_runtime.agents.tools import build_tools

    assert TOOL_SEMGREP_SCAN in AGENT_DEFINITIONS["review_security_agent"].tool_whitelist
    assert TOOL_SEMGREP_SCAN in AGENT_DEFINITIONS["review_security_agent"].readonly_tools
    assert TOOL_SEMGREP_SCAN not in AGENT_DEFINITIONS["repository_research_agent"].tool_whitelist
    assert "semgrep_scan" in {tool.name for tool in build_tools("review_security_agent")}
    assert "semgrep_scan" not in {tool.name for tool in build_tools("planner_agent")}


def test_client_discovers_only_controlled_semgrep_tool(tmp_path):
    events: list[dict] = []

    tools = _client(tmp_path, events).discover_tools()

    assert tools == ["semgrep_scan"]


def test_client_denies_unallowlisted_tool_name_before_spawning_server(tmp_path):
    (tmp_path / "sample.py").write_text("pass", encoding="utf-8")
    events: list[dict] = []

    result = _client(tmp_path, events).call_tool(
        "read_file", {"relative_path": "sample.py"}, _identity(tmp_path)
    )

    assert result["status"] == "denied"
    assert result["error_type"] == "permission_denied"
    assert events[-1]["outcome"] == "denied"


def test_client_cancel_interrupts_inflight_call_and_audits(tmp_path):
    (tmp_path / "slow.py").write_text("pass", encoding="utf-8")
    events: list[dict] = []
    client = _client(tmp_path, events, timeout=30)
    cancel_event = threading.Event()
    timer = threading.Timer(0.3, cancel_event.set)
    timer.start()
    started = time.perf_counter()

    result = client.call_tool(
        "semgrep_scan", {"relative_path": "slow.py"}, _identity(tmp_path), cancel_event=cancel_event
    )
    elapsed = time.perf_counter() - started
    timer.cancel()

    assert result["status"] == "cancelled"
    assert result["error_type"] == "cancelled"
    assert events[-1]["outcome"] == "cancelled"
    # 取消路径 = 0.3s 定时 → 轮询命中 → _abandon（最多等 2s）→ 会话/子进程清理；
    # 满负载全量套件下清理可再耗 ~0.5s，3.0s 上界会偶发假红（实测 3.0166s），放宽到 5.0s 仍远低于 30s 工具超时。
    assert elapsed < 5.0
    # 取消后客户端必须能重新建立会话继续服务
    follow_up = _client(tmp_path, events).call_tool(
        "semgrep_scan", {"relative_path": "slow.py"}, _identity(tmp_path), cancel_event=threading.Event()
    )
    assert follow_up["status"] == "ok"


def test_client_maps_invalid_server_response_to_sanitized_error(tmp_path):
    (tmp_path / "invalid_json.py").write_text("pass", encoding="utf-8")
    events: list[dict] = []

    result = _client(tmp_path, events).call_tool(
        "semgrep_scan", {"relative_path": "invalid_json.py"}, _identity(tmp_path)
    )

    assert result["status"] == "error"
    assert result["error_type"] == "invalid_response"
    assert "not-json" not in json.dumps(result)
    assert "not-json" not in json.dumps(events)
    assert events[-1]["outcome"] == "invalid_response"


def test_client_rejects_disallowed_file_type_and_oversized_file(tmp_path):
    (tmp_path / "note.txt").write_text("pass", encoding="utf-8")
    (tmp_path / "big.py").write_bytes(b"#" * (1024 * 1024 + 1))
    events: list[dict] = []
    client = _client(tmp_path, events)

    wrong_type = client.call_tool("semgrep_scan", {"relative_path": "note.txt"}, _identity(tmp_path))
    too_big = client.call_tool("semgrep_scan", {"relative_path": "big.py"}, _identity(tmp_path))

    assert wrong_type["status"] == "denied"
    assert "file type" in wrong_type["message"]
    assert too_big["status"] == "denied"
    assert "size limit" in too_big["message"]
    assert [event["outcome"] for event in events] == ["denied", "denied"]


def test_audit_events_carry_correlation_ids_without_arguments(tmp_path):
    from app.enterprise_runtime.mcp_client import _SERVER_TOKEN

    (tmp_path / "sample.py").write_text("pass", encoding="utf-8")
    events: list[dict] = []

    _client(tmp_path, events).call_tool(
        "semgrep_scan", {"relative_path": "sample.py"}, _identity(tmp_path, run_id="run-1")
    )

    event = events[-1]
    assert set(event) == {
        "event_type", "tenant_id", "actor_id", "agent_type", "task_id", "run_id",
        "call_id", "tool_name", "outcome", "duration_ms",
    }
    assert event["run_id"] == "run-1"
    assert len(event["call_id"]) == 32
    serialized = json.dumps(events)
    assert "sample.py" not in serialized
    assert _SERVER_TOKEN not in serialized
    assert "capability" not in serialized
