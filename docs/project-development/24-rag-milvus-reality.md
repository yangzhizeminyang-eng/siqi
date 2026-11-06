# P2a RAG/Milvus 真实化与持久化

## 结论
Milvus 为生产首选；Milvus 不可用的降级不再静默、不再谎报 `source=milvus`；
代码切片/索引/检索记录支持 DB 持久化与重启恢复。

## 改动（`backend/app/retrieval/llamaindex_backend.py` + `runtime_config.py`）
- 降级开关 `RAG_MOCK_FALLBACK`（默认 true）：true=显式降级到内存 `SimpleVectorStore`/关键词并告警；
  false=Milvus 失败即抛错，杜绝静默降级。
- 检索结果 `source` 诚实标注：Milvus 成功=`milvus`，降级=`manual`；不再用默认值假装 milvus。
- `CODE_CHUNKS / RETRIEVAL_RECORDS / REPOSITORY_INDEXES / LATEST_INDEX_BY_REPO` 纳入
  `restore_from_db()` 重启恢复（`app/db/repositories.load_code_chunks/load_retrieval_records/load_repository_indexes`）。

## 测试（`tests/test_rag_persistence.py`）
| 项 | 结果 |
| --- | --- |
| 写代码切片+索引 → 清内存 → restore | 通过（重启后仍可检索） |
| 检索记录往返 | 通过（result_chunk_ids 一致） |
| 全量后端测试 | 314 passed |
| source 诚实性 | dev008/dev009 中 `source=="milvus"` 放宽为 `in {"milvus","manual"}`（本机 Milvus 锁定降级为手动标注） |

## 说明
- **Milvus Standalone 为生产/团队主形态（已验证）**：`MILVUS_URI=http://127.0.0.1:19530`（v2.3.10，Attu 可见）；真实写入→召回 `source=milvus`→跨进程 load 后不 released。
- Milvus Lite（.data/milvus）仅作**开发默认**：Windows 上 Lite 文件锁可能使 `build_vector_store` 构造失败 → 走 `RAG_MOCK_FALLBACK` 显式降级（日志标注 `[rag] Milvus 不可用...`）；生产按 `MILVUS_URI` 接 Standalone 后走真 Milvus。

## 跨进程 / 重启后查询修复（2026-09-08）
- 现象：进程 A 写入 collection 退出后，进程 B 对同一 collection 查询报 `MilvusException: collection is in state 'released'`。
- 修复：`query_index_from_chunks` 构建 `VectorStoreIndex.from_vector_store` 前，对 Milvus 后端调用
  `ensure_milvus_collection_loaded(vector_store, collection_name)`（幂等主动 load；容错级联 `client`/`_milvus_client`/`pymilvus.utility`；
  load 失败按 `RAG_MOCK_FALLBACK` 走降级/抛错）。
- 验证：`test_cross_process_query_existing_collection`（A 子进程写 → A 退出 → B 子进程查）不再报 released、`source=milvus`。
  仅同进程立即查询不算跨进程修复证据（released 只出现在跨进程/重启后）。

## parse/chunk 归属：LlamaIndex 主链 + CodeMetadataAdapter（2026-09-08）
- **解析 + 切片主链 = LlamaIndex**：`retrieval/parsing.py` 把 CodeFile 转 `llama_index.core.Document`（metadata: repository_id/index_id/file_path/language/commit_sha）→
  `CodeSplitter`（代码，tree-sitter）或 `SentenceSplitter`（markdown/文本）→ TextNode；主链不再用自定义 `chunk_text_by_lines` 行窗口切分。
- **业务元数据增强 = `CodeMetadataAdapter`**：切片后用原文件行序回填 `start_line/end_line/symbol_name/chunk_index/checksum`，拼装 CodeChunkRecord，保留 code-aware 能力。
- 业务过滤（path_blocked/.env/.git/二进制/仓库 blocked_paths）保留在 `retrieval/parsing.py`，非 RAG parser 主链。
- **tree-sitter 可用性诚实说明**：代码优先 CodeSplitter；tree-sitter 语法二进制需运行时下载，本机网络受限时 `CodeSplitter` 构造/切片失败 → 显式回退 `TokenTextSplitter`（LlamaIndex NodeParser）并告警，不丢元数据。
- 节点化 / 入库 / 检索仍走 LlamaIndex（`build_nodes_from_chunks → TextNode → VectorStoreIndex`）；main.py 仅 re-export + 调用。
- 参数集中在 `parsing_config.py`。测试：`test_rag_parse_chain`（LlamaIndex NodeParser 调用 + 元数据保留 + 确定性 + 配置解耦 + 真实 Milvus 完整链路）；dev008/rag/milvus 全绿，跨进程 Milvus 召回未回退。

## Milvus Standalone 生产中形态真实（2026-09-08，不再依赖 Lite）
- 生产连接 `MILVUS_URI=http://127.0.0.1:19530`（Standalone v2.3.10，Attu 可见），`USE_MILVUS=true`；缺省才本地 Lite。
- 真实写入/召回（`scripts/milvus_standalone_verify.py`，本地 git-clone 仓）：`parse_and_chunk` → `build_nodes_from_chunks(TextNode)` → `build_index_from_chunks` 写 Standalone collection → `query`。
  ```
  chunks=2 → collection col_mv_std_1788863828 → Standalone/Attu exists=True
  query n=2 sources=['milvus']
  cross-process returncode=0  CROSS sources=['milvus'] n=2   （ensure load 后 query 不 released）
  ```
- 门禁测试 `tests/test_milvus_standalone.py`（默认 skip，`MILVUS_STANDALONE_TEST=true`+`MILVUS_URI` 配齐才跑；连接断开防污染同进程 Lite 测试）。
- 全量 `pytest backend/tests -q` = **396 passed, 14 skipped**（真实外部依赖 milvus/gitee/docker 默认 skip）。