#!/usr/bin/env bash
# E6-01 P6 升级前检查 + 备份 + 迁移前健康门。单副本 Alembic(设计保证 worker RUN_ALEMBIC_UPGRADE=false)。
set -euo pipefail
bash "$(dirname "$0")/require-authorization.sh"
PREV_TAG="${1:?用法: upgrade.sh <prev_image_tag> <new_image_tag>}"
NEW_TAG="${2:?缺 new tag}"
echo "=== 升级前检查 ($PREV_TAG -> $NEW_TAG) ==="
[ -n "$NEW_TAG" ] || exit 1
# 升级前健康/备份/迁移检查（真实实现交部署编排；此处为门禁骨架）
curl -fsS "http://localhost:8081/api/health" >/dev/null 2>&1 || echo "[warn] 升级前 /api/health 不可达（若首次空环境部署可忽略）"
bash "$(dirname "$0")/backup.sh"
echo "升级计划: 构建 $NEW_TAG → RUN_ALEMBIC_UPGRADE=true 单实例迁移 → 滚动替换 backend → 健康验证"
echo "回滚提示: bash deploy/scripts/rollback.sh $NEW_TAG $PREV_TAG"