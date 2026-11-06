#!/usr/bin/env bash
# E6-01 P7 并发压测：登录 + 健康读，输出并发/成功率/4xx/5xx/timeout/p50·p95·p99（可移植 awk/sort）。
# 真实满量压测（审批/Runtime/RAG/副本重启/依赖阻断）需在目标环境施压，此处为可执行骨架。
set -uo pipefail
bash "$(dirname "$0")/require-authorization.sh" || exit 2
BASE="${LOADTEST_BASE:-http://localhost:8081}"
N="${LOADTEST_CONCURRENCY:-20}"
LOGIN_BODY='{"account":"admin@company.com","password":"Admin123!"}'
echo "=== 并发压测 base=$BASE concurrency=$N ==="
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
run(){ # $1=label $2=curl-cmd-template-with-%N
  local label="$1" code ms u t0 t1 i
  : >"$TMP/${label}_raw"
  for i in $(seq "$N"); do
    t0=$(date +%s%N)
    code=$(curl -s -o /dev/null -w "%{http_code}" -m 10 "$2" 2>/dev/null)
    t1=$(date +%s%N); ms=$(( (t1-t0)/1000000 )); echo "$code $ms" >>"$TMP/${label}_raw" &
  done
  wait
  echo "== $label（concurrency=$N）=="
  # 状态分布
  awk '{c[$1]++; n++} END{ ok=c["200"]?c["200"]:0; five=0; four=0; to=0;
    for(k in c){ if(k>=500)five+=c[k]; else if(k>=400)four+=c[k]; else if(k=="000")to+=c[k] }
    printf "  总请求=%d 成功(200)=%d 成功率=%.1f%% 4xx=%d 5xx=%d timeout/000=%d\n", n, ok, (n>0?ok*100/n:0), four, five, to }' "$TMP/${label}_raw"
  # p50/p95/p99（portable）
  local n; n=$(wc -l <"$TMP/${label}_raw")
  if [ "$n" -gt 0 ]; then
    awk '{print $2}' "$TMP/${label}_raw" | sort -n >"$TMP/${label}_lat"
    local lo=$((n*50/100)); [ "$lo" -lt 1 ] && lo=1; [ "$lo" -gt "$n" ] && lo=$n
    local hi=$((n*95/100)); [ "$hi" -lt 1 ] && hi=1; [ "$hi" -gt "$n" ] && hi=$n
    local nx=$((n*99/100)); [ "$nx" -lt 1 ] && nx=1; [ "$nx" -gt "$n" ] && nx=$n
    local p50 p95 p99
    p50=$(sed -n "${lo}p" "$TMP/${label}_lat"); p95=$(sed -n "${hi}p" "$TMP/${label}_lat"); p99=$(sed -n "${nx}p" "$TMP/${label}_lat")
    echo "  p50=${p50}ms p95=${p95}ms p99=${p99}ms"
  fi
}
[ -n "${1:-}" ] && BASE="$1"   # 可选 base 覆盖
[ -n "${2:-}" ] && N="$2"      # 可选并发数覆盖
run login "$BASE/api/auth/login -s -o /dev/null -w '%{http_code}' -X POST -H 'Content-Type: application/json' -d '$LOGIN_BODY'"
run health "$BASE/api/health -s -o /dev/null -w '%{http_code}'"
echo "=== 压测骨架完成；满量(审批/Runtime/RAG/副本重启/组件短断)待目标环境 ==="