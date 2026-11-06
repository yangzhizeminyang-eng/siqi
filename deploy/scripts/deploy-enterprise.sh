#!/usr/bin/env bash
# E6-01 P2 空环境部署入口：预检通过后，以全新 project/卷部署企业版（与团队版隔离）。
set -euo pipefail
bash "$(dirname "$0")/require-authorization.sh"
cd "$(dirname "$0")/.."
bash scripts/preflight.sh
echo "=== 空环境部署企业版 (agent-enterprise) ==="
# 全新/隔离：显式 project 名与全新卷（compose 已定义 name: agent-enterprise + agent_enterprise_* 卷）
docker compose -f docker-compose.enterprise.yml up -d --build
echo "=== 健康验证 ==="
sleep 5
curl -fsS "http://localhost:${ENTERPRISE_PUBLIC_PORT:-8081}/api/health" && echo && echo "部署完成"