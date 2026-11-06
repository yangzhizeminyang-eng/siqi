"""E6-01 P4：轻量 Prometheus 文本指标（进程内，不含敏感字段）。

只暴露非敏感聚合：HTTP 请求量/按状态码/5xx·4xx 异常率、延迟和(sum+count)、DB 连接池、milvus/redis 可用性。
供监控采集；不输出 Token/Authorization/请求体。
"""

from __future__ import annotations

import threading
import time

_LOCK = threading.Lock()
_COUNTS = {
    "http_requests_total": 0,
    "http_5xx_total": 0,
    "http_4xx_total": 0,
    "http_latency_sum_ms": 0.0,
    "http_latency_count": 0,
    "by_status": {},
}


def record(status_code: int, latency_ms: float) -> None:
    with _LOCK:
        _COUNTS["http_requests_total"] += 1
        _COUNTS["http_latency_sum_ms"] += latency_ms
        _COUNTS["http_latency_count"] += 1
        _COUNTS["by_status"][int(status_code)] = _COUNTS["by_status"].get(int(status_code), 0) + 1
        if status_code and status_code >= 500:
            _COUNTS["http_5xx_total"] += 1
        elif status_code and status_code >= 400:
            _COUNTS["http_4xx_total"] += 1


def _availability() -> dict:
    """采集 db 连接池 / milvus / redis / worker / 证书剩余天数（非敏感）。
    直接调用与 /api/health 相同的依赖检查（不再依赖不存在的 main.api_health）。"""
    out: dict[str, str | int] = {}
    from app.core.runtime_config import load_runtime_settings

    def st(s: str) -> str:
        return "1" if s in ("ok", "degraded", "skipped") else "0"

    settings = load_runtime_settings()
    try:
        from app.db.session import check_database_health
        out["db"] = st(check_database_health().get("status", "down"))
    except Exception:  # noqa: BLE001
        out["db"] = "0"
    # redis：恒输出（对齐"三条恒存在"契约）；未配置 celery 时恒定 1=skipped/非故障，配置时真查
    if not (getattr(settings, "use_celery", False) or getattr(settings, "celery_required", lambda: False)()):
        out["redis"] = "1"
    else:
        try:
            from app import main
            out["redis"] = st(main._check_redis_health().get("status", "down"))
        except Exception:  # noqa: BLE001
            out["redis"] = "0"
    # milvus：恒输出（对齐 /api/health 契约）；USE_MILVUS=false（apponly 无真实 Milvus）时恒定 1=skipped/非故障，
    # 避免对不存在的 Milvus 偶发连接报错造成假阳；USE_MILVUS=true 时才做真实健康检查。
    if not getattr(settings, "use_milvus", False):
        out["milvus"] = "1"
    else:
        try:
            from app.retrieval.llamaindex_backend import check_milvus_health
            out["milvus"] = st(check_milvus_health().get("status", "down"))
        except Exception:  # noqa: BLE001
            out["milvus"] = "0"
    # worker：celery ping（不泄露 broker 凭据）。导入名必须是 celery_app（模块导出 celery_app，非 app），
    # 否则 ImportError 落入 except 恒置 0，无法反映真实 worker 状态。
    try:
        from app.workers.celery_app import celery_app as ca
        r = ca.control.ping(timeout=2)
        out["worker"] = 1 if r else 0
    except Exception:  # noqa: BLE001
        out["worker"] = 0
    # 证书剩余天数：仅路径存在时读取到期时间（不读私钥）
    try:
        import subprocess, os
        cert_path = os.environ.get("E6_CERT") or "/etc/nginx/ssl/agentforge.ltd_bundle.crt"
        if os.path.exists(cert_path):
            end = subprocess.run(["openssl", "x509", "-enddate", "-noout", "-in", cert_path],
                                 capture_output=True, text=True, timeout=5).stdout
            if "notAfter=" in end:
                import datetime as _dt
                d = _dt.datetime.strptime(end.split("notAfter=")[1].strip(), "%b %d %H:%M:%S %Y %Z")
                out["cert_days_left"] = max(0, (d.replace(tzinfo=_dt.timezone.utc) - _dt.datetime.now(_dt.timezone.utc)).days)
    except Exception:  # noqa: BLE001
        pass
    return out


def metrics_text() -> str:
    with _LOCK:
        c = dict(_COUNTS)
    av = _availability()
    total = max(1, c["http_requests_total"])
    lat_avg = (c["http_latency_sum_ms"] / c["http_latency_count"]) if c["http_latency_count"] else 0.0
    lines = [
        "# HELP agent_http_requests_total 累计 HTTP 请求数",
        "# TYPE agent_http_requests_total counter",
        f"agent_http_requests_total {c['http_requests_total']}",
        "# TYPE agent_http_5xx_total counter",
        f"agent_http_5xx_total {c['http_5xx_total']}",
        "# TYPE agent_http_4xx_total counter",
        f"agent_http_4xx_total {c['http_4xx_total']}",
        "# TYPE agent_http_5xx_rate gauge  (5xx 占比)",
        f"agent_http_5xx_rate {c['http_5xx_total'] / total:.6f}",
        "# TYPE agent_http_latency_avg_ms gauge",
        f"agent_http_latency_avg_ms {lat_avg:.3f}",
        "# TYPE agent_http_status_total counter",
    ]
    for code, n in sorted(c["by_status"].items()):
        lines.append(f'agent_http_status_total{{code="{code}"}} {n}')
    for name, val in av.items():
        if isinstance(val, bool):
            lines.append(f"agent_dependency_{name} {1 if val else 0}")
        elif isinstance(val, int):
            lines.append(f"agent_dependency_{name} {val}")
        else:
            # _availability 返回 "0"/"1"
            lines.append(f"agent_dependency_{name} {val}")
    return "\n".join(lines) + "\n"


async def metrics_middleware(request, call_next):
    """ASGI 中间件：记录每个已响应请求的状态码与耗时，并回显后端实例标识（多副本负载均衡验证）。"""
    start = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        record(500, (time.perf_counter() - start) * 1000.0)
        raise
    record(getattr(response, "status_code", 0), (time.perf_counter() - start) * 1000.0)
    try:
        import socket
        response.headers.setdefault("X-Backend-Host", socket.gethostname())
    except Exception:  # noqa: BLE001
        pass
    return response