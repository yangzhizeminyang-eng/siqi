"""分布式锁封装单测（DEV-016 生产前修复）。

用 FakeRedis 精确模拟 redis-py 的 SET NX PX 与 Lua CAS 释放语义，不依赖真实 Redis 进程；
端点级并发互斥由 test_dev016b/test_dev016c 的线程并发测试覆盖。
"""

import threading
import time

import pytest
import redis as redis_package

from app.core import distributed_lock as dl


class FakeRedis:
    """模拟 redis-py 客户端：SET NX PX、GET/DEL、register_script，支持手动过期与故障注入。"""

    def __init__(self) -> None:
        self.store: dict[str, tuple[str, float | None]] = {}
        self.ttls_ms: dict[str, int] = {}
        self.raise_on_set = False
        self.raise_on_script = False
        self._guard = threading.Lock()

    def _purge(self, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        expired = [key for key, (_, expire_at) in self.store.items() if expire_at is not None and expire_at <= now]
        for key in expired:
            self.store.pop(key, None)
            self.ttls_ms.pop(key, None)

    def set(self, key, value, nx=False, px=None, **kwargs):
        if self.raise_on_set:
            raise redis_package.exceptions.ConnectionError("simulated redis down")
        with self._guard:
            now = time.monotonic()
            self._purge(now)
            if nx and key in self.store:
                return None
            expire_at = now + px / 1000 if px else None
            self.store[key] = (value, expire_at)
            if px is not None:
                self.ttls_ms[key] = px
            return True

    def get(self, key):
        with self._guard:
            self._purge()
            return self.store.get(key, (None,))[0]

    def delete(self, key):
        with self._guard:
            return 1 if self.store.pop(key, None) is not None else 0

    def register_script(self, _lua: str):
        def script(keys, args):
            if self.raise_on_script:
                raise redis_package.exceptions.TimeoutError("simulated release timeout")
            key, token = keys[0], args[0]
            if self.get(key) == token:
                return self.delete(key)
            return 0

        return script

    def force_expire(self, key: str) -> None:
        with self._guard:
            self.store.pop(key, None)


@pytest.fixture()
def redis_backend(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setattr(redis_package.Redis, "from_url", classmethod(lambda cls, *a, **k: fake))
    dl.set_lock_backend(None)
    backend = dl.get_lock_backend()
    yield backend, fake
    dl.set_lock_backend(None)


def test_default_backend_is_process_lock_when_no_redis_url(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    dl.set_lock_backend(None)
    backend = dl.get_lock_backend()
    assert isinstance(backend, dl.ProcessLockBackend)


def test_redis_backend_selected_when_redis_url_configured(redis_backend):
    backend, _ = redis_backend
    assert isinstance(backend, dl.RedisLockBackend)


def test_same_key_mutual_exclusion(redis_backend):
    backend, fake = redis_backend
    first = backend.try_acquire("team_request:sync:req_1", 30)
    second = backend.try_acquire("team_request:sync:req_1", 30)
    assert first is not None
    assert second is None  # 同 key 未释放前，第二个执行者拿不到锁


def test_different_scope_and_request_do_not_block_each_other(redis_backend):
    backend, _ = redis_backend
    sync_lock = backend.try_acquire("team_request:sync:req_1", 30)
    task_lock = backend.try_acquire("team_request:create_task:req_1", 30)
    other_request = backend.try_acquire("team_request:sync:req_2", 30)
    assert all(token is not None for token in (sync_lock, task_lock, other_request))


def test_ttl_is_passed_as_milliseconds(redis_backend):
    backend, fake = redis_backend
    backend.try_acquire("team_request:sync:req_1", 2.5)
    assert fake.ttls_ms["team_request:sync:req_1"] == 2500


def test_release_allows_reacquire(redis_backend):
    backend, _ = redis_backend
    key = "team_request:sync:req_1"
    token = backend.try_acquire(key, 30)
    backend.release(key, token)
    assert backend.try_acquire(key, 30) is not None


def test_cas_release_does_not_delete_successors_lock(redis_backend):
    backend, fake = redis_backend
    key = "team_request:sync:req_1"
    old_token = backend.try_acquire(key, 30)
    # 模拟旧持有者 TTL 到期、新持有者已拿到锁
    fake.force_expire(key)
    new_token = backend.try_acquire(key, 30)
    assert new_token is not None and new_token != old_token
    # 旧持有者迟到的释放不能删掉新持有者的锁
    backend.release(key, old_token)
    assert fake.get(key) == new_token


def test_ttl_expiry_is_recoverable(redis_backend):
    backend, fake = redis_backend
    key = "team_request:sync:req_1"
    assert backend.try_acquire(key, 30) is not None
    assert backend.try_acquire(key, 30) is None
    # 锁 TTL 到期（持有者崩溃未释放）后，新执行者可以重新获取，系统可恢复
    fake.force_expire(key)
    assert backend.try_acquire(key, 30) is not None


def test_acquire_falls_back_when_redis_unavailable(redis_backend):
    backend, fake = redis_backend
    fake.raise_on_set = True
    key = "team_request:sync:req_1"
    token = backend.try_acquire(key, 30)
    assert token is not None  # Redis 故障时降级进程锁，服务不瘫
    assert backend.degraded_count >= 1
    backend.release(key, token)  # 释放进程兜底锁，避免污染模块级单例
    # Redis 恢复后，之前的进程兜底锁必须已经释放，不能把同实例后续请求锁死。
    fake.raise_on_set = False
    assert backend.try_acquire(key, 30) is not None


def test_process_fallback_token_is_not_released_through_redis(redis_backend):
    backend, fake = redis_backend
    key = "team_request:sync:req_fallback_release"
    fake.raise_on_set = True
    fallback_token = backend.try_acquire(key, 30)
    assert fallback_token is not None
    assert fallback_token.startswith(dl.PROCESS_TOKEN_PREFIX)

    fake.raise_on_set = False
    backend.release(key, fallback_token)
    assert backend.try_acquire(key, 30) is not None


def test_release_exception_is_swallowed(redis_backend):
    backend, fake = redis_backend
    key = "team_request:sync:req_1"
    token = backend.try_acquire(key, 30)
    fake.raise_on_script = True
    # 释放异常不外抛：TTL 会兜底，业务请求正常返回
    backend.release(key, token)
    assert backend.degraded_count >= 1


def test_acquired_lock_double_release_is_idempotent(redis_backend):
    backend, _ = redis_backend
    acquired = dl.try_acquire_team_request_lock("sync", "req_1")
    assert acquired is not None
    acquired.release()
    acquired.release()  # 重复释放不报错、不影响后继锁


def test_context_manager_raises_on_contention(redis_backend):
    assert dl.try_acquire_team_request_lock("sync", "req_1") is not None
    with pytest.raises(dl.LockUnavailable):
        with dl.team_request_lock("sync", "req_1"):
            pytest.fail("锁竞争时不应进入临界区")


def test_context_manager_releases_on_exception(redis_backend):
    with pytest.raises(RuntimeError, match="boom"):
        with dl.team_request_lock("create_task", "req_9"):
            raise RuntimeError("boom")
    # 业务异常后锁必须释放，后继请求可重新获取
    assert dl.try_acquire_team_request_lock("create_task", "req_9") is not None


def test_process_backend_same_key_mutual_exclusion(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    dl.set_lock_backend(None)
    backend = dl.get_lock_backend()
    token = backend.try_acquire("team_request:sync:req_1", 30)
    assert token is not None
    assert backend.try_acquire("team_request:sync:req_1", 30) is None
    backend.release("team_request:sync:req_1", token)
    assert backend.try_acquire("team_request:sync:req_1", 30) is not None
