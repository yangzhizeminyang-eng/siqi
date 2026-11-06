"""FIX-01 多主机长稳驱动：持续触发隔离评测并跟踪终态/attempts/报告。

运行在持有共享 DB + 真实 Redis broker 的独立进程；业务运行时保持内存态
（USE_SQLALCHEMY=false），跨进程状态经 ENTERPRISE_SHARED_PERSISTENCE=true 落 MySQL。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import dotenv

dotenv.load_dotenv = lambda *args, **kwargs: False  # noqa: E731

_TERMINAL = {"succeeded", "blocked", "failed"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fix_soak_multi_host")
    parser.add_argument("--runs", type=int, default=120)
    parser.add_argument("--interval", type=float, default=30.0)
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--dataset", default="e3_05_baseline.jsonl")
    parser.add_argument("--mode", default="hybrid")
    parser.add_argument("--top-k", type=int, default=8)
    parser.add_argument("--output", default="/reports/soak_summary.json")
    parser.add_argument("--heartbeat", default="/reports/soak_heartbeat.json")
    parser.add_argument("--authorization-operation",
                        default=os.getenv("AUTHORIZATION_OPERATION", ""))
    parser.add_argument("--authorization-log",
                        default=os.getenv("AUTHORIZATION_LOG", ""))
    args = parser.parse_args(argv)

    from scripts.authorization_gate import require_authorization

    require_authorization(args.authorization_operation, args.authorization_log or None)

    from fastapi.testclient import TestClient

    from app import main as app_main
    from app.db.session import create_schema
    from app.enterprise_rag import routes as rag_routes

    reports_dir = (os.getenv("FIX01_REPORTS_DIR") or "").strip()
    if reports_dir:
        rag_routes.REPORTS_DIR = Path(reports_dir)

    create_schema()
    client = TestClient(app_main.app)
    login = client.post("/api/auth/login", json={
        "account": "admin@company.com", "password": "Admin123!"})
    if login.status_code != 200:
        raise RuntimeError(f"登录失败: {login.status_code} {login.text[:300]}")

    output = Path(args.output)
    heartbeat = Path(args.heartbeat)
    summary = {
        "schema": "fix-soak-multi-host-v1",
        "started_at": _now(),
        "finished_at": None,
        "config": {
            "runs": args.runs,
            "interval_seconds": args.interval,
            "timeout_seconds": args.timeout,
            "dataset": args.dataset,
            "mode": args.mode,
            "top_k": args.top_k,
        },
        "runs": [],
        "checks": {},
        "status": "running",
    }
    _write(output, summary)

    for index in range(args.runs):
        cycle_start = time.monotonic()
        record = {"index": index, "triggered_at": _now()}
        try:
            resp = client.post("/api/enterprise/rag/evaluations", json={
                "dataset": args.dataset,
                "retrieval_mode": args.mode,
                "top_k": args.top_k,
            })
            record["trigger_status"] = resp.status_code
            if resp.status_code != 200:
                record["error"] = resp.text[:500]
                record["final_status"] = "trigger_failed"
                summary["runs"].append(record)
                _write(output, summary)
                time.sleep(args.interval)
                continue
            payload = resp.json()["data"]
            run_id = payload["run"]["run_id"]
            record["run_id"] = run_id
            record["dispatch"] = payload.get("dispatch")
            heartbeat.write_text(json.dumps({
                "index": index, "run_id": run_id, "phase": "triggered",
                "at": _now()}, ensure_ascii=False), encoding="utf-8")

            deadline = time.monotonic() + args.timeout
            final = {}
            while time.monotonic() < deadline:
                detail = client.get(f"/api/enterprise/rag/evaluations/{run_id}")
                if detail.status_code != 200:
                    time.sleep(1)
                    continue
                final = detail.json()["data"]["run"]
                if final.get("status") in _TERMINAL:
                    break
                time.sleep(2)
            record["final_status"] = final.get("status", "timeout")
            record["validation_status"] = final.get("validation_status", "")
            record["attempts"] = final.get("attempts")
            record["report_name"] = final.get("report_name")
            record["summary"] = final.get("summary")
            record["error"] = final.get("error", "")
            record["finished_at"] = _now()
            if record["final_status"] == "succeeded" and record["report_name"]:
                report = client.get(f"/api/enterprise/rag/reports/{record['report_name']}")
                record["report_read_status"] = report.status_code
                if report.status_code == 200:
                    record["report_dataset_size"] = report.json()["data"].get("dataset_size")
            heartbeat.write_text(json.dumps({
                "index": index, "run_id": run_id, "phase": record["final_status"],
                "attempts": record["attempts"], "at": _now()}, ensure_ascii=False),
                encoding="utf-8")
        except Exception as exc:  # noqa: BLE001 记录后继续，长稳不因单次异常中断
            record["error"] = f"{type(exc).__name__}: {exc}"[:500]
            record["final_status"] = record.get("final_status", "driver_error")
        summary["runs"].append(record)
        _write(output, summary)
        elapsed = time.monotonic() - cycle_start
        time.sleep(max(0.0, args.interval - elapsed))

    records = summary["runs"]
    terminal = [r for r in records if r.get("final_status") in _TERMINAL]
    succeeded = [r for r in records if r.get("final_status") == "succeeded"]
    failed = [r for r in records if r.get("final_status") == "failed"]
    records_with_attempts = [r for r in terminal if isinstance(r.get("attempts"), int)]
    summary["finished_at"] = _now()
    summary["checks"] = {
        "run_count": len(records),
        "terminal_count": len(terminal),
        "succeeded_count": len(succeeded),
        "failed_count": len(failed),
        "trigger_failed_count": sum(1 for r in records if r.get("final_status") == "trigger_failed"),
        "timeout_count": sum(1 for r in records if r.get("final_status") == "timeout"),
        "all_terminal": len(terminal) == len(records),
        "all_succeeded": len(succeeded) == len(records),
        "attempts_recorded": len(records_with_attempts) == len(terminal),
        "attempts_at_least_one": all(r.get("attempts", 0) >= 1 for r in records_with_attempts),
        "recovered_attempts": [r["run_id"] for r in records_with_attempts if r.get("attempts", 0) > 1],
        "all_succeeded_have_report_name": all(r.get("report_name") for r in succeeded),
        "cloud_report_read_ok_count": sum(1 for r in succeeded if r.get("report_read_status") == 200),
        "report_404_count": sum(1 for r in succeeded if r.get("report_read_status") == 404),
        "max_attempts": max((r.get("attempts", 0) for r in records_with_attempts), default=0),
    }
    summary["status"] = "passed" if all(summary["checks"][k] for k in (
        "all_terminal", "all_succeeded", "attempts_recorded", "attempts_at_least_one",
        "all_succeeded_have_report_name")) else "failed"
    _write(output, summary)
    print(json.dumps({"status": summary["status"], "checks": summary["checks"],
                      "output": str(output)}, ensure_ascii=False))
    return 0 if summary["status"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
