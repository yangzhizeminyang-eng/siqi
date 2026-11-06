"""离线 RAG 检索质量评测。"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Sequence


@dataclass(frozen=True)
class RetrievalEvalCase:
    case_id: str
    query: str
    expected_chunk_ids: tuple[str, ...] = ()
    expected_file_paths: tuple[str, ...] = ()

    def expected_targets(self) -> set[str]:
        return set(self.expected_chunk_ids or self.expected_file_paths)

    def target_kind(self) -> str:
        return "chunk_id" if self.expected_chunk_ids else "file_path"


@dataclass(frozen=True)
class RetrievalEvalDataset:
    name: str
    cases: tuple[RetrievalEvalCase, ...]
    repository_id: str | None = None
    index_id: str | None = None
    commit_sha: str | None = None


@dataclass(frozen=True)
class RetrievalCaseMetrics:
    case_id: str
    first_relevant_rank: int | None
    recall_at: dict[str, float]
    precision_at: dict[str, float]
    ndcg_at: dict[str, float]


@dataclass(frozen=True)
class RetrievalEvaluationMetrics:
    total_cases: int
    recall_at: dict[str, float]
    precision_at: dict[str, float]
    ndcg_at: dict[str, float]
    mrr: float
    cases: tuple[RetrievalCaseMetrics, ...]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["cases"] = [asdict(case) for case in self.cases]
        return data


def load_eval_dataset(path: str | Path) -> RetrievalEvalDataset:
    """加载版本化 JSON 评测集，拒绝无 Query 或无相关性标注的脏样本。"""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    items = data.get("cases") if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise ValueError("评测集必须是数组或包含 cases 数组的 JSON 对象")
    dataset_name = Path(path).stem
    repository_id = index_id = commit_sha = None
    if isinstance(data, dict):
        dataset_name = str(data.get("name") or dataset_name).strip() or dataset_name
        repository_id = str(data.get("repository_id") or "").strip() or None
        index_id = str(data.get("index_id") or "").strip() or None
        commit_sha = str(data.get("commit_sha") or "").strip() or None
        if any((repository_id, index_id, commit_sha)) and not all((repository_id, index_id, commit_sha)):
            raise ValueError("评测集绑定仓库时必须同时提供 repository_id、index_id、commit_sha")
    cases: list[RetrievalEvalCase] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"评测样本 #{index} 必须是对象")
        case_id = str(item.get("id") or item.get("case_id") or "").strip()
        query = str(item.get("query") or "").strip()
        chunk_ids = tuple(str(value).strip() for value in item.get("expected_chunk_ids", []) if str(value).strip())
        file_paths = tuple(str(value).strip() for value in item.get("expected_file_paths", []) if str(value).strip())
        if not case_id or not query or (not chunk_ids and not file_paths):
            raise ValueError(f"评测样本 #{index} 缺少 id、query 或预期相关文件/chunk")
        if case_id in seen_ids:
            raise ValueError(f"评测样本 id 重复：{case_id}")
        seen_ids.add(case_id)
        cases.append(
            RetrievalEvalCase(
                case_id=case_id,
                query=query,
                expected_chunk_ids=chunk_ids,
                expected_file_paths=file_paths,
            )
        )
    return RetrievalEvalDataset(
        name=dataset_name,
        cases=tuple(cases),
        repository_id=repository_id,
        index_id=index_id,
        commit_sha=commit_sha,
    )


def load_eval_cases(path: str | Path) -> list[RetrievalEvalCase]:
    """兼容旧调用方，仅返回评测样本。"""
    return list(load_eval_dataset(path).cases)


def _matches(case: RetrievalEvalCase, candidate: Any) -> bool:
    field = case.target_kind()
    return str(getattr(candidate, field, "") or "") in case.expected_targets()


def _relevance(case: RetrievalEvalCase, retrieved: Sequence[Any]) -> list[int]:
    """同一标注目标只在首次出现的位置计为相关，避免多 chunk 文件虚增排序指标。"""
    field = case.target_kind()
    matched_targets: set[str] = set()
    relevance: list[int] = []
    for candidate in retrieved:
        target = str(getattr(candidate, field, "") or "")
        if _matches(case, candidate) and target not in matched_targets:
            matched_targets.add(target)
            relevance.append(1)
        else:
            relevance.append(0)
    return relevance


def _mean(values: Sequence[float]) -> float:
    return round(sum(values) / len(values), 6) if values else 0.0


def _ndcg(relevance: Sequence[int], *, expected_count: int, k: int) -> float:
    dcg = sum(value / math.log2(rank + 1) for rank, value in enumerate(relevance[:k], start=1))
    ideal_count = min(expected_count, k)
    ideal_dcg = sum(1.0 / math.log2(rank + 1) for rank in range(1, ideal_count + 1))
    return dcg / ideal_dcg if ideal_dcg else 0.0


def evaluate_retrieval(
    cases: Sequence[RetrievalEvalCase],
    retrieve: Callable[[str, int], Sequence[Any]],
    *,
    ks: tuple[int, ...] = (1, 3, 5),
) -> RetrievalEvaluationMetrics:
    """以样本标注为真值评估召回、精度、排序质量。"""
    normalized_ks = tuple(sorted({int(k) for k in ks if int(k) > 0}))
    if not normalized_ks:
        raise ValueError("ks 至少包含一个正整数")
    max_k = max(normalized_ks)
    per_case: list[RetrievalCaseMetrics] = []
    recall_values: dict[str, list[float]] = {str(k): [] for k in normalized_ks}
    precision_values: dict[str, list[float]] = {str(k): [] for k in normalized_ks}
    ndcg_values: dict[str, list[float]] = {str(k): [] for k in normalized_ks}
    reciprocal_ranks: list[float] = []

    for case in cases:
        expected = case.expected_targets()
        retrieved = list(retrieve(case.query, max_k))[:max_k]
        relevance = _relevance(case, retrieved)
        first_rank = next((index for index, value in enumerate(relevance, start=1) if value), None)
        reciprocal_ranks.append(1.0 / first_rank if first_rank else 0.0)
        case_recall: dict[str, float] = {}
        case_precision: dict[str, float] = {}
        case_ndcg: dict[str, float] = {}
        for k in normalized_ks:
            prefix = retrieved[:k]
            matched = {
                str(getattr(candidate, case.target_kind(), "") or "")
                for candidate in prefix
                if _matches(case, candidate)
            }
            recall = len(matched) / len(expected)
            precision = sum(relevance[:k]) / k
            ndcg = _ndcg(relevance, expected_count=len(expected), k=k)
            key = str(k)
            case_recall[key] = round(recall, 6)
            case_precision[key] = round(precision, 6)
            case_ndcg[key] = round(ndcg, 6)
            recall_values[key].append(recall)
            precision_values[key].append(precision)
            ndcg_values[key].append(ndcg)
        per_case.append(
            RetrievalCaseMetrics(
                case_id=case.case_id,
                first_relevant_rank=first_rank,
                recall_at=case_recall,
                precision_at=case_precision,
                ndcg_at=case_ndcg,
            )
        )
    return RetrievalEvaluationMetrics(
        total_cases=len(cases),
        recall_at={key: _mean(values) for key, values in recall_values.items()},
        precision_at={key: _mean(values) for key, values in precision_values.items()},
        ndcg_at={key: _mean(values) for key, values in ndcg_values.items()},
        mrr=_mean(reciprocal_ranks),
        cases=tuple(per_case),
    )
