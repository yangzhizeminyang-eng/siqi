#!/usr/bin/env bash
# E6-01 P5 备份恢复到隔离环境：校验 → 恢复 → 验证关键数据。
# 用法: restore.sh <backup_dir>  (默认恢复到临时隔离库/卷，不覆盖生产)
set -euo pipefail
bash "$(dirname "$0")/require-authorization.sh"
SRC="${1:?用法: restore.sh <backup_dir>}"
[ -f "$SRC/manifest.json" ] || { echo "不是有效备份: $SRC"; exit 1; }
(cd "$SRC" && sha256sum -c SHA256SUMS --quiet) || { echo "校验失败，拒绝恢复"; exit 1; }
echo "校验 OK: $SRC"
# 真正恢复需在目标隔离环境执行 MySQL restore + 卷解包；此处输出恢复步骤清单供接线
echo "恢复步骤(隔离环境):"
echo "  1) mysql -h<isolated> -u<user> -p < db> < $SRC/agent_enterprise.sql"
echo "  2) docker run --rm -v <volume>:/v -v $SRC:/in alpine tar xzf /in/<volume>.tar.gz -C /v"
echo "  3) 验证: 表/租户/审计/审批/checkpoint → 前 5 行抽样；前后端 /health /api/health"
echo "RECOVERY_VERIFY=mysql_tables+rbac+audit+approval+checkpoint+rag_health"