#!/usr/bin/env bash
# 外部/生产操作强制门禁：AUTHORIZATION_OPERATION + 人工确认标记 + 结构校验 + Ed25519 签名。
# 公钥由 AUTHORIZATION_CONFIRMATION_PUBLIC_KEY[_FILE] 提供；私钥由用户持有。
# 结构检查不能替代用户在对话中的逐项确认；否则退出 2。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${AUTHORIZATION_REPO_ROOT:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
OP="${AUTHORIZATION_OPERATION:-}"
LOG="${AUTHORIZATION_LOG:-$REPO_ROOT/docs/project-development/assets/fix/fix_external_operation_authorization_20261019.json}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
GATE="${AUTHORIZATION_GATE:-$REPO_ROOT/backend/scripts/authorization_gate.py}"

if [ -z "$OP" ]; then
  echo "拒绝执行：未设置 AUTHORIZATION_OPERATION（逐项授权 ID）" >&2
  exit 2
fi

if [ "${AUTHORIZATION_HUMAN_APPROVAL:-}" != "1" ]; then
  echo "拒绝执行：结构检查不能替代人工确认；请由人在核对操作/目标/范围后设置 AUTHORIZATION_HUMAN_APPROVAL=1" >&2
  exit 2
fi

if ! GATE_OUT="$("$PYTHON_BIN" "$GATE" \
  --repo-root "$REPO_ROOT" --log "$LOG" --operation "$OP" --preflight \
  --require-signature --json 2>&1)"; then
  echo "拒绝执行：$OP 缺少有效逐项授权或 Ed25519 签名（log=$LOG）" >&2
  echo "$GATE_OUT" >&2
  exit 2
fi
