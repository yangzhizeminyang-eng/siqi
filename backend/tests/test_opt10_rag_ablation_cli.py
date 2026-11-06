"""OPT-10 消融 CLI 端到端（--quick）：子进程隔离执行，产出真实可复现报告。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]


def test_ablation_cli_quick_end_to_end(tmp_path):
    out = tmp_path / "opt10_ablation.json"
    env = os.environ.copy()
    env["ENV_FILE"] = str(_BACKEND / "tests" / ".env.hermetic-nonexistent")
    result = subprocess.run(
        [sys.executable, str(_BACKEND / "scripts" / "opt10_rag_ablation.py"), "--quick", "--out", str(out)],
        cwd=str(_BACKEND), env=env, capture_output=True, text=True, timeout=900)

    assert result.returncode == 0, f"stdout={result.stdout[-1500:]}\nstderr={result.stderr[-2500:]}"
    report = json.loads(out.read_text(encoding="utf-8"))

    assert report["schema_version"] == "rag-ablation-report-v1"
    names = [c["name"] for c in report["configs"]]
    assert names[0] == "baseline" and len(names) >= 4

    assert report["determinism"]["checked"] is True
    assert report["determinism"]["identical"] is True

    chunk_names = [v["name"] for v in report["chunking"]["variants"]]
    assert "max_chars_1200" in chunk_names
    assert len(report["chunking"]["variants"]) >= 2

    environment = report["environment"]
    assert environment["execution"] == "local_in_memory"
    assert environment["validation_status"] in ("local_only", "blocked")

    for config in report["configs"]:
        assert len(config["config_sha256"]) == 16
        assert isinstance(config["per_case"], list) and config["per_case"]

    assert set(report["deltas_vs_baseline"]) == set(names) - {"baseline"}
