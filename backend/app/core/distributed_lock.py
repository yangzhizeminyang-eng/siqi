"""团队版平台需求操作的分布式锁封装（DEV-016 生产前修复）。

为什么需要它：
- DEV-016-B/C 早期按 request_id 使用 threading.Lock 做幂等，只在单进程单 Worker 内有效；
  多 Worker / 多容器部署时，两个实例可能同时读到 DRAFT/SYNCED 并各自创建 Gitee Issue 或 Agent 任务。
- 本模块统一两条链路（sync-gitee / create-task）的锁语义：
  * 配置 REDIS_URL 时使用 Redis 分布式锁（SET key token NX PX ttl + Lua 比对 token 释放），
    跨进程/跨容器互斥，锁粒度为「操作域 + request_id」；
  * 未配置 REDIS_URL（本地开发、单进程测试）时降级为进程内 per-key 非阻塞锁，保持零外部依赖；
  * Redis 运行期瞬时不可用时，当次获取降级进程锁并记录降级次数，保证服务不瘫；Redis 恢复后自动回到分布式锁。

安全与死锁约定：
- 锁带 TTL（默认 30s，TEAM_REQUEST_LOCK_TTL_SECONDS 可调），持有者崩溃也会自动释放，不产生死锁；
- 释放只删自己 token 的锁（Lua CAS），不会误删后继持有者；
- 释放异常不抛给业务（TTL 会兜底），仅记录；
- 获取失败立即返回（非阻塞），由调用方转成明确的 409 可重试提示，不排队、不吞掉。
"""

from __future__ import annotations

import math
import os
import secrets
import threading
from contextlib import contextmanager
from typing import Iterator, Protocol


DEFAULT_TTL_SECONDS = 30.0
# Redis 操作的连接/读写超时：锁只做幂等闸门，不能长时间拖住业务请求
REDIS_SOCKET_TIMEOUT_SECONDS = 1.5
PROCESS_TOKEN_PREFIX = "process:"


class LockUnavailable(RuntimeError):
    """锁已被其他执行者持有时抛出，调用方负责转成 409 + 可重试提示。"""

    def __init__(self, key: str, scope: str) -> None:
        self.key = key
        self.scope = scope
        super().__init__(f"lock unavailable: {key}")


class LockBackend(Protocol):
    """锁后端协议：try_acquire 非阻塞，成功返回持有者 token，失败返回 None。"""

    def try_acquire(self, key: str, ttl_seconds: float) -> str | None: ...

    def renew(self, key: str, token: str, ttl_seconds: float) -> bool: ...

    def release(self, key: str, token: str) -> None: ...


class ProcessLockBackend:
    """进程内降级锁：per-key threading.Lock 非阻塞获取，仅保证单进程幂等。"""

    def __init__(self) -> None:
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()
        # 记录每个 key 当前持有者 token，释放时比对，语义与 Redis CAS 对齐
        self._owners: dict[str, str] = {}
        self.degraded_count = 0

    def _lock_for(self, key: str) -> threading.Lock:
        with self._guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._locks[key] = lock
            return lock

    def try_acquire(self, key: str, ttl_seconds: float) -> str | None:
        lock = self._lock_for(key)
        if not lock.acquire(blocking=False):
            return None
        token = secrets.token_hex(12)
        with self._guard:
            self._owners[key] = token
        return token

    def renew(self, key: str, token: str, ttl_seconds: float) -> bool:
        """进程锁没有 TTL 语义：仅校验 key 仍归该 token 持有（续约即继续持有）。"""
        if not token:
            return False
        with self._guard:
            return self._owners.get(key) == token

    def release(self, key: str, token: str) -> None:
        with self._guard:
            owner = self._owners.get(key)
            if owner != token:
                # 不是自己的锁（理论上 TTL/异常路径才会出现），不释放别人的锁
                return
            self._owners.pop(key, None)
        lock = self._locks.get(key)
        if lock is not None:
            lock.release()

    def reset(self) -> None:
        """重置全部进程锁状态（仅供测试夹具使用）。"""
        with self._guard:
            self._locks.clear()
            self._owners.clear()
            self.degraded_count = 0


