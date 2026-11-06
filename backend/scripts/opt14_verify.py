"""OPT-14 验证 harness（默认隔离环境；真实 HTTP + 真实 SQLite + 真实子进程）。

不做假验证：拉起真实 uvicorn 服务（隔离环境变量、内存态），用真实 HTTP 会话验证
审计导出（租户作用域、响应头、自审计、权限门、CSV）；真实 SQLite 落库验证审计保留
（CLI dry-run 默认 / apply 真删 / 下限拒绝）；子进程验证 AUTH_MODE fail-closed 与
ORM 租户分类、角色权限矩阵快照。所有断言来自真实返回值与退出码，不调用真实模型。

用法：python -m scripts.opt14_verify
退出码：0=全部检查通过；2=存在失败检查（报告如实落盘，不伪造成功）。
报告：backend/.data/opt14-exp/opt14_evidence.json（终稿副本入 docs/project-development/assets/opt14/）。
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable
OUT_DIR = BACKEND_ROOT / ".data" / "opt14-exp"
HERMETIC_ENV_FILE = BACKEND_ROOT / "tests" / ".env.hermetic-nonexistent"

ACCOUNTS = {
    "admin": ("admin@company.com", "Admin123!"),
    "developer": ("developer@company.com", "Dev123!"),
    "observer": ("observer@company.com", "Observer123!"),
}


def _isolated_env(extra: dict | None = None) -> dict:
    env = dict(os.environ)
    env.update({
        "ENV_FILE": str(HERMETIC_ENV_FILE),
        "USE_SQLALCHEMY": "false",
        "MODEL_MOCK_FALLBACK": "true",
        "USE_CELERY": "false",
        "PYTHONUNBUFFERED": "1",
        "PYTHONIOENCODING": "utf-8",
    })
    for leaky in ("REDIS_URL", "CELERY_BROKER_URL", "CELERY_RESULT_BACKEND", "AUTH_MODE",
                  "AUDIT_RETENTION_DAYS"):
        env.pop(leaky, None)
    if extra:
        env.update(extra)
    return env


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _run_py(code: str, env_extra: dict | None = None, timeout: int = 180) -> subprocess.CompletedProcess:
    return subprocess.run(
        [PY, "-c", code], cwd=str(BACKEND_ROOT), env=_isolated_env(env_extra),
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
    )


# ---------------- 真实 HTTP ----------------

class Client:
    def __init__(self, base: str):
        jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        self.base = base

    def request(self, method: str, path: str, payload=None) -> tuple[int, dict, str]:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            resp = self.opener.open(req, timeout=30)
            return resp.status, dict(resp.headers), resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers), exc.read().decode("utf-8")

    def login(self, who: str) -> None:
        account, password = ACCOUNTS[who]
        status, _, body = self.request("POST", "/api/auth/login", {"account": account, "password": password})
        assert status == 200, f"login {who} -> {status}: {body[:300]}"


def _hdr(headers: dict, name: str) -> str:
    lowered = name.lower()
    for key, value in headers.items():
        if key.lower() == lowered:
            return value
    return ""


def _wait_health(base: str, timeout_s: int = 180) -> None:
    deadline = time.time() + timeout_s
    last_err = ""
    while time.time() < deadline:
        try:
            status, _, _ = Client(base).request("GET", "/api/health")
            if status == 200:
                return
            last_err = f"status={status}"
        except Exception as exc:  # noqa: BLE001 冷启动期连接拒绝属预期
            last_err = repr(exc)
        time.sleep(1)
    raise TimeoutError(f"服务未在 {timeout_s}s 内就绪：{last_err}")


def main() -> int:
    parser = argparse.ArgumentParser(description="OPT-14 验证 harness")
    parser.add_argument("--keep-logs", action="store_true", help="保留并打印服务日志路径（默认也保留）")
    args = parser.parse_args()
    del args  # 预留

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    checks: list[dict] = []

    def check(cid: str, desc: str, ok: bool, evidence: dict) -> None:
        checks.append({"id": cid, "desc": desc, "status": "pass" if ok else "fail", "evidence": evidence})

    report: dict = {
        "schema": "opt14-evidence-v1",
        "harness": "opt14_verify",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "environment": {
            "python": PY,
            "backend_root": str(BACKEND_ROOT),
            "model_mode": "deterministic/mock（不调真实模型）",
        },
        "validation_status": {},
        "checks": checks,
        "summary": {},
    }

    server_log = OUT_DIR / "opt14_uvicorn.log"

    # ------------------------------------------------------------------
    # A. 真实 HTTP：租户隔离 + 审计导出
    # ------------------------------------------------------------------
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    report["environment"]["server"] = {"port": port, "mode": "内存态（USE_SQLALCHEMY=false）"}
    with open(server_log, "w", encoding="utf-8") as log_fh:
        proc = subprocess.Popen(
            [PY, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
            cwd=str(BACKEND_ROOT), env=_isolated_env(), stdout=log_fh, stderr=subprocess.STDOUT,
        )
    try:
        _wait_health(base)

        admin = Client(base)
        admin.login("admin")

        # A1 建租户 B + 切 B + 跨租户读默认项目 → 403（产生 B 侧审计）
        status, _, body = admin.request("POST", "/api/enterprise/tenants", {"name": "OPT14 Evidence Corp"})
        tenant_b = json.loads(body)["data"]["id"]
        admin.request("POST", "/api/enterprise/tenant-context/switch", {"tenant_id": tenant_b})
        status_deny, _, _ = admin.request("GET", "/api/projects/proj_payment")
        check("A1-cross-tenant-deny", "切到租户 B 后读默认租户项目 → 403 且留 B 侧审计",
              status_deny == 403 and status == 200,
              {"create_tenant": status, "tenant_b": tenant_b, "read_proj_payment": status_deny})

        _, _, body_b = admin.request("GET", "/api/audit-events")
        events_b = json.loads(body_b)["data"]
        b_ids = {e["id"] for e in events_b}
        b_events_are_b = bool(events_b) and all(e["tenant_id"] == tenant_b for e in events_b)
        check("A2-tenant-b-audit-scope", "租户 B 审计列表仅含 B 事件（含越权拒绝事件）",
              b_events_are_b and any((e["event_type"] or "").startswith("tenant.") for e in events_b),
              {"count": len(events_b), "sample": [{"id": e["id"], "type": e["event_type"], "tenant": e["tenant_id"]}
                                                  for e in events_b[:3]]})

        # A3 切回默认租户 → 列表与导出均不含 B 事件
        admin.request("POST", "/api/enterprise/tenant-context/switch", {"tenant_id": "tenant_default"})
        _, _, body_d = admin.request("GET", "/api/audit-events")
        events_d = json.loads(body_d)["data"]
        d_ids = {e["id"] for e in events_d}
        status_exp, exp_headers, exp_body = admin.request("GET", "/api/audit-events/export")
        rows = [json.loads(line) for line in exp_body.strip().splitlines() if line]
        row_ids = {r["id"] for r in rows}
        scoped_ok = (
            status_exp == 200
            and all(e["tenant_id"] == "tenant_default" for e in events_d)
            and d_ids.isdisjoint(b_ids)
            and bool(rows)
            and {r["tenant_id"] for r in rows} <= {"tenant_default"}
            and row_ids.isdisjoint(b_ids)
        )
        check("A3-default-scope-isolation", "默认租户列表/导出不含 B 事件（导出 JSONL）", scoped_ok,
              {"list_count": len(events_d), "export_status": status_exp, "export_rows": len(rows),
               "b_ids_leak": sorted(row_ids & b_ids),
               "headers": {"content-type": _hdr(exp_headers, "content-type"),
                           "content-disposition": _hdr(exp_headers, "content-disposition"),
                           "x-audit-export-count": _hdr(exp_headers, "x-audit-export-count"),
                           "x-audit-export-truncated": _hdr(exp_headers, "x-audit-export-truncated"),
                           "x-audit-export-format": _hdr(exp_headers, "x-audit-export-format")},
               "sample_row_keys": sorted(rows[0].keys()) if rows else []})

        # A4 导出自身留审计：第二次导出的 count 比第一次多 1（自审计事件入列）；
        # 查询时默认租户已发生 A3/A4 两次导出 → resource_type=audit_export 应为 2 条
        count1 = int(_hdr(exp_headers, "x-audit-export-count"))
        status_exp2, exp2_headers, _ = admin.request("GET", "/api/audit-events/export")
        count2 = int(_hdr(exp2_headers, "x-audit-export-count"))
        _, _, body_exp_audit = admin.request("GET", "/api/audit-events?resource_type=audit_export")
        export_audits = json.loads(body_exp_audit)["data"]
        check("A4-export-self-audit", "导出动作留审计（tenant.audit.exported），下一次导出可见",
              status_exp2 == 200 and count2 == count1 + 1 and len(export_audits) == 2,
              {"first_export_count": count1, "second_export_count": count2,
               "export_audit_events": [{"type": e["event_type"], "summary": e["summary"]} for e in export_audits]})

        # A5 CSV 格式真实响应
        status_csv, csv_headers, csv_body = admin.request("GET", "/api/audit-events/export?format=csv")
        csv_lines = csv_body.splitlines()
        check("A5-csv-format", "CSV 导出：content-type=text/csv 且首列为 id 表头",
              status_csv == 200 and _hdr(csv_headers, "content-type").startswith("text/csv")
              and csv_lines and csv_lines[0].split(",")[0] == "id",
              {"status": status_csv, "content-type": _hdr(csv_headers, "content-type"),
               "header_row_head": csv_lines[0][:80] if csv_lines else "", "rows": max(0, len(csv_lines) - 1)})

        # A6 权限门：developer 导出 403；observer(auditor) 200
        dev = Client(base)
        dev.login("developer")
        status_dev, _, _ = dev.request("GET", "/api/audit-events/export")
        observer = Client(base)
        observer.login("observer")
        status_obs, _, _ = observer.request("GET", "/api/audit-events/export")
        check("A6-permission-gate", "导出权限门：developer=403，observer(auditor)=200",
              status_dev == 403 and status_obs == 200,
              {"developer": status_dev, "observer": status_obs})

        # A7 参数校验：limit=0 / format=xml / since=not-a-date → 422
        codes = {}
        for q in ("limit=0", "format=xml", "since=not-a-date"):
            status_q, _, _ = admin.request("GET", f"/api/audit-events/export?{q}")
            codes[q] = status_q
        check("A7-param-validation", "非法导出参数 → 422", all(v == 422 for v in codes.values()), codes)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=15)
    report["environment"]["server"]["log"] = str(server_log.relative_to(BACKEND_ROOT))

    # ------------------------------------------------------------------
    # B. 审计保留：真实 SQLite + 真实 CLI 子进程
    # ------------------------------------------------------------------
    db_path = OUT_DIR / "opt14_retention.db"
    if db_path.exists():
        db_path.unlink()
    db_env = {"USE_SQLALCHEMY": "true", "DATABASE_URL": f"sqlite:///{db_path.as_posix()}", "PERSIST_STRICT": "false"}

    seed_code = (
        "import json\n"
        "from datetime import datetime, timedelta, timezone\n"
        "from types import SimpleNamespace\n"
        "from app.db.session import get_engine\n"
        "from app.models.orm import Base\n"
        "from app.db.repositories import upsert_audit_event\n"
        "Base.metadata.create_all(get_engine())\n"
        "now = datetime.now(timezone.utc)\n"
        "for eid, days in (('opt14_old', 100), ('opt14_new', 2)):\n"
        "    upsert_audit_event(SimpleNamespace(id=eid, event_type='task.analysis_completed', tenant_id='tenant_default',"
        " resource_type='task', resource_id='T-1', actor_id='user_admin', actor_name='', summary=f'seed {eid}',"
        " diff_summary=[], created_at=now - timedelta(days=days), execution_step_ids=[]))\n"
        "print(json.dumps({'seeded': ['opt14_old', 'opt14_new']}))\n"
    )
    seed = _run_py(seed_code, db_env)
    check("B0-seed-sqlite", "真实 SQLite 落库 2 条审计事件（100 天前 / 2 天前）",
          seed.returncode == 0 and "opt14_old" in seed.stdout,
          {"returncode": seed.returncode, "stdout": seed.stdout.strip()[-200:], "db": str(db_path)})

    cli = [PY, "-m", "scripts.audit_retention"]

    dry = subprocess.run([*cli, "--days", "90", "--json"], cwd=str(BACKEND_ROOT), env=_isolated_env(db_env),
                         capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
    dry_payload = json.loads(dry.stdout.strip().splitlines()[-1]) if dry.stdout.strip() else {}
    check("B1-cli-dry-run-default", "CLI 默认 dry-run：候选=1、删除=0、数据未动",
          dry.returncode == 0 and dry_payload.get("dry_run") is True and dry_payload.get("deleted") == 0
          and dry_payload.get("candidates") == 1,
          {"returncode": dry.returncode, "payload": dry_payload})

    refused = subprocess.run([*cli, "--days", "7", "--apply", "--json"], cwd=str(BACKEND_ROOT), env=_isolated_env(db_env),
                             capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
    refused_payload = json.loads(refused.stdout.strip().splitlines()[-1]) if refused.stdout.strip() else {}
    check("B2-cli-refuses-below-min", "CLI 低于 min_days 默认拒绝（rc=1，refused=true）",
          refused.returncode == 1 and refused_payload.get("refused") is True and refused_payload.get("deleted") == 0,
          {"returncode": refused.returncode, "payload": refused_payload})

    applied = subprocess.run([*cli, "--days", "90", "--apply", "--json"], cwd=str(BACKEND_ROOT), env=_isolated_env(db_env),
                             capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
    applied_payload = json.loads(applied.stdout.strip().splitlines()[-1]) if applied.stdout.strip() else {}
    verify_code = (
        "import json\n"
        "from app.db.repositories import load_audit_events\n"
        "print(json.dumps({'ids': sorted(r['id'] for r in load_audit_events())}))\n"
    )
    after = _run_py(verify_code, db_env)
    after_payload = json.loads(after.stdout.strip().splitlines()[-1]) if after.stdout.strip() else {}
    check("B3-cli-apply-deletes", "CLI apply 真删旧事件（deleted_db=1；DB 仅剩新事件）",
          applied.returncode == 0 and applied_payload.get("deleted_db") == 1
          and after_payload.get("ids") == ["opt14_new"],
          {"returncode": applied.returncode, "payload": applied_payload, "db_ids_after": after_payload.get("ids")})

    no_days = subprocess.run([*cli, "--apply"], cwd=str(BACKEND_ROOT), env=_isolated_env(db_env),
                             capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    check("B4-cli-requires-days", "CLI 缺少 --days → argparse 参数错误 rc=2",
          no_days.returncode == 2,
          {"returncode": no_days.returncode, "stderr_tail": no_days.stderr.strip()[-200:]})

    # ------------------------------------------------------------------
    # C. 身份边界：AUTH_MODE fail-closed
    # ------------------------------------------------------------------
    local = _run_py("from app.core.runtime_config import load_runtime_settings as L; print(L().auth_mode)")
    check("C1-auth-mode-local-default", "AUTH_MODE 默认 local",
          local.returncode == 0 and local.stdout.strip() == "local",
          {"returncode": local.returncode, "stdout": local.stdout.strip()})

    oidc = _run_py("from app.core.runtime_config import load_runtime_settings as L; L()", {"AUTH_MODE": "oidc"})
    stderr_tail = oidc.stderr.strip()[-600:]
    check("C2-auth-mode-fail-closed", "AUTH_MODE=oidc（未实现）→ 拒绝启动，报错含 AUTH_MODE/fail-closed",
          oidc.returncode != 0 and "AUTH_MODE" in oidc.stderr and "fail-closed" in oidc.stderr,
          {"returncode": oidc.returncode, "stderr_tail": stderr_tail})

    # ------------------------------------------------------------------
    # D. ORM 租户分类 / 角色权限矩阵 快照
    # ------------------------------------------------------------------
    orm_code = (
        "import json\n"
        "from app.models.orm import Base\n"
        "tables = Base.metadata.tables\n"
        "direct = sorted(t for t in tables if 'tenant_id' in tables[t].columns)\n"
        "print(json.dumps({'total': len(tables), 'direct': direct, 'shared': sorted(t for t in tables if t not in direct)}))\n"
    )
    orm = _run_py(orm_code)
    orm_payload = json.loads(orm.stdout.strip().splitlines()[-1]) if orm.stdout.strip() else {}
    check("D1-orm-tenant-classification", "ORM 全表租户分类快照（30 表：15 直挂 tenant_id）",
          orm.returncode == 0 and orm_payload.get("total") == 30 and len(orm_payload.get("direct", [])) == 15,
          {"total": orm_payload.get("total"), "direct_count": len(orm_payload.get("direct", [])),
           "direct": orm_payload.get("direct")})

    perms_code = (
        "import json\n"
        "from app.tenancy.permissions import ENT_ROLE_PERMISSIONS\n"
        "print(json.dumps({r: ('audit:view' in p) for r, p in ENT_ROLE_PERMISSIONS.items()}))\n"
    )
    perms = _run_py(perms_code)
    perms_payload = json.loads(perms.stdout.strip().splitlines()[-1]) if perms.stdout.strip() else {}
    expected_audit = {"tenant_owner", "tenant_admin", "security_admin", "auditor"}
    check("D2-role-matrix-audit-view", "audit:view 仅 owner/admin/security_admin/auditor 四角色持有",
          perms.returncode == 0 and {r for r, ok in perms_payload.items() if ok} == expected_audit,
          {"matrix": perms_payload})

    # ------------------------------------------------------------------
    # E. pytest 门禁（OPT-14 专项 + OPT-11 路由冻结）
    # ------------------------------------------------------------------
    tests_log = OUT_DIR / "opt14_pytest.txt"
    pytest_run = subprocess.run(
        [PY, "-m", "pytest", "tests/test_opt14_audit_export_retention.py",
         "tests/test_opt14_tenant_isolation_regression.py", "tests/test_opt11_main_split.py", "-q"],
        cwd=str(BACKEND_ROOT), env=_isolated_env(), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=1200,
    )
    tests_log.write_text(pytest_run.stdout + "\n" + pytest_run.stderr, encoding="utf-8")
    summary_line = ""
    for line in reversed((pytest_run.stdout + pytest_run.stderr).splitlines()):
        if "passed" in line or "failed" in line or "error" in line:
            summary_line = line.strip()
            break
    check("E1-pytest-gate", "专项 pytest 门禁（OPT-14 22 条 + OPT-11 冻结 4 条）",
          pytest_run.returncode == 0 and "26 passed" in summary_line,
          {"returncode": pytest_run.returncode, "summary": summary_line, "log": str(tests_log.relative_to(BACKEND_ROOT))})

    # ------------------------------------------------------------------
    passed = sum(1 for c in checks if c["status"] == "pass")
    report["summary"] = {"total": len(checks), "passed": passed, "failed": len(checks) - passed}
    report["validation_status"] = {
        "real_model_calls": False,
        "real_oidc_provider": "not_collected：OIDC 未实现（边界声明 + fail-closed 已验证）",
        "prod_mysql_rls": "not_implemented：共享 schema + 应用层租户作用域（演进路径见 doc 126）",
        "http_server": "真实 uvicorn 子进程（内存态）；生产部署外的 UI 浏览器路径未采集",
    }

    report_path = OUT_DIR / "opt14_evidence.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[opt14-verify] {passed}/{len(checks)} checks passed")
    for c in checks:
        print(f"  [{'PASS' if c['status'] == 'pass' else 'FAIL'}] {c['id']}: {c['desc']}")
    print(f"[opt14-verify] report: {report_path}")
    return 0 if passed == len(checks) else 2


if __name__ == "__main__":
    sys.exit(main())
