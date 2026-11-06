#!/usr/bin/env bash
# E6-01 P3 HTTPS 验证：证书链、重定向、安全头和服务健康。
# 证书路径/私钥不入库；默认按 agentforge.ltd 的公网入口检查。
set -uo pipefail

DOMAIN="${1:?用法: verify-https.sh <domain>}"
EXPECTED_REDIRECT="${E6_EXPECTED_REDIRECT:-https://${DOMAIN}/}"

echo "=== HTTPS 链路验证 domain=$DOMAIN ==="

fail() {
  echo "[verify-https] FAIL: $*" >&2
  exit 1
}

command -v openssl >/dev/null 2>&1 || fail "缺少 openssl"
command -v curl >/dev/null 2>&1 || fail "缺少 curl"

# 1) TLS 握手和证书链校验。
TLS="$(printf '' | timeout 12 openssl s_client \
  -connect "${DOMAIN}:443" \
  -servername "$DOMAIN" \
  -verify_return_error 2>&1 || true)"
echo "$TLS" | grep -q "Verify return code: 0" \
  || fail "TLS 握手失败、证书链不完整或域名不匹配"
echo "TLS 握手: OK（证书链验证通过）"

# 2) HTTP 必须跳转到 HTTPS。
RD="$(curl -sS -o /dev/null -w "%{redirect_url}" -m 10 "http://${DOMAIN}/" 2>/dev/null || true)"
echo "HTTP 重定向: ${RD:-无}"
[[ "$RD" == "$EXPECTED_REDIRECT"* ]] \
  || fail "HTTP 重定向不符合预期，期望前缀: $EXPECTED_REDIRECT"

# 3) HTTPS 安全头和应用健康。
HDRS="$(curl -fsS -D - -o /dev/null -m 10 "https://${DOMAIN}/" 2>/dev/null || true)"
[[ -n "$HDRS" ]] || fail "HTTPS 首页不可达"
for header in Strict-Transport-Security X-Content-Type-Options X-Frame-Options; do
  echo "$HDRS" | grep -qiE "^${header}:" \
    || fail "缺少安全头: ${header}"
  echo "安全头 ${header}: 存在"
done

STATUS="$(curl -ksS -o /dev/null -w "%{http_code}" -m 10 "https://${DOMAIN}/api/health" 2>/dev/null || true)"
echo "HTTPS /api/health: ${STATUS:-不可达}"
[[ "$STATUS" == "200" ]] || fail "HTTPS /api/health 非 200"

echo "HTTPS 验证: PASS"
