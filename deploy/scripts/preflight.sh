#!/usr/bin/env bash
# E6-01 P2/P5-P7 空环境部署前置检查（企业版 / 团队版通用）。
# 硬约束：缺少必需变量 / 端口占用 / docker 不可用 → 明确失败(exit 1)，绝不静默使用危险默认值。
set -euo pipefail

REQUIRED_ENV_KEYS="APP_ENV DATA_DIR AUTH_JWT_SECRET AUTH_RECOVERY_CODE DATABASE_URL"
ENV_FILE="${ENTERPRISE_ENV_FILE:-./env/enterprise.env.example}"
WORKDIR="$(dirname "$0")/.."

fail() { echo "[preflight] FAIL: $*" >&2; exit 1; }
info() { echo "[preflight] $*"; }

echo "=== E6-01 空环境部署前置检查（$WORKDIR）==="

# 1) docker / compose
command -v docker >/dev/null 2>&1 || fail "docker 不可用"
docker info >/dev/null 2>&1 || fail "docker daemon 未运行"
docker compose version >/dev/null 2>&1 || fail "docker compose 插件不可用"
info "docker 可用：$(docker --version 2>/dev/null | tr -d '\r')"

# 2) 端口占用：默认 80/443(HTTPS 入口)、8080(团队)、8081(企业)。仅当未显式覆盖时检查
for port in 80 443 8080 8081; do
  if command -v ss >/dev/null 2>&1 && ss -ltn 2>/dev/null | grep -q ":$port "; then
    fail "端口 $port 已占用（若为本机其他合法实例，请显式指定 ENTERPRISE_PUBLIC_PORT/HTTPS 端口）"
  fi
done
info "端口 80/443/8080/8081 未监听（公网必要端口）；提示：3306/6379/19530 不应映射到公网"

# 3) 磁盘 / 内存初检
if command -v df >/dev/null 2>&1; then
  avail_kb=$(df -k "$WORKDIR" | awk 'NR==2{print $4}')
  [ -n "$avail_kb" ] && [ "$avail_kb" -lt 2097152 ] && fail "磁盘可用 <2GB，不满足企业栈基线" || info "磁盘可用约 $((avail_kb/1024)) MB"
fi

# 4) 必需 env：加载 env 文件，逐项检查
[ -f "$ENV_FILE" ] || fail "env 文件不存在: $ENV_FILE"
set -a; . "$ENV_FILE"; set +a
missing=""
for k in $REQUIRED_ENV_KEYS; do
  v="${!k:-}"
  case "$v" in
    ""|replace-with-*|user:pass*) missing="$missing $k";;
  esac
done
# 生产模式必须有 MySQL DATABASE_URL
case "${APP_ENV:-local}" in
  production|enterprise|team)
    [[ "${DATABASE_URL:-}" == mysql+pymysql://* ]] || fail "生产/企业/团队版必须配置 DATABASE_URL 指向 MySQL"
    ;;
esac
if [ -n "$missing" ]; then
  fail "缺少必需或仍为占位值的变量:$missing（必须替换为真实强值后启动）"
fi
info "必需 env 已就绪"

# 5) 时区
if command -v date >/dev/null 2>&1; then
  info "当前时间: $(date -u +%Y-%m-%dT%H:%M:%SZ) UTC"
fi

info "preflight 通过"