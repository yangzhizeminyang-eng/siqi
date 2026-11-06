"""Shared scan policy for the local Semgrep MCP client and server (defense in depth).

Both sides enforce the same bounds so a bypass on one side is still rejected on the other.
"""

from __future__ import annotations

from pathlib import Path

ALLOWED_SCAN_SUFFIXES = frozenset({
    ".c", ".cc", ".cpp", ".cs", ".go", ".h", ".hpp", ".java", ".js", ".jsx", ".json",
    ".kt", ".php", ".py", ".rb", ".rs", ".sh", ".sql", ".swift", ".toml", ".ts", ".tsx",
    ".vue", ".yaml", ".yml",
})

MAX_SCAN_FILE_BYTES = 1024 * 1024

DEFAULT_MAX_CONCURRENT_SCANS = 2
DEFAULT_SCAN_ACQUIRE_TIMEOUT_SECONDS = 5.0


def scan_target_violation(
    target: Path,
    root: Path,
    *,
    max_bytes: int = MAX_SCAN_FILE_BYTES,
) -> str | None:
    """Return a bounded, content-free reason when the scan target violates policy."""
    if not target.is_file():
        return "scan target must be a regular file"
    if not target.is_relative_to(root):
        return "scan target is outside the isolated root"
    if target.suffix.lower() not in ALLOWED_SCAN_SUFFIXES:
        return "scan target file type is not allowed"
    try:
        size = target.stat().st_size
    except OSError:
        return "scan target size cannot be determined"
    if size > max_bytes:
        return "scan target exceeds the scan size limit"
    return None
