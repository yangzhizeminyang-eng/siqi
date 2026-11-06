"""FIX 故障演练监视器：轮询共享 DB，命中 running/attempts>=1 后经 Docker socket 强杀 Worker。"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

import pymysql


def _docker_kill(container: str) -> str:
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(5)
    sock.connect("/var/run/docker.sock")
    request = (
        f"POST /containers/{container}/kill HTTP/1.1\r\n"
        "Host: localhost\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
    )
    sock.sendall(request.encode("ascii"))
    response = sock.recv(1024).decode("utf-8", errors="replace")
    sock.close()
    return response.splitlines()[0] if response else "no-response"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fix_fault_watch")
    parser.add_argument("--container", required=True)
    parser.add_argument("--output", default="/reports/fault_kill.json")
    parser.add_argument("--poll", type=float, default=0.05)
    parser.add_argument("--authorization-operation",
                        default=os.getenv("AUTHORIZATION_OPERATION", ""))
    parser.add_argument("--authorization-log",
                        default=os.getenv("AUTHORIZATION_LOG", ""))
    args = parser.parse_args(argv)

    from scripts.authorization_gate import require_authorization

    require_authorization(args.authorization_operation, args.authorization_log or None)

    url = urllib.parse.urlsplit(os.environ["DATABASE_URL"])
    conn = pymysql.connect(
        host=url.hostname, port=url.port or 3306, user=url.username,
        password=url.password, database=url.path.lstrip("/"), autocommit=True,
        connect_timeout=5,
    )

    def latest():
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT run_id, status, attempts FROM enterprise_rag_evaluation_runs "
                "ORDER BY created_at DESC LIMIT 1"
            )
            return cursor.fetchone()

    baseline = latest()
    print(json.dumps({"baseline": baseline}, ensure_ascii=False), flush=True)
    while True:
        row = latest()
        if (row and (baseline is None or row[0] != baseline[0])
                and row[1] == "running" and (row[2] or 0) >= 1):
            status_line = _docker_kill(args.container)
            payload = {
                "run_id": row[0], "status": row[1], "attempts": row[2],
                "container": args.container, "docker_response": status_line,
                "killed_at": datetime.now(timezone.utc).isoformat(),
            }
            Path(args.output).write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                                          encoding="utf-8")
            print(json.dumps(payload, ensure_ascii=False), flush=True)
            return 0
        time.sleep(args.poll)


if __name__ == "__main__":
    sys.exit(main())
