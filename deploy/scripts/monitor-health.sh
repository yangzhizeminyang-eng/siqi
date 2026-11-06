#!/usr/bin/env bash
# E6-01 P4 轻量健康/监控采集：输出 /api/health + 各端点状态 + 容器重启次数；可被 cron/systemd 轮询。
set -euo pipefail
BASE="${MONITOR_BASE:-http://localhost:8081}"
OUT="${MONITOR_OUT:-/tmp/agent-enterprise-health.txt}"
: > "$OUT"
st(){ local name="$1" url="$2"; local code; code=$(curl -s -o /dev/null -w "%{http_code}" -m 5 "$url" 2>/dev/null || echo 000); echo "$name=$code" >>"$OUT"; echo "[monitor] $name $code"; }
st /health "$BASE/health"
st /api/health "$BASE/api/health"
st /login "$BASE/login"
if command -v docker >/dev/null 2>&1; then
  echo "# restarts" >>"$OUT"
  docker ps --format '{{.Names}} {{.Status}}' 2>/dev/null | grep -iE "agent-enterprise|agent_enterprise" >> "$OUT" || true
fi
grep -qE "/health=200|/api/health=200" "$OUT" || { echo "[monitor] FAIL: 健康检查非 200"; exit 1; }
echo "[monitor] 基线健康 OK -> $OUT"