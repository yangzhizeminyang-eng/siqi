"""FIX-01 真实跨进程闭环：API/生产者进程 → 本地隔离 broker → 独立 Celery Worker → API 查询。

隔离边界：
- broker 是本次测试启动的独立 fakeredis TcpFakeServer（真实 RESP，临时端口）；
- SQLite 是 pytest 临时目录；
- 不连接用户已有 Redis、不访问生产 DB/Milvus/模型；
- 业务运行时保持内存态（isolated 评测），仅 run/audit/trace 共享状态落 SQLite。
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from app import main
from app.enterprise_rag import eval_store
from app.enterprise_runtime import observability as obs

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable
WORKER_TIMEOUT = 180
RUN_TIMEOUT = 180


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_port(port: int, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.2)
    raise AssertionError(f"broker 未在 {timeout}s 内监听 127.0.0.1:{port}")


def _wait_log(log_path: Path, needle: str, timeout: float) -> str:
    deadline = time.time() + timeout
    while time.time() < deadline:
        text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
        if needle in text:
            return text
        time.sleep(0.5)
    text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
    raise AssertionError(f"日志未出现 {needle!r}（{timeout}s）：\n{text[-4000:]}")


def _kill(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                       capture_output=True, timeout=30)
    else:
        proc.terminate()
    try:
        proc.wait(timeout=20)
    except subprocess.TimeoutExpired:
        proc.kill()


def _env(database_url: str, port: int, reports: Path) -> dict[str, str]:
    env = dict(os.environ)
    env.update({
        "APP_ENV": "local",
        "USE_SQLALCHEMY": "false",
        "ENTERPRISE_SHARED_PERSISTENCE": "true",
        "DATABASE_URL": database_url,
        "ENV_FILE": str(BACKEND_ROOT / "tests" / ".env.hermetic-nonexistent"),
        "USE_CELERY": "true",
        "CELERY_BROKER_URL": f"redis://127.0.0.1:{port}/0",
        "CELERY_RESULT_BACKEND": f"redis://127.0.0.1:{port}/1",
        "USE_MILVUS": "false",
        "MILVUS_URI": "",
        "MODEL_MOCK_FALLBACK": "true",
        "RAG_EVAL_RUN_LEASE_SECONDS": "60",
        "FIX01_REPORTS_DIR": str(reports),
        "PYTHONUNBUFFERED": "1",
        "PYTHONIOENCODING": "utf-8",
    })
    env.pop("REDIS_URL", None)
    for key in ("ZHIPU_API_KEY", "EMBEDDING_DIMENSIONS", "LLM_API_KEY",
                "GITEE_TOKEN", "GITHUB_TOKEN", "GITLAB_TOKEN"):
        env.pop(key, None)
    return env


def _producer(env: dict[str, str], extra: list[str] | None = None) -> dict:
    proc = subprocess.run(
        [PY, "-m", "scripts.fix01_producer", "--json", *(extra or [])],
        cwd=str(BACKEND_ROOT), env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=120,
    )
    assert proc.returncode == 0, f"producer failed:\nSTDOUT={proc.stdout[-2000:]}\nSTDERR={proc.stderr[-3000:]}"
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_cross_process_api_broker_worker_api(shared_db, tmp_path):
    port = _free_port()
    reports = tmp_path / "reports"
    reports.mkdir()
    env = _env(shared_db, port, reports)
    broker_log = tmp_path / "broker.log"
    worker_log = tmp_path / "worker.log"
    broker = None
    worker = None
    worker_fh = None
    try:
        with broker_log.open("w", encoding="utf-8") as broker_fh:
            broker = subprocess.Popen(
                [PY, "-m", "scripts.opt13_fake_redis", "--port", str(port)],
                cwd=str(BACKEND_ROOT), env=env, stdout=broker_fh,
                stderr=subprocess.STDOUT,
            )
        _wait_port(port)

        worker_fh = worker_log.open("w", encoding="utf-8")
        worker = subprocess.Popen(
            [PY, "-m", "celery", "-A", "scripts.fix01_worker_boot:celery_app",
             "worker", "--pool=solo", "-n", "fix01@127.0.0.1", "-l", "INFO"],
            cwd=str(BACKEND_ROOT), env=env, stdout=worker_fh, stderr=subprocess.STDOUT,
        )
        _wait_log(worker_log, "ready.", timeout=WORKER_TIMEOUT)

        produced = _producer(env)
        run_id = produced["run_id"]
        assert produced["dispatch"]["mode"] == "redis"
        assert produced["dispatch"]["enqueued"] is True
        assert produced["status"] == "queued"

        deadline = time.time() + RUN_TIMEOUT
        run = None
        while time.time() < deadline:
            run = eval_store.get(run_id)
            if run is not None and run.status in {"succeeded", "blocked", "failed"}:
                break
            time.sleep(1)
        assert run is not None, "API/查询进程读不到 Worker 写入的 run"
        assert run.status == "succeeded", run.to_public()
        assert run.validation_status == "local_only"
        assert run.attempts == 1
        assert run.finished_at is not None
        assert run.report_name == f"online_{run_id}.json"
        assert (reports / run.report_name).is_file(), "Worker 报告必须可被回读"
        assert run.summary.get("total_cases", 0) > 0

        import app.enterprise_rag.routes as rag_routes

        rag_routes.REPORTS_DIR = reports
        admin = TestClient(main.app)
        assert admin.post("/api/auth/login", json={
            "account": "admin@company.com", "password": "Admin123!"}).status_code == 200
        detail = admin.get(f"/api/enterprise/rag/evaluations/{run_id}")
        assert detail.status_code == 200, detail.text
        assert detail.json()["data"]["run"]["status"] == "succeeded"
        assert detail.json()["data"]["run"]["finished_at"]
        report_resp = admin.get(f"/api/enterprise/rag/reports/{run.report_name}")
        assert report_resp.status_code == 200, report_resp.text
        assert report_resp.json()["data"]["validation_status"] == "local_only"
        assert report_resp.json()["data"]["dataset_size"] > 0

        # Worker → API 的 trace span 同源可见；租户过滤有效。
        spans = obs.query_spans("tenant_default", trace_id=run_id, limit=500)
        categories = {span.category for span in spans}
        assert "worker" in categories and "rag" in categories, categories
        assert obs.query_spans("tenant_other", trace_id=run_id) == []

        # 审计导出：Worker 完成事件可读、事件时间接近本次运行而非模块导入时刻。
        auditor = TestClient(main.app)
        assert auditor.post("/api/auth/login", json={
            "account": "observer@company.com", "password": "Observer123!"}).status_code == 200
        export = auditor.get("/api/audit-events/export", params={
            "event_type": "tenant.rag.eval.completed",
            "since": (run.created_at - timedelta(seconds=5)).isoformat(),
            "until": (datetime.now(timezone.utc) + timedelta(seconds=5)).isoformat(),
        })
        assert export.status_code == 200, export.text
        rows = [json.loads(line) for line in export.text.strip().splitlines() if line]
        assert rows, "跨进程完成审计必须可导出"
        event_time = datetime.fromisoformat(rows[-1]["created_at"])
        if event_time.tzinfo is None:
            event_time = event_time.replace(tzinfo=timezone.utc)
        assert abs((datetime.now(timezone.utc) - event_time).total_seconds()) < 600, \
            "Worker 审计时间必须是本次运行时间，而非进程启动/模块导入时间"

        # 重复 delivery：Worker 消费但 run 保持终态、不重复执行（attempts 不变、span 不翻倍）。
        before_log = worker_log.read_text(encoding="utf-8", errors="replace")
        completion_marker = f"backend.run_rag_evaluation({run_id}) 完成 -> succeeded"
        before_count = before_log.count(completion_marker)
        assert before_count >= 1, "首次 delivery 必须有完成日志"
        before_spans = len(spans)
        redelivery = _producer(env, ["--run-id", run_id])
        assert redelivery["redelivered"] is True
        deadline = time.time() + 120
        while time.time() < deadline:
            text = worker_log.read_text(encoding="utf-8", errors="replace")
            if text.count(completion_marker) > before_count:
                break
            time.sleep(0.5)
        else:
            raise AssertionError("重复 delivery 未被 Worker 消费：\n"
                                 + worker_log.read_text(encoding="utf-8", errors="replace")[-4000:])
        after = eval_store.get(run_id)
        assert after.status == "succeeded" and after.attempts == 1
        assert len(obs.query_spans("tenant_default", trace_id=run_id, limit=500)) == before_spans
    finally:
        if worker is not None:
            _kill(worker)
        if worker_fh is not None:
            worker_fh.close()
        if broker is not None:
            _kill(broker)
