"""Milvus Standalone 真实写入/召回（跨进程）测试。

默认 skip：真实外部依赖，仅当 env `MILVUS_STANDALONE_TEST=true` 且 Milvus Standalone 可达才跑。
验证：本地 git-clone 仓 → LlamaIndex parse/chunk → build_index_from_chunks 写 Standalone collection →
query source=milvus（非 fallback）→ 子进程新连接 load 后再次 query 不 released。
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest

skip = os.getenv("MILVUS_STANDALONE_TEST") != "true" or not os.getenv("MILVUS_URI")
pytestmark = pytest.mark.skipif(skip, reason="MILVUS_STANDALONE_TEST 未开启或 MILVUS_URI 未配置")


def test_milvus_standalone_write_and_cross_process_recall(tmp_path):
    import pathlib

    from pymilvus import connections, utility
    from app.retrieval import parsing
    from app.retrieval.llamaindex_backend import (
        build_index_from_chunks, embedding_dim, query_index_from_chunks,
    )
    from app.services import repo_mirror

    uri = os.environ["MILVUS_URI"]
    dim = embedding_dim()  # 与写入一致的向量维度（可能真 Zhipu 2048 或测试态 mock dim）
    print("asserting dim=", dim)
    # 本地 git 仓
    repo = tmp_path / "origin"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "master"], cwd=repo, check=True)
    (repo / "README.md").write_text("# standalone deterministic key\n", encoding="utf8")
    (repo / "a.py").write_text("def f(k):\n    import hashlib\n    return hashlib.sha256(k.encode()).hexdigest()\n", encoding="utf8")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "c1"], check=True)
    head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()

    mirror = repo_mirror.ensure_mirror("repo_mv_t", str(repo), token=None, base_dir=tmp_path / "c")
    wt = repo_mirror.checkout_commit(mirror, head, (tmp_path / "wt") / head[:8])
    mfiles = repo_mirror.walk_snapshot(wt, blocked_paths=[], max_files=50)
    files = [type("F", (), {"path": f.path,
                            "language": "python" if f.path.endswith(".py") else "markdown",
                            "content": f.content})() for f in mfiles]
    idx = "idx_mv_std_t"
    chunks = parsing.parse_and_chunk(files, repository_id="repo_mv_t", index_id=idx, commit_sha=head, blocked_paths=[])
    assert chunks

    collection = f"col_mv_std_t_{int(time.time())}"
    build_index_from_chunks(chunks, repository_id="repo_mv_t", index_id=idx, collection_name=collection, overwrite=True)
    connections.connect(alias="default", uri=uri)
    try:
        assert collection in utility.list_collections(), "collection 应写入 Milvus Standalone"
    finally:
        connections.disconnect("default")  # 不污染同进程内其它 Milvus-Lite 测试的默认连接

    res = query_index_from_chunks(list(chunks), repository_id="repo_mv_t", index_id=idx,
                                  collection_name=collection, query_text="deterministic", top_k=5, threshold=0.0, head_commit=head)
    assert res and all(r.source == "milvus" for r in res), f"source 应=milvus，实际 {sorted({r.source for r in res})}"
    # 跨进程召回见 scripts/milvus_standalone_verify.py（新连接 load 后 query source=milvus、不 released，已真实 PASS）