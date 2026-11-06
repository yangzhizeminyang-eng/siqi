"""User-held Ed25519 key helper for external-operation authorization.

The private key belongs to the user and must stay outside the repository.
The executor only receives the public key (base64) via
``AUTHORIZATION_CONFIRMATION_PUBLIC_KEY`` or ``..._FILE``.

Examples:
    python -m scripts.authorization_sign keygen --private-key C:/keys/siqi.pem \
        --public-key C:/keys/siqi.pub
    python -m scripts.authorization_sign sign --log path/to/authorization.json \
        --private-key C:/keys/siqi.pem
    python -m scripts.authorization_sign fingerprint --public-key C:/keys/siqi.pub
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
from typing import Sequence

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from scripts.authorization_gate import SIGNATURE_ALGORITHM, signature_payload


def _read_public_key(path: Path) -> tuple[bytes, bytes]:
    raw = path.read_text(encoding="utf-8").strip()
    key = base64.b64decode(raw, validate=True)
    if len(key) != 32:
        raise SystemExit("public key must be 32 raw bytes (base64)")
    return key, raw.encode("ascii")


def _fingerprint(key: bytes) -> str:
    return hashlib.sha256(key).hexdigest()[:16]


def _keygen(args: argparse.Namespace) -> int:
    private_key = ed25519.Ed25519PrivateKey.generate()
    public_key = private_key.public_key()
    private_path = Path(args.private_key).expanduser()
    public_path = Path(args.public_key).expanduser()
    private_path.parent.mkdir(parents=True, exist_ok=True)
    public_path.parent.mkdir(parents=True, exist_ok=True)
    private_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_bytes = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    private_path.write_bytes(private_bytes)
    try:
        os.chmod(private_path, 0o600)
    except OSError:
        pass
    encoded = base64.b64encode(public_bytes).decode("ascii")
    public_path.write_text(encoded + "\n", encoding="ascii")
    print(json.dumps({
        "public_key": encoded,
        "public_key_file": str(public_path),
        "private_key_file": str(private_path),
        "fingerprint": _fingerprint(public_bytes),
        "warning": "private key is unencrypted; keep it outside the repository and never share it",
    }, ensure_ascii=False, indent=2))
    return 0


def _sign(args: argparse.Namespace) -> int:
    private_path = Path(args.private_key).expanduser()
    private_key = serialization.load_pem_private_key(
        private_path.read_bytes(), password=None)
    if not isinstance(private_key, ed25519.Ed25519PrivateKey):
        raise SystemExit("private key is not Ed25519")
    log_path = Path(args.log).expanduser()
    log = json.loads(log_path.read_text(encoding="utf-8"))
    signature = private_key.sign(signature_payload(log))
    log["confirmation_signature_algorithm"] = SIGNATURE_ALGORITHM
    log["confirmation_signature"] = base64.b64encode(signature).decode("ascii")
    out_path = Path(args.out).expanduser() if args.out else log_path
    out_path.write_text(json.dumps(log, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    public_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    print(json.dumps({
        "log": str(out_path),
        "algorithm": SIGNATURE_ALGORITHM,
        "public_key": base64.b64encode(public_bytes).decode("ascii"),
        "fingerprint": _fingerprint(public_bytes),
    }, ensure_ascii=False, indent=2))
    return 0


def _fingerprint_cmd(args: argparse.Namespace) -> int:
    key, encoded = _read_public_key(Path(args.public_key).expanduser())
    print(json.dumps({
        "public_key": encoded.decode("ascii"),
        "fingerprint": _fingerprint(key),
    }, ensure_ascii=False, indent=2))
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="authorization_sign")
    sub = parser.add_subparsers(dest="command", required=True)
    keygen = sub.add_parser("keygen", help="generate an unencrypted Ed25519 keypair")
    keygen.add_argument("--private-key", required=True)
    keygen.add_argument("--public-key", required=True)
    keygen.set_defaults(func=_keygen)
    sign = sub.add_parser("sign", help="sign an authorization log")
    sign.add_argument("--log", required=True)
    sign.add_argument("--private-key", required=True)
    sign.add_argument("--out", default="")
    sign.set_defaults(func=_sign)
    fingerprint = sub.add_parser("fingerprint", help="show public key fingerprint")
    fingerprint.add_argument("--public-key", required=True)
    fingerprint.set_defaults(func=_fingerprint_cmd)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
