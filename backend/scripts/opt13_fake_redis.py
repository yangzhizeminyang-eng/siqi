"""OPT-13 harness：进程内 fake Redis（fakeredis TcpFakeServer）真实 TCP 端点。

用途：为多 Worker 恢复验证提供零外部依赖的 broker/result/lock 后端
（真实 RESP 协议 + 真实 Redis 锁语义，含 Lua；不依赖本机 Redis 服务）。

Windows 修正（已实证）：fakeredis 的 TCP handler 依赖 `fcntl` 把连接 socket 置为非阻塞；
Windows 无 fcntl → socket 保持阻塞 → handler 线程卡在 `rfile.readline()`，
服务端主动推送（pub/sub、阻塞命令唤醒）永远冲不出来。修正：无 fcntl 时改用
`sock.setblocking(False)`（已验证 readline 会返回 b''）。

用法：.venv/Scripts/python.exe -m scripts.opt13_fake_redis --port 16380
"""

from __future__ import annotations

import argparse
import sys

import fakeredis._clients._tcp_server as tcp_server
from fakeredis import TcpFakeServer


def _apply_windows_nonblocking_fix() -> bool:
    if tcp_server.HAS_FCNTL:
        return False
    _orig_setup = tcp_server.TCPFakeRequestHandler.setup

    def _setup(self) -> None:  # type: ignore[no-untyped-def]
        _orig_setup(self)
        self.connection.setblocking(False)

    tcp_server.TCPFakeRequestHandler.setup = _setup
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="OPT-13 harness fake redis server")
    parser.add_argument("--port", type=int, default=16380)
    args = parser.parse_args()

    fixed = _apply_windows_nonblocking_fix()
    server = TcpFakeServer(("127.0.0.1", args.port), server_type="redis")
    print(f"READY 127.0.0.1:{args.port} (nonblocking-fix={fixed})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
