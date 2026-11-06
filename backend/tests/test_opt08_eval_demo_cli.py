"""OPT-08 演示 CLI 子进程测试：敌对环境自压制 + 证据可复读。

演示脚本必须自证隔离：即使父进程注入敌意 USE_CELERY/USE_SQLALCHEMY/USE_MILVUS 与
凭据变量，脚本内置隔离块也要把运行压回内存态（database/celery/milvus 全 disabled），
全程零外呼；生产模式在演示环境中必须以 blocked 诚实收尾（不冒称 verified）。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _run_demo(cwd: Path, out: Path, reports_dir: Path) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env.update({
        "USE_SQLALCHEMY": "true",
        "DATABASE_URL": "mysql+pymysql://root:hostile@127.0.0.1:3306/hostile",
        "USE_CELERY": "true",
        "CELERY_BROKER_URL": "redis://127.0.0.1:6399/0",
        "CELERY_RESULT_BACKEND": "redis://127.0.0.1:6399/1",
        "USE_MILVUS": "true",
        "MILVUS_URI": "http://127.0.0.1:19999",
        "GITEE_TOKEN": "hostile-gitee-token",
        "LLM_API_KEY": "hostile-llm-key",
        "PYTHONIOENCODING": "utf-8",
    })
    return subprocess.run(
        [sys.executable, str(BACKEND_ROOT / "scripts" / "opt08_rag_eval_online_demo.py"),
         "--out", str(out), "--reports-dir", str(reports_dir)],
        cwd=cwd, env=env, capture_output=True, text=True, timeout=300, check=False,
    )


def test_opt08_demo_cli_isolated_evidence(tmp_path):
    out = tmp_path / "opt08_evidence.json"
    reports_dir = tmp_path / "reports"
    result = _run_demo(tmp_path, out, reports_dir)
    assert result.returncode == 0, result.stdout + result.stderr

    report = json.loads(out.read_text(encoding="utf-8"))
    env = report["environment"]
    assert env["stores"] == "in_memory"
    assert env["database"] == "disabled"
    assert env["celery"] == "disabled"
    assert env["milvus"] == "disabled"
    assert env["production_real_executed"] is False

    checks = report["checks"]
    assert checks["all_passed"] is True, checks
    assert checks["developer_trigger_denied"] is True
    assert checks["auditor_trigger_denied"] is True
    assert checks["production_requires_confirmation"] is True
    assert checks["production_confirmed_honest_blocked"] is True

    run = report["trigger"]["run"]
    assert run["status"] == "succeeded"
    assert run["validation_status"] == "local_only"
    assert run["summary"]["total_cases"] == 32 and run["summary"]["passed_cases"] == 29

    assert report["report"]["validation_status"] == "local_only"
    assert report["report"]["dataset_sha256"]

    # 在线报告确实落在演示报告目录（--reports-dir 隔离，不污染 canonical reports/）
    written = list(reports_dir.glob("online_rageval_*.json"))
    assert len(written) >= 1
