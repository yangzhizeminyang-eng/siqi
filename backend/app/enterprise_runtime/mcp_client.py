"""Restricted local stdio MCP client for enterprise agent tools."""

from __future__ import annotations

import asyncio
import atexit
import concurrent.futures
import json
import os
import secrets
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from app.enterprise_runtime.mcp_policy import scan_target_violation

_SERVER_LOCK = threading.Lock()
_SERVER_TOKEN = secrets.token_urlsafe(32)
_SESSIONS: dict[tuple, "_PersistentMcpSession"] = {}
_CANCEL_POLL_SECONDS = 0.1


class McpCancelledError(RuntimeError):
    """Raised when a caller cancels an in-flight MCP tool call."""


class _PersistentMcpSession:
    def __init__(self, server: StdioServerParameters):
        self.server = server
        self.loop = asyncio.new_event_loop()
        self.ready = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True, name="local-mcp-stdio")
        self.session = None
        self.start_error: BaseException | None = None
        self.thread.start()
        if not self.ready.wait(20):
            self.close()
            raise TimeoutError("MCP stdio server initialization timed out")
        if self.start_error:
            raise RuntimeError("MCP stdio server initialization failed") from self.start_error

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.shutdown_event = asyncio.Event()
        self.loop.create_task(self._lifecycle())
        self.loop.run_forever()
        self.loop.close()

    async def _lifecycle(self) -> None:
        try:
            async with stdio_client(self.server) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    self.session = session
                    await session.initialize()
                    self.ready.set()
                    await self.shutdown_event.wait()
        except BaseException as error:  # noqa: BLE001
            if not self.ready.is_set():
                self.start_error = error
                self.ready.set()
        finally:
            self.session = None
            self.loop.call_soon(self.loop.stop)

    async def _call(self, tool_name: str, arguments: dict):
        return await self.session.call_tool(tool_name, arguments)

    async def _list_tools(self):
        return await self.session.list_tools()

    def _await_with_cancel(self, future, timeout: float, cancel_event: threading.Event | None):
        deadline = time.monotonic() + max(0.05, timeout)
        while True:
            if cancel_event is not None and cancel_event.is_set():
                self._abandon(future)
                raise McpCancelledError("MCP tool call cancelled")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._abandon(future)
                raise TimeoutError("MCP tool timed out")
            try:
                return future.result(timeout=min(_CANCEL_POLL_SECONDS, remaining))
            except concurrent.futures.TimeoutError:
                continue

    def _abandon(self, future) -> None:
        future.cancel()
        try:
            future.result(timeout=2)
        except BaseException:  # noqa: BLE001
            pass
        self.close()

    def call(self, tool_name: str, arguments: dict, timeout: float,
             cancel_event: threading.Event | None = None):
        future = asyncio.run_coroutine_threadsafe(self._call(tool_name, arguments), self.loop)
        return self._await_with_cancel(future, timeout, cancel_event)

    def list_tools(self, timeout: float):
        future = asyncio.run_coroutine_threadsafe(self._list_tools(), self.loop)
        try:
            result = future.result(timeout=max(0.05, timeout))
        except concurrent.futures.TimeoutError:
            self._abandon(future)
            raise TimeoutError("MCP tool discovery timed out")
        return list(getattr(result, "tools", []) or [])

    def close(self) -> None:
        if self.loop.is_running():
            self.loop.call_soon_threadsafe(self.shutdown_event.set)
        if self.thread.is_alive() and threading.current_thread() is not self.thread:
            self.thread.join(timeout=5)


def _close_sessions() -> None:
    for session in list(_SESSIONS.values()):
        session.close()


atexit.register(_close_sessions)


class McpConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True)
class McpIdentity:
    tenant_id: str
    actor_id: str
    role: str
    agent_type: str
    task_id: str
    workspace_root: Path
    run_id: str = ""