# 释放锁的 Lua：只删除 value 等于本持有者 token 的 key，避免 TTL 过期后误删后继锁
_RELEASE_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
else
    return 0
end
"""

# 续约锁的 Lua：只有仍持有该 key 的 token 才能延长 TTL（CAS，防止给后继持有者续约）
_RENEW_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('pexpire', KEYS[1], ARGV[2])
else
    return 0
end
"""


class RedisLockBackend:
    """Redis 分布式锁：SET key token NX PX ttl，跨 Worker/跨容器互斥。"""

    def __init__(self, redis_url: str) -> None:
        import redis  # 延迟导入：未配置 Redis 的环境不需要建立客户端

        self._redis = redis.Redis.from_url(
            redis_url,
            socket_connect_timeout=REDIS_SOCKET_TIMEOUT_SECONDS,
            socket_timeout=REDIS_SOCKET_TIMEOUT_SECONDS,
            decode_responses=True,
        )
        self._release_script = self._redis.register_script(_RELEASE_LUA)
        self._renew_script = self._redis.register_script(_RENEW_LUA)
        self.degraded_count = 0

    def try_acquire(self, key: str, ttl_seconds: float) -> str | None:
        token = secrets.token_hex(12)
        ttl_ms = max(1, int(ttl_seconds * 1000))
        try:
            acquired = self._redis.set(key, token, nx=True, px=ttl_ms)
        except Exception:
            # Redis 瞬时不可用：降级进程锁兜底本次操作（仅保证单实例幂等），记录降级次数供观测
            self.degraded_count += 1
            process_token = _PROCESS_FALLBACK.try_acquire(key, ttl_seconds)
            # 标记 token 来源，避免 Redis 恢复后 release 误走 Redis Lua，
            # 导致进程兜底锁未释放并阻塞同实例后续请求。
            return f"{PROCESS_TOKEN_PREFIX}{process_token}" if process_token else None
        return token if acquired else None

    def renew(self, key: str, token: str, ttl_seconds: float) -> bool:
        """Lua CAS 续约：只有仍持有该 key 的 token 才延长 TTL。后端瞬时不可用返回 False。"""
        if token.startswith(PROCESS_TOKEN_PREFIX):
            return _PROCESS_FALLBACK.renew(key, token.removeprefix(PROCESS_TOKEN_PREFIX), ttl_seconds)
        ttl_ms = max(1, int(ttl_seconds * 1000))
        try:
            return bool(self._renew_script(keys=[key], args=[token, ttl_ms]))
        except Exception:
            self.degraded_count += 1
            return False

    def release(self, key: str, token: str) -> None:
        if token.startswith(PROCESS_TOKEN_PREFIX):
            _PROCESS_FALLBACK.release(key, token.removeprefix(PROCESS_TOKEN_PREFIX))
            return
        try:
            self._release_script(keys=[key], args=[token])
        except Exception:
            # 释放失败不影响业务响应：TTL 到期会自动清理；尝试进程兜底锁的清理
            self.degraded_count += 1
            _PROCESS_FALLBACK.release(key, token)


# 进程兜底后端是模块级单例：Redis 降级路径与未配置 Redis 的默认路径共用一套 per-key 锁
_PROCESS_FALLBACK = ProcessLockBackend()
_BACKEND: LockBackend | None = None


def _lock_ttl_seconds() -> float:
    try:
        return max(1.0, float(os.getenv("TEAM_REQUEST_LOCK_TTL_SECONDS", str(DEFAULT_TTL_SECONDS))))
    except ValueError:
        return DEFAULT_TTL_SECONDS


