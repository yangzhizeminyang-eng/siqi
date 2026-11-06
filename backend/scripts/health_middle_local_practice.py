"""Local-only health-middle vector/hybrid practice with Milvus Lite.

No production DB/Redis/Milvus, no real model, no external writes.
Mock embeddings are intentional: this verifies Milvus Lite write/query wiring
and lifecycle gating, not retrieval quality.

Note: pymilvus reads MILVUS_URI at import time and rejects local ``.db`` paths.
The app derives its local Lite path from DATA_DIR instead, so this script
removes MILVUS_URI before importing app modules.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Sequence


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Local-only health-middle Milvus Lite practice")
    parser.add_argument("--repo-root", required=True, help="local health-middle git clone")
    parser.add_argument("--data-dir", default="", help="local scratch dir (default: backend/.data/health_middle_local_practice)")
    parser.add_argument("--out", default="", help="write JSON here instead of stdout")
    parser.add_argument("--top-k", type=int, default=8)
    parser.add_argument("--modes", default="vector,hybrid,bm25")
    return parser.parse_args(argv)


def _prepare_environment(repo_root: Path, data_dir: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    os.environ.update({
        "APP_ENV": "local",
        "ENV_FILE": str(Path("tests/.env.hermetic-nonexistent").resolve()),
        "USE_SQLALCHEMY": "false",
        "USE_MILVUS": "true",
        "DATA_DIR": str(data_dir),
        "MODEL_MOCK_FALLBACK": "true",
        "RAG_MOCK_FALLBACK": "false",
        "RAG_QUERY_EXPANSION": "false",
        "RAG_QUERY_LLM": "false",
        "RAG_RERANK_ENABLED": "false",
    })
    for key in ("DATABASE_URL", "CELERY_BROKER_URL", "REDIS_URL", "GITEE_TOKEN",
                "ZHIPU_API_KEY", "MILVUS_URI", "MILVUS_TOKEN"):
        os.environ.pop(key, None)

    import dotenv
    dotenv.load_dotenv = lambda *args, **kwargs: False  # noqa: E731


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    repo_root = Path(args.repo_root).expanduser().resolve()
    if not repo_root.is_dir():
        print(f"repo root not found: {repo_root}", file=sys.stderr)
        return 2
    data_dir = Path(args.data_dir).expanduser().resolve() if args.data_dir else (
        Path(__file__).resolve().parents[1] / ".data" / "health_middle_local_practice")
    _prepare_environment(repo_root, data_dir)

    from app import main
    from app.core import runtime_config
    from app.enterprise_rag import index_lifecycle
    from app.enterprise_rag.evaluation import evaluate_dataset, load_cases
    from app.retrieval import llamaindex_backend as rag_backend
    from app.services import embedding_service
    from evaluation.enterprise_rag.health_middle import EVAL_SET, REPO_URL, build_real_index
    from scripts.evaluate_enterprise_rag import _build_context

    embedding_service.probe_embedding_dimension = lambda: len(
        embedding_service.mock_embed_texts(["probe"])[0])

    chunks = build_real_index(repo_root, tenant_id="tenant_default",
                              persist_vector=True, require_milvus=False)
    if not chunks:
        print("no chunks parsed from repository", file=sys.stderr)
        return 3
    record = main.REPOSITORY_INDEXES[chunks[0].index_id]
    index_lifecycle.register_build(
        index_id=record.id, tenant_id=record.tenant_id,
        repository_id=record.repository_id, project_id=record.project_id,
        base_branch=record.base_branch, commit_sha=record.commit_sha,
        collection_name=record.milvus_collection,
    )
    lifecycle = index_lifecycle.mark_ready(record, chunk_count=len(chunks))
    identity = {
        "commit_sha": str(chunks[0].commit_sha),
        "index_id": str(chunks[0].index_id),
        "collection_name": str(chunks[0].milvus_collection),
    }

    probe_chunk = next((c for c in chunks if c.symbol_name and c.symbol_name != "__module__"), chunks[0])
    probe_results = rag_backend.query_index_from_chunks(
        chunks,
        repository_id=record.repository_id,
        index_id=record.id,
        collection_name=record.milvus_collection,
        query_text=probe_chunk.symbol_name or probe_chunk.file_path,
        top_k=5,
        threshold=-1.0,
        head_commit=None,
        max_chunks_per_file=None,
        retrieval_mode="vector",
    )
    vector_store = rag_backend.build_vector_store(record.milvus_collection, overwrite=False)
    stats = vector_store.client.get_collection_stats(record.milvus_collection)
    row_count = int(stats.get("row_count", 0))
    probe_sources = sorted({r.source for r in probe_results})
    if lifecycle.status != "ready" or row_count <= 0 or not probe_results or probe_sources != ["milvus"]:
        print(
            "local Milvus Lite probe failed: "
            f"lifecycle={lifecycle.status} rows={row_count} sources={probe_sources}",
            file=sys.stderr,
        )
        return 4

    cases = load_cases(str(EVAL_SET))
    ctx = _build_context("user_admin")
    modes = [m.strip().lower() for m in args.modes.split(",") if m.strip()]
    results = {}
    for mode in modes:
        report = evaluate_dataset(ctx, cases, top_k=args.top_k, retrieval_mode=mode,
                                  require_milvus=False, index_identity=identity,
                                  repository_url=REPO_URL)
        results[mode] = {
            "validation_status": report.get("validation_status"),
            "vector_backend": report.get("vector_backend"),
            "milvus_verified": report.get("milvus_verified"),
            "fallback_used": report.get("fallback_used"),
            "summary": report.get("summary"),
            "needs_improvement": report.get("needs_improvement"),
        }

    payload = {
        "repository": REPO_URL,
        "commit_sha": identity["commit_sha"],
        "index_id": identity["index_id"],
        "collection_name": identity["collection_name"],
        "chunks": len(chunks),
        "milvus_lite_path": runtime_config.load_runtime_settings().milvus_path,
        "milvus_collection_row_count": row_count,
        "lifecycle": {
            "status": lifecycle.status,
            "embedding_model": lifecycle.embedding.model,
            "embedding_dimension": lifecycle.embedding.dimension,
            "embedding_fingerprint": lifecycle.embedding.fingerprint,
        },
        "direct_vector_probe": {
            "query": probe_chunk.symbol_name or probe_chunk.file_path,
            "expected_path": probe_chunk.file_path,
            "sources": sorted({r.source for r in probe_results}),
            "top": [
                {"file_path": r.file_path, "symbol_name": r.symbol_name,
                 "score": r.score, "source": r.source}
                for r in probe_results[:3]
            ],
        },
        "mock_embedding": True,
        "quality_claim": False,
        "dataset_size": len(cases),
        "results": results,
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.out:
        out = Path(args.out).expanduser()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
