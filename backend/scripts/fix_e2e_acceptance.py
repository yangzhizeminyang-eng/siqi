"""FIX-01..05 隔离端到端验收（可复现证据脚本）。

隔离边界：
- broker：本脚本启动的独立 fakeredis TcpFakeServer（真实 RESP，临时端口）；
- Worker：独立 Celery solo 进程；
- DB：`backend/.data/fix-e2e/<timestamp>/` 下的临时 SQLite；
- 不连接用户已有 Redis、生产 DB/Milvus、真实模型；不执行真实 Gitee 写入；
- 评测数据仅使用 `e3_05_baseline.jsonl` 隔离基线；production scope 不触发。

用法（任意 cwd）：
  .venv/Scripts/python.exe scripts/fix_e2e_acceptance.py
  .venv/Scripts/python.exe scripts/fix_e2e_acceptance.py --evidence-out <new-file.json>
产物：
  docs/project-development/assets/fix/fix_e2e_evidence_<run_id>.json（默认，新文件）
  backend/.data/fix-e2e/<run_id>/（broker/worker 日志）
禁止覆盖已有证据文件；显式 --evidence-out 也不得指向已存在文件。
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
REPO_ROOT = BACKEND_ROOT.parent
EVIDENCE_DIR = REPO_ROOT / "docs" / "project-development" / "assets" / "fix"
RUN_ID = datetime.now().strftime("run_%Y%m%d_%H%M%S_%f")
RUN_ROOT = BACKEND_ROOT / ".data" / "fix-e2e" / RUN_ID
PY = sys.executable
ENV_SENTINEL = BACKEND_ROOT / "tests" / ".env.hermetic-nonexistent"


def _default_evidence_path() -> Path:
    """默认写到带唯一 run ID 的新文件，绝不覆盖历史 fix_e2e_evidence.json。"""
    return EVIDENCE_DIR / f"fix_e2e_evidence_{RUN_ID}.json"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fix_e2e_acceptance")
    parser.add_argument(
        "--evidence-out", default=None,
        help="证据 JSON 输出路径（必须是尚不存在的文件；默认使用带 run ID 的新文件）")
    return parser


def _write_evidence(evidence: dict, path: str | Path) -> str:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(evidence, ensure_ascii=False, indent=2)
    try:
        # O_EXCL 原子独占创建：并发写同一路径时只有一个进程能成功，
        # 不能退化为 exists() 检查后再 write_text() 的竞态写法。
        with target.open("x", encoding="utf-8") as handle:
            handle.write(payload)
    except FileExistsError as exc:
        raise FileExistsError(f"拒绝覆盖已有证据文件：{target}") from exc
    return str(target)


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
    raise RuntimeError(f"broker 未在 {timeout}s 内监听 127.0.0.1:{port}")


def _wait_log(path: Path, needle: str, timeout: float) -> str:
    deadline = time.time() + timeout
    while time.time() < deadline:
        text = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
        if needle in text:
            return text
        time.sleep(0.5)
    text = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
    raise RuntimeError(f"日志未出现 {needle!r}：\n{text[-4000:]}")


def _kill(proc: subprocess.Popen | None) -> None:
    if proc is None or proc.poll() is not None:
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


def _child_env(port: int, reports: Path, *, extra: dict | None = None) -> dict[str, str]:
    env = dict(os.environ)
    env.update({
        "APP_ENV": "local",
        "USE_SQLALCHEMY": "false",
        "ENTERPRISE_SHARED_PERSISTENCE": "true",
        "DATABASE_URL": f"sqlite:///{(RUN_ROOT / 'app.db').as_posix()}",
        "ENV_FILE": str(ENV_SENTINEL),
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
    if extra:
        env.update(extra)
    return env


def _run_python(code: str, env: dict[str, str], *, timeout: int = 120) -> dict:
    proc = subprocess.run(
        [PY, "-c", code], cwd=str(BACKEND_ROOT), env=env, capture_output=True,
        text=True, encoding="utf-8", errors="replace", timeout=timeout,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"child failed rc={proc.returncode}\nSTDOUT={proc.stdout[-2000:]}\nSTDERR={proc.stderr[-3000:]}")
    line = [item for item in proc.stdout.strip().splitlines() if item.strip()][-1]
    return json.loads(line)


def _prepare_process_env() -> None:
    os.environ.update({
        "APP_ENV": "local",
        "USE_SQLALCHEMY": "false",
        "ENTERPRISE_SHARED_PERSISTENCE": "true",
        "DATABASE_URL": f"sqlite:///{(RUN_ROOT / 'app.db').as_posix()}",
        "ENV_FILE": str(ENV_SENTINEL),
        "USE_CELERY": "false",
        "USE_MILVUS": "false",
        "MILVUS_URI": "",
        "MODEL_MOCK_FALLBACK": "true",
        "FIX01_REPORTS_DIR": str(RUN_ROOT / "reports"),
    })
    os.environ.pop("REDIS_URL", None)
    for key in ("ZHIPU_API_KEY", "EMBEDDING_DIMENSIONS", "LLM_API_KEY",
                "GITEE_TOKEN", "GITHUB_TOKEN", "GITLAB_TOKEN"):
        os.environ.pop(key, None)


def _retention_demo() -> dict:
    db_path = RUN_ROOT / "retention.db"
    env = dict(os.environ)
    env.update({
        "USE_SQLALCHEMY": "true",
        "ENTERPRISE_SHARED_PERSISTENCE": "",
        "DATABASE_URL": f"sqlite:///{db_path.as_posix()}",
        "ENV_FILE": str(ENV_SENTINEL),
    })
    seed_code = (
        "import dotenv as _d; _d.load_dotenv = lambda *a, **k: False\n"
        "import json\n"
        "from datetime import datetime, timedelta, timezone\n"
        "from app.db.session import create_schema\n"
        "create_schema()\n"
        "from app import main\n"
        "from app.db.repositories import upsert_audit_event\n"
        "old = datetime.now(timezone.utc) - timedelta(days=100)\n"
        "for i in range(3):\n"
        "    ev = main.AuditEventRecord(id=f'fix_e2e_old_{i}', event_type='task.lifecycle', "
        "tenant_id='tenant_default', resource_type='task', resource_id=f'old_{i}', "
        "actor_id='u', actor_name='', summary=f'old {i}', diff_summary=[], "
        "created_at=old + timedelta(minutes=i), execution_step_ids=[])\n"
        "    upsert_audit_event(ev)\n"
        "print(json.dumps({'seeded': 3}))\n"
    )
    _run_python(seed_code, env)

    def _cli(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [PY, "-m", "scripts.audit_retention", *args],
            cwd=str(BACKEND_ROOT), env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=120,
        )

    dry = _cli("--days", "90", "--json")
    invalid = _cli("--days", "90", "--apply", "--max-delete", "0", "--json")
    count_code = (
        "import dotenv as _d; _d.load_dotenv = lambda *a, **k: False\n"
        "import json\n"
        "from app.db.repositories import load_audit_events\n"
        "print(json.dumps({'rows': len(load_audit_events())}))\n"
    )
    # 必须在合法 apply 之前读取：证明非法 --max-delete 0 没有删除任何数据。
    after_invalid = _run_python(count_code, env)
    applied = _cli("--days", "90", "--apply", "--max-delete", "2", "--json")
    after_apply = _run_python(count_code, env)
    return {
        "db": str(db_path),
        "dry_run": {"rc": dry.returncode, **json.loads(dry.stdout.strip().splitlines()[-1])},
        "invalid_zero": {"rc": invalid.returncode, **json.loads(invalid.stdout.strip().splitlines()[-1]),
                         "rows_after": after_invalid["rows"]},
        "apply_capped": {"rc": applied.returncode, **json.loads(applied.stdout.strip().splitlines()[-1]),
                         "rows_after": after_apply["rows"]},
    }


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    evidence_path = Path(args.evidence_out) if args.evidence_out else _default_evidence_path()
    # 快速失败；真正的并发保护在 _write_evidence 的 O_EXCL，不能只依赖此检查。
    if evidence_path.exists():
        raise FileExistsError(f"拒绝覆盖已有证据文件：{evidence_path}")
    RUN_ROOT.mkdir(parents=True, exist_ok=True)
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    reports = RUN_ROOT / "reports"
    reports.mkdir()
    _prepare_process_env()

    import dotenv

    dotenv.load_dotenv = lambda *args, **kwargs: False

    from app.db.session import create_schema

    create_schema()

    port = _free_port()
    env = _child_env(port, reports)
    broker_log = RUN_ROOT / "broker.log"
    worker_log = RUN_ROOT / "worker.log"
    broker = worker = None
    worker_fh = None
    checks: dict[str, bool] = {}
    evidence: dict = {
        "schema": "fix-e2e-evidence-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "boundaries": {
            "broker": "fakeredis TcpFakeServer (isolated temporary port)",
            "real_redis": "not_used",
            "database": str(RUN_ROOT / "app.db"),
            "production_db": "not_used",
            "milvus": "not_used",
            "real_model": "not_used",
            "real_gitee_write": "not_used",
            "production_evaluation": "not_used",
        },
    }
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
             "worker", "--pool=solo", "-n", "fix-e2e@127.0.0.1", "-l", "INFO"],
            cwd=str(BACKEND_ROOT), env=env, stdout=worker_fh, stderr=subprocess.STDOUT,
        )
        _wait_log(worker_log, "ready.", timeout=180)

        produced = subprocess.run(
            [PY, "-m", "scripts.fix01_producer", "--json"],
            cwd=str(BACKEND_ROOT), env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=120,
        )
        if produced.returncode != 0:
            raise RuntimeError(f"producer failed: {produced.stdout[-1000:]} {produced.stderr[-3000:]}")
        trigger = json.loads(produced.stdout.strip().splitlines()[-1])
        run_id = trigger["run_id"]
        checks["dispatch_redis_queued"] = (
            trigger["dispatch"]["mode"] == "redis" and trigger["status"] == "queued")

        from app.enterprise_rag import eval_store
        from app.enterprise_runtime import observability as obs

        run = None
        deadline = time.time() + 180
        while time.time() < deadline:
            run = eval_store.get(run_id)
            if run is not None and run.status in {"succeeded", "blocked", "failed"}:
                break
            time.sleep(1)
        checks["worker_terminal_succeeded"] = bool(
            run is not None and run.status == "succeeded" and run.validation_status == "local_only")
        checks["run_persisted_after_memory_reset"] = bool(
            run is not None and eval_store.get(run_id) is not None)
        checks["report_file_written"] = bool(
            run is not None and run.report_name and (reports / run.report_name).is_file())

        import app.enterprise_rag.routes as rag_routes

        rag_routes.REPORTS_DIR = reports
        from fastapi.testclient import TestClient
        from app import main as app_main

        admin = TestClient(app_main.app)
        checks["admin_login"] = admin.post("/api/auth/login", json={
            "account": "admin@company.com", "password": "Admin123!"}).status_code == 200
        detail = admin.get(f"/api/enterprise/rag/evaluations/{run_id}")
        checks["api_reads_worker_terminal"] = bool(
            detail.status_code == 200
            and detail.json()["data"]["run"]["status"] == "succeeded"
            and detail.json()["data"]["run"]["finished_at"])
        report_resp = admin.get(f"/api/enterprise/rag/reports/{run.report_name}")
        checks["api_reads_worker_report"] = bool(
            report_resp.status_code == 200
            and report_resp.json()["data"]["validation_status"] == "local_only")

        spans = obs.query_spans("tenant_default", trace_id=run_id, limit=500)
        checks["worker_trace_visible"] = "worker" in {s.category for s in spans}
        checks["rag_trace_visible"] = "rag" in {s.category for s in spans}
        checks["trace_tenant_filtered"] = obs.query_spans("tenant_other", trace_id=run_id) == []
        secret = "fix-e2e-secret-token-0123456789"
        obs.record_span("tool", "tool.redaction_probe", trace_id="fix-e2e-redact",
                        tenant_id="tenant_default",
                        attributes={"url": f"https://oauth2:{secret}@gitee.com/x.git",
                                    "api_key": "sk-test"})
        redaction_payload = json.dumps(
            [s.model_dump(mode="json") for s in obs.query_spans(
                "tenant_default", trace_id="fix-e2e-redact")], ensure_ascii=False)
        checks["trace_redaction"] = secret not in redaction_payload and "sk-test" not in redaction_payload

        observer = TestClient(app_main.app)
        checks["observer_login"] = observer.post("/api/auth/login", json={
            "account": "observer@company.com", "password": "Observer123!"}).status_code == 200
        export = observer.get("/api/audit-events/export", params={
            "event_type": "tenant.rag.eval.completed",
            "since": (run.created_at - timedelta(seconds=5)).isoformat(),
            "until": (datetime.now(timezone.utc) + timedelta(seconds=5)).isoformat(),
        })
        rows = [json.loads(line) for line in export.text.strip().splitlines() if line]
        event_time = None
        if rows:
            event_time = datetime.fromisoformat(rows[-1]["created_at"])
            if event_time.tzinfo is None:
                event_time = event_time.replace(tzinfo=timezone.utc)
        checks["audit_export_time_filtered"] = bool(
            export.status_code == 200 and len(rows) == 1)
        checks["audit_event_time_is_current"] = bool(
            event_time and abs((datetime.now(timezone.utc) - event_time).total_seconds()) < 600)

        # FIX-02：隔离种子索引兼容 → 改 Embedding 后向量拒绝 / BM25 放行并标注。
        from evaluation.enterprise_rag.seed import seed_baseline

        seed_baseline()
        search_body = {
            "query": "handle_refund ERR_401", "project_id": "proj_demo_payment",
            "repository_id": "repo_demo_payment", "commit_sha": "e3baseline0000000000000000000000000000",
            "retrieval_mode": "hybrid",
        }
        compatible = admin.post("/api/enterprise/rag/search", json=search_body)
        checks["isolated_seed_compatible"] = bool(
            compatible.status_code == 200
            and compatible.json()["data"]["index_embedding_compatible"] is True)

        os.environ["ZHIPU_API_KEY"] = "fix-e2e-fake-key"
        os.environ["EMBEDDING_DIMENSIONS"] = "2048"
        rejected = admin.post("/api/enterprise/rag/search", json=search_body)
        checks["embedding_change_vector_rejected"] = bool(
            rejected.status_code == 409
            and rejected.json()["detail"]["data"]["compatibility"] == "incompatible")
        bm25_body = dict(search_body, retrieval_mode="bm25")
        bm25 = admin.post("/api/enterprise/rag/search", json=bm25_body)
        checks["embedding_change_bm25_annotated"] = bool(
            bm25.status_code == 200
            and bm25.json()["data"]["index_embedding_compatible"] is False)
        os.environ.pop("ZHIPU_API_KEY", None)
        os.environ.pop("EMBEDDING_DIMENSIONS", None)

        retention = _retention_demo()
        checks["retention_dry_run_no_delete"] = bool(
            retention["dry_run"]["rc"] == 0 and retention["dry_run"]["deleted"] == 0
            and retention["dry_run"]["candidates"] == 3)
        checks["retention_invalid_zero_rejected_before_change"] = bool(
            retention["invalid_zero"]["rc"] == 2
            and retention["invalid_zero"]["error_code"] == "invalid_max_delete"
            and retention["invalid_zero"]["rows_after"] == 3)
        checks["retention_apply_capped"] = bool(
            retention["apply_capped"]["rc"] == 0
            and retention["apply_capped"]["deleted"] == 2
            and retention["apply_capped"]["rows_after"] == 1)

        evidence.update({
            "run": run.to_public() if run is not None else None,
            "trigger": trigger,
            "trace": {
                "count": len(spans),
                "categories": sorted({s.category for s in spans}),
                "tenant_other_count": len(obs.query_spans("tenant_other", trace_id=run_id)),
            },
            "audit_export": {"rows": rows, "event_time": event_time.isoformat() if event_time else None},
            "retention": retention,
            "checks": checks,
            "work_dir": str(RUN_ROOT),
            "logs": {"broker": str(broker_log), "worker": str(worker_log)},
            "evidence_path": str(evidence_path),
        })
        evidence["all_passed"] = all(checks.values())
        _write_evidence(evidence, evidence_path)
        print(json.dumps({"checks": checks, "all_passed": evidence["all_passed"],
                          "evidence": str(evidence_path),
                          "work_dir": str(RUN_ROOT)}, ensure_ascii=False))
        return 0 if evidence["all_passed"] else 2
    finally:
        _kill(worker)
        if worker_fh is not None:
            worker_fh.close()
        _kill(broker)


if __name__ == "__main__":
    sys.exit(main())