def get_lock_backend() -> LockBackend:
    """锁后端单例工厂：配置 REDIS_URL 走 Redis 分布式锁，否则进程内降级锁。"""
    global _BACKEND
    if _BACKEND is not None:
        return _BACKEND
    redis_url = os.getenv("REDIS_URL", "").strip()
    if redis_url:
        _BACKEND = RedisLockBackend(redis_url)
    else:
        _BACKEND = _PROCESS_FALLBACK
    return _BACKEND


def set_lock_backend(backend: LockBackend | None) -> None:
    """测试/运维钩子：注入自定义后端，传 None 恢复按环境变量自动选择。"""
    global _BACKEND
    _BACKEND = backend


def team_request_lock_key(scope: str, request_id: str) -> str:
    return f"team_request:{scope}:{request_id}"


class AcquiredLock:
    """已获取锁的句柄，业务侧在 finally 中调用 release()，释放异常不外抛。"""

    def __init__(self, backend: LockBackend, key: str, token: str) -> None:
        self._backend = backend
        self.key = key
        self.token = token
        self._released = False

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        try:
            self._backend.release(self.key, self.token)
        except Exception:
            backend = self._backend
            if isinstance(backend, RedisLockBackend):
                backend.degraded_count += 1


def try_acquire_team_request_lock(
    scope: str, request_id: str, *, ttl_seconds: float | None = None
) -> AcquiredLock | None:
    """非阻塞获取团队需求操作锁；竞争失败返回 None（调用方转 409），成功返回释放句柄。"""
    backend = get_lock_backend()
    key = team_request_lock_key(scope, request_id)
    token = backend.try_acquire(key, ttl_seconds or _lock_ttl_seconds())
    if token is None:
        return None
    return AcquiredLock(backend, key, token)


@contextmanager
def team_request_lock(scope: str, request_id: str, *, ttl_seconds: float | None = None) -> Iterator[str]:
    """按 (scope, request_id) 加锁的上下文管理器，非阻塞；竞争失败抛 LockUnavailable。

    scope 取值：``sync``（同步 Gitee）/ ``create_task``（创建 Agent 任务），两条链路使用不同 key 互不阻塞。
    """
    acquired = try_acquire_team_request_lock(scope, request_id, ttl_seconds=ttl_seconds)
    if acquired is None:
        key = team_request_lock_key(scope, request_id)
        raise LockUnavailable(key, scope)
    try:
        yield acquired.token
    finally:
        acquired.release()


# ---- Agent 任务派发去重的分布式锁 ----
# 进程内存 _AGENT_ENQUEUED 只在本进程内有效，多 Worker/HTTP 进程会重复投递同一 run_agent 导致并发卡死。
# 这里用跨进程锁承载「某任务是否已有一个 run_agent 在途」，worker 完成后释放 key，任务即可续投下一步。
AGENT_DISPATCH_TTL_SECONDS = float(os.getenv("AGENT_DISPATCH_LOCK_TTL_SECONDS", "900") or "900")

# 企业 v2 派发锁：短 TTL + 心跳续约（OPT-13）。v1 的 900s 常量不含续约机制，保持不动。
ENTERPRISE_DISPATCH_DEFAULT_TTL_SECONDS = 60.0


def enterprise_dispatch_ttl_seconds() -> float:
    """企业路径派发锁 TTL：默认 60s，下限 5s（小于该值时心跳续约窗口太窄）。"""
    raw = os.getenv("ENTERPRISE_AGENT_DISPATCH_TTL_SECONDS", str(ENTERPRISE_DISPATCH_DEFAULT_TTL_SECONDS))
    try:
        return max(5.0, float(raw))
    except (TypeError, ValueError):
        return ENTERPRISE_DISPATCH_DEFAULT_TTL_SECONDS


def enterprise_dispatch_retry_countdown() -> int:
    """锁竞争重试间隔：约 TTL/3，保证一个 TTL 窗口内至少检查 3 次。"""
    return max(2, int(enterprise_dispatch_ttl_seconds() / 3))


