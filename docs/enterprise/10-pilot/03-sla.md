# SLA（服务等级协议草案）

> E7-01 交付物 3/6。面向商业试点客户，对齐 E6 真实交付能力。数值若与客户合同冲突，以双方法律文本为准。
> 本草案基于单租户私有化企业栈（`agentforge.ltd`，2 副本 backend + 外部 MySQL + 非root Redis）。

## 1. 可用性与恢复目标

| 指标 | 目标 | 依据/说明 |
|---|---|---|
| 月度可用性（公网 HTTPS 入口） | ≥ 99.5% | 单区单机私有化；多副本 backend 且 frontend 轮询，节点故障自愈 |
| 恢复时间目标 RTO（数据库从备份恢复） | ≤ 30 分钟（小库实际 ~4s）| `restore.sh` 隔离演练实测（§19）|
| 恢复点目标 RPO | ≤ 24h（默认每日备份）| 可调高备份频率以缩 RPO；备份含 sha256 校验 |
| 升级失败回滚恢复 | ≤ 15 分钟 | 受支持回滚点 `e6-rollback-compat-61a6752` |
| 证书续期 | 到期前 ≥30d 触发告警；≥CRITICAL 过期处置 | `/etc/cron.d/e6-cert` 每日 + monitor `cert.days` |

## 2. 容量与性能（实测参考）

| 项 | 实测 | 说明 |
|---|---|---|
| 2 副本登录压测 | qps≈97，p50≈108ms / p95≈975ms / p99≈1059ms，0 4xx/5xx | N=120 并发 40（§27）|
| 健康检查 | 2 副本 frontend 轮询 10/10=200 | — |
| RAG 检索 | 依赖真实 Milvus 时 verified；apponly 无向量（USE_MILVUS=false）| 向量检索需启用 Milvus 栈 |
| 数据规模上限 | 单租户；建议集库 < 数十万切片的 RAG 前先压测 | 容量未做极值压测，如实声明 |

## 3. 可用性责任边界

| 责任方 | 内容 |
|---|---|
| 交付方 | 栈部署正确、备份/恢复可用、回滚点就绪、监控告警接线、证书续期提醒 |
| 客户方 | 公网/域名/ICP 备案；DNS；安全组放行 HTTPS；数据备份触发与恢复演练执行 |
| 共同 | 异常状态的及时通报与维护窗口协调（如 redis 非 root 替换需短暂中断）|

## 4. 服务分级与告警映射

| 级别 | 场景 | 目标响应 | 通知 |
|---|---|---|---|
| P1 | 依赖（DB/Redis/Milvus）down >30s、备份失败 | ≤15min | 邮件/钉钉（`MONITOR_WEBHOOK`）|
| P2 | 5xx>5%、证书<30d、磁盘<20% | ≤2h | 同上 |
| P3 | RAG 质量回归、前端可用性降级 | 下一个工作日 | 同上 |

## 5. SLA 内权益限制（不承诺）

- 多活跨区/跨地域容灾（当前单区单机）。
- 生产开启 `ALLOW_UNKNOWN_MIGRATION_REVISION`（禁止）。
- 原始 legacy 镜像的跨 schema 回滚（不受支持，见 `06-boundaries-and-limitations.md`）。
- 因 ICP 备案/DNSPod/运营商导致的公网拦截不再计入（§16–18）。
- 未做极端承载压测（容量上限为后续商业协议而非本文档承诺）。

## 6. 上报与升级路径

监控 `agent-monitor.py` ALERT→RECOVERY 事件写入 `/var/log/agent-monitor/alerts.log`；值班按 §4 响应；恢复后记录时间戳归档（真实事件留证，如 redis P1 ALERT→RECOVERY）。