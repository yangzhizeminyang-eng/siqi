#!/bin/sh
# 企业版 worker 健康检查：反映「Celery worker 应答 + broker 可用」，而非仅进程存在。
#
# 原则：
#   - 使用 Celery 控制面 `inspect ping --destination=<node>`：worker 会通过 broker 应答 pong，
#     证明该 worker 节点已注册且 broker 双向可用；不投递业务任务、不产生副作用。
#   - 预热期（容器 uptime < WORKER_HEALTH_START_GRACE 秒）放行，配合 compose `start_period`
#     双保险，避免冷启动/依赖就绪瞬间被误判 unhealthy。
#   - Redis 短暂抖动用脚本内重试 + compose interval/retries 兜底。
#   - 纯只读探测：不删任务、不重置审批/索引、不跑迁移（RUN_ALEMBIC_UPGRADE=false 由编排约束）。
set -eu

GRACE="${WORKER_HEALTH_START_GRACE:-60}"
# 若运维自定义了 celery -n 节点名，可用此变量覆盖默认 celery@<hostname>
NODE="${WORKER_HEALTH_NODE:-celery@$(hostname)}"

UPTIME_S="$(awk '{print int($1)}' /proc/uptime 2>/dev/null || echo 0)"
if [ "$UPTIME_S" -lt "$GRACE" ]; then
  # 预热窗口内不判死
  exit 0
fi

attempt=0
max=3
while [ "$attempt" -lt "$max" ]; do
  if celery -A app.workers.celery_app inspect ping --timeout=6 --destination="$NODE" 2>/dev/null | grep -q "pong"; then
    exit 0
  fi
  attempt=$((attempt + 1))
  [ "$attempt" -lt "$max" ] && sleep 2
done

exit 1