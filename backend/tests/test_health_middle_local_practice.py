"""Local-only health-middle practice harness: safe defaults and fail-fast CLI."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "scripts.health_middle_local_practice", *args],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def test_help_is_available():
    proc = _run("--help")
    assert proc.returncode == 0, proc.stderr
    assert "Local-only" in proc.stdout


def test_missing_repo_root_fails_fast(tmp_path):
    proc = _run("--repo-root", str(tmp_path / "missing"), "--data-dir", str(tmp_path / "data"))
    assert proc.returncode == 2
    assert "repo root not found" in proc.stderr
