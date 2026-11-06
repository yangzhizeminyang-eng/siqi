# 客户部署与运维手册

> E7-01 交付物 2/6。内容沿用 E6 真实命令与路径（`docs/project-development/112-enterprise-e6-prod-ops.md` §1–34）。
> 适用私有化部署 `agentforge.ltd`（<YOUR_SERVER_IP>）的实际运维。

## 1. 生产栈组成（真实）

| 组件 | 镜像/配置 | 说明 |
|---|---|---|
| backend | `agent-enterprise-backend:e6-61a6752-replica` ×2 | 多副本，经 frontend 负载均衡（`X-Backend-Host`）|
| frontend | nginx / `127.0.0.1:8081` | host-tls 收敛，公网仅 80/443 |
| redis | `redis:7.4-alpine` `--user 999` | 非 root；卷 `agent_enterprise_redis_data` |
| MySQL | 外部 `agent_enterprise`（29 表，head `e5f6071a2b3c`）| 不公网 |
| 团队版 | `agent-team-*` :8080 | 按序保留 |

部署文件集：`deploy/docker-compose.enterprise.apponly.yml` + `deploy/docker-compose.enterprise.host-tls.yml` + `deploy/docker-compose.enterprise.redis-nonroot.yml`。

## 2. 部署（空环境）

```bash
# 预检：必填占位拒绝（DATABASE_URL 须 mysql+pymysql、JWT/恢复码、MINIO 等）
bash deploy/scripts/preflight.sh
# 部署（含 host-tls + redis-nonroot override）
ENTERPRISE_ENV_FILE=./env/enterprise.env \
docker compose -f deploy/docker-compose.enterprise.apponly.yml \
  -f deploy/docker-compose.enterprise.host-tls.yml \
  -f deploy/docker-compose.enterprise.redis-nonroot.yml up -d
# 迁移在 backend 启动时自动执行至 head；验证
curl -sk https://agentforge.ltd/api/health   # 200
```

> 注意：凡涉及 redis/backend 的 compose 命令都须带 `redis-nonroot.yml`，否则 redis 回落 root 默认（§33）。

## 3. HTTPS / 证书

```bash
# 证书路径（真实）：/etc/nginx/ssl/agentforge.ltd_bundle.crt（644）+ .key（600 root）
sudo -E E6_DOMAIN=agentforge.ltd bash deploy/scripts/configure-host-https.sh
bash deploy/scripts/verify-dns.sh agentforge.ltd
bash deploy/scripts/verify-https.sh agentforge.ltd   # 308/TLS/安全头/health
# 续期监控：每日 cron /etc/cron.d/e6-cert（03:17）追加 /var/log/agent-monitor/alerts.log
```

## 4. 备份与恢复

```bash
# 备份（只读 mysqldump；root 密码仅服务器内 $MYSQL_ROOT_PASSWORD）
bash deploy/scripts/backup.sh           # 产出 /var/backups/agent-enterprise/<ts>/ + sha256
bash deploy/scripts/list-backups.sh
# 隔离恢复演练（不碰生产）：还原到 agent_e4_restore_* → 对拍表/行数 → DROP
bash deploy/scripts/restore.sh <backup_dir>
```
真实证据：小库恢复 RTO≈4s（§19）；表数/行数对拍全部一致（users=5/…），生产零改动。

## 5. 升级与回滚

```bash
# 回滚（受支持回滚点 = e6-rollback-compat-61a6752）
docker compose -f ...apponly.yml -f ...host-tls.yml -f ...redis-nonroot.yml \
  up -d --scale backend=1 backend   # 显式带 ENTERPRISE_ENV_FILE + 镜像 tag（§21 教训）
# 原始 legacy 镜像（1e5caba）不支持跨 schema 回滚（fail-closed）；不得已时须人工评估,
# 且 ALLOW_UNKNOWN_MIGRATION_REVISION 禁止在生产开启。
```

## 6. 监控与告警

```bash
# 指标端点
curl -sk https://agentforge.ltd/api/enterprise/ops/metrics   # agent_dependency_{db,redis,milvus,worker}
# 监控器（主机）已经 /opt/agent-monitor.py 实时轮询 + 告警日志
tail -f /var/log/agent-monitor/alerts.log    # ALERT → RECOVERY 时间线
```
告警阈值（§6/§22/§26）：5xx>5%(P2)、依赖 down>30s(P1)、证书<30d(P2/CRITICAL)、磁盘<20%(P2)、备份失败(P1)。

## 7. 安全基线核对

backend/frontend 非 root；企业 8081 仅 `127.0.0.1`；DB/Redis/Milvus 不公网；`enterprise.env` 600 不入库；`AUTH_COOKIE_SECURE=true`；`RAG/MODEL_MOCK_FALLBACK=false`；证书私钥 600。运行 `deploy/scripts/security-scan.sh`（脱敏输出）。

## 8. 已知运维约束（边界）

- 回滚点 fixed：`e6-rollback-compat-61a6752`；原始 legacy 跨 schema 回滚不受支持（§34）。
- `ALLOW_UNKNOWN_MIGRATION_REVISION` 禁止生产开启。
- 中国大陆公网依赖 ICP 备案；备案中途 DNSPod 可能拦截 80/443（§16–18）。
- 部署命令缺 redis-nonroot override 会使 redis 回落 root。