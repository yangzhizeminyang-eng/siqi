"""对指定仓库索引运行版本化 RAG 评测集。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate a repository RAG index.")
    parser.add_argument("--dataset", type=Path, default=ROOT / "evaluation" / "rag_eval_cases.json")
    parser.add_argument("--repository-id", required=True)
    parser.add_argument("--index-id", required=True)
    parser.add_argument("--head-commit", default="")
    parser.add_argument("--mode", choices=("vector", "hybrid"), default="vector")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument(
        "--query-expansion",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="覆盖 RAG_QUERY_EXPANSION，开启或关闭 Query Rewrite / Multi-Query。",
    )
    parser.add_argument(
        "--rerank",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="覆盖 RAG_RERANK_ENABLED，开启或关闭 LLM rerank。",
    )
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.top_k < 1:
        raise SystemExit("--top-k 必须大于 0")
    from app import main as app_main
    from app.core.runtime_config import load_runtime_settings
    from app.db.reads import code_chunks_of_index, get_index
    from app.retrieval.evaluation import evaluate_retrieval, load_eval_dataset
    from app.retrieval.llamaindex_backend import query_index_from_chunks

    index = get_index(args.index_id)
    if not index or index.repository_id != args.repository_id:
        raise SystemExit("指定 index_id 不属于 repository_id 或不存在")
    chunks = code_chunks_of_index(args.index_id)
    if not chunks:
        raise SystemExit("指定索引没有可评测代码切片")
    head_commit = args.head_commit or index.commit_sha
    dataset = load_eval_dataset(args.dataset)
    if dataset.repository_id and dataset.repository_id != args.repository_id:
        raise SystemExit("评测集 repository_id 与命令参数不一致")
    if dataset.index_id and dataset.index_id != args.index_id:
        raise SystemExit("评测集 index_id 与命令参数不一致")
    if dataset.commit_sha and dataset.commit_sha != head_commit:
        raise SystemExit("评测集 commit_sha 与待评测索引版本不一致")
    settings = load_runtime_settings()
    query_expansion = settings.rag_query_expansion if args.query_expansion is None else args.query_expansion
    rerank = settings.rag_rerank_enabled if args.rerank is None else args.rerank

    def retrieve(query_text: str, top_k: int):
        return query_index_from_chunks(
            chunks,
            repository_id=args.repository_id,
            index_id=args.index_id,
            collection_name=app_main.index_collection_name(args.index_id),
            query_text=query_text,
            top_k=top_k,
            threshold=0.0,
            head_commit=head_commit,
            retrieval_mode=args.mode,
            query_expansion=query_expansion,
            rerank=rerank,
        )

    report = evaluate_retrieval(dataset.cases, retrieve, ks=(1, 3, args.top_k)).to_dict()
    report["evaluation"] = {
        "dataset": dataset.name,
        "repository_id": args.repository_id,
        "index_id": args.index_id,
        "commit_sha": head_commit,
        "mode": args.mode,
        "top_k": args.top_k,
        "query_expansion": query_expansion,
        "rerank": rerank,
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
