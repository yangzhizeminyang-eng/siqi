"""外部/生产操作逐项确认门禁回归。"""

from __future__ import annotations

import base64
import hashlib
import json
import subprocess
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

FIX_REL = Path("docs/project-development/assets/fix")


def _valid_log(tmp_path: Path) -> dict:
    fix = tmp_path / FIX_REL
    fix.mkdir(parents=True, exist_ok=True)
    evidence = fix / "evidence.json"
    evidence.write_text(json.dumps({"ok": True}), encoding="utf-8")
    artifact = fix / "confirmation.md"
    artifact.write_text(
        "AUTH-OP-TEST\nconfirmation_quote: test-quote\n", encoding="utf-8")
    return {
        "schema": "external-operation-authorization-log-v1",
        "confirmation_artifact": str(FIX_REL / "confirmation.md"),
        "confirmation_source": "conversation:test-message",
        "confirmation_sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
        "authorization_basis": {"per_item_confirmation_recorded": True},
        "ratification": {
            "status": "ratified",
            "explicit_ratification_received": True,
        },
        "forward_gate": {"status": "active"},
        "operations": [
            {
                "id": "AUTH-OP-TEST",
                "operation": "local test operation",
                "target": "tmp",
                "per_item_confirmed": True,
                "confirmed_at": "2026-10-19T10:00:00+08:00",
                "approved_by": "user: test",
                "rollback": "delete temp file",
                "evidence": str(FIX_REL / "evidence.json"),
                "confirmation_quote": "test-quote",
            }
        ],
    }


def _sign_log(log: dict) -> str:
    from scripts.authorization_gate import signature_payload

    private_key = ed25519.Ed25519PrivateKey.generate()
    log["confirmation_signature_algorithm"] = "ed25519"
    log["confirmation_signature"] = base64.b64encode(
        private_key.sign(signature_payload(log))).decode("ascii")
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return base64.b64encode(public_key).decode("ascii")


def test_unsigned_log_is_blocked_when_signature_required(tmp_path):
    from scripts import authorization_gate as gate

    log = _valid_log(tmp_path)
    reasons = gate.validate(log, repo_root=tmp_path, require_signature=True)
    assert any("confirmation_signature" in reason for reason in reasons)


