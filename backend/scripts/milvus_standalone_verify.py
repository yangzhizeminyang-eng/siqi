"""Milvus Standalone 真实写入/召回复验（不用 Lite）。"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import tempfile
import time

try:
    sys.stdout.reconfigure(errors="replace")
except Exception:
    pass

os.environ.setdefault("USE_MILVUS", "true")
os.environ.setdefault("RAG_MOCK_FALLBACK", "false")
os.environ.setdefault("MILVUS_URI", "http://127.0.0.1:19530")
os.environ.setdefault("USE_SQLALCHEMY", "false")
os.environ.setdefault("DATA_DIR", ".data")


def _sh(args, cwd):
    r = subprocess.run(args, cwd=str(cwd), capture_output=True, text=True)
    assert r.returncode == 0, (args, r.stderr)


def _make_origin(tmp):
    repo = tmp / "origin"
    repo.mkdir()
    _sh(["git", "init", "-q", "-b", "master"], repo)
    (repo / "README.md").write_text("# Milvus Standalone verify deterministic code\n", encoding="utf8")
    (repo / "app.py").write_text("def process(key):\n    return {'ok': True, 'key': key}\n", encoding="utf8")
    _sh(["git", "-C", str(repo), "add", "-A"], repo)
    _sh(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "c1"], repo)
    head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    return repo, head


def _query_chunks(query_text, head, collection, index_id, chunks):
    from app.retrieval.llamaindex_backend import query_index_from_chunks
    res = query_index_from_chunks(chunks, repository_id="repo_mv", index_id=index_id,
                                  collection_name=collection, query_text=query_text, top_k=5, threshold=0.0, head_commit=head)
    return res


def main():
    from scripts.authorization_gate import require_authorization

    require_authorization(
        os.environ.get("AUTHORIZATION_OPERATION", ""),
        os.environ.get("AUTHORIZATION_LOG", "") or None,
    )
    from pymilvus import connections, utility
    from app.retrieval import parsing
    from app.retrieval.llamaindex_backend import build_index_from_chunks
    from app.services import repo_mirror

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="msv_"))
    repo, head = _make_origin(tmp)
    mirror = repo_mirror.ensure_mirror("repo_mv", str(repo), token=None, base_dir=tmp / "cache")
    wt = repo_mirror.checkout_commit(mirror, head, (tmp / "wt") / head[:8])
    mfiles = repo_mirror.walk_snapshot(wt, blocked_paths=[], max_files=50)
    files = [type("F", (), {"path": f.path,
                            "language": "python" if f.path.endswith(".py") else "markdown",
                            "content": f.content})() for f in mfiles]
    index_id = "idx_mv_standalone"
    chunks = parsing.parse_and_chunk(files, repository_id="repo_mv", index_id=index_id, commit_sha=head, blocked_paths=[])
    print("chunks:", len(chunks), "index_id=", index_id, "head=", head[:10])

    collection = f"col_mv_std_{int(time.time())}"
    print("MILVUS_URI=", os.environ.get("MILVUS_URI"))
    build_index_from_chunks(chunks, repository_id="repo_mv", index_id=index_id, collection_name=collection, overwrite=True)
    print("build_index_from_chunks done ->", collection)

    connections.connect(alias="default", uri="http://127.0.0.1:19530")
    cols = utility.list_collections()
    exists = collection in cols
    print("collection exists in Standalone (Attu可见):", exists)
    if not exists:
        print("MILVUS_FAIL collection missing"); return 2

    # 同进程 query → source=milvus（chunks 非空以走 milvus 路径）
    res = _query_chunks("deterministic code", head, collection, index_id, list(chunks))
    print("query(first): n=", len(res), "sources=", sorted({r.source for r in res}))
    if not res or not all(r.source == "milvus" for r in res):
        print("MILVUS_FAIL query non-milvus"); return 2

    # 跨进程：新进程/新连接，chunks 用非空 dummy，显式 load 后再 query，验证不 released + source=milvus
    sub_code = (
        "import os,sys\n"
        "os.environ['MILVUS_URI']='http://127.0.0.1:19530'; os.environ['USE_MILVUS']='true'\n"
        "from llama_index.vector_stores.milvus import MilvusVectorStore\n"
        "from app.retrieval.llamaindex_backend import ensure_milvus_collection_loaded as L, query_index_from_chunks as Q\n"
        f"ci='{collection}'; idx='{index_id}'; head='{head}'\n"
        "vs=MilvusVectorStore(uri='http://127.0.0.1:19530', collection_name=ci, dim=2048, use_async_client=False)\n"
        "L(vs, ci)\n"
        "dummy=[{'content':'x'}]\n"
        "res=Q(dummy, repository_id='repo_mv', index_id=idx, collection_name=ci, query_text='deterministic code', top_k=5, threshold=0.0, head_commit=head)\n"
        "print('CROSS sources=', sorted({r.source for r in res}), 'n=', len(res))\n"
        "raise SystemExit(0 if res and all(r.source=='milvus' for r in res) else 3)\n"
    )
    p = subprocess.run([sys.executable, "-c", sub_code], capture_output=True, text=True, cwd=os.getcwd(), timeout=150)
    print("cross returncode=", p.returncode, "stdout=", (p.stdout or "").strip()[-160:].replace(chr(10), " "))
    if p.returncode != 0:
        print("MILVUS_FAIL cross-process", (p.stderr or "")[-200:]); return 3
    print("MILVUS_STANDALONE_PASS write+query source=milvus; cross-process no-released; collection in Attu")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())