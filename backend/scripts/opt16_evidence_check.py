#!/usr/bin/env python
r"""OPT-16 简历成果证据包校验：声明 → 归档证据文件的机械可核对性门禁。

背景：简历/面试材料中每一条「成果声明」都必须能指向仓库内真实存在的
证据文件，且命中文件中的字面 marker（指标、ID、哈希等），并标注验证环境
等级（kind），防止叙述漂移为不可核验的宣称。

检查项：
- A `schema/claims/boundaries` 结构：schema 版本、声明 ID 唯一（C\d+）、
  kind 枚举、声明文本与 metrics 非空；边界条目（B\d+）同样入档；
- B `evidence_missing` / `evidence_outside_repo`：evidence_files 均须为
  仓库内真实文件（拒绝绝对路径与 `..` 逃逸）；
- C `marker_file_missing` / `marker_missing`：markers 的 must_contain 必须
  字面命中目标文件（UTF-8/BOM 容忍），miss 即违规；
- D `doc_missing_claim`：主文档必须收录全部声明 ID 与边界 ID（防索引与
  叙述脱钩）。

退出码：0 = 无违规；1 = 有违规。stdout 输出 JSON 报告（供测试断言与
`assets/opt16/` 归档）；`--json-out` 可另存到文件。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INDEX = (
    REPO_ROOT / "docs" / "project-development" / "assets" / "opt16" / "opt16_evidence_index.json"
)
DEFAULT_DOC = REPO_ROOT / "docs" / "project-development" / "128-opt16-resume-evidence-pack.md"

_SCHEMA = "opt16-evidence-index-v1"
_REPORT_SCHEMA = "opt16-evidence-check-v1"
_KINDS = frozenset({"production_cloud", "local_real_ext", "local_offline", "synthetic"})
_BOUNDARY_KINDS = frozenset({"not_collected", "not_implemented", "limitation"})
_CLAIM_ID = re.compile(r"^C\d{2,3}$")
_BOUNDARY_ID = re.compile(r"^B\d{2,3}$")


def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT)).replace("\\", "/")
    except ValueError:
        return str(path)


def _inside_repo(raw: str) -> bool:
    p = Path(raw)
    if p.is_absolute():
        return False
    resolved = (REPO_ROOT / raw).resolve()
    try:
        resolved.relative_to(REPO_ROOT)
    except ValueError:
        return False
    return True


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8-sig", errors="strict")
    except (OSError, UnicodeDecodeError):
        return None


def check(index_path: Path, doc_path: Path) -> dict:
    violations: list[dict] = []

    def v(claim_id: str | None, check: str, detail: str) -> None:
        item = {"check": check, "detail": detail}
        if claim_id:
            item["claim_id"] = claim_id
        violations.append(item)

    raw = _read_text(index_path)
    if raw is None:
        v(None, "index_unreadable", f"索引不可读: {_rel(index_path)}")
        return _report(index_path, doc_path, 0, 0, 0, violations)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        v(None, "index_unreadable", f"索引 JSON 解析失败: {exc}")
        return _report(index_path, doc_path, 0, 0, 0, violations)

    if data.get("schema") != _SCHEMA:
        v(None, "schema_invalid", f"schema 期望 {_SCHEMA}，实际 {data.get('schema')!r}")

    claims = data.get("claims")
    boundaries = data.get("boundaries")
    if not isinstance(claims, list) or not claims:
        v(None, "claims_invalid", "claims 必须为非空列表")
        claims = []
    if not isinstance(boundaries, list) or not boundaries:
        v(None, "boundaries_invalid", "boundaries 必须为非空列表")
        boundaries = []

    seen_ids: set[str] = set()
    total_markers = 0
    for claim in claims:
        cid = str(claim.get("id", "?"))
        if not _CLAIM_ID.match(cid):
            v(cid, "claim_id_invalid", f"声明 ID 需匹配 C\\d+: {cid!r}")
        if cid in seen_ids:
            v(cid, "claim_id_duplicate", f"声明 ID 重复: {cid}")
        seen_ids.add(cid)
        kind = claim.get("kind")
        if kind not in _KINDS:
            v(cid, "kind_invalid", f"kind 不在枚举 {sorted(_KINDS)}: {kind!r}")
        if not str(claim.get("statement", "")).strip():
            v(cid, "statement_empty", "statement 不得为空")
        metrics = claim.get("metrics")
        if not isinstance(metrics, dict) or not metrics:
            v(cid, "metrics_invalid", "metrics 必须为非空对象")
        elif any(val is None for val in metrics.values()):
            v(cid, "metrics_invalid", "metrics 值不得为 null")

        for rel in claim.get("evidence_files") or []:
            if not _inside_repo(str(rel)):
                v(cid, "evidence_outside_repo", f"证据路径越出仓库: {rel}")
                continue
            if not (REPO_ROOT / rel).is_file():
                v(cid, "evidence_missing", f"证据文件不存在: {rel}")

        markers = claim.get("markers")
        if not isinstance(markers, list) or not markers:
            v(cid, "markers_invalid", "markers 必须为非空列表")
            markers = []
        for marker in markers:
            total_markers += 1
            mfile = str(marker.get("file", ""))
            needle = str(marker.get("must_contain", ""))
            if not needle:
                v(cid, "markers_invalid", f"marker 缺少 must_contain: {mfile}")
                continue
            if not _inside_repo(mfile):
                v(cid, "evidence_outside_repo", f"marker 文件越出仓库: {mfile}")
                continue
            mpath = REPO_ROOT / mfile
            if not mpath.is_file():
                v(cid, "marker_file_missing", f"marker 目标不存在: {mfile}")
                continue
            text = _read_text(mpath)
            if text is None:
                v(cid, "marker_file_unreadable", f"marker 目标不可读(非 UTF-8?): {mfile}")
                continue
            if needle not in text:
                v(cid, "marker_missing", f"{mfile} 未命中字面 marker: {needle[:80]!r}")

    for boundary in boundaries:
        bid = str(boundary.get("id", "?"))
        if not _BOUNDARY_ID.match(bid):
            v(bid, "boundary_id_invalid", f"边界 ID 需匹配 B\\d+: {bid!r}")
        if boundary.get("kind") not in _BOUNDARY_KINDS:
            v(bid, "boundary_kind_invalid", f"边界 kind 非法: {boundary.get('kind')!r}")
        if not str(boundary.get("statement", "")).strip():
            v(bid, "boundary_statement_empty", "边界 statement 不得为空")
        if not str(boundary.get("note", "")).strip():
            v(bid, "boundary_note_empty", "边界 note 不得为空")

    doc_text = _read_text(doc_path)
    if doc_text is None:
        v(None, "doc_unreadable", f"主文档不可读: {_rel(doc_path)}")
    else:
        for item in [*claims, *boundaries]:
            iid = str(item.get("id", "?"))
            if f"`{iid}`" not in doc_text and f" {iid} " not in doc_text and not doc_text.startswith(iid):
                v(iid, "doc_missing_claim", f"主文档未收录 ID: {iid}")

    return _report(index_path, doc_path, len(claims), len(boundaries), total_markers, violations)


def _report(
    index_path: Path,
    doc_path: Path,
    total_claims: int,
    total_boundaries: int,
    total_markers: int,
    violations: list[dict],
) -> dict:
    return {
        "schema": _REPORT_SCHEMA,
        "index": _rel(index_path),
        "doc": _rel(doc_path),
        "total_claims": total_claims,
        "total_boundaries": total_boundaries,
        "total_markers": total_markers,
        "violation_count": len(violations),
        "violations": violations,
        "ok": not violations,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="OPT-16 简历成果证据包校验器")
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX, help="证据索引 JSON 路径")
    parser.add_argument("--doc", type=Path, default=DEFAULT_DOC, help="主文档路径（须收录全部 ID）")
    parser.add_argument("--json-out", type=Path, default=None, help="将 JSON 报告另存到文件")
    args = parser.parse_args()

    report = check(args.index, args.doc)
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
