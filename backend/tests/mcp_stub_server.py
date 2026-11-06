"""Local MCP test server for stdio client protocol tests."""

import asyncio
import json
import os

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("mcp-client-test")


@mcp.tool()
async def semgrep_scan(relative_path: str, capability: str, rules_path: str = "") -> str:
    expected = os.environ.get("MCP_SESSION_TOKEN", "")
    if not expected or capability != expected:
        raise PermissionError("invalid capability")
    if relative_path == "slow.py":
        await asyncio.sleep(2)
    if relative_path == "error.py":
        raise RuntimeError("fixture failure")
    if relative_path == "invalid_json.py":
        return "not-json {{{"
    return json.dumps({"status": "ok", "relative_path": relative_path})


if __name__ == "__main__":
    mcp.run(transport="stdio")
