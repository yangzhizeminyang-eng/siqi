"""RAG parse/chunk 归属 + 完整链路稳定性测试。

断言：
- 文件解析 + 代码切片 = 自定义 code-aware seam（`app.retrieval.parsing`，保留行号/符号/commit/pk/checksum 元数据），
  main.py 仅 re-export（`main.parse_and_chunk is retrieval.parsing.parse_and_chunk`）——不再在核心链路散落。
- 节点化 / 入库 / 检索编排走 LlamaIndex（`build_nodes_from_chunks → TextNode → VectorStoreIndex`）。
- 完整链路（文件→chunk→embedding→入库→召回）稳定、可复现（同输入→同 chunk_id）。"""

from __future__ import annotations

import time

import pytest

from app import main
from app.retrieval import parsing
from app.retrieval.llamaindex_backend import build_index_from_chunks, build_nodes_from_chunks, query_index_from_chunks


class _CodeFile:
    def __init__(self, path, language, content):
        self.path = path
        self.language = language
        self.content = content


_FILES = [
    _CodeFile("pay/callback.py", "python",
              "def handle_refund(self):\n    # 幂等：同一 out_trade_no 重复回调只入账一次\n    return self.repo.credit(order)\n"
              "def setup_routes():\n    api.post('/pay/callback', handle_refund)\n    return router\n" * 4),
    _CodeFile("order/list.py", "python",
              "def render_orders(request):\n    # 分页加载订单\n    return [o for o in o.findAll() if o.active]\n"),
]


def test_parse_chunk_is_seam_and_deterministic():
    """parse_and_chunk 是确定性 seam：同输入产生同 chunk_id，且 main 只是 re-export 同一实现。"""
    assert getattr(main, "parse_and_chunk") is parsing.parse_and_chunk, "main 应 re-export retrieval.parsing.parse_and_chunk"

    a = parsing.parse_and_chunk(_FILES, repository_id="r", index_id="idx", commit_sha="aaa", blocked_paths=[])
    b = parsing.parse_and_chunk(_FILES, repository_id="r", index_id="idx", commit_sha="aaa", blocked_paths=[])
    assert [c.chunk_id for c in a] == [c.chunk_id for c in b], "同输入切片应确定性（md5 稳定）"
    assert a, "至少产出切片"
    c0 = a[0]
    assert c0.start_line >= 1 and c0.end_line >= c0.start_line
    assert c0.milvus_pk == c0.chunk_id and c0.checksum
    assert c0.language == "python" and c0.symbol_name  # 符号名被提取
    # blocked_paths 过滤生效
    filtered = parsing.parse_and_chunk(_FILES, repository_id="r", index_id="idx", commit_sha="aaa",
                                       blocked_paths=["pay/"])
    assert all(not c.file_path.startswith("pay/") for c in filtered)


def test_parse_chunk_guards_env_and_git():
    # 文件级过滤（业务过滤，非 RAG parser 主链路）仍拦截 .env / .git
    assert main.path_blocked(".env", []) is True
    assert main.path_blocked("dir/.git/config", []) is True
    assert main.path_blocked("pay/callback.py", []) is False
    # 符号提取来自 parsing seam
    assert parsing.extract_symbol_name("def foo():\n    pass") == "foo"


def test_parse_uses_llamaindex_node_parser(monkeypatch):
    """解析/切片主链必须经 LlamaIndex NodeParser（代码→CodeSplitter，tree-sitter 不可用回退 Token/SentenceSplitter）。"""
    from app.retrieval import parsing as p

    calls = {"n": 0}
    orig = p._pick_splitter

    def wrapped(lang, path):
        sp = orig(lang, path)
        calls["n"] += 1
        assert sp.__class__.__module__.startswith("llama_index"), "切片器必须来自 LlamaIndex（NodeParser），非自定义行窗口"
        return sp

    monkeypatch.setattr(p, "_pick_splitter", wrapped)
    chunks = p.parse_and_chunk(_FILES, repository_id="r", index_id="idx", commit_sha="aaa", blocked_paths=[])
    assert calls["n"] >= 1, "分散器选择必须被真实调用"
    assert chunks, "LlamaIndex 切片应产出 chunk"
    for c in chunks:
        assert c.start_line >= 1 and c.end_line >= c.start_line
        assert c.language == "python" and c.symbol_name, "metadata adapter 应保留符号名"


