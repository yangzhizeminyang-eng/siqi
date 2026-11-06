"""OPT-08 RAG 在线评测入口演示（隔离启动：内存态 + 零外呼 + 报告写演示目录）。

链路（全部真实执行，不伪造）：
- 真实 HTTP 路由 + 真实鉴权/租户上下文（TestClient 走 main.app 完整登录与权限链）；
- 触发（POST /api/enterprise/rag/evaluations）→ dispatch 降级标记 eager(test)
  → 内联执行与 Worker 完全相同的 run_evaluation；
- 状态查询（GET /evaluations/{run_id}、GET /evaluations）→ 报告回读（GET /reports/{name}，复用 E3-08）；
- 权限边界：developer/auditor 触发 403（缺 rag:evaluate）；状态查看 developer 200 / auditor 403（缺 rag:search）；
- 生产门：production 未确认 → 422 + 拒绝审计、不建 run；确认后 require_milvus 语义
  → Milvus 不可用一律 blocked（如实，绝不冒称 verified）。

默认强制隔离（脚本自身设定，不依赖外部环境；父进程注入敌意 USE_CELERY/USE_SQLALCHEMY/USE_MILVUS 也压回内存态）：
- USE_SQLALCHEMY/USE_CELERY/USE_MILVUS=false；ENV_FILE 指向不存在文件；broker/Milvus/凭据变量清空
- 报告写在 --reports-dir（演示默认 backend/.data/opt08-demo/reports），不污染 canonical reports/

用法（任意 cwd）：
  .venv/Scripts/python.exe scripts/opt08_rag_eval_online_demo.py --out evidence.json [--reports-dir DIR]

退出码：0=全部检查通过；2=存在失败检查（如实报告）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

DEMO_ROOT = BACKEND_ROOT / ".data" / "opt08-demo"

ACCOUNTS = {
    "admin": ("admin@company.com", "Admin123!"),          # tenant_owner
    "developer": ("developer@company.com", "Dev123!"),    # developer（无 rag:evaluate）
    "auditor": ("observer@company.com", "Observer123!"),  # auditor（无 rag:search）
}

REMOTE_WRITE_MODULES = ("app.services.enterprise_release_service",)


def _force_isolated_env() -> None:
    os.environ["APP_ENV"] = "local"
    os.environ["MODEL_MOCK_FALLBACK"] = "true"
    os.environ["USE_SQLALCHEMY"] = "false"
    os.environ["USE_CELERY"] = "false"
    os.environ["CELERY_BROKER_URL"] = ""
    os.environ["CELERY_RESULT_BACKEND"] = ""
    os.environ["USE_MILVUS"] = "false"
    os.environ["MILVUS_URI"] = ""
    os.environ["DATA_DIR"] = str(DEMO_ROOT)
    os.environ["ENV_FILE"] = str(DEMO_ROOT / "no-such.env")
    for key in ("LLM_API_KEY", "ZHIPU_API_KEY", "GITEE_TOKEN", "GITHUB_TOKEN", "GITLAB_TOKEN"):
        os.environ.pop(key, None)
    DEMO_ROOT.mkdir(parents=True, exist_ok=True)


def _client(who: str):
    from fastapi.testclient import TestClient

    from app import main

    client = TestClient(main.app)
    email, pwd = ACCOUNTS[who]
    resp = client.post("/api/auth/login", json={"account": email, "password": pwd})
    if resp.status_code != 200:
        raise RuntimeError(f"登录失败 {who}: {resp.status_code} {resp.text[:200]}")
    return client


def _audit_counts(main) -> dict[str, int]:
    counts: dict[str, int] = {}
    for event in main.AUDIT_EVENTS.values():
        key = getattr(event, "event_type", "")
        counts[key] = counts.get(key, 0) + 1
    return counts


def _run_demo(reports_dir: Path) -> dict:
    from app import main
    from app.core import runtime_config
    from app.db.repositories import persistence_enabled
    from app.enterprise_rag import eval_store
    from app.enterprise_rag import routes as rag_routes
    from app.workers import dispatch

    reports_dir = Path(reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)
    rag_routes.REPORTS_DIR = reports_dir  # 演示报告目录隔离（eval_service 懒读取此处）

    eval_store.reset_store()
    main.AUDIT_EVENTS.clear()

    admin = _client("admin")
    developer = _client("developer")
    auditor = _client("auditor")

    # ---- 权限边界（真实 403/200） ----
    dev_trigger = developer.post("/api/enterprise/rag/evaluations", json={"dataset": "e3_05_baseline.jsonl"})
    aud_trigger = auditor.post("/api/enterprise/rag/evaluations", json={"dataset": "e3_05_baseline.jsonl"})
    dev_view = developer.get("/api/enterprise/rag/evaluations")
    aud_view = auditor.get("/api/enterprise/rag/evaluations")

    # ---- 主链路：默认隔离触发 → 状态 → 报告 ----
    triggered = admin.post("/api/enterprise/rag/evaluations", json={
        "dataset": "e3_05_baseline.jsonl", "retrieval_mode": "hybrid", "top_k": 8})
    if triggered.status_code != 200:
        raise RuntimeError(f"触发失败: {triggered.status_code} {triggered.text[:300]}")
    data = triggered.json()["data"]
    run = data["run"]
    run_id = run["run_id"]

    detail = admin.get(f"/api/enterprise/rag/evaluations/{run_id}").json()["data"]["run"]
    listed = admin.get("/api/enterprise/rag/evaluations").json()["data"]["runs"]

    view = admin.get(f"/api/enterprise/rag/reports/{run['report_name']}")
    report_view = view.json()["data"] if view.status_code == 200 else {}

    # ---- 生产门：未确认拦截 → 确认后诚实 blocked ----
    runs_before = len(listed)
    prod_refused = admin.post("/api/enterprise/rag/evaluations", json={
        "dataset": "e3_05_baseline.jsonl", "execution_scope": "production"})
    runs_after_refused = admin.get("/api/enterprise/rag/evaluations").json()["data"]["runs"]
    prod_confirmed = admin.post("/api/enterprise/rag/evaluations", json={
        "dataset": "e3_05_baseline.jsonl", "execution_scope": "production", "confirm_production": True})
    prod_run = prod_confirmed.json()["data"]["run"] if prod_confirmed.status_code == 200 else {}
    prod_view = (admin.get(f"/api/enterprise/rag/reports/{prod_run['report_name']}")
                 if prod_run.get("report_name") else None)

    # ---- 环境自证（全部从真实配置读取，不硬编码） ----
    settings = runtime_config.load_runtime_settings()
    in_memory = not persistence_enabled()
    env_block = {
        "stores": "in_memory" if in_memory else "persistent",
        "database": "disabled" if in_memory else "enabled",
        "celery": "disabled" if not dispatch.celery_enabled() else "enabled",
        "milvus": "disabled" if not settings.use_milvus else "enabled",
        "data_dir": str(DEMO_ROOT),
        "reports_dir": str(reports_dir),
    }
    env_block["production_real_executed"] = bool(prod_run.get("validation_status") == "verified")

    counts = _audit_counts(main)
    summary = run.get("summary") or {}
    prod_refused_message = ""
    try:
        prod_refused_message = str((prod_refused.json().get("detail") or {}).get("message", ""))
    except Exception:  # noqa: BLE001 响应体解析失败不影响检查
        pass
    prod_view_ok = bool(
        prod_view is not None and prod_view.status_code == 200
        and prod_view.json()["data"].get("validation_status") == "blocked"
    )

    checks = {
        "isolation_enforced": env_block["stores"] == "in_memory" and env_block["database"] == "disabled"
                              and env_block["celery"] == "disabled" and env_block["milvus"] == "disabled",
        "developer_trigger_denied": dev_trigger.status_code == 403,
        "auditor_trigger_denied": aud_trigger.status_code == 403,
        "status_view_permission_split": dev_view.status_code == 200 and aud_view.status_code == 403,
        "dispatch_eager_labeled": data["dispatch"]["mode"] == "eager(test)"
                                  and data["dispatch"]["enqueued"] is False,
        "isolated_default_succeeded": run.get("status") == "succeeded"
                                      and run.get("execution_scope") == "isolated",
        "isolation_local_only_honest": run.get("validation_status") == "local_only",
        "baseline_reproduced": summary.get("total_cases") == 32 and summary.get("passed_cases") == 29,
        "timestamps_ordered": bool(
            run.get("created_at") and run.get("started_at") and run.get("finished_at")
            and run["created_at"] <= run["started_at"] <= run["finished_at"]),
        "status_query_matches": detail.get("status") == run.get("status") == "succeeded"
                                and any(r["run_id"] == run_id for r in listed),
        "report_view_roundtrip": bool(
            report_view.get("validation_status") == "local_only" and report_view.get("dataset_sha256")),
        "production_requires_confirmation": prod_refused.status_code == 422
                                            and len(runs_after_refused) == runs_before,
        "production_confirmed_honest_blocked": bool(
            prod_run.get("status") == "blocked" and prod_run.get("validation_status") == "blocked"
            and prod_run.get("report_name") and prod_view_ok),
        "audit_events_recorded": bool(
            counts.get("tenant.rag.eval.triggered") and counts.get("tenant.rag.eval.completed")
            and counts.get("tenant.rag_evaluation.denied")),
        "no_remote_write_module": all(m not in sys.modules for m in REMOTE_WRITE_MODULES),
    }
    return {
        "demo": "opt08_rag_online_evaluation",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "tenant_id": "tenant_default",
        "environment": env_block,
        "permission_probe": {
            "developer_trigger_status": dev_trigger.status_code,
            "auditor_trigger_status": aud_trigger.status_code,
            "developer_view_status": dev_view.status_code,
            "auditor_view_status": aud_view.status_code,
        },
        "trigger": {"run": run, "dispatch": data["dispatch"]},
        "status_query": {"detail_status": detail.get("status"), "list_count": len(listed),
                         "run_ids": [r["run_id"] for r in listed]},
        "report": report_view,
        "production_gate": {
            "without_confirmation": {"status": prod_refused.status_code,
                                     "message": prod_refused_message},
            "runs_before": runs_before,
            "runs_after_refused": len(runs_after_refused),
            "confirmed_run": prod_run,
        },
        "audit": {"counts": counts, "total": sum(counts.values())},
        "checks": {**checks, "all_passed": all(checks.values())},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="OPT-08 RAG 在线评测入口隔离演示")
    parser.add_argument("--out", default=str(DEMO_ROOT / "opt08_eval_online_evidence.json"))
    parser.add_argument("--reports-dir", default=str(DEMO_ROOT / "reports"))
    args = parser.parse_args()

    _force_isolated_env()
    report = _run_demo(Path(args.reports_dir))

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    checks = report["checks"]
    passed = sum(1 for value in checks.values() if value is True)
    total = len(checks)
    run = report["trigger"]["run"]
    print(f"OPT-08 demo: run={run.get('run_id')} status={run.get('status')} "
          f"validation={run.get('validation_status')} checks={passed}/{total}")
    print(f"evidence={out.resolve()}")
    if not checks["all_passed"]:
        print(json.dumps(checks, ensure_ascii=False, indent=2))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
