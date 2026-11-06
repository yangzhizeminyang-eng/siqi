"""企业版 OPT-07 全链路可观测性：跨模型/工具/RAG/审批/Worker/产物的统一 span 记录。

设计要点（企业边界内，内存态 + 租户隔离 + 保留上限）：
- span 只记「发生了什么」：类别/名称/状态/耗时/可解释属性；Token 未采集时如实标 not_collected，
  绝不伪造数值；属性统一走 scrub_attributes（凭据值脱敏 + 敏感 key 掩码 + 超长截断）。
- 租户隔离：query 只返回本租户 span；空 tenant 的 span 不进入任何租户查询结果。
- 保留边界：OBSERVABILITY_MAX_SPANS（默认 2000）超限淘汰最旧并累计 dropped_total，可查询。
- 上下文：contextvars 绑定 trace（task_id 维度），Agent 执行/Tool 调用的 span 自动继承，
  串起 Worker→模型→工具→RAG→产物 的同一条 trace。
"""

from __future__ import annotations

import contextvars
import logging
import os
import re
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

from pydantic import BaseModel, Field

logger = logging.getLogger("backend.enterprise_runtime.observability")

CATEGORIES = ("model", "tool", "rag", "approval", "worker", "artifact")

MAX_QUERY_LIMIT = 500
MAX_ATTR_VALUE_LEN = 300
DEFAULT_MAX_SPANS = 2000

_SENSITIVE_KEY_RE = re.compile(
    r"(?i)(token|secret|password|passwd|api_key|apikey|authorization|cookie|credential"
    r"|access_key|private_key)"
)
# tokens 中这些键是合法用量统计，必须保留数值/类型；其余命中敏感 key 正则的仍掩码。
_USAGE_TOKEN_KEYS = frozenset({
    "input_tokens", "output_tokens", "total_tokens",
    "prompt_tokens", "completion_tokens", "cached_tokens",
    "reasoning_tokens", "cache_creation_input_tokens", "cache_read_input_tokens",
})

_TRACE: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "e4_observability_trace", default=None
)
_SPANS: list["TraceSpan"] = []
_LOCK = threading.RLock()
_DROPPED_TOTAL = 0


class TraceSpan(BaseModel):
    span_id: str
    trace_id: str = ""
    tenant_id: str = ""
    category: str
    name: str
    status: str = "ok"  # ok | error | blocked
    error_kind: str = ""
    duration_ms: float | None = None
    attributes: dict = Field(default_factory=dict)
    tokens: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


def _max_spans() -> int:
    raw = (os.environ.get("OBSERVABILITY_MAX_SPANS") or "").strip()
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            pass
    return DEFAULT_MAX_SPANS


def _persistent() -> bool:
    try:
        from app.db.repositories import shared_persistence_enabled

        return shared_persistence_enabled()
    except Exception:  # noqa: BLE001
        return False


def reset_store() -> None:
    global _DROPPED_TOTAL
    with _LOCK:
        _SPANS.clear()
        _DROPPED_TOTAL = 0


# ---------------------------------------------------------------------------
# 脱敏
# ---------------------------------------------------------------------------


def scrub_attributes(attrs: dict | None) -> dict:
    """属性落库前统一脱敏：敏感 key 掩码、环境凭据值/URL 凭据替换、超长截断。"""
    return _scrub_obj(attrs or {}, depth=0)


def scrub_tokens(tokens: dict | None) -> dict:
    """token 用量落库/返回前统一脱敏；合法用量字段保留原数值与类型。

    与 attributes 的差异：`input_tokens`/`output_tokens`/`total_tokens` 等用量键
    即使包含 `token` 字样也不掩码；其余敏感 key（token/secret/password/api_key/
    authorization 等）仍掩码。字符串递归走 redact_secrets，嵌套 dict/list 同样处理。
    """
    return _scrub_obj(tokens or {}, depth=0, token_usage=True)


def _scrub_obj(value, depth: int, *, token_usage: bool = False):
    if depth > 4:
        return "<truncated-depth>"
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            name = str(key)
            if token_usage and name.strip().lower() in _USAGE_TOKEN_KEYS:
                out[name] = _scrub_obj(item, depth + 1, token_usage=True)
            elif _SENSITIVE_KEY_RE.search(name):
                out[name] = "***"
            else:
                out[name] = _scrub_obj(item, depth + 1, token_usage=token_usage)
        return out
    if isinstance(value, (list, tuple)):
        return [_scrub_obj(item, depth + 1, token_usage=token_usage) for item in value[:50]]
    if isinstance(value, str):
        from app.enterprise_runtime.agents.tools import redact_secrets

        return redact_secrets(value)[:MAX_ATTR_VALUE_LEN]
    if value is None or isinstance(value, (int, float, bool)):
        return value
    return str(value)[:MAX_ATTR_VALUE_LEN]


# ---------------------------------------------------------------------------
# 上下文绑定
# ---------------------------------------------------------------------------


def bind_trace(*, trace_id: str, tenant_id: str, run_id: str = "", agent_type: str = ""):
    return _TRACE.set({
        "trace_id": str(trace_id or ""),
        "tenant_id": str(tenant_id or ""),
        "run_id": str(run_id or ""),
        "agent_type": str(agent_type or ""),
    })


def clear_trace(token=None) -> None:
    if token is not None:
        _TRACE.reset(token)
    else:
        _TRACE.set(None)


def current_trace() -> dict | None:
    return _TRACE.get()


