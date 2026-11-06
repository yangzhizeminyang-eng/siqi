"""企业版 E3-06 真实目标仓库评测框架 + 云端 Milvus 质量门禁测试。

诚实边界：本机无真实 Milvus/真实目标仓库（不伪造、不含凭据）。
因此真实验证标 local_only/blocked；框架/多模式/报告 schema/门禁在此验证，verified 需云端 Milvus 补证据。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.enterprise_rag.evaluation import load_cases, evaluate_dataset

_BACKEND = Path(__file__).resolve().parents[1]
_EVAL = _BACKEND / "evaluation" / "enterprise_rag"
_DATASET = _EVAL / "e3_06_real_repository.jsonl"
_DSET_TEXT = _DATASET.read_text(encoding="utf-8")
MODES = ["bm25", "vector", "hybrid", "rerank"]


def _seed():
    from evaluation.enterprise_rag.seed import seed_baseline

    seed_baseline()


def _ctx():
    from app.tenancy import context as tc
    from app.tenancy import store as ts

    return tc.build_context(ts.active_membership_for_user("user_admin"))


# 1) E3-06 数据集 ≥50 / case_id 唯一 / 无敏感
def test_dataset_loads_50_unique_and_clean():
    cases = load_cases(str(_DATASET))
    assert len(cases) >= 50
    ids = [c.case_id for c in cases]
    assert len(ids) == len(set(ids))
    low = _DSET_TEXT.lower()
    for sensitive in ("password", "jwt", "cookie", "recovery", "secret_key"):
        assert sensitive not in low
    assert "def " not in low


# 2) 四种模式共享过滤/边界，报告 schema 齐全，本地态为 local_only
@pytest.mark.parametrize("mode", MODES)
def test_all_modes_share_boundaries_and_report_schema(monkeypatch, mode):
    monkeypatch.setenv("USE_MILVUS", "false")
    _seed()
    cases = load_cases(str(_DATASET))
    report = evaluate_dataset(_ctx(), cases, top_k=8, retrieval_mode=mode)
    assert report["retrieval_mode"] == mode
    # 共享边界：403/422 仍全过
    assert report["summary"]["unauthorized_pass_rate"] == 1.0
    # 报告 schema 字段
    for key in ("vector_backend", "milvus_verified", "fallback_used", "validation_status",
                "commit_sha", "retrieval_mode", "index_id"):
        assert key in report or any(key in c for c in report.get("per_case_results", [])), key
    assert report["validation_status"] in ("verified", "local_only", "blocked")
    for metric in ("recall_at_5", "recall_at_10", "precision_at_5", "ndcg_at_5",
                   "latency_p50_ms", "latency_p95_ms"):
        assert metric in report["summary"]
    assert "retrieval_mode" in report


# 3) 本地/内存态（无 Milvus）绝不谎报 verified
def test_local_mode_never_verified(monkeypatch):
    monkeypatch.setenv("USE_MILVUS", "false")
    _seed()
    cases = load_cases(str(_DATASET))
    for mode in MODES:
        report = evaluate_dataset(_ctx(), cases, top_k=8, retrieval_mode=mode)
        assert report["validation_status"] == "local_only"
        assert report["milvus_verified"] is False
        assert report["fallback_used"] is True


# 4) --require-milvus + 无 Milvus -> blocked（CLI 退出非 0）
def test_require_milvus_blocked_via_dataset(tmp_path, monkeypatch):
    monkeypatch.setenv("USE_MILVUS", "false")
    _seed()
    cases = load_cases(str(_DATASET))
    report = evaluate_dataset(_ctx(), cases, top_k=8, retrieval_mode="hybrid", require_milvus=True)
    assert report["validation_status"] == "blocked"
    assert report["milvus_verified"] is False


def test_cli_require_milvus_blocks(tmp_path):
    out = tmp_path / "r.json"
    env = {**os.environ, "USE_SQLALCHEMY": "false", "MODEL_MOCK_FALLBACK": "true", "PYTHONIOENCODING": "utf-8", "USE_MILVUS": "false"}
    proc = subprocess.run(
        [sys.executable, "-m", "scripts.evaluate_enterprise_rag", str(_DATASET),
         "-o", str(out), "--memory-seed", "--top-k", "8", "--mode", "hybrid", "--require-milvus"],
        cwd=str(_BACKEND), env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert proc.returncode != 0  # Milvus 不可用 -> blocked -> 退出码 3
    assert "blocked" in proc.stdout.lower() or "require-milvus" in (proc.stdout + proc.stderr).lower()


# 5) CLI --all-modes 产出 mode_comparison
def test_cli_all_modes_produce_mode_comparison(tmp_path):
    out = tmp_path / "all.json"
    env = {**os.environ, "USE_SQLALCHEMY": "false", "MODEL_MOCK_FALLBACK": "true", "PYTHONIOENCODING": "utf-8", "USE_MILVUS": "false"}
    proc = subprocess.run(
        [sys.executable, "-m", "scripts.evaluate_enterprise_rag", str(_DATASET),
         "-o", str(out), "--memory-seed", "--top-k", "8", "--all-modes"],
        cwd=str(_BACKEND), env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    report = json.loads(out.read_text(encoding="utf-8"))
    assert "mode_comparison" in report
    assert set(report["mode_comparison"].keys()) == set(MODES)
    assert report["validation_status"] == "local_only"


# 6) 报告不落代码正文/凭据
def test_report_no_sensitive_fields(monkeypatch):
    monkeypatch.setenv("USE_MILVUS", "false")
    _seed()
    cases = load_cases(str(_DATASET))
    report = evaluate_dataset(_ctx(), cases, top_k=8, retrieval_mode="hybrid")
    raw = json.dumps(report, ensure_ascii=False).lower()
    for sensitive in ("password", "jwt", "cookie", "recovery", "secret_key", "def "):
        assert sensitive not in raw