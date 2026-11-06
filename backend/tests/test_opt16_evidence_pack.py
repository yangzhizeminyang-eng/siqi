"""OPT-16 简历成果证据包：门禁（真实索引零违规）+ 侦测能力（临时索引）。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
_REPO_ROOT = _BACKEND.parent
_SCRIPT = _BACKEND / "scripts" / "opt16_evidence_check.py"
_REAL_INDEX = _REPO_ROOT / "docs" / "project-development" / "assets" / "opt16" / "opt16_evidence_index.json"
_REAL_DOC = _REPO_ROOT / "docs" / "project-development" / "128-opt16-resume-evidence-pack.md"
_KINDS = {"production_cloud", "local_real_ext", "local_offline", "synthetic"}

# 一个稳定的真实 marker 目标：OPT-12 对拍报告（内容冻结，不再改写）。
_MARKER_FILE = "docs/project-development/assets/opt12/after-current.json"
_MARKER_TEXT = '"violation_count": 0'


def _run(index: Path | None = None, doc: Path | None = None) -> subprocess.CompletedProcess:
    cmd = [sys.executable, str(_SCRIPT)]
    if index is not None:
        cmd += ["--index", str(index)]
    if doc is not None:
        cmd += ["--doc", str(doc)]
    env = os.environ.copy()
    env["ENV_FILE"] = str(_BACKEND / "tests" / ".env.hermetic-nonexistent")
    return subprocess.run(
        cmd, cwd=str(_BACKEND), env=env,
        capture_output=True, text=True, encoding="utf-8", timeout=300,
    )


def _minimal_index(**claim_overrides) -> dict:
    claim = {
        "id": "C01",
        "statement": "合成最小声明（测试夹具）。",
        "kind": "local_offline",
        "metrics": {"value": 1},
        "evidence_files": [_MARKER_FILE],
        "markers": [{"file": _MARKER_FILE, "must_contain": _MARKER_TEXT}],
        "repro": "tests/fixtures",
    }
    claim.update(claim_overrides)
    return {
        "schema": "opt16-evidence-index-v1",
        "generated_at": "2026-10-16",
        "claims": [claim],
        "boundaries": [
            {"id": "B01", "kind": "not_collected", "statement": "边界夹具。", "note": "test"},
        ],
    }


def _write_json(tmp_path: Path, payload: dict, name: str = "index.json") -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def _write_doc(tmp_path: Path, claim_ids: list[str], name: str = "doc.md") -> Path:
    p = tmp_path / name
    p.write_text("# 合成文档\n\n" + "\n".join(f"- {i}" for i in claim_ids) + "\n", encoding="utf-8")
    return p


def test_checker_passes_on_real_index():
    """门禁：真实证据索引（assets/opt16）与主文档当前必须零违规。"""
    result = _run()
    assert result.returncode == 0, f"stdout={result.stdout[-3000:]}\nstderr={result.stderr[-2000:]}"
    report = json.loads(result.stdout)
    assert report["schema"] == "opt16-evidence-check-v1"
    assert report["ok"] is True
    assert report["violation_count"] == 0
    assert report["violations"] == []
    assert report["total_claims"] >= 15
    assert report["total_markers"] >= 25


def test_index_schema_and_claim_integrity():
    """索引自身结构：schema、ID 格式唯一、kind 枚举、证据文件存在、marker 命中。"""
    data = json.loads(_REAL_INDEX.read_text(encoding="utf-8"))
    assert data["schema"] == "opt16-evidence-index-v1"
    claims = data["claims"]
    assert len(claims) >= 15
    ids = [c["id"] for c in claims]
    assert len(ids) == len(set(ids))
    for c in claims:
        assert c["id"].startswith("C")
        assert c["kind"] in _KINDS, c["id"]
        assert len(c["statement"]) >= 10
        assert c["evidence_files"], c["id"]
        for rel in c["evidence_files"]:
            assert (_REPO_ROOT / rel).is_file(), f"{c['id']} -> {rel}"
        assert c["markers"], c["id"]
    boundaries = data["boundaries"]
    assert len(boundaries) >= 5
    for b in boundaries:
        assert b["id"].startswith("B")
        assert b["kind"] in {"not_collected", "not_implemented", "limitation"}
        assert b["statement"] and b["note"]


def test_tampered_marker_detected(tmp_path):
    """侦测：marker 字面不再命中时以非零码退出并定位违规。"""
    payload = _minimal_index()
    payload["claims"][0]["markers"][0]["must_contain"] = "no_such_marker_xyz_123"
    idx = _write_json(tmp_path, payload)
    doc = _write_doc(tmp_path, ["C01", "B01"])
    result = _run(index=idx, doc=doc)
    assert result.returncode == 1
    report = json.loads(result.stdout)
    checks = {v["check"] for v in report["violations"]}
    assert "marker_missing" in checks
    assert any(v.get("claim_id") == "C01" for v in report["violations"])


def test_missing_evidence_file_detected(tmp_path):
    """侦测：evidence_files 指向不存在的路径时以非零码退出。"""
    payload = _minimal_index(evidence_files=["docs/does/not/exist.json"])
    idx = _write_json(tmp_path, payload)
    doc = _write_doc(tmp_path, ["C01", "B01"])
    result = _run(index=idx, doc=doc)
    assert result.returncode == 1
    report = json.loads(result.stdout)
    assert any(v["check"] == "evidence_missing" for v in report["violations"])


def test_unknown_kind_detected(tmp_path):
    """侦测：kind 不在枚举内时以非零码退出。"""
    payload = _minimal_index(kind="local")
    idx = _write_json(tmp_path, payload)
    doc = _write_doc(tmp_path, ["C01", "B01"])
    result = _run(index=idx, doc=doc)
    assert result.returncode == 1
    report = json.loads(result.stdout)
    assert any(v["check"] == "kind_invalid" for v in report["violations"])


def test_doc_must_cover_claim_ids(tmp_path):
    """侦测：主文档未收录某条声明 ID 时以非零码退出。"""
    payload = _minimal_index()
    idx = _write_json(tmp_path, payload)
    doc = _write_doc(tmp_path, ["B01"])  # 缺 C01
    result = _run(index=idx, doc=doc)
    assert result.returncode == 1
    report = json.loads(result.stdout)
    assert any(v["check"] == "doc_missing_claim" for v in report["violations"])


def test_marker_outside_repo_rejected(tmp_path):
    """侦测：markers/evidence 引用仓库外绝对路径时拒绝（防索引外泄/失效）。"""
    outside_repo = "C:/Windows/win.ini" if os.name == "nt" else "/etc/hostname"
    payload = _minimal_index(evidence_files=[outside_repo])
    idx = _write_json(tmp_path, payload)
    doc = _write_doc(tmp_path, ["C01", "B01"])
    result = _run(index=idx, doc=doc)
    assert result.returncode == 1
    report = json.loads(result.stdout)
    assert any(v["check"] == "evidence_outside_repo" for v in report["violations"])
