"""Real local MCP smoke run: client -> self-hosted stdio server -> Semgrep.

Everything stays on this machine (no remote scanning, no network services).
Writes an evidence JSON with tool discovery, call results and audit events only.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


def _semgrep_version() -> str:
    try:
        output = subprocess.run(
            ["semgrep", "--version"], capture_output=True, text=True, timeout=60, check=False
        )
        return (output.stdout or output.stderr).strip().splitlines()[0] if output.stdout or output.stderr else "unknown"
    except Exception as error:  # noqa: BLE001
        return f"unavailable:{type(error).__name__}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Real local MCP Semgrep smoke run (isolated workspace)")
    parser.add_argument("--out", required=True, help="Evidence JSON output path")
    args = parser.parse_args()

    from app.enterprise_runtime.mcp_client import LocalMcpClient, McpIdentity

    with tempfile.TemporaryDirectory(prefix="mcp-smoke-") as workspace_dir:
        workspace = Path(workspace_dir).resolve()
        (workspace / "clean_sample.py").write_text("print('clean')\n", encoding="utf-8")
        (workspace / "eval_sample.py").write_text(
            "def load(raw):\n    return eval(raw)\n", encoding="utf-8"
        )

        events: list[dict] = []
        client = LocalMcpClient(allowed_root=workspace, audit=events.append)
        identity = McpIdentity(
            tenant_id="tenant-smoke", actor_id="smoke-user", role="reviewer",
            agent_type="review_security_agent", task_id="task-smoke", run_id="run-smoke",
            workspace_root=workspace,
        )

        discovered = client.discover_tools()
        clean = client.call_tool("semgrep_scan", {"relative_path": "clean_sample.py"}, identity)
        risky = client.call_tool("semgrep_scan", {"relative_path": "eval_sample.py"}, identity)
        escaped = client.call_tool("semgrep_scan", {"relative_path": "../outside.py"}, identity)

    findings = risky.get("findings") or []
    checks = {
        "discovery_restricted": discovered == ["semgrep_scan"],
        "clean_scan_ok": clean.get("status") == "ok" and clean.get("finding_count") == 0,
        "eval_scan_detected": risky.get("status") == "ok" and any(
            "dangerous-python-eval" in str(item.get("rule_id", "")) for item in findings
        ),
        "path_escape_denied": escaped.get("status") == "denied",
        "audit_has_no_arguments": "clean_sample.py" not in json.dumps(events),
        "audit_has_no_capability": "capability" not in json.dumps(events),
    }
    evidence = {
        "kind": "opt01_local_mcp_semgrep_smoke",
        "run_at": datetime.now(timezone.utc).isoformat(),
        "environment": {
            "python": sys.version.split()[0],
            "semgrep": _semgrep_version(),
            "transport": "stdio",
            "server": "app.enterprise_runtime.mcp_server",
        },
        "discovered_tools": discovered,
        "calls": {"clean": clean, "risky": risky, "path_escape": escaped},
        "audit_events": events,
        "checks": checks,
        "passed": all(checks.values()),
    }
    target = Path(args.out)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"discovered_tools={discovered}")
    print(f"clean={json.dumps(clean, ensure_ascii=False)}")
    print(f"risky_findings={json.dumps(findings, ensure_ascii=False)}")
    print(f"path_escape={json.dumps(escaped, ensure_ascii=False)}")
    print(f"checks={json.dumps(checks, ensure_ascii=False)}")
    print(f"evidence={target.resolve()}")
    return 0 if evidence["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
