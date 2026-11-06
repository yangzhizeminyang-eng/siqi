"""FIX-01 cross-process producer: API 触发写入 queued run 并经真实 broker 投递。

用法：
  python -m scripts.fix01_producer --json
  python -m scripts.fix01_producer --run-id rageval_xxx   # 仅重复投递（幂等验证）

所有外部写入均被隔离环境变量约束（调用方负责 ENV_FILE/broker/DB）。
"""

from __future__ import annotations

import argparse
import json
import sys

import dotenv

dotenv.load_dotenv = lambda *args, **kwargs: False  # noqa: E731


def _trigger() -> dict:
    from fastapi.testclient import TestClient

    from app import main
    from app.db.session import create_schema

    create_schema()
    client = TestClient(main.app)
    login = client.post("/api/auth/login", json={
        "account": "admin@company.com", "password": "Admin123!"})
    if login.status_code != 200:
        raise RuntimeError(f"登录失败: {login.status_code} {login.text[:300]}")
    resp = client.post("/api/enterprise/rag/evaluations", json={
        "dataset": "e3_05_baseline.jsonl", "retrieval_mode": "hybrid", "top_k": 8})
    if resp.status_code != 200:
        raise RuntimeError(f"触发失败: {resp.status_code} {resp.text[:500]}")
    data = resp.json()["data"]
    return {"run_id": data["run"]["run_id"], "dispatch": data["dispatch"],
            "status": data["run"]["status"]}


def _redeliver(run_id: str) -> dict:
    from app.workers.tasks import run_rag_evaluation

    result = run_rag_evaluation.delay(run_id=run_id)
    return {"run_id": run_id, "async_result_id": str(result.id), "redelivered": True}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fix01_producer")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    payload = _redeliver(args.run_id) if args.run_id else _trigger()
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
