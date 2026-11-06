#!/usr/bin/env bash
# E6-01 DNS/证书前置检查：不修改 DNS，只检查 A/NS/TXT 公开解析。
set -euo pipefail
DOMAIN="${1:-agentforge.ltd}"
echo "=== DNS 验证 domain=$DOMAIN ==="
command -v dig >/dev/null 2>&1 || { echo "缺少 dig（安装 dnsutils）" >&2; exit 1; }
echo "-- A $DOMAIN"
dig +short A "$DOMAIN"
echo "-- A www.$DOMAIN"
dig +short A "www.$DOMAIN"
echo "-- NS $DOMAIN"
dig +short NS "$DOMAIN"
echo "-- TXT _dnsauth.$DOMAIN（若证书已完成 DNS 验证可为空）"
dig +short TXT "_dnsauth.$DOMAIN"
