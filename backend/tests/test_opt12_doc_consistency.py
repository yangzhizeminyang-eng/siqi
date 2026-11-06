"""OPT-12 文档一致性检查器：门禁（当前语料零违规）+ 侦测能力（临时语料）。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
_SCRIPT = _BACKEND / "scripts" / "opt12_doc_consistency.py"


def _run(corpus: Path | None = None) -> subprocess.CompletedProcess:
    cmd = [sys.executable, str(_SCRIPT), "--json"]
    if corpus is not None:
        cmd += ["--corpus", str(corpus)]
    env = os.environ.copy()
    env["ENV_FILE"] = str(_BACKEND / "tests" / ".env.hermetic-nonexistent")
    return subprocess.run(
        cmd, cwd=str(_BACKEND), env=env,
        capture_output=True, text=True, encoding="utf-8", timeout=300,
    )


def test_current_corpus_is_clean():
    """门禁：正式开发文档语料（docs/project-development）当前必须零违规。"""
    result = _run()
    assert result.returncode == 0, f"stdout={result.stdout[-2000:]}\nstderr={result.stderr[-2000:]}"
    report = json.loads(result.stdout)
    assert report["schema"] == "opt12-doc-consistency-v1"
    assert report["docs_scanned"] >= 60
    assert report["violation_count"] == 0
    assert report["violations"] == []


def test_stale_line_ref_detected_in_temp_corpus(tmp_path):
    """侦测：`main.py:<行号>` 引用被检出并以非零码退出。"""
    (tmp_path / "bad.md").write_text("见 `backend/app/main.py:123` 的处理。\n", encoding="utf-8")
    result = _run(tmp_path)
    assert result.returncode == 1
    report = json.loads(result.stdout)
    assert report["violation_count"] == 1
    item = report["violations"][0]
    assert item["check"] == "stale_line_ref"
    assert item["doc"] == "bad.md"
    assert "main.py:123" in item["detail"]


def test_synthetic_demo_paths_allowed(tmp_path):
    """合成演示数据：评测语料中的虚构仓库文件不报 missing_path。"""
    (tmp_path / "demo.md").write_text(
        "命中片段 `src/payment/refund.py` 与 `services/callback/timeout_handler.py`。\n",
        encoding="utf-8",
    )
    result = _run(tmp_path)
    assert result.returncode == 0, f"stdout={result.stdout[-2000:]}"


def test_missing_path_and_symbol_detected(tmp_path):
    """侦测：不存在的 .py 路径与悬空符号锚点分别报 missing_path / missing_symbol。"""
    (tmp_path / "reffail.md").write_text(
        "路径 `backend/app/this_file_does_not_exist.py` 不存在；\n"
        "锚点 `backend/app/main.py（no_such_symbol_xyz）` 悬空。\n",
        encoding="utf-8",
    )
    result = _run(tmp_path)
    assert result.returncode == 1
    report = json.loads(result.stdout)
    checks = {item["check"] for item in report["violations"]}
    assert "missing_path" in checks
    assert "missing_symbol" in checks


def test_valid_symbol_anchor_passes(tmp_path):
    """正向：真实存在的符号锚点通过校验。"""
    (tmp_path / "ok.md").write_text(
        "锚点 `backend/app/models/orm.py（ProjectORM）` 存在。\n", encoding="utf-8"
    )
    result = _run(tmp_path)
    assert result.returncode == 0, f"stdout={result.stdout[-2000:]}"