# ---------------------------------------------------------------------------
# 记录与查询
# ---------------------------------------------------------------------------


def _error_kind(exc: BaseException) -> str:
    from fastapi import HTTPException

    if isinstance(exc, HTTPException):
        return f"http_{exc.status_code}"
    return type(exc).__name__


def _append_memory(span: TraceSpan) -> None:
    global _DROPPED_TOTAL
    with _LOCK:
        _SPANS.append(span)
        limit = _max_spans()
        while len(_SPANS) > limit:
            _SPANS.pop(0)
            _DROPPED_TOTAL += 1


def _append(span: TraceSpan) -> TraceSpan:
    global _DROPPED_TOTAL
    span.attributes = scrub_attributes(dict(span.attributes or {}))
    span.tokens = scrub_tokens(dict(span.tokens or {}))
    if not _persistent():
        _append_memory(span)
        return span
    try:
        from app.db import shared_stores

        dropped = shared_stores.insert_trace_span(span, max_spans=_max_spans())
        with _LOCK:
            _DROPPED_TOTAL += int(dropped or 0)
    except Exception:  # noqa: BLE001 观测失败绝不影响业务；本进程内存兜底可查
        logger.warning("[observability] span 持久化失败 span=%s（内存兜底）", span.span_id, exc_info=True)
        _append_memory(span)
    return span


def record_span(category: str, name: str, *, trace_id: str = "", tenant_id: str = "",
                status: str = "ok", error_kind: str = "", duration_ms: float | None = None,
                attributes: dict | None = None, tokens: dict | None = None) -> TraceSpan:
    span = TraceSpan(
        span_id=f"span_{uuid.uuid4().hex[:12]}",
        trace_id=str(trace_id or ""), tenant_id=str(tenant_id or ""),
        category=category, name=name, status=status, error_kind=error_kind,
        duration_ms=duration_ms, attributes=dict(attributes or {}), tokens=dict(tokens or {}),
    )
    return _append(span)


@contextmanager
def span(category: str, name: str, *, trace_id: str | None = None, tenant_id: str | None = None,
         attributes: dict | None = None, tokens: dict | None = None):
    """计时 span：正常退出记 ok，异常记 error + error_kind 后原样抛出（不改变业务语义）。

    trace_id/tenant_id 未显式给出时继承当前绑定的 trace（runtime_tool_context/显式 bind）。
    """
    ambient = current_trace() or {}
    span_obj = TraceSpan(
        span_id=f"span_{uuid.uuid4().hex[:12]}",
        trace_id=str(trace_id if trace_id is not None else ambient.get("trace_id") or ""),
        tenant_id=str(tenant_id if tenant_id is not None else ambient.get("tenant_id") or ""),
        category=category, name=name, attributes=dict(attributes or {}),
        tokens=dict(tokens or {}),
    )
    started = time.perf_counter()
    try:
        yield span_obj
    except Exception as exc:  # noqa: BLE001 记录后原样抛出
        span_obj.status = "error"
        span_obj.error_kind = _error_kind(exc)
        span_obj.duration_ms = round((time.perf_counter() - started) * 1000, 2)
        _append(span_obj)
        raise
    span_obj.duration_ms = round((time.perf_counter() - started) * 1000, 2)
    _append(span_obj)


def query_spans(tenant_id: str, *, trace_id: str = "", category: str = "", status: str = "",
                limit: int = 100, offset: int = 0) -> list[TraceSpan]:
    """租户作用域查询（最新在前）；category 未知 → 空结果；limit 收敛到 [1, 500]。"""
    try:
        safe_limit = max(1, min(int(limit), MAX_QUERY_LIMIT))
    except (TypeError, ValueError):
        safe_limit = 100
    try:
        safe_offset = max(0, int(offset))
    except (TypeError, ValueError):
        safe_offset = 0
    if _persistent():
        try:
            from app.db import shared_stores

            return [TraceSpan.model_validate(item) for item in shared_stores.load_trace_spans(
                tenant_id, trace_id=trace_id, category=category, status=status,
                limit=safe_limit, offset=safe_offset)]
        except Exception:  # noqa: BLE001 DB 查询失败降级到本进程缓存（可观测性不阻断业务）
            logger.warning("[observability] span 查询失败 tenant=%s（内存兜底）", tenant_id, exc_info=True)
    with _LOCK:
        rows = [
            s for s in reversed(_SPANS)
            if s.tenant_id == tenant_id
            and (not trace_id or s.trace_id == trace_id)
            and (not category or s.category == category)
            and (not status or s.status == status)
        ]
    return rows[safe_offset:safe_offset + safe_limit]


def retention_info() -> dict:
    if _persistent():
        try:
            from app.db import shared_stores

            info = shared_stores.trace_retention_info()
            return {
                "max_spans": _max_spans(),
                "count": int(info.get("count", 0)),
                "dropped_total": int(info.get("dropped_total", 0)),
                "by_category": dict(info.get("by_category", {})),
            }
        except Exception:  # noqa: BLE001
            logger.warning("[observability] retention 查询失败（内存兜底）", exc_info=True)
    with _LOCK:
        by_category: dict[str, int] = {}
        for item in _SPANS:
            by_category[item.category] = by_category.get(item.category, 0) + 1
        return {
            "max_spans": _max_spans(),
            "count": len(_SPANS),
            "dropped_total": _DROPPED_TOTAL,
            "by_category": by_category,
        }
