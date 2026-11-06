#!/usr/bin/env bash
# E6-01 P3/P4 证书到期检查（供 cron 告警）。不含私钥读取。
# 默认路径与本项目真实部署（deploy/scripts/configure-host-https.sh 装入 /etc/nginx/ssl/）一致；
# 其他部署可通过 E6_CERT 覆盖。
set -uo pipefail
CERT="${E6_CERT:-/etc/nginx/ssl/agentforge.ltd_bundle.crt}"
WARN_DAYS="${CERT_WARN_DAYS:-30}"
if [ ! -f "$CERT" ]; then echo "[cert] 证书不可达 $CERT"; exit 1; fi
OPENSSL=$(openssl x509 -enddate -noout -in "$CERT" 2>/dev/null || true)
if [ -z "$OPENSSL" ]; then echo "[cert] 无法解析 $CERT"; exit 2; fi
EXP=$(echo "$OPENSSL" | cut -d= -f2)
EXPE=$(date -d "$EXP" +%s 2>/dev/null || date -j -f "%b %d %H:%M:%S %Y %Z" "$EXP" +%s 2>/dev/null || true)
NOW=$(date +%s)
DAYS=$(( (EXPE - NOW) / 86400 ))
echo "[cert] 到期: $EXP（剩余 ${DAYS}d）"
if [ "$DAYS" -lt 0 ]; then echo "[cert] CRITICAL: 证书已过期"; exit 3; fi
if [ "$DAYS" -lt "$WARN_DAYS" ]; then echo "[cert] WARN: 剩余 ${DAYS}d < ${WARN_DAYS}d，请续期"; exit 4; fi
echo "[cert] OK"