class LocalMcpClient:
    """One-shot stdio MCP client with server-side path and tool authorization."""

    def __init__(
        self,
        *,
        server_command: Sequence[str] | None = None,
        allowed_root: Path | str | None = None,
        rules_path: Path | str | None = None,
        timeout_seconds: float = 40.0,
        audit: Callable[[dict], None] | None = None,
        server_env: Mapping[str, str] | None = None,
    ):
        backend_root = Path(__file__).resolve().parents[2]
        self.backend_root = backend_root
        configured_root = allowed_root or os.environ.get("MCP_SCAN_ALLOWED_ROOT")
        # This directory is intentionally separate from the application workspace.
        self.allowed_root = Path(configured_root or backend_root / ".data" / "mcp-sandbox").resolve()
        self.rules_path = Path(rules_path or os.environ.get(
            "MCP_SEMGREP_RULES", backend_root / "app" / "enterprise_runtime" / "semgrep_rules.yml"
        )).resolve()
        self.server_command = tuple(server_command or (
            os.environ.get("MCP_PYTHON", os.sys.executable), "-m", "app.enterprise_runtime.mcp_server"
        ))
        self.timeout_seconds = max(0.05, float(timeout_seconds))
        self.audit = audit
        self.server_env = dict(server_env or {})

    def discover_tools(self) -> list[str]:
        """List tool names exposed by the self-hosted stdio server (names only, no schemas)."""
        tools = asyncio.run(self._discover())
        return sorted({str(getattr(tool, "name", "")) for tool in tools if getattr(tool, "name", "")})

    def call_tool(
        self,
        tool_name: str,
        arguments: dict,
        identity: McpIdentity,
        cancel_event: threading.Event | None = None,
    ) -> dict:
        started = time.perf_counter()
        outcome = "error"
        call_id = uuid.uuid4().hex
        result: dict
        try:
            normalized = self._authorize(tool_name, arguments, identity)
            result = asyncio.run(self._invoke(tool_name, normalized, cancel_event))
            outcome = "success" if result.get("status") == "ok" else result.get("error_type", "error")
            return result
        except PermissionError as exc:
            outcome = "denied"
            return {"status": "denied", "error_type": "permission_denied", "message": str(exc)}
        except McpCancelledError:
            outcome = "cancelled"
            return {"status": "cancelled", "error_type": "cancelled", "message": "MCP tool call cancelled"}
        except TimeoutError:
            outcome = "timeout"
            return {"status": "error", "error_type": "timeout", "message": "MCP tool timed out"}
        except ExceptionGroup as exc:
            outcome = "timeout" if _contains_timeout(exc) else "error"
            return {
                "status": "error",
                "error_type": "timeout" if outcome == "timeout" else "server_error",
                "message": "MCP tool timed out" if outcome == "timeout" else "MCP server failed",
            }
        except Exception as exc:  # noqa: BLE001
            outcome = "error"
            return {"status": "error", "error_type": "server_error", "message": type(exc).__name__}
        finally:
            self._audit_event(identity, tool_name, outcome, (time.perf_counter() - started) * 1000, call_id)

    def _authorize(self, tool_name: str, arguments: dict, identity: McpIdentity) -> dict:
        if tool_name != "semgrep_scan":
            raise PermissionError("MCP tool is not allowlisted")
        if identity.agent_type != "review_security_agent" or identity.role not in {"admin", "reviewer", "security_admin"}:
            raise PermissionError("identity is not authorized for security scanning")
        if not identity.tenant_id or not identity.actor_id or not identity.task_id:
            raise PermissionError("authenticated task identity is required")
        try:
            workspace = Path(identity.workspace_root).resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise PermissionError("workspace does not exist") from exc
        if workspace != self.allowed_root:
            raise PermissionError("workspace is not the configured isolated scan root")
        relative_path = str((arguments or {}).get("relative_path", "")).strip()
        if not relative_path or Path(relative_path).is_absolute():
            raise PermissionError("scan path must be a relative file path")
        try:
            target = (workspace / relative_path).resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise PermissionError("scan target does not exist or cannot be resolved") from exc
        if not target.is_relative_to(workspace):
            raise PermissionError("scan path must resolve to a file inside the isolated workspace")
        violation = scan_target_violation(target, workspace)
        if violation:
            raise PermissionError(violation)
        try:
            rules = self.rules_path.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise McpConfigurationError("local Semgrep rules are unavailable") from exc
        if not rules.is_file():
            raise McpConfigurationError("local Semgrep rules are unavailable")
        return {"relative_path": target.relative_to(workspace).as_posix(), "rules_path": str(rules)}

    def _server_parameters(self) -> StdioServerParameters:
        env = {
            "PATH": os.environ.get("PATH", ""),
            "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
            "WINDIR": os.environ.get("WINDIR", ""),
            "TEMP": os.environ.get("TEMP", ""),
            "TMP": os.environ.get("TMP", ""),
            "HOME": os.environ.get("USERPROFILE", ""),
            "USERPROFILE": os.environ.get("USERPROFILE", ""),
            "LOCALAPPDATA": os.environ.get("LOCALAPPDATA", ""),
            "PYTHONPATH": str(self.backend_root),
            **self.server_env,
            "MCP_SESSION_TOKEN": _SERVER_TOKEN,
            "MCP_SCAN_ALLOWED_ROOT": str(self.allowed_root),
            "MCP_SEMGREP_RULES": str(self.rules_path),
        }
        return StdioServerParameters(
            command=self.server_command[0],
            args=list(self.server_command[1:]),
            env=env,
            cwd=str(self.backend_root),
        )

    def _session(self) -> _PersistentMcpSession:
        server = self._server_parameters()
        key = (self.server_command, str(self.allowed_root), str(self.rules_path), tuple(sorted(self.server_env.items())))
        with _SERVER_LOCK:
            session = _SESSIONS.get(key)
            if session is None or not session.thread.is_alive() or session.session is None:
                session = _PersistentMcpSession(server)
                _SESSIONS[key] = session
        return session

    async def _discover(self) -> list:
        session = self._session()
        return await asyncio.to_thread(session.list_tools, self.timeout_seconds)

    async def _invoke(self, tool_name: str, arguments: dict, cancel_event: threading.Event | None) -> dict:
        session = self._session()
        response = await asyncio.to_thread(
            session.call, tool_name, {**arguments, "capability": _SERVER_TOKEN}, self.timeout_seconds, cancel_event
        )
        if response.isError:
            return {"status": "error", "error_type": "server_error", "message": "MCP tool failed"}
        for item in response.content or []:
            if getattr(item, "type", "") == "text":
                try:
                    value = json.loads(item.text)
                except (TypeError, ValueError):
                    return {
                        "status": "error",
                        "error_type": "invalid_response",
                        "message": "MCP server returned an invalid response",
                    }
                return value if isinstance(value, dict) else {"status": "ok", "result": value}
        structured = dict(response.structuredContent or {})
        return structured if structured else {"status": "ok"}

    def _audit_event(self, identity: McpIdentity, tool_name: str, outcome: str,
                     duration_ms: float, call_id: str) -> None:
        if self.audit is None:
            return
        event = {
            "event_type": "runtime.mcp.tool_call",
            "tenant_id": identity.tenant_id,
            "actor_id": identity.actor_id,
            "agent_type": identity.agent_type,
            "task_id": identity.task_id,
            "run_id": identity.run_id,
            "call_id": call_id,
            "tool_name": tool_name,
            "outcome": outcome,
            "duration_ms": round(duration_ms, 2),
        }
        try:
            self.audit(event)
        except Exception:  # noqa: BLE001
            pass


def _contains_timeout(error: BaseException) -> bool:
    if isinstance(error, (TimeoutError, asyncio.TimeoutError)):
        return True
    return any(_contains_timeout(item) for item in getattr(error, "exceptions", ()))