def test_signed_log_passes_signature_verification(tmp_path, monkeypatch, capsys):
    from scripts import authorization_gate as gate

    log = _valid_log(tmp_path)
    public_key = _sign_log(log)
    path = tmp_path / "authorization.json"
    path.write_text(json.dumps(log), encoding="utf-8")
    monkeypatch.setenv("AUTHORIZATION_CONFIRMATION_PUBLIC_KEY", public_key)

    assert gate.main([
        "--log", str(path), "--repo-root", str(tmp_path),
        "--require-signature", "--json",
    ]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["signature_verified"] is True
    assert result["proof"] == "ed25519_signature"
    assert result["public_key_fingerprint"]


def test_tampered_signed_log_fails_verification(tmp_path):
    from scripts import authorization_gate as gate

    log = _valid_log(tmp_path)
    public_key = base64.b64decode(_sign_log(log))
    log["operations"][0]["target"] = "changed-after-signing"
    reasons = gate.validate(
        log, repo_root=tmp_path, require_signature=True, public_key=public_key)
    assert any("confirmation_signature 校验失败" in reason for reason in reasons)


def test_current_log_is_blocked_until_valid_ratification():
    from scripts import authorization_gate as gate

    log = json.loads(gate.DEFAULT_LOG.read_text(encoding="utf-8"))
    reasons = gate.validate(log)
    assert any("per_item_confirmation_recorded" in reason for reason in reasons)
    assert any("ratification.status" in reason for reason in reasons)
    assert any("confirmation_artifact" in reason for reason in reasons)
    assert gate.main(["--log", str(gate.DEFAULT_LOG), "--json"]) == 2


def test_fully_confirmed_log_passes(tmp_path):
    from scripts import authorization_gate as gate

    log = _valid_log(tmp_path)
    path = tmp_path / "authorization.json"
    path.write_text(json.dumps(log), encoding="utf-8")

    assert gate.validate(log, repo_root=tmp_path) == []
    assert gate.main([
        "--log", str(path), "--repo-root", str(tmp_path), "--json",
    ]) == 0


def test_pending_log_is_blocked(tmp_path):
    from scripts import authorization_gate as gate

    log = _valid_log(tmp_path)
    log["authorization_basis"]["per_item_confirmation_recorded"] = False
    log["ratification"]["status"] = "pending_user_ratification"
    log["ratification"]["explicit_ratification_received"] = False
    reasons = gate.validate(log, repo_root=tmp_path)
    assert any("per_item_confirmation_recorded" in reason for reason in reasons)
    assert any("ratification.status" in reason for reason in reasons)


def test_operation_filter_passes_and_unknown_operation_blocks(tmp_path):
    from scripts import authorization_gate as gate

    path = tmp_path / "authorization.json"
    path.write_text(json.dumps(_valid_log(tmp_path)), encoding="utf-8")

    assert gate.main([
        "--log", str(path), "--repo-root", str(tmp_path),
        "--operation", "AUTH-OP-TEST", "--json",
    ]) == 0
    assert gate.main([
        "--log", str(path), "--repo-root", str(tmp_path),
        "--operation", "AUTH-OP-MISSING", "--json",
    ]) == 2


def test_missing_confirmation_or_evidence_blocks(tmp_path):
    from scripts import authorization_gate as gate

    log = _valid_log(tmp_path)
    log["operations"][0]["per_item_confirmed"] = False
    log["operations"][0]["evidence"] = str(FIX_REL / "missing.json")
    reasons = gate.validate(log, repo_root=tmp_path)

    assert any("per_item_confirmed" in reason for reason in reasons)
    assert any("evidence 不存在" in reason for reason in reasons)


def test_string_false_is_not_accepted(tmp_path):
    from scripts import authorization_gate as gate

    log = _valid_log(tmp_path)
    log["operations"][0]["per_item_confirmed"] = "false"
    reasons = gate.validate(log, repo_root=tmp_path)
    assert any("不是布尔 true" in reason for reason in reasons)


def test_naive_timestamp_is_rejected(tmp_path):
    from scripts import authorization_gate as gate

    log = _valid_log(tmp_path)
    log["operations"][0]["confirmed_at"] = "2026-10-19T10:00:00"
    reasons = gate.validate(log, repo_root=tmp_path)
    assert any("带时区的 ISO-8601" in reason for reason in reasons)


def test_evidence_outside_fix_dir_is_rejected(tmp_path):
    from scripts import authorization_gate as gate

    log = _valid_log(tmp_path)
    outside = tmp_path / "other.json"
    outside.write_text("{}", encoding="utf-8")
    log["operations"][0]["evidence"] = "other.json"
    reasons = gate.validate(log, repo_root=tmp_path)
    assert any("必须在 docs/project-development/assets/fix/ 下" in reason
               for reason in reasons)


def test_missing_confirmation_artifact_blocks(tmp_path):
    from scripts import authorization_gate as gate

    log = _valid_log(tmp_path)
    log.pop("confirmation_artifact")
    reasons = gate.validate(log, repo_root=tmp_path)
    assert any("confirmation_artifact 缺失" in reason for reason in reasons)


def test_artifact_without_operation_id_blocks(tmp_path):
    from scripts import authorization_gate as gate

    log = _valid_log(tmp_path)
    artifact = tmp_path / FIX_REL / "confirmation.md"
    artifact.write_text("no matching operation here\n", encoding="utf-8")
    reasons = gate.validate(log, repo_root=tmp_path)
    assert any("confirmation_sha256" in reason for reason in reasons)

    log["confirmation_sha256"] = hashlib.sha256(artifact.read_bytes()).hexdigest()
    reasons = gate.validate(log, repo_root=tmp_path)
    assert any("未包含 operation=AUTH-OP-TEST" in reason for reason in reasons)


def test_preflight_allows_declared_but_missing_evidence(tmp_path):
    from scripts import authorization_gate as gate

    log = _valid_log(tmp_path)
    (tmp_path / FIX_REL / "evidence.json").unlink()

    assert gate.validate(log, repo_root=tmp_path, check_evidence=False) == []
    reasons = gate.validate(log, repo_root=tmp_path, check_evidence=True)
    assert any("evidence 不存在" in reason for reason in reasons)


def test_missing_confirmation_source_blocks(tmp_path):
    from scripts import authorization_gate as gate

    log = _valid_log(tmp_path)
    log.pop("confirmation_source")
    reasons = gate.validate(log, repo_root=tmp_path)
    assert any("confirmation_source" in reason for reason in reasons)


def test_confirmation_sha_mismatch_blocks(tmp_path):
    from scripts import authorization_gate as gate

    log = _valid_log(tmp_path)
    log["confirmation_sha256"] = "0" * 64
    reasons = gate.validate(log, repo_root=tmp_path)
    assert any("confirmation_sha256" in reason for reason in reasons)


def test_missing_log_returns_2(tmp_path):
    from scripts import authorization_gate as gate

    assert gate.main(["--log", str(tmp_path / "missing.json"), "--json"]) == 2


def test_sign_cli_keygen_and_sign_round_trip(tmp_path):
    from scripts import authorization_gate as gate

    backend = Path(__file__).resolve().parents[1]
    private_key = tmp_path / "key.pem"
    public_key = tmp_path / "key.pub"
    keygen = subprocess.run(
        [sys.executable, "-m", "scripts.authorization_sign", "keygen",
         "--private-key", str(private_key), "--public-key", str(public_key)],
        cwd=backend, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=60,
    )
    assert keygen.returncode == 0, keygen.stderr

    log = _valid_log(tmp_path)
    path = tmp_path / "authorization.json"
    path.write_text(json.dumps(log), encoding="utf-8")
    sign = subprocess.run(
        [sys.executable, "-m", "scripts.authorization_sign", "sign",
         "--log", str(path), "--private-key", str(private_key)],
        cwd=backend, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=60,
    )
    assert sign.returncode == 0, sign.stderr

    signed = json.loads(path.read_text(encoding="utf-8"))
    raw_public = base64.b64decode(public_key.read_text(encoding="utf-8").strip())
    assert gate.validate(
        signed, repo_root=tmp_path, require_signature=True, public_key=raw_public) == []