def enterprise_dispatch_retry_budget() -> int:
    """锁竞争有界重试次数：覆盖 ≥1 个 TTL 窗口（等尸锁过期），另加 2 次接棒余量。"""
    ttl = enterprise_dispatch_ttl_seconds()
    countdown = enterprise_dispatch_retry_countdown()
    return max(2, int(math.ceil(ttl / countdown)) + 2)


def agent_dispatch_key(task_id: str) -> str:
    return f"agent_run:{task_id}"


def try_acquire_agent_dispatch(task_id: str, *, ttl_seconds: float | None = None) -> str | None:
    """非阻塞抢占「该任务唯一 run_agent 投递权」；成功返回 token，已被别处投递返回 None。

    未配置 Redis 时降级为进程锁（仅单进程幂等），此时配合进程内存 _AGENT_ENQUEUED 使用。
    """
    backend = get_lock_backend()
    key = agent_dispatch_key(task_id)
    return backend.try_acquire(key, ttl_seconds or AGENT_DISPATCH_TTL_SECONDS)


def renew_agent_dispatch(task_id: str, token: str, *, ttl_seconds: float | None = None) -> bool:
    """续约「该任务唯一 run_agent 投递权」；token 非当前持有者返回 False。"""
    backend = get_lock_backend()
    return backend.renew(agent_dispatch_key(task_id), token,
                         ttl_seconds or enterprise_dispatch_ttl_seconds())


class LockHeartbeat:
    """持锁期间的后台续约线程：每 interval 续约一次，续约失败（锁易主/后端不可用）即停止。

    - daemon 线程，不阻塞进程退出；
    - renewals 记录成功次数、lost 标记是否已丢失锁，供调用方/测试观测；
    - stop() 幂等，join 有超时，绝不外抛异常。
    """

    def __init__(self, backend: LockBackend, key: str, token: str, *,
                 ttl_seconds: float, interval_seconds: float | None = None) -> None:
        self._backend = backend
        self.key = key
        self.token = token
        self.ttl_seconds = ttl_seconds
        self.interval_seconds = interval_seconds if interval_seconds is not None else max(1.0, ttl_seconds / 3.0)
        self.renewals = 0
        self.lost = False
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name=f"lock-heartbeat:{self.key}", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop_event.wait(self.interval_seconds):
            try:
                ok = self._backend.renew(self.key, self.token, self.ttl_seconds)
            except Exception:  # noqa: BLE001 续约异常按丢失处理，不打断业务线程
                ok = False
            if not ok:
                self.lost = True
                return
            self.renewals += 1

    def stop(self, timeout: float = 2.0) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout)


def start_agent_dispatch_heartbeat(task_id: str, token: str, *,
                                   ttl_seconds: float | None = None) -> LockHeartbeat:
    """启动企业派发锁心跳（默认用企业 TTL）；调用方在 finally 中 stop()。"""
    ttl = ttl_seconds or enterprise_dispatch_ttl_seconds()
    heartbeat = LockHeartbeat(get_lock_backend(), agent_dispatch_key(task_id), token, ttl_seconds=ttl)
    heartbeat.start()
    return heartbeat


def release_agent_dispatch(task_id: str, token: str | None = None) -> None:
    """释放（删除）某任务的派发锁，允许任务续投下一步。token 提供时走 CAS，缺省直接删 key。"""
    backend = get_lock_backend()
    key = agent_dispatch_key(task_id)
    if token:
        backend.release(key, token)
    elif isinstance(backend, RedisLockBackend):
        try:
            backend._redis.delete(key)
        except Exception:  # noqa: BLE001 - 删除失败不阻塞，TTL 兜底
            backend.degraded_count += 1
    # ProcessLockBackend：单进程本就由 _AGENT_ENQUEUED 承，无需额外清理
