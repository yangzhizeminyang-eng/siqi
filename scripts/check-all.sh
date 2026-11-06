#!/usr/bin/env bash
# OPT-05 固定检查命令（本地/CI 同口径）：
#   1) 后端 pytest（隔离：不读 backend/.env、无外部服务、无机密、无生产地址）
#   2) 前端 vitest
#   3) 前端 vue-tsc + vite build
#
# 用法：
#   bash scripts/check-all.sh                # 全量
#   bash scripts/check-all.sh --backend-only
#   bash scripts/check-all.sh --frontend-only
#
# requires_redis 用例：broker 不可达时自动 skip 并标注「环境缺失（非代码失败）」；
# 如需真实 Redis 验证：先启动 Redis，或设置 TEST_REDIS_URL=redis://host:port/0
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODE="${1:-all}"

# ---- 测试隔离：显式声明本次检查所需环境（与 CI 相同，不注入任何机密）----
export APP_ENV=local
export USE_SQLALCHEMY=false
export USE_CELERY=false
export MODEL_MOCK_FALLBACK=true
export ENV_FILE="$ROOT/backend/tests/.env.hermetic-nonexistent"
unset CELERY_BROKER_URL CELERY_RESULT_BACKEND DATABASE_URL MILVUS_URI MILVUS_TOKEN REDIS_URL
unset GITEE_TOKEN LLM_API_KEY ZHIPU_API_KEY

fail=0

if [ "$MODE" = "all" ] || [ "$MODE" = "--backend-only" ]; then
  if [ -x "$ROOT/backend/.venv/Scripts/python.exe" ]; then PY="$ROOT/backend/.venv/Scripts/python.exe"
  elif [ -x "$ROOT/backend/.venv/bin/python" ]; then PY="$ROOT/backend/.venv/bin/python"
  else PY="python3"; fi
  echo "== backend pytest =="
  (cd "$ROOT/backend" && "$PY" -m pytest -q -rs) || fail=1
fi

if [ "$MODE" = "all" ] || [ "$MODE" = "--frontend-only" ]; then
  echo "== frontend vitest =="
  (cd "$ROOT/frontend" && npm run test) || fail=1
  echo "== frontend build (vue-tsc + vite) =="
  (cd "$ROOT/frontend" && npm run build) || fail=1
fi

if [ "$fail" -ne 0 ]; then
  echo "CHECK-ALL: FAILED"
  exit 1
fi
echo "CHECK-ALL: OK"
