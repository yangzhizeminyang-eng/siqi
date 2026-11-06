# 交付清单（Checklist）

> E7-01 交付物 4/6。私有化商业试点交付核对表；勾选即验收。基于 E6 真实证据（`112` 文档 §1–34）。

## A. 环境与网络

- [ ] 公网域名 `agentforge.ltd` A 记录 → `<YOUR_SERVER_IP>`；中国大陆 ICP 备案完成（未经由 DNSPod 拦截的路径将被记录）。
- [ ] `http://` → `308` → `https://`；HTTPS `200`；TLSv1.3 `Verify ok`（CN=agentforge.ltd）。
- [ ] 安全组仅公网放行 80/443（+团队 8080 按需）；DB/Redis/Milvus **不**公网。

## B. 栈与配置

- [ ] 生产栈 = apponly + host-tls + **redis-nonroot**；backend ×2 healthy；frontend（`127.0.0.1:8081`）。
- [ ] `deploy/env/enterprise.env`：600、不入库；必填占位拒绝生效（DATABASE_URL=mysql+pymysql）。
- [ ] `AUTH_COOKIE_SECURE=true`；`RAG/MODEL_MOCK_FALLBACK=false`；`USE_MILVUS` 按是否需要向量显式声明。
- [ ] Redis 非 root（`--user 999`）；backend/frontend 非 root。

## C. 数据与迁移

- [ ] MySQL `agent_enterprise`：29 表；head=`e5f6071a2b3c`。
- [ ] 关键计数基线：users=5 / tenants=1 / tenant_memberships=5 / audit_events=2 / repositories=1。
- [ ] 迁移为前向-only；`ALEMBIC` 多行/多 head/unknown 均 fail-closed（人工核对只读启动需开关，生产禁开）。

## D. 备份 / 恢复 / 回滚

- [ ] `backup.sh` 产出含 sha256 的 dump（示例 `agent_enterprise_20260924…sql`，50114B / sha256 `0e37b1c7…`）。
- [ ] 隔离恢复演练通过（还原 `agent_e4_restore_*` → 表/行数对拍一致 → DROP）。
- [ ] **受支持回滚点镜像** `agent-enterprise-backend:e6-rollback-compat-61a6752`（id `62f50ad3aa2e`）存在可用。
- [ ] 回滚命令显式带 `ENTERPRISE_ENV_FILE` + 镜像 tag（`--scale backend=1`）。

## E. 监控与认证

- [ ] `/api/enterprise/ops/metrics` `200`（`agent_dependency_{db,redis,milvus,worker}`）。
- [ ] `/api/enterprise/ops/status` auth `200`（`org:manage`）；auditor `403`。
- [ ] `agent-monitor.py` 运行中；告警日志 ALERT→RECOVERY 时间线存在。
- [ ] 证书续期：`/etc/cron.d/e6-cert`（每日 03:17）；证书剩余天数（WARN<30d / CRITICAL 过期）。

## F. 安全基线

- [ ] `security-scan.sh` 脱敏运行：env 不入 Git、Git 0 密钥/私钥、无公网 DB/Redis/Milvus、私钥 600、备份 0 密钥。
- [ ] 企业公网 8081 已收敛（仅 `127.0.0.1`）。

## G. 试点素材

- [ ] 演示数据/走查脚本（`01-demo-data-and-walkthrough.md`）就绪。
- [ ] 客户手册（`02-…`）、SLA（`03-…`）、边界（`06-…`）就绪。
- [ ] 试点验收脚本（`05-pilot-acceptance-script.md`）可复现；脱敏材料齐。

> 勾选全部后，客户可在验收签收单签字；未勾选项如实标注为未覆盖/受限（不得默认承诺）。