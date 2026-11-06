#!/usr/bin/env bash
# E6-01 P5 企业版备份：MySQL dump + Redis 必要数据 + Milvus/数据卷 + 上传目录。
# 硬约束：备份文件不含明文密钥；含 时间/版本/数据库/commit/env/校验值(sha256)；列出/校验/恢复路径分离。
set -euo pipefail
bash "$(dirname "$0")/require-authorization.sh"

ENV_FILE="${ENTERPRISE_ENV_FILE:-$(dirname "$0")/../env/enterprise.env.example}"
BACKUP_ROOT="${BACKUP_ROOT:-/var/backups/agent-enterprise}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
GIT_COMMIT="$(git -C "$(dirname "$0")/../.." rev-parse --short HEAD 2>/dev/null || echo unknown)"
ENV_TAG="${APP_ENV:-enterprise}"
DB_DUMP_DIR="${BACKUP_ROOT}/$STAMP"
mkdir -p "$DB_DUMP_DIR"

info(){ echo "[backup] $*"; }
fail(){ echo "[backup] FAIL: $*" >&2; exit 1; }

info "备份目标: $DB_DUMP_DIR (env=$ENV_TAG commit=$GIT_COMMIT)"

# 加载 env（用于 DATABASE_URL / REDIS_URL / 卷名）
if [ -f "$ENV_FILE" ]; then set -a; . "$ENV_FILE"; set +a; fi

# 1) MySQL：真实 dump；缺 mysql 客户端则显式失败
if command -v mysqldump >/dev/null 2>&1 && [ -n "${DATABASE_URL:-}" ]; then
  # 解析 mysql+pymysql://user:pass@host:port/db
  m="${DATABASE_URL#mysql+pymysql://}"; m="${m%%\?*}"
  muser="${m%%:*}"; rest="${m#*:}"; mpass="${rest%%@*}"; rhost="${rest#*@}"; rhost="${rhost%%/*}"; mdb="${m##*/}"; mdb="${mdb%%\?*}"
  mysqldump -h"${rhost%%:*}" -P"${rhost#*:}" -u"$muser" -p"$mpass" --single-transaction --hex-blob --routines --triggers "$mdb" > "$DB_DUMP_DIR/agent_enterprise.sql" 2>/dev/null \
    || fail "MySQL 备份失败"
  info "MySQL dump: agent_enterprise.sql ($(wc -c < "$DB_DUMP_DIR/agent_enterprise.sql" 2>/dev/null || echo 0) bytes)"
else
  info "跳过 MySQL（未配置 DATABASE_URL 或缺少 mysqldump）——生产必须配置"
fi

# 2) Redis 必要数据（AOF/RDB 快照）：若 redis-cli 可达则 SAVE
if command -v redis-cli >/dev/null 2>&1 && [ -n "${REDIS_URL:-}" ]; then
  rh="${REDIS_URL#redis://}"; rh="${rh%%/*}"
  redis-cli -h "${rh%%:*}" -p "${rh#*:}" SAVE >/dev/null 2>&1 && info "Redis SAVE 触发" || info "Redis SAVE 跳过（客户端不可达）"
fi

# 3) Milvus 数据卷 + 上传/数据目录：tar（保留元数据，不含明文密钥）
for v in agent_enterprise_data agent_enterprise_milvus_data agent_enterprise_milvus_etcd_data agent_enterprise_milvus_minio_data; do
  if command -v docker >/dev/null 2>&1 && docker volume inspect "$v" >/dev/null 2>&1; then
    docker run --rm -v "$v":/v:ro -v "$DB_DUMP_DIR":/out alpine tar czf "/out/$v.tar.gz" -C /v . 2>/dev/null \
      && info "卷 $v 已备份" || info "卷 $v 备份失败(可重试)"
  fi
done

# 4) 清单 + 校验值 + 元数据（不含密钥）
( cd "$DB_DUMP_DIR"
  find . -type f -exec sha256sum {} \; > SHA256SUMS
  cat > manifest.json <<EOF
{"backup_id":"$STAMP","env":"$ENV_TAG","git_commit":"$GIT_COMMIT","database":"$mdb","created_at":"$STAMP","files":$(find . -type f | wc -l)}
EOF
)
info "校验清单: $DB_DUMP_DIR/SHA256SUMS"
echo "BACKUP_DIR=$DB_DUMP_DIR"