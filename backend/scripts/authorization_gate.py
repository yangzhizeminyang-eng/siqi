"""外部/生产操作逐项确认门禁。

在执任何外部写、生产迁移/写入、生产部署/重启或故障演练前，先用本脚本校验
authorization log。未逐项确认、未追认、确认工件缺失或证据不合规时退出码为 2。

默认只做**结构性校验**（字段、时间、确认工件、证据路径）。执行入口使用
``--require-signature`` 时还必须验证用户持有的 Ed25519 私钥签名；公钥由
``AUTHORIZATION_CONFIRMATION_PUBLIC_KEY[_FILE]`` 提供。签名验证不等于生产状态
已被独立运维核实。
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
from datetime import datetime
from pathlib import Path

SCHEMA = "external-operation-authorization-log-v1"
REPO_ROOT = Path(__file__).resolve().parents[2]
EVIDENCE_REL = Path("docs/project-development/assets/fix")
DEFAULT_LOG = REPO_ROOT / EVIDENCE_REL / "fix_external_operation_authorization_20261019.json"
ALLOWED_EVIDENCE_SUFFIXES = {".json", ".txt", ".log", ".md"}
SIGNATURE_ALGORITHM = "ed25519"
SIGNATURE_FIELDS = ("confirmation_signature", "confirmation_signature_algorithm")


def signature_payload(log: dict) -> bytes:
    """Canonical bytes covered by the authorization signature."""
    clean = {key: value for key, value in log.items() if key not in SIGNATURE_FIELDS}
    return json.dumps(
        clean, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def trusted_public_key() -> tuple[bytes | None, str]:
    """Read the user-held Ed25519 public key from env (base64) or a file."""
    raw = os.getenv("AUTHORIZATION_CONFIRMATION_PUBLIC_KEY", "").strip()
    source = "env:AUTHORIZATION_CONFIRMATION_PUBLIC_KEY"
    if not raw:
        path_value = os.getenv("AUTHORIZATION_CONFIRMATION_PUBLIC_KEY_FILE", "").strip()
        if not path_value:
            return None, ""
        source = path_value
        try:
            raw = Path(path_value).expanduser().read_text(encoding="utf-8").strip()
        except OSError:
            return None, source
    try:
        key = base64.b64decode(raw, validate=True)
    except Exception:  # noqa: BLE001 - malformed base64 is a validation failure
        return None, source
    return (key, source) if len(key) == 32 else (None, source)


def _signature_error(log: dict, public_key: bytes | None) -> str | None:
    algorithm = log.get("confirmation_signature_algorithm")
    if algorithm != SIGNATURE_ALGORITHM:
        return f"confirmation_signature_algorithm 必须是 {SIGNATURE_ALGORITHM}"
    signature = log.get("confirmation_signature")
    if not isinstance(signature, str) or not signature.strip():
        return "confirmation_signature 缺失"
    if public_key is None:
        return "缺少受信任的 Ed25519 公钥（AUTHORIZATION_CONFIRMATION_PUBLIC_KEY[_FILE]）"
    try:
        from cryptography.hazmat.primitives.asymmetric import ed25519

        signature_bytes = base64.b64decode(signature.strip(), validate=True)
        ed25519.Ed25519PublicKey.from_public_bytes(public_key).verify(
            signature_bytes, signature_payload(log)
        )
    except Exception as exc:  # noqa: BLE001 - any verify failure is a block
        return f"confirmation_signature 校验失败: {type(exc).__name__}"
    return None


def _evidence_paths(value) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [part.strip() for part in value.split(";") if part.strip()]
    return []


def _resolve(path_like, repo_root: Path) -> Path:
    path = Path(str(path_like))
    return path if path.is_absolute() else repo_root / path


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _parse_timestamp(value) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _confirmation_artifact_error(log: dict, selected: list[dict], repo_root: Path) -> str | None:
    value = log.get("confirmation_artifact")
    if not isinstance(value, str) or not value.strip():
        return "confirmation_artifact 缺失"
    source = log.get("confirmation_source")
    if not isinstance(source, str) or not source.strip():
        return "confirmation_source 缺失（无法定位确认来源）"
    artifact = _resolve(value, repo_root)
    fix_root = repo_root / EVIDENCE_REL
    if not _is_within(artifact, fix_root):
        return "confirmation_artifact 必须位于 docs/project-development/assets/fix/"
    if not artifact.is_file():
        return f"confirmation_artifact 不存在: {value}"
    try:
        raw = artifact.read_bytes()
        text = raw.decode("utf-8", errors="replace")
    except OSError as exc:
        return f"confirmation_artifact 读取失败: {exc}"
    expected = log.get("confirmation_sha256")
    actual = hashlib.sha256(raw).hexdigest()
    if not isinstance(expected, str) or expected.strip().lower() != actual:
        return "confirmation_sha256 缺失或与工件不一致"
    for item in selected:
        op_id = str(item.get("id") or "")
        quote = str(item.get("confirmation_quote") or "")
        if not op_id or op_id not in text:
            return f"confirmation_artifact 未包含 operation={op_id or '<empty>'}"
        if not quote or quote not in text:
            return f"{op_id}.confirmation_quote 未在 confirmation_artifact 中出现"
    return None


def validate(
    log: dict,
    operation: str | None = None,
    *,
    repo_root: Path = REPO_ROOT,
    check_evidence: bool = True,
    require_signature: bool = False,
    public_key: bytes | None = None,
) -> list[str]:
    reasons: list[str] = []
    if log.get("schema") != SCHEMA:
        reasons.append(f"schema 必须是 {SCHEMA}")
    if require_signature:
        signature_error = _signature_error(log, public_key)
        if signature_error:
            reasons.append(signature_error)

    basis = log.get("authorization_basis") or {}
    if basis.get("per_item_confirmation_recorded") is not True:
        reasons.append("authorization_basis.per_item_confirmation_recorded 不是 true")

    ratification = log.get("ratification") or {}
    if ratification.get("status") != "ratified":
        reasons.append(
            f"ratification.status={ratification.get('status')!r}，必须为 'ratified'")
    if ratification.get("explicit_ratification_received") is not True:
        reasons.append("ratification.explicit_ratification_received 不是 true")

    gate = log.get("forward_gate") or {}
    if gate.get("status") != "active":
        reasons.append("forward_gate.status 不是 active")

    operations = log.get("operations")
    if not isinstance(operations, list) or not operations:
        reasons.append("operations 缺失或为空")
        return reasons

    selected = [
        item for item in operations
        if operation is None or item.get("id") == operation
    ]
    if operation is not None and not selected:
        reasons.append(f"未找到 operation={operation}")
        return reasons

    artifact_error = _confirmation_artifact_error(log, selected, repo_root)
    if artifact_error:
        reasons.append(artifact_error)

    fix_root = repo_root / EVIDENCE_REL
    for item in selected:
        op_id = item.get("id") or "<unknown>"
        if item.get("per_item_confirmed") is not True:
            reasons.append(f"{op_id}.per_item_confirmed 不是布尔 true")
        if _parse_timestamp(item.get("confirmed_at")) is None:
            reasons.append(f"{op_id}.confirmed_at 不是带时区的 ISO-8601 时间")
        approved_by = item.get("approved_by")
        if not isinstance(approved_by, str) or not approved_by.startswith("user:"):
            reasons.append(f"{op_id}.approved_by 必须是非空 'user:' 前缀字符串")
        rollback = item.get("rollback")
        if not isinstance(rollback, str) or not rollback.strip():
            reasons.append(f"{op_id}.rollback 缺失或为空")

        evidence = _evidence_paths(item.get("evidence"))
        if not evidence:
            reasons.append(f"{op_id}.evidence 缺失")
        for raw in evidence:
            candidate = _resolve(raw, repo_root)
            if not _is_within(candidate, fix_root):
                reasons.append(
                    f"{op_id}.evidence 必须在 docs/project-development/assets/fix/ 下: {raw}")
                continue
            if candidate.suffix.lower() not in ALLOWED_EVIDENCE_SUFFIXES:
                reasons.append(f"{op_id}.evidence 类型不允许: {raw}")
                continue
            if check_evidence and not candidate.is_file():
                reasons.append(f"{op_id}.evidence 不存在: {raw}")
                continue
            if candidate.suffix.lower() == ".json" and check_evidence:
                try:
                    json.loads(candidate.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as exc:
                    reasons.append(f"{op_id}.evidence 不是有效 JSON: {raw} ({exc})")
    return reasons


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="authorization_gate")
    parser.add_argument("--log", default=str(DEFAULT_LOG))
    parser.add_argument("--operation", default=None, help="只校验某个 AUTH-OP ID")
    parser.add_argument("--repo-root", default=str(REPO_ROOT))
    parser.add_argument(
        "--preflight", action="store_true",
        help="操作前校验：跳过证据文件存在性/JSON 校验，但仍校验路径、类型、确认工件")
    parser.add_argument(
        "--require-signature", action="store_true",
        help="要求 Ed25519 签名；公钥由 AUTHORIZATION_CONFIRMATION_PUBLIC_KEY[_FILE] 提供")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    repo_root = Path(args.repo_root).resolve()
    path = _resolve(args.log, repo_root)
    try:
        log = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(json.dumps({
            "structurally_complete": False,
            "human_approval_required": True,
            "proof": "none_structural_check_only",
            "reasons": [f"日志不存在: {path}"],
        }, ensure_ascii=False))
        return 2
    except json.JSONDecodeError as exc:
        print(json.dumps({
            "structurally_complete": False,
            "human_approval_required": True,
            "proof": "none_structural_check_only",
            "reasons": [f"JSON 解析失败: {exc}"],
        }, ensure_ascii=False))
        return 2

    check_evidence = not args.preflight
    public_key, public_key_source = trusted_public_key() if args.require_signature else (None, "")
    reasons = validate(
        log, args.operation, repo_root=repo_root, check_evidence=check_evidence,
        require_signature=args.require_signature, public_key=public_key,
    )
    signature_verified = bool(
        args.require_signature and public_key is not None
        and _signature_error(log, public_key) is None
    )
    fingerprint = hashlib.sha256(public_key).hexdigest()[:16] if public_key else ""
    result = {
        "structurally_complete": not reasons,
        "human_approval_required": True,
        "structural_only": not signature_verified,
        "signature_required": bool(args.require_signature),
        "signature_verified": signature_verified,
        "public_key_source": public_key_source,
        "public_key_fingerprint": fingerprint,
        "proof": "ed25519_signature" if signature_verified else "none_structural_check_only",
        "check_evidence": check_evidence,
        "log": str(path),
        "operation": args.operation,
        "reasons": reasons,
        "note": (
            "Ed25519 签名只证明持有对应私钥；仍需用户核对公钥指纹并在对话中逐项确认"
            "操作、目标与范围。签名验证不等于生产状态已被独立运维核实。"
        ),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not reasons else 2


def require_authorization(operation: str, log: str | None = None) -> None:
    """高风险脚本的强制前置检查：结构检查 + 人工确认标记 + Ed25519 签名。

    仅当调用方显式设置 AUTHORIZATION_HUMAN_APPROVAL=1，且用户公钥与日志签名验证
    通过时才返回；否则 SystemExit(2)。公钥由
    AUTHORIZATION_CONFIRMATION_PUBLIC_KEY[_FILE] 提供，私钥由用户持有。
    """
    operation = (operation or "").strip()
    if not operation:
        print("[authorization] 拒绝执行：缺少 AUTHORIZATION_OPERATION", file=sys.stderr)
        raise SystemExit(2)
    if os.getenv("AUTHORIZATION_HUMAN_APPROVAL", "").strip() != "1":
        print(
            "[authorization] 拒绝执行：结构检查不能替代人工确认；"
            "请由人在核对操作/目标/范围后设置 AUTHORIZATION_HUMAN_APPROVAL=1",
            file=sys.stderr,
        )
        raise SystemExit(2)
    args = ["--operation", operation, "--preflight", "--require-signature", "--json"]
    if log:
        args.extend(["--log", log])
    if main(args) != 0:
        raise SystemExit(2)


if __name__ == "__main__":
    sys.exit(main())
