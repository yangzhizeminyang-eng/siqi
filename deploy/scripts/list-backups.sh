#!/usr/bin/env bash
# E6-01 P5 备份列出/校验。
set -euo pipefail
BACKUP_ROOT="${BACKUP_ROOT:-/var/backups/agent-enterprise}"
[ -d "$BACKUP_ROOT" ] || { echo "无备份目录 $BACKUP_ROOT"; exit 0; }
for d in "$BACKUP_ROOT"/*/; do
  [ -d "$d" ] || continue
  echo "== ${d} =="
  if [ -f "$d/manifest.json" ]; then cat "$d/manifest.json"; echo; fi
  if [ -f "$d/SHA256SUMS" ]; then
    if (cd "$d" && sha256sum -c SHA256SUMS --quiet) 2>/dev/null; then echo "  校验: OK"; else echo "  校验: 失败(不完整)"; fi
  fi
done