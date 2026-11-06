#!/usr/bin/env bash
# E6-01: install a real domain certificate into host Nginx and bind enterprise
# frontend to localhost before enabling HTTPS.
set -euo pipefail
bash "$(dirname "$0")/require-authorization.sh"

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DOMAIN="${E6_DOMAIN:-agentforge.ltd}"
CERT_SOURCE="${E6_CERT_SOURCE:-}"
KEY_SOURCE="${E6_KEY_SOURCE:-}"
NGINX_TARGET="/etc/nginx/conf.d/${DOMAIN}.tls.conf"
CERT_TARGET="/etc/nginx/ssl/${DOMAIN}_bundle.crt"
KEY_TARGET="/etc/nginx/ssl/${DOMAIN}.key"
COMPOSE_FILE="${E6_COMPOSE_FILE:-$ROOT/docker-compose.enterprise.apponly.yml}"
TLS_OVERRIDE="$ROOT/docker-compose.enterprise.host-tls.yml"
ENV_FILE="${ENTERPRISE_ENV_FILE:-}"

fail() { echo "[configure-host-https] FAIL: $*" >&2; exit 1; }
info() { echo "[configure-host-https] $*"; }

[[ "$(id -u)" == "0" ]] || fail "请以 root 运行"
[[ -n "$CERT_SOURCE" && -f "$CERT_SOURCE" ]] || fail "E6_CERT_SOURCE 不存在或不是文件"
[[ -n "$KEY_SOURCE" && -f "$KEY_SOURCE" ]] || fail "E6_KEY_SOURCE 不存在或不是文件"
command -v nginx >/dev/null 2>&1 || fail "未安装 nginx；先安装 nginx 后再执行"
command -v openssl >/dev/null 2>&1 || fail "缺少 openssl"
command -v docker >/dev/null 2>&1 || fail "缺少 docker"
[[ -f "$COMPOSE_FILE" ]] || fail "Compose 文件不存在: $COMPOSE_FILE"
[[ -n "$ENV_FILE" && -f "$ENV_FILE" ]] || fail "必须通过 ENTERPRISE_ENV_FILE 指定服务器真实 env 文件，拒绝使用 enterprise.env.example"

CERT_SUBJECT="$(openssl x509 -in "$CERT_SOURCE" -noout -subject 2>/dev/null || true)"
[[ -n "$CERT_SUBJECT" ]] || fail "证书文件不是可读的 X.509 证书"
CERT_KEY="$(openssl x509 -in "$CERT_SOURCE" -pubkey -noout 2>/dev/null | openssl pkey -pubin -outform DER 2>/dev/null | sha256sum | cut -d' ' -f1)"
KEY_KEY="$(openssl pkey -in "$KEY_SOURCE" -pubout 2>/dev/null | openssl pkey -pubin -outform DER 2>/dev/null | sha256sum | cut -d' ' -f1)"
[[ -n "$CERT_KEY" && "$CERT_KEY" == "$KEY_KEY" ]] || fail "证书与私钥不匹配"

install -d -m 0755 /etc/nginx/ssl
install -m 0644 "$CERT_SOURCE" "$CERT_TARGET"
install -m 0600 "$KEY_SOURCE" "$KEY_TARGET"
info "证书已安装: $CERT_TARGET"
info "私钥已安装并限制为 0600: $KEY_TARGET"

if [[ -f "$NGINX_TARGET" ]]; then
  cp -p "$NGINX_TARGET" "${NGINX_TARGET}.bak.$(date -u +%Y%m%d%H%M%S)"
fi

sed \
  -e "s/agentforge\.ltd/$DOMAIN/g" \
  -e "s#127\.0\.0\.1:8081#127.0.0.1:${ENTERPRISE_PUBLIC_PORT:-8081}#g" \
  "$ROOT/nginx.enterprise.tls.conf" > "$NGINX_TARGET"

if [[ -f /etc/nginx/nginx.conf ]] && ! grep -Eq 'include[[:space:]]+/etc/nginx/conf\.d/\*\.conf;' /etc/nginx/nginx.conf; then
  fail "/etc/nginx/nginx.conf 未 include /etc/nginx/conf.d/*.conf，未自动改写，请手动接入 $NGINX_TARGET"
fi

nginx -t
export ENTERPRISE_ENV_FILE="$ENV_FILE"
docker compose -f "$COMPOSE_FILE" -f "$TLS_OVERRIDE" config --quiet
docker compose -f "$COMPOSE_FILE" -f "$TLS_OVERRIDE" up -d
systemctl reload nginx 2>/dev/null || nginx -s reload
info "HTTPS 配置已加载，企业 frontend 已切换为本机 127.0.0.1:${ENTERPRISE_PUBLIC_PORT:-8081}"
info "下一步执行: $ROOT/scripts/verify-https.sh $DOMAIN"
