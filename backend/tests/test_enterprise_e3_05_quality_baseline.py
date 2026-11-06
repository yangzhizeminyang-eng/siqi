"""企业版 E3-05 真实评测集与质量基线报告测试。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.enterprise_rag.evaluation import (
    RECOMMENDED_THRESHOLDS,
    load_cases,
    evaluate_dataset,
    quality_gate,
)

_BACKEND = Path(__file__).resolve().parents[1]
_EVAL = _BACKEND / "evaluation" / "enterprise_rag"
_DATASET = _EVAL / "e3_05_baseline.jsonl"
_DSET_TEXT = _DATASET.read_text(encoding="utf-8")

_ALLOWED_TAGS = {
    "code", "symbol", "error_code", "api", "config", "file_path", "natural_language",
    "cross_file", "source_type", "fresh", "stale", "empty", "boundary", "nl",
}
_ALLOWED_DIFF = {"easy", "medium", "hard"}


def _seed():
    from evaluation.enterprise_rag.seed import seed_baseline

    seed_baseline()


# 1) 数据集加载 / 规模 / case_id 唯一
def test_dataset_loads_and_has_enough_cases():
    cases = load_cases(str(_DATASET))
    assert len(cases) >= 30


def test_case_fields_complete_and_case_id_unique():
    cases = load_cases(str(_DATASET))
    ids = [c.case_id for c in cases]
    assert len(ids) == len(set(ids)), "case_id 重复"
    for case in cases:
        assert case.case_id and case.query and case.project_id and case.repository_id
        assert case.expected_status in (200, 403, 422)
        assert case.rationale
        if case.expected_status == 200:
            assert case.commit_sha or case.ref


# 3) tags / difficulty 合法
def test_tags_and_difficulty_valid():
    cases = load_cases(str(_DATASET))
    for case in cases:
        assert set(case.tags) <= _ALLOWED_TAGS, case.case_id
        assert case.difficulty in _ALLOWED_DIFF, case.case_id


# 4) 数据集不含敏感/明显代码正文
def test_dataset_no_sensitive_or_code_body():
    low = _DSET_TEXT.lower()
    for sensitive in ("password", "jwt", "cookie", "recovery", "secret_key"):
        assert sensitive not in low
    assert "def " not in low  # 不落代码正文


# 5/6/7) CLI 读数据集并输出合格报告
def test_cli_produces_report(tmp_path, monkeypatch):
    _seed()
    out = tmp_path / "report.json"
    env = {**os.environ, "USE_SQLALCHEMY": "false", "MODEL_MOCK_FALLBACK": "true", "PYTHONIOENCODING": "utf-8", "USE_MILVUS": "false"}
    proc = subprocess.run(
        [sys.executable, "-m", "scripts.evaluate_enterprise_rag",
         str(_DATASET), "-o", str(out), "--memory-seed", "--top-k", "8"],
        cwd=str(_BACKEND), env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["dataset_size"] >= 30
    for key in ("summary", "metrics_by_tag", "failed_cases", "config_snapshot",
                "recommended_thresholds", "needs_improvement"):
        assert key in report
    for metric in ("recall_at_k", "precision_at_k", "mrr", "ndcg_at_k",
                   "freshness_rate", "empty_result_rate", "unauthorized_pass_rate"):
        assert metric in report["summary"]
    raw = json.dumps(report, ensure_ascii=False).lower()
    for sensitive in ("password", "jwt", "cookie", "recovery", "secret_key"):
        assert sensitive not in raw


# 8) 阈值 / needs_improvement 逻辑
def test_quality_gate_logic():
    ok = quality_gate({"recall_at_k": 0.8, "mrr": 0.5, "ndcg_at_k": 0.7})
    assert ok["needs_improvement"] is False and ok["below"] == {}
    poor = quality_gate({"recall_at_k": 0.8, "mrr": 0.1, "ndcg_at_k": 0.7})
    assert poor["needs_improvement"] is True and set(poor["below"]) == {"mrr"}
    assert set(RECOMMENDED_THRESHOLDS) == {"recall_at_k", "precision_at_k", "mrr", "ndcg_at_k"}


# 9) 403/422 边界样本进入 unauthorized_pass_rate
def test_boundary_403_422_in_unauthorized_pass_rate(monkeypatch):
    monkeypatch.setenv("USE_MILVUS", "false")
    _seed()
    cases = load_cases(str(_DATASET))
    boundary = [c for c in cases if c.expected_status in (403, 422)]
    assert boundary, "数据集应含 403/422 边界样本"
    report = evaluate_dataset(_build_ctx(), boundary, top_k=8)
    assert report["summary"]["unauthorized_pass_rate"] == 1.0


# 10) 复用 E3-04 主链路：命中样本 recall 真实>0
def test_reuses_e3_04_chain_and_scores_hits(monkeypatch):
    monkeypatch.setenv("USE_MILVUS", "false")
    _seed()
    cases = load_cases(str(_DATASET))
    report = evaluate_dataset(_build_ctx(), cases, top_k=8)
    assert report["summary"]["recall_at_k"] > 0
    # 至少一个 code 命中样本 recall=1.0（精确符号）
    hit = next((c for c in report["per_case_results"]
                if c["case_id"] == "q01"), None)
    assert hit is not None and hit["recall_at_k"] == 1.0


def _build_ctx():
    from app.tenancy import context as tc
    from app.tenancy import store as ts

    return tc.build_context(ts.active_membership_for_user("user_admin"))