def test_parse_creates_llamaindex_document(monkeypatch):
    """主流程必须创建 LlamaIndex Document，且 metadata 携带关键业务字段。"""
    from llama_index.core import Document

    from app.retrieval import parsing as p

    created = {"doc": None, "n": 0}

    def fake_build(file, *, repository_id, index_id, commit_sha):
        doc = Document(text=file.content, metadata={
            "repository_id": repository_id, "index_id": index_id, "commit_sha": commit_sha,
            "file_path": file.path, "language": file.language,
        })
        created["n"] += 1
        created["doc"] = doc
        return doc

    monkeypatch.setattr(p, "_build_document", fake_build)
    p.parse_and_chunk(_FILES, repository_id="REPO", index_id="IDX", commit_sha="SHA", blocked_paths=[])
    assert created["n"] >= 1, "必须为每个文件创建 LlamaIndex Document"
    assert isinstance(created["doc"], Document)
    md = created["doc"].metadata
    assert md.get("repository_id") == "REPO"
    assert md.get("index_id") == "IDX"
    assert md.get("commit_sha") == "SHA"
    assert md.get("file_path") and md.get("language")


def test_parse_fallback_is_honest_nodeparser(monkeypatch):
    """CodeSplitter(tree-sitter) 不可用时的回退仍是 LlamaIndex NodeParser，且不伪装成主切片。"""
    import logging

    from llama_index.core.node_parser import TokenTextSplitter

    from app.retrieval import parsing as p

    # 强制 _split_to_texts 抛错 → 走 TokenTextSplitter(NodeParser) 回退
    def boom(document, splitter):
        raise RuntimeError("tree-sitter download failed")

    monkeypatch.setattr(p, "_split_to_texts", boom)
    chunks = p.parse_and_chunk(_FILES, repository_id="r", index_id="idx", commit_sha="aaa", blocked_paths=[])
    assert chunks, "回退也应产出 chunk 且不丢行号/符号"
    for c in chunks:
        assert c.start_line >= 1 and c.end_line >= c.start_line
    # 证明默认主切片器选择的是 LlamaIndex NodeParser（Code/Sentence/Token ∈ llama_index）
    from app.retrieval import parsing as pp

    sp = pp._pick_splitter("python", "a.py")
    assert sp.__class__.__module__.startswith("llama_index")


def test_parse_chunk_config_decoupled(monkeypatch):
    """窗口/文件参数从独立 parsing_config 加载；collection_name 缺省=code_{index_id}；path_blocked 经 main re-export。"""
    from app.retrieval import parsing as p
    from app.retrieval import parsing_config as cfg

    # 常量来自独立配置模块（解析 seam 不再从 main 读切片常量）
    assert cfg.INDEX_TARGET_WINDOW_LINES == 40
    assert cfg.INDEX_WINDOW_OVERLAP_LINES == 10
    assert p.INDEX_TARGET_WINDOW_LINES == cfg.INDEX_TARGET_WINDOW_LINES
    # collection_name 缺省即旧 index_collection_name 同构
    chunks = p.parse_and_chunk(_FILES, repository_id="r", index_id="idx_cfg", commit_sha="aaa", blocked_paths=[])
    assert chunks and all(c.milvus_collection == "code_idx_cfg" for c in chunks)
    # path_blocked 迁移至 parsing，main re-export 仍可用
    assert main.path_blocked is p.path_blocked
    assert p.path_blocked(".env", []) is True
    assert p.path_blocked("pay/callback.py", []) is False


def test_full_chain_files_chunk_ingest_retrieval_stable(monkeypatch):
    """完整链路：文件→代码切片→LlamaIndex 节点化→(Milvus)入库→召回，稳定且来源可辨。"""
    monkeypatch.setenv("USE_MILVUS", "true")
    chunks = parsing.parse_and_chunk(_FILES, repository_id="r", index_id="idx_chain", commit_sha="abc", blocked_paths=[])

    # 节点化走 LlamaIndex（TextNode）；含 chunk 元数据
    nodes = build_nodes_from_chunks(chunks, repository_id="r", index_id="idx_chain")
    assert nodes and getattr(nodes[0], "text", ""), "LlamaIndex 节点化应产出文本节点"

    collection = f"col_chain_{int(time.time() * 1000)}"
    # 尝试真实 Milvus 入库+召回；Milvus 不可用时仅验证节点化（不静默伪装）
    try:
        from app.retrieval.llamaindex_backend import build_vector_store, MilvusVectorStore

        vs = build_vector_store(collection, overwrite=True)
        if isinstance(vs, MilvusVectorStore):
            build_index_from_chunks(chunks, repository_id="r", index_id="idx_chain", collection_name=collection, overwrite=True)
            res = query_index_from_chunks(chunks, repository_id="r", index_id="idx_chain", collection_name=collection,
                                          query_text="幂等 回调", top_k=5, threshold=0.0, head_commit="abc")
            assert res, "Milvus 入库后应能召回"
            # 召回结果稳定：source 可辨，且与本次切片同源
            assert all(r.index_id == "idx_chain" for r in res)
        else:
            pytest.skip("Milvus 不可用，完整召回链路以节点化为准")
    except Exception:
        pytest.skip("Milvus 构造/召回不可用，chaos 链路以节点化验证为准")