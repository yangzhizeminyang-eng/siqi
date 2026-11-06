"""OPT-13 harness：broker 恢复路径取证 + kombu unacked_mutex 释放加固（仅 harness Worker 引导加载，生产零改动）。

背景（2026-10-16 全量失败运行实证，原始日志见 .data/opt13-exp/failed_run_20261016_1405/）：
- kombu redis transport 在 mingle 广播 drain 与连接空闲轮询的 `get()` 尾部会调用
  `QoS.restore_visible` 做可见性恢复扫描；扫描用 redis `unacked_mutex`（redis-py Lock，TTL=300s）互斥。
- redis-py 解锁走 Lua（EVALSHA 未命中 → NoScriptError → 回退 SCRIPT LOAD）。本机 fakeredis
  （TcpFakeServer）在该回退路径上偶发「连接被服务端关闭」，导致**解锁从未执行**：
  `unacked_mutex` 以 300s TTL 残留，其后所有 Worker 的恢复扫描全部静默跳过（MutexHeld），
  崩溃重投被整体抑制（S3 的 B 在残锁窗口内 350s 零接收）。
- 证据：worker_opt13a.log 14:05:18 / worker_opt13b-again.log 14:13:41 的
  `restore_visible → Mutex.__exit__ → lock.release → NoScriptError → 10053 / server closed` traceback。

本模块（仅被 scripts.opt13_worker_boot 导入，随 harness Worker 生效）：
1. 【加固】以无 Lua 实现替换 `kombu.transport.redis.Mutex`：
   加锁 = `SET name token NX PX expire`；解锁 = `WATCH/GET 比对/MULTI/DEL` 原子 CAS 删除。
   语义与 kombu 一致：非阻塞、争用抛 `MutexHeld`、TTL = unacked_mutex_expire；释放失败只记日志不上抛
   （避免 fakeredis 抖动级联为连接丢失 + 残锁）。
2. 【取证】恢复链路关键点打印到 Worker 日志（`OPT13_TRACE_RESTORE=0` 可关，默认开）：
   restore_visible 扫描窗口计数、restore_by_tag 重投 tag、_do_restore_message 重投目标队列、
   QoS.append/ack 的 tag 生命周期、mutex 获取/争用/释放事件。供 OPT-13 文档与报告的证据链引用。
"""

from __future__ import annotations

import os
import time
from contextlib import contextmanager
from uuid import uuid4

TRACE = os.getenv("OPT13_TRACE_RESTORE", "1") != "0"


def _now() -> str:
    return time.strftime("%H:%M:%S", time.localtime()) + f".{int((time.time() % 1) * 1000):03d}"


def _log(msg: str) -> None:
    print(f"[opt13-broker {_now()}] {msg}", flush=True)


def _install_robust_mutex() -> None:
    from kombu.transport import redis as kr

    @contextmanager
    def _robust_mutex(client, name, expire):
        token = uuid4().hex
        try:
            acquired = bool(client.set(name, token, nx=True, px=int(expire * 1000)))
        except Exception as exc:  # noqa: BLE001 连接抖动视同「锁被占」：跳过本轮扫描，绝不上抛
            _log(f"mutex acquire-error name={name} err={type(exc).__name__}: {exc}")
            raise kr.MutexHeld() from exc
        if not acquired:
            if TRACE:
                ttl = None
                try:
                    ttl = client.ttl(name)
                except Exception:  # noqa: BLE001
                    pass
                _log(f"mutex busy name={name} ttl={ttl}s")
            raise kr.MutexHeld()
        if TRACE:
            _log(f"mutex acquire name={name} ttl={expire}s")
        try:
            yield
        finally:
            _release_quietly(client, name, token)

    kr.Mutex = _robust_mutex


def _release_quietly(client, name: str, token: str) -> None:
    from redis.exceptions import WatchError

    try:
        with client.pipeline() as pipe:
            try:
                pipe.watch(name)
                current = pipe.get(name)
                if isinstance(current, bytes):
                    current = current.decode()
                if current == token:
                    pipe.multi()
                    pipe.delete(name)
                    pipe.execute()
                    if TRACE:
                        _log(f"mutex release name={name}")
                elif TRACE:
                    _log(f"mutex release-skip name={name}（token 不匹配/已过期）")
            except WatchError:
                _log(f"mutex release-abort name={name}（CAS 期间被他人变更）")
            except Exception as exc:  # noqa: BLE001
                _log(f"mutex release-error name={name} err={type(exc).__name__}: {exc}")
            finally:
                try:
                    pipe.reset()
                except Exception:  # noqa: BLE001
                    pass
    except Exception as exc:  # noqa: BLE001 pipeline 创建失败等
        _log(f"mutex release-error name={name} err={type(exc).__name__}: {exc}")


def _install_trace() -> None:
    from kombu.transport import redis as kr

    orig_restore_visible = kr.QoS.restore_visible

    def restore_visible(self, start=0, num=10, interval=10):
        nxt = self._vrestore_count + 1
        if (nxt - 1) % interval == 0:
            _log(f"restore-scan-window count={nxt} visibility_timeout={self.visibility_timeout}s")
        return orig_restore_visible(self, start=start, num=num, interval=interval)

    orig_restore_by_tag = kr.QoS.restore_by_tag

    def restore_by_tag(self, tag, client=None, leftmost=False):
        _log(f"restore-tag tag={tag}")
        return orig_restore_by_tag(self, tag, client=client, leftmost=leftmost)

    orig_do_restore = kr.Channel._do_restore_message

    def _do_restore_message(self, payload, exchange, routing_key, pipe, leftmost=False):
        _log(f"restore-push exchange={exchange} routing_key={routing_key} leftmost={leftmost}")
        return orig_do_restore(self, payload, exchange, routing_key, pipe, leftmost)

    orig_append = kr.QoS.append

    def append(self, message, delivery_tag):
        _log(f"deliver tag={delivery_tag}")
        return orig_append(self, message, delivery_tag)

    orig_ack = kr.QoS.ack

    def ack(self, delivery_tag):
        _log(f"ack tag={delivery_tag}")
        return orig_ack(self, delivery_tag)

    kr.QoS.restore_visible = restore_visible
    kr.QoS.restore_by_tag = restore_by_tag
    kr.Channel._do_restore_message = _do_restore_message
    kr.QoS.append = append
    kr.QoS.ack = ack


_install_robust_mutex()
if TRACE:
    _install_trace()
