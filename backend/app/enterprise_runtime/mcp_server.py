"""Self-hosted, local-only MCP Server exposing a bounded Semgrep file scan."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from app.enterprise_runtime.mcp_policy import (
    DEFAULT_MAX_CONCURRENT_SCANS,
    DEFAULT_SCAN_ACQUIRE_TIMEOUT_SECONDS,
    scan_target_violation,
)

mcp = FastMCP("enterprise-local-semgrep")


class ScanConcurrencyLimitError(RuntimeError):
    """Raised when the server is already running the maximum number of scans."""


def _max_concurrent_scans() -> int:
    try:
        return max(1, int(os.environ.get("MCP_SEMGREP_MAX_CONCURRENT", DEFAULT_MAX_CONCURRENT_SCANS)))
    except ValueError:
        return DEFAULT_MAX_CONCURRENT_SCANS


_SCAN_SEMAPHORE = asyncio.Semaphore(_max_concurrent_scans())
_SCAN_ACQUIRE_TIMEOUT_SECONDS = float(
    os.environ.get("MCP_SEMGREP_ACQUIRE_TIMEOUT_SECONDS", DEFAULT_SCAN_ACQUIRE_TIMEOUT_SECONDS)
)
_SCAN_TIMEOUT_SECONDS = 30


def _validate_scan_target(relative_path: str, allowed_root: Path) -> Path:
    relative = Path(relative_path)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise PermissionError("scan path must be a relative file path")
    try:
        target = (allowed_root / relative_path).resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise PermissionError("scan target does not exist or cannot be resolved") from error
    violation = scan_target_violation(target, allowed_root)
    if violation:
        raise PermissionError(violation)
    return target


def _scan_environment() -> dict:
    return {
        key: os.environ.get(key, "")
        for key in ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "HOME", "USERPROFILE", "LOCALAPPDATA")
    } | {"SEMGREP_SEND_METRICS": "off"}


async def _semgrep_scan(relative_path: str, rules_path: str, capability: str) -> str:
    """Scan one file under the configured isolated root with local rules only."""
    expected_capability = os.environ.get("MCP_SESSION_TOKEN", "")
    allowed_root = Path(os.environ.get("MCP_SCAN_ALLOWED_ROOT", ".data/mcp-sandbox")).resolve()
    configured_rules = Path(os.environ.get(
        "MCP_SEMGREP_RULES", "app/enterprise_runtime/semgrep_rules.yml"
    )).resolve()
    if not expected_capability or not secrets_compare(capability, expected_capability):
        raise PermissionError("invalid MCP session capability")
    if Path(rules_path).resolve() != configured_rules or not configured_rules.is_file():
        raise PermissionError("only the configured local Semgrep rules are allowed")
    target = _validate_scan_target(relative_path, allowed_root)
    try:
        await asyncio.wait_for(_SCAN_SEMAPHORE.acquire(), timeout=_SCAN_ACQUIRE_TIMEOUT_SECONDS)
    except (asyncio.TimeoutError, TimeoutError) as error:
        raise ScanConcurrencyLimitError("local semgrep scan concurrency limit reached") from error
    try:
        return await _run_semgrep(relative_path, target, configured_rules, allowed_root)
    finally:
        _SCAN_SEMAPHORE.release()


async def _run_semgrep(relative_path: str, target: Path, configured_rules: Path, allowed_root: Path) -> str:
    try:
        process = subprocess.Popen(
            ["semgrep", "scan", "--json", "--metrics=off", "--config", str(configured_rules),
             "--", str(target)],
            cwd=allowed_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            env=_scan_environment(),
        )
    except FileNotFoundError as error:
        raise RuntimeError("local semgrep executable is not installed") from error
    try:
        stdout, _stderr = await asyncio.to_thread(process.communicate, timeout=_SCAN_TIMEOUT_SECONDS)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        process.kill()
        await asyncio.to_thread(process.wait)
        raise
    except subprocess.TimeoutExpired as error:
        process.kill()
        await asyncio.to_thread(process.wait)
        raise TimeoutError("local semgrep scan timed out") from error
    if process.returncode not in {0, 1}:
        raise RuntimeError("local semgrep scan failed")
    try:
        report = json.loads(stdout.decode("utf-8", errors="replace") or "{}")
    except ValueError as error:
        raise RuntimeError("local semgrep returned invalid JSON") from error
    findings = [
        {
            "rule_id": item.get("check_id", ""),
            "severity": (item.get("extra") or {}).get("severity", "INFO"),
            "path": relative_path,
            "start_line": (item.get("start") or {}).get("line"),
            "message": str((item.get("extra") or {}).get("message", ""))[:500],
        }
        for item in (report.get("results") or [])[:100]
    ]
    return json.dumps({"status": "ok", "findings": findings, "finding_count": len(findings)})


@mcp.tool()
async def semgrep_scan(relative_path: str, rules_path: str, capability: str) -> str:
    try:
        return await _semgrep_scan(relative_path, rules_path, capability)
    except PermissionError:
        return json.dumps({"status": "denied", "error_type": "permission_denied"})
    except ScanConcurrencyLimitError:
        return json.dumps({"status": "error", "error_type": "concurrency_limit"})
    except (asyncio.TimeoutError, TimeoutError):
        return json.dumps({"status": "error", "error_type": "timeout"})
    except Exception as error:  # noqa: BLE001
        return json.dumps({"status": "error", "error_type": type(error).__name__})


def secrets_compare(left: str, right: str) -> bool:
    import hmac
    return hmac.compare_digest(left, right)


if __name__ == "__main__":
    mcp.run(transport="stdio")
