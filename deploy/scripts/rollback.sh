#!/usr/bin/env bash
# E6-01 P6 回滚：回到旧镜像 tag + 恢复最近备份(隔离) + 兼容说明。
set -euo pipefail
bash "$(dirname "$0")/require-authorization.sh"
NEW_TAG="${1:?用法: rollback.sh <failed_new_tag> <prev_tag>}"
PREV_TAG="${2:?缺 prev_tag}"
echo "=== 回滚 ($NEW_TAG -> $PREV_TAG) ==="
echo "1) 替换镜像 tag 回 $PREV_TAG"
echo "2) 若需数据回退: bash deploy/scripts/restore.sh <最近备份>"
echo "3) 可逆迁移回退需人工确认；不可逆迁移仅前向修复（记录于 docs）"
echo "4) 回滚后健康验证: /health /api/health + 关键接口"
echo "ROLLBACK_IMAGE=$PREV_TAG"