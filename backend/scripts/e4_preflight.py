"""E4 v2 真实 6-Agent 闭环——基础设施 + 模型硬门禁预检。

输出：逐项 PASS/BLOCKED + 最终 READY 或 BLOCKED（列出阻塞项）。
不把“代码/配置存在”当环境已验证；每项做真实探测。
"""

from __future__ import annotations

import json
import os
import socket
import subprocess

from app.core import model_config
from app.core.model_config import MODEL_TOOL_CHOICE_INCOMPATIBLE


def _probe_gitee_pr_backend() -> tuple[bool, str]:
    from app.services.enterprise_release_service import probe_pr_backend

    return probe_pr_backend()


def _probe_evidence_backend() -> tuple[bool, str]:
    from app.services.enterprise_release_service import probe_release_evidence_backend

    return probe_release_evidence_backend()


def _probe_release_backends() -> tuple[tuple[str, str], tuple[str, str]]:
    pr_ok, pr_detail = _probe_gitee_pr_backend()
    evidence_ok, evidence_detail = _probe_evidence_backend()
    return (
        ("PASS" if pr_ok else "BLOCKED", pr_detail),
        ("PASS" if evidence_ok else "BLOCKED", evidence_detail),
    )


def _reach(host: str, port: int) -> bool:
    try:
        s = socket.create_connection((host, int(port)), 1.2)
        s.close()
        return True
    except Exception:  # noqa: BLE001
        return False


def _gates() -> dict:
    out: dict = {}
    st = model_config.load_llm_settings()

    # 1) 模型
    if not getattr(st, "api_key", None):
        out["model"] = ("BLOCKED", "LLM_API_KEY 未配置")
    else:
        probe = model_config.probe_model_tool_choice(st)
        out["model"] = ("COMPATIBLE" if probe.get("compat") == "COMPATIBLE" else "BLOCKED",
                        f"{probe.get('compat')}: {probe.get('error', '')}"[:140])

    # 2) MySQL + migration head
    try:
        from app.db.session import get_engine
        from sqlalchemy import text
        with get_engine().connect() as c:
            v = c.execute(text("SELECT version_num FROM alembic_version")).scalar()
        expected_head = "a7b8c9d0e1f2"
        out["mysql"] = ("PASS", v) if v == expected_head else ("BLOCKED", f"migration {v}")
    except Exception as e:  # noqa: BLE001
        out["mysql"] = ("BLOCKED", type(e).__name__)

    # 3) Redis / broker
    from app.core.runtime_config import load_runtime_settings
    rs = load_runtime_settings()
    broker = bool(getattr(rs, "celery_broker_url", None))
    out["redis"] = ("PASS" if broker and _reach("127.0.0.1", 6379) else "BLOCKED",
                    "broker 配置 + 6379" + (" OPEN" if _reach("127.0.0.1", 6379) else " DOWN"))

    # 4) 隔离工作区
    root = os.environ.get("AGENT_WORKSPACE_ROOT", "").strip()
    out["workspace"] = ("PASS" if root and os.path.isdir(root) and os.access(root, os.W_OK) else "BLOCKED",
                        root or "AGENT_WORKSPACE_ROOT 未设置/不可写")

    # 5) RAG/Milvus
    uri = getattr(rs, "milvus_uri", "") or ""
    h = uri.split("://")[-1].split("/")[0]
    host = h.split(":")[0]; port = h.split(":")[1] if ":" in h else 19530
    out["rag"] = ("PASS" if getattr(rs, "use_milvus", False) and _reach(host, port) else "BLOCKED",
                  f"{uri} -> {'REACHABLE' if _reach(host, port) else 'DOWN'}")

    # 6) Docker / Test Runner
    dk = subprocess.run(["docker", "info"], capture_output=True)
    out["docker"] = ("PASS" if dk.returncode == 0 else "BLOCKED", "docker daemon " + ("up" if dk.returncode == 0 else "down"))

    # 7) E4 PR 后端：可使用现有 Gitee 适配器直连，不强制虚构一个 PR_SERVICE_URL。
    pr, evidence = _probe_release_backends()
    out["pr"] = pr
    out["release_evidence"] = evidence
    return out


if __name__ == "__main__":
    gates = _gates()
    blocked = [k for k, (s, _) in gates.items() if s == "BLOCKED"]
    print(json.dumps(gates, ensure_ascii=False, indent=2))
    if blocked:
        print(f"\n=> BLOCKED: {', '.join(blocked)}")
    else:
        print("\n=> READY")
