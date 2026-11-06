"""E6-01-PROD-OPS 验收测试：metrics 端点 / ops status 门禁 / 运维脚本存在性与语法。

不堆 mock：metrics 用真实 TestClient 请求触发计数后断言真实累计。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

from app import main
from app.enterprise_ops import metrics as ops_metrics


def _login(who: str) -> TestClient:
    c = TestClient(main.app)
    acct = {
        "admin": ("admin@company.com", "Admin123!"),
        "auditor": ("observer@company.com", "Observer123!"),
    }[who]
    assert c.post("/api/auth/login", json={"account": acct[0], "password": acct[1]}).status_code == 200
    return c


def test_metrics_endpoint_returns_prometheus_text():
    c = _login("admin")
    # 触发真实请求后，metrics 累计应 >0
    c.get("/api/health")
    r = c.get("/api/enterprise/ops/metrics")
    assert r.status_code == 200
    text = r.text
    assert "agent_http_requests_total" in text
    assert "agent_http_5xx_rate" in text
    assert "agent_http_status_total" in text


def test_metrics_all_four_dependencies_present():
    """四条 agent_dependency_{db,redis,milvus,worker} 必须恒存在（对齐 /api/health 契约），值为 0/1 非 None/缺失。"""
    c = _login("admin")
    text = c.get("/api/enterprise/ops/metrics").text
    for dep in ("agent_dependency_db", "agent_dependency_redis", "agent_dependency_milvus", "agent_dependency_worker"):
        line = next((l for l in text.splitlines() if l.startswith(dep + " ")), None)
        assert line is not None, f"缺少 {dep}"
        val = line.split()[-1]
        assert val in ("0", "1"), f"{dep} 值非法: {val}"


def test_metrics_middleware_counts_requests():
    ops_metrics._COUNTS["http_requests_total"] = 0
    ops_metrics._COUNTS["by_status"] = {}
    c = _login("admin")
    c.get("/api/health")
    assert ops_metrics._COUNTS["http_requests_total"] > 0
    assert any(k == 200 for k in ops_metrics._COUNTS["by_status"])


def test_ops_status_org_manage_gated():
    admin = _login("admin")
    assert admin.get("/api/enterprise/ops/status").status_code == 200
    auditor = _login("auditor")  # auditor 无 org:manage
    assert auditor.get("/api/enterprise/ops/status").status_code == 403


def test_prod_ops_scripts_present_and_syntax_ok():
    root = Path(__file__).resolve().parent.parent.parent
    scripts_dir = root / "deploy" / "scripts"
    required = [
        "preflight.sh", "deploy-enterprise.sh", "backup.sh", "restore.sh",
        "list-backups.sh", "upgrade.sh", "rollback.sh", "monitor-health.sh", "loadtest.sh",
    ]
    for name in required:
        p = scripts_dir / name
        assert p.is_file(), f"缺少 {name}"
        # Windows 下 bash 走 WSL shim 无法以 Windows 路径跑 bash -n；语法校验仅在非 Windows 环境执行
        if os.name != "nt" and shutil.which("bash"):
            r = subprocess.run(["bash", "-n", str(p)], capture_output=True, timeout=30)
            assert r.returncode == 0, f"{name} 语法错误: {r.stderr.decode()}"
    # TLS nginx 模板
    tls_conf = root / "deploy" / "nginx.enterprise.tls.conf"
    assert tls_conf.is_file()
    tls_text = tls_conf.read_text(encoding="utf-8")
    assert "listen 80;" in tls_text
    assert "listen 443 ssl http2;" in tls_text
    assert "agentforge.ltd" in tls_text
    assert "127.0.0.1:8081" in tls_text
    assert (root / "deploy" / "docker-compose.enterprise.host-tls.yml").is_file()
    assert (scripts_dir / "configure-host-https.sh").is_file()
    assert (scripts_dir / "verify-dns.sh").is_file()
    assert (scripts_dir / "verify-https.sh").is_file()
    assert (root / "deploy" / "docker-compose.enterprise.multireplica.yml").is_file()
