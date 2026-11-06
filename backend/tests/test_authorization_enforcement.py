"""授权门禁必须接入生产/外部操作入口。"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from scripts.authorization_gate import signature_payload

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND = REPO_ROOT / "backend"
GUARD = REPO_ROOT / "deploy" / "scripts" / "require-authorization.sh"
FIX_REL = Path("docs/project-development/assets/fix")
DEPLOY_ENTRYPOINTS = (
    "deploy-enterprise.sh",
    "upgrade.sh",
    "rollback.sh",
    "restore.sh",
    "backup.sh",
    "configure-host-https.sh",
    "loadtest.sh",
)
PYTHON_ENTRYPOINTS = (
    "fix_fault_watch.py",
    "fix_soak_multi_host.py",
    "milestone_a_gitee_real_e2e.py",
    "evaluate_enterprise_rag.py",
    "init_mysql.py",
    "milestone_a_real_pr.py",
    "milestone_a_resume.py",
    "mysql_e2e.py",
    "dev019_notifications_real_verify.py",
    "e4_runtime_real_loop.py",
    "e4_agent_real_loop.py",
    "celery_real_smoke.py",
    "milvus_standalone_verify.py",
    "check_redis_worker.py",
    "team_history_acceptance_seed.py",
)


GIT_BASH = Path(r"C:\Program Files\Git\bin\bash.exe")


def _posix(path) -> str:
    return Path(path).resolve().as_posix()


def _msys(path) -> str:
    resolved = Path(path).resolve()
    drive = resolved.drive.rstrip(":").lower()
    return f"/{drive}{resolved.as_posix()[2:]}" if drive else resolved.as_posix()


def _run_guard(extra_env: dict) -> subprocess.CompletedProcess:
    bash = GIT_BASH if GIT_BASH.is_file() else Path(shutil.which("bash") or "")
    if not bash.is_file():
        pytest.skip("bash 不可用，跳过 shell 门禁集成测试")
    env = os.environ.copy()
    env.update({
        "AUTHORIZATION_OPERATION": "",
        "AUTHORIZATION_REPO_ROOT": _posix(REPO_ROOT),
        "PYTHON_BIN": _msys(sys.executable),
        "PYTHONIOENCODING": "utf-8",
    })
    env.update(extra_env)
    return subprocess.run(
        [str(bash), str(GUARD)], cwd=REPO_ROOT, env=env,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=60,
    )


def _valid_log(tmp_path: Path) -> tuple[Path, str]:
    fix = tmp_path / FIX_REL
    fix.mkdir(parents=True, exist_ok=True)
    (fix / "confirmation.md").write_text(
        "AUTH-OP-TEST\nconfirmation_quote: test-quote\n", encoding="utf-8")
    (fix / "evidence.json").write_text("{}", encoding="utf-8")
    log = {
        "schema": "external-operation-authorization-log-v1",
        "confirmation_artifact": str(FIX_REL / "confirmation.md"),
        "confirmation_source": "conversation:test-message",
        "confirmation_sha256": hashlib.sha256(
            (fix / "confirmation.md").read_bytes()).hexdigest(),
        "authorization_basis": {"per_item_confirmation_recorded": True},
        "ratification": {
            "status": "ratified",
            "explicit_ratification_received": True,
        },
        "forward_gate": {"status": "active"},
        "operations": [{
            "id": "AUTH-OP-TEST",
            "operation": "local test operation",
            "target": "tmp",
            "per_item_confirmed": True,
            "confirmed_at": "2026-10-19T10:00:00+08:00",
            "approved_by": "user: test",
            "rollback": "delete temp file",
            "evidence": str(FIX_REL / "evidence.json"),
            "confirmation_quote": "test-quote",
        }],
    }
    private_key = ed25519.Ed25519PrivateKey.generate()
    log["confirmation_signature_algorithm"] = "ed25519"
    log["confirmation_signature"] = base64.b64encode(
        private_key.sign(signature_payload(log))).decode("ascii")
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    path = tmp_path / "authorization.json"
    path.write_text(json.dumps(log), encoding="utf-8")
    return path, base64.b64encode(public_key).decode("ascii")


def test_deploy_entrypoints_call_authorization_guard():
    for name in DEPLOY_ENTRYPOINTS:
        text = (REPO_ROOT / "deploy" / "scripts" / name).read_text(encoding="utf-8")
        assert "require-authorization.sh" in text, f"{name} 未接入授权门禁"


def test_python_entrypoints_call_authorization_guard():
    for name in PYTHON_ENTRYPOINTS:
        text = (BACKEND / "scripts" / name).read_text(encoding="utf-8")
        assert "require_authorization" in text, f"{name} 未接入授权门禁"


def test_guard_blocks_without_operation():
    result = _run_guard({"AUTHORIZATION_OPERATION": ""})
    assert result.returncode == 2
    assert "AUTHORIZATION_OPERATION" in result.stderr


def test_guard_blocks_pending_historical_log():
    result = _run_guard({
        "AUTHORIZATION_OPERATION": "AUTH-OP-01",
        "AUTHORIZATION_HUMAN_APPROVAL": "1",
        "AUTHORIZATION_LOG": _posix(
            REPO_ROOT / FIX_REL / "fix_external_operation_authorization_20261019.json"),
    })
    assert result.returncode == 2
    assert "缺少有效逐项授权" in result.stderr


def test_guard_passes_with_valid_temp_log(tmp_path):
    log, public_key = _valid_log(tmp_path)
    result = _run_guard({
        "AUTHORIZATION_OPERATION": "AUTH-OP-TEST",
        "AUTHORIZATION_HUMAN_APPROVAL": "1",
        "AUTHORIZATION_REPO_ROOT": _posix(tmp_path),
        "AUTHORIZATION_LOG": _posix(log),
        "AUTHORIZATION_GATE": _posix(BACKEND / "scripts" / "authorization_gate.py"),
        "AUTHORIZATION_CONFIRMATION_PUBLIC_KEY": public_key,
        "PYTHON_BIN": _msys(sys.executable),
    })
    assert result.returncode == 0, result.stderr


def test_guard_blocks_signed_log_without_public_key(tmp_path):
    log, _ = _valid_log(tmp_path)
    result = _run_guard({
        "AUTHORIZATION_OPERATION": "AUTH-OP-TEST",
        "AUTHORIZATION_HUMAN_APPROVAL": "1",
        "AUTHORIZATION_REPO_ROOT": _posix(tmp_path),
        "AUTHORIZATION_LOG": _posix(log),
        "AUTHORIZATION_GATE": _posix(BACKEND / "scripts" / "authorization_gate.py"),
        "PYTHON_BIN": _msys(sys.executable),
    })
    assert result.returncode == 2
    assert "Ed25519 公钥" in result.stderr


def test_guard_blocks_without_human_approval(tmp_path):
    log, _ = _valid_log(tmp_path)
    result = _run_guard({
        "AUTHORIZATION_OPERATION": "AUTH-OP-TEST",
        "AUTHORIZATION_REPO_ROOT": _posix(tmp_path),
        "AUTHORIZATION_LOG": _posix(log),
        "AUTHORIZATION_GATE": _posix(BACKEND / "scripts" / "authorization_gate.py"),
        "PYTHON_BIN": _msys(sys.executable),
    })
    assert result.returncode == 2
    assert "人工确认" in result.stderr


def test_migrate_blocks_production_without_authorization():
    env = os.environ.copy()
    env.update({
        "APP_ENV": "enterprise",
        "ENV_FILE": str(BACKEND / "tests" / ".env.hermetic-nonexistent"),
        "AUTHORIZATION_OPERATION": "",
        "PYTHONPATH": str(BACKEND),
        "PYTHONIOENCODING": "utf-8",
    })
    result = subprocess.run(
        [sys.executable, "-m", "scripts.migrate"], cwd=BACKEND, env=env,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=60,
    )
    assert result.returncode == 2
    assert "AUTHORIZATION_OPERATION" in (result.stdout + result.stderr)


def _run_python_module(module: str, args: list[str]) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env.update({
        "AUTHORIZATION_OPERATION": "",
        "AUTHORIZATION_HUMAN_APPROVAL": "",
        "ENV_FILE": str(BACKEND / "tests" / ".env.hermetic-nonexistent"),
        "PYTHONPATH": str(BACKEND),
        "PYTHONIOENCODING": "utf-8",
    })
    return subprocess.run(
        [sys.executable, "-m", module, *args], cwd=BACKEND, env=env,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=90,
    )


def test_fix_fault_watch_requires_authorization():
    result = _run_python_module("scripts.fix_fault_watch", ["--container", "x"])
    assert result.returncode == 2
    assert "AUTHORIZATION_OPERATION" in result.stderr


def test_fix_soak_requires_authorization():
    result = _run_python_module("scripts.fix_soak_multi_host", [])
    assert result.returncode == 2
    assert "AUTHORIZATION_OPERATION" in result.stderr


def test_evaluate_enterprise_rag_real_mode_requires_authorization():
    result = _run_python_module(
        "scripts.evaluate_enterprise_rag",
        ["evaluation/enterprise_rag/e3_05_baseline.jsonl", "--require-milvus"],
    )
    assert result.returncode == 2
    assert "AUTHORIZATION_OPERATION" in result.stderr


@pytest.mark.parametrize("module", [
    "scripts.init_mysql",
    "scripts.mysql_e2e",
    "scripts.dev019_notifications_real_verify",
    "scripts.e4_runtime_real_loop",
    "scripts.e4_agent_real_loop",
    "scripts.check_redis_worker",
    "scripts.team_history_acceptance_seed",
])
def test_db_entrypoints_require_authorization(module):
    result = _run_python_module(module, [])
    assert result.returncode == 2
    assert "AUTHORIZATION_OPERATION" in result.stderr
