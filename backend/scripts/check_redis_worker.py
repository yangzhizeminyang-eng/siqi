"""Redis + Celery Worker 真实验收检查。

用法（working dir = backend）：
    # 若 Redis 未启动，按提示用 Docker Desktop 或已有/远程 Redis：
    #   docker run -d --name agent-redis -p 6379:6379 redis:7-alpine
    $env:CELERY_BROKER_URL="redis://127.0.0.1:6379/0"
    $env:CELERY_RESULT_BACKEND="redis://127.0.0.1:6379/1"
    $env:USE_CELERY="true"
    ...deploy/dev-worker.ps1    # 终端1: 启动 worker(日志见 logs/celery-worker.*.log)
    ...scripts.check_redis_worker    # 终端2: 验收

行为：
- 检查 broker 是否配置、Redis 是否可达；
- 检查是否有在线 Worker（celery inspect ping）；
- 投递 backend.smoke 并等待 AsyncResult；
- 输出 Worker 消费日志位置（logs/celery-worker.out.log）。
- 不静默回退 eager：Redis 不可达或 worker 未消费即失败退出（exit!=0）。
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import redis as _redis_lib

from app.core.runtime_config import load_runtime_settings
from app.workers.celery_app import celery_app


def redis_ping(broker_url: str) -> bool:
    try:
        client = _redis_lib.Redis.from_url(broker_url, socket_connect_timeout=2, socket_timeout=2)
        ok = bool(client.ping())
        client.close()
        return ok
    except Exception:
        return False


def main() -> int:
    from scripts.authorization_gate import require_authorization

    require_authorization(
        os.environ.get("AUTHORIZATION_OPERATION", ""),
        os.environ.get("AUTHORIZATION_LOG", "") or None,
    )
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass
    s = load_runtime_settings()
    broker = s.celery_broker_url
    result = s.celery_result_backend
    log_file = Path(__file__).resolve().parents[2] / "logs" / "celery-worker.out.log"

    print("=== Redis + Celery Worker 验收 ===")
    print(f"[cfg] USE_CELERY={s.use_celery} APP_ENV={s.app_env} broker={'已配置' if broker else '缺失'} result={'已配置' if result else '未配'}")
    print(f"[cfg] worker 日志：{log_file}")

    if not s.use_celery or not broker:
        print("[FAIL] USE_CELERY=true 且 CELERY_BROKER_URL 必须可用的真实 broker；否则不构成异步验收（eager 只是本地降级）。")
        print("       启动 Redis：docker run -d --name agent-redis -p 6379:6379 redis:7-alpine  （或用已有/远程 Redis）")
        return 2

    if not redis_ping(broker):
        print("[FAIL] Redis 不可达。请先启动 Redis：")
        print("        方案A Docker Desktop：docker run -d --name agent-redis -p 6379:6379 redis:7-alpine")
        print("        方案B 已有 Redis 服务：确认其监听 6379")
        print("        方案C 远程 Redis：把 CELERY_BROKER_URL 指向远程")
        print("        （本脚本不会静默回退 eager 冒充 worker 已消费）")
        return 2

    print("[OK] Redis 可达")

    try:
        workers = celery_app.control.inspect(timeout=3).ping() or {}
    except Exception as exc:
        workers = {}
        print(f"[warn] inspect.ping 异常：{exc}")
    online = [k for k, v in (workers or {}).items() if v]
    if online:
        print(f"[OK] 在线 Worker：{online}")
    else:
        print("[WARN] 暂未发现在线 Worker。请在另一个终端执行 .\\deploy\\dev-worker.ps1 启动，再重跑本脚本。")

    from app.workers.tasks import smoke

    r = smoke.delay(message=f"probe-{int(time.time())}")
    print(f"[dispatch] backend.smoke 已投递 async_result={r.id}，等待 worker 消费...")
    deadline = time.time() + 15
    state = "PENDING"
    result_payload = None
    while time.time() < deadline:
        state = r.state
        if state in {"SUCCESS", "FAILURE", "REVOKED"}:
            try:
                result_payload = r.result
            except Exception:
                result_payload = None
            break
        time.sleep(0.5)
    print(f"[result] backend.smoke state={state} payload={result_payload}")
    if state == "SUCCESS":
        print("[PASS] Worker 真实消费了 backend.smoke。验收日志见：")
        print(f"       {log_file}  （应含 'Worker 消费 backend.smoke'）")
        return 0
    print("[FAIL] smoke 未在 15s 内被 Worker 消费。请确认 Worker 已连接 broker（无 worker 则不会消费，不构成异步验收）。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
