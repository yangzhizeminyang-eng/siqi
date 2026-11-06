"""OPT-07 trace 演示 CLI 验收：隔离自持 + 6 类 span 全链路 + 跨 cwd 可复现。

启动子进程并在「敌对环境」（真实 provider + DB/Redis/Celery + 凭据变量在场）下运行，
证明演示脚本自持隔离、不依赖外部服务、不伪造成功；两次不同 cwd 的结构投影一致。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

_STABLE_DROP_KEYS = {"generated_at", "duration_ms", "span_id", "approval_id", "started_at"}


def _stable_projection(value):
    if isinstance(value, dict):
        return {k: _stable_projection(v) for k, v in sorted(value.items()) if k not in _STABLE_DROP_KEYS}
    if isinstance(value, list):
        return [_stable_projection(v) for v in value]
    return value


def _run_demo(cwd: Path, out: Path) -> subprocess.CompletedProcess:
    backend_root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    # 敌对环境：真实 provider + DB/Redis/Celery + Gitee/LLM 凭据全部在场，演示必须自行强制隔离
    env.update({
        "APP_ENV": "local",
        "AGENT_PROVIDER": "langchain",
        "USE_SQLALCHEMY": "true",
        "DATABASE_URL": "mysql+pymysql://root:hostile@127.0.0.1:1/none",
        "USE_CELERY": "true",
        "CELERY_BROKER_URL": "redis://127.0.0.1:1/0",
        "CELERY_RESULT_BACKEND": "redis://127.0.0.1:1/0",
        "GITEE_TOKEN": "hostile-token",
        "LLM_API_KEY": "hostile-llm-key",
        "PYTHONIOENCODING": "utf-8",
    })
    return subprocess.run(
        [sys.executable, str(backend_root / "scripts" / "opt07_trace_demo.py"), "--out", str(out)],
        cwd=cwd, env=env, capture_output=True, text=True, timeout=180, check=False,
    )


def test_opt07_demo_cli_isolated_all_checks_pass(tmp_path):
    out = tmp_path / "opt07_evidence.json"
    result = _run_demo(tmp_path, out)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(out.read_text(encoding="utf-8"))

    env = report["environment"]
    assert env["agent_provider"] == "deterministic"
    assert env["database"] == "disabled" and env["celery"] == "disabled"
    assert report["checks"]["all_passed"] is True, report["checks"]

    summary = report["span_summary"]
    assert summary["total"] >= 6
    assert set(summary["by_category"]) >= {"worker", "model", "artifact", "tool", "rag", "approval"}
    assert summary["status_counts"]["error"] >= 1, "存在真实错误链路（404）"

    # Token 未采集如实标记；错误类型可检索；脱敏强制执行
    assert report["model_span"]["tokens"]["status"] == "not_collected"
    assert report["error_span"]["error_kind"] == "http_404"
    assert report["checks"]["redaction_enforced"] is True
    assert report["retention"]["max_spans"] > 0 and report["retention"]["dropped_total"] == 0
    assert report["workflow"]["final_status"] == "succeeded"


def test_opt07_demo_reproducible_across_cwd(tmp_path):
    out_a = tmp_path / "a" / "evidence.json"
    out_b = tmp_path / "b" / "evidence.json"
    out_a.parent.mkdir()
    out_b.parent.mkdir()
    cwd_a = tmp_path / "run_a"
    cwd_b = tmp_path / "run_b"
    cwd_a.mkdir()
    cwd_b.mkdir()

    first = _run_demo(cwd_a, out_a)
    second = _run_demo(cwd_b, out_b)
    assert first.returncode == 0, first.stdout + first.stderr
    assert second.returncode == 0, second.stdout + second.stderr

    stable_a = _stable_projection(json.loads(out_a.read_text(encoding="utf-8")))
    stable_b = _stable_projection(json.loads(out_b.read_text(encoding="utf-8")))
    assert stable_a == stable_b
