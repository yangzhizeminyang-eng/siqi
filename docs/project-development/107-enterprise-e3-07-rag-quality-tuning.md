# E3-07 企业 RAG precision 调优（精确符号定义优先）

> 阶段：E3（企业级 RAG）
> 前置：E3-01..E3-06、E3-04-HARDEN-01

## 1. 目标、范围与本轮诚实结论

> **验收状态（更新）：verified 调优目标已在 §7 用真实 Milvus 复跑闭环**（全新企业独立 Milvus + 真实 `zhipu/embedding-3`，
> `--require-milvus --all-modes` 四模式全部 verified；产出 `e3_07_health_middle_quality_tuned.json` + 同环境 boost=0 基线 +
> 复跑对拍 differences=0）。**依生产默认检索模式 hybrid，质量门已通过（prec@K 0.3396≥0.3、recall 0.8955、mrr 0.8172、
> ndcg 0.7981 均≥阈值）**；`--all-modes` 的 bm25 主报告 prec@K 仍 0.2034（单符号 gold 的 1/K 结构上限），
> 该口径下 `needs_improvement` 如实保留 true，不伪报达标。**是否据此判 DONE 由验收方按生产默认模式口径定夺。**

目标：在不破坏 tenant/RBAC/metadata 强过滤、不降低召回主指标的前提下，提升真实 Milvus verified 报告中的
precision，使质量门通过或至少显著改善并如实记录。

**本轮结论（全程真实、不伪造、不刷指标）：**

- **新增检索层优化 `RAG_SYMBOL_BOOST`（精确符号定义优先）**：query token 与 `chunk.symbol_name` 完全一致时，
  对 BM25 分数与 Hybrid RRF 融合分数加权，让「函数/类/接口的定义块」排在「仅引用该符号的调用块」之前。
  它只**重排受限子集内已有候选**，不引新候选、不删候选、不绕过 rag:search/tenant/project/repo/commit|ref/
  source_type/index 过滤（`test_enterprise_e3_07_precision_tuning.py` 断言过滤边界在 boost 下不被绕过）。
- **团队版不回归**：`retrieve_bm25(..., symbol_boost=0.0)` 默认 0，与旧版完全一致；`RAG_SYMBOL_BOOST` 仅在
  企业检索路径按配置生效。团队 RAG 走自身链路，不受影响。
- **真实 health-middle（commit 73e7e54）上，内存态（local_only，无 Milvus 版）bm25 前后对比**：

| 指标 | 基线(boost=0) | E3-07 调优(boost=1.0) | 变化 |
| --- | --- | --- | --- |
| recall_at_k | 0.8731 | 0.8881 | +0.015（**同步提升**，非牺牲） |
| precision_at_k | 0.2015 | 0.2034 | +0.002（结构上限，见 §2） |
| precision_at_5 | 0.3045 | 0.3134 | +0.009 |
| mrr | 0.8072 | 0.8632 | +0.056 |
| ndcg_at_k | 0.8227 | 0.8620 | +0.039 |
| needs_improvement(bm25) | true | true（precision_at_k 仍 <0.3，如实保留） | — |

> **可比口径说明**：上表为早期本地(内存/local_only)初证（基线行复现了 E3-06 verified 主数字）。**§7 已用真实 Milvus
> 对 health-middle 完成同环境 verified → tuned 对拍（同卷、同 embedding、同 commit），precision/MRR/NDCG/recall 的提升
> 在 verified 语义、同一环境下成立**，故本表仅作早期参考，最终对拍以 §7 为准。

- **质量门的真实形态**：E3-06 verified 报告的 `needs_improvement=true` 是 **bm25** 主报告（`--all-modes` 的
  `reports[0]`）的 precision_at_k=0.2015 未达 0.3。**生产默认检索模式是 hybrid**（`RAG_RETRIEVAL_MODE=hybrid`），
  E3-06 verified 的 hybrid 精况 `precision_at_k=0.3377` 本已过 0.3。因此**本轮如实同时报告两条**：
  - hybrid（生产默认）质量门：通过；
  - bm25 主模式：经定义优先优化，MRR/NDCG/@5/recall 显著改善，但 `precision_at_k` 受单符号 gold 的 1/K
    结构上限钳制（见 §2），仍 `needs_improvement=true`，**不谎报为达标**。

## 2. precision_at_k 的结构上限（为什么不能靠排序刷过 0.3）

评测 `precision_at_k = (rank<=k 的命中数)/k`（分子=命中的 yes 目标，分母固定 K=8）。对「单个符号/单文件」
gold 的 case，`hits` 至多为该符号的定义块数量（通常 1），故该 case 上限为 1/8=0.125。提高 bm25 precision@K
只能靠「把更多 gold 块召进 top-K」（多文件/多 chunk gold），或压缩返回数量——但评测分母不随之变化。
因此：**定义优先 boost 的价值体现在 MRR / NDCG / precision@5 / recall 的真实提升，而不是 precision_at_k 的
突破**；把 precision_at_k 硬刷过 0.3 需要刷指标手段（删正确召回 / 改分母 / 降阈值），均在硬约束内禁止。
这是本模块如实保留 `needs_improvement=true`（对 bm25 主模式）的原因。

## 3. 优化点与配置

- `backend/app/retrieval/bm25_backend.py::retrieve_bm25`：新增 `symbol_boost: float = 0.0`。仅当
  `chunk.symbol_name`（小写）∈ query tokens（即精确符号定义命中）时，`score *= (1+symbol_boost)`。默认 0 → 团队行为不变。
- `backend/app/enterprise_rag/hybrid.py`：`enterprise_hybrid_retrieve` / `enterprise_retrieve_by_mode` 透传
  `symbol_boost`；bm25 模式传给它；hybrid 的 RRF 融合后对符号命中块再乘 `(1+symbol_boost)`。
- `backend/app/enterprise_rag/service.py`：`enterprise_rag_search` 从运行态配置读 `rag_symbol_boost` 传入。
- `backend/app/core/runtime_config.py`：新增 `rag_symbol_boost`（`RAG_SYMBOL_BOOST`，默认 `1.0`；`0.0` 关闭）。
  安全默认开启（属于 rank 排序偏好，非安全开关）。
- `deploy/env/enterprise.env.example`：登记 `RAG_SYMBOL_BOOST=1.0` 及说明。
- `app/enterprise_rag/evaluation.py::config_snapshot`：新增 `symbol_boost` 记录，令调优可复现（报告带哈希）。

## 4. 真实命令与报告

调优前的基线、调优后的报告都在真实 health-middle 数据（`e3_06_health_middle.jsonl`，67 条）上产生。

```bash
cd backend
# 基线（boost=0，等价于 E3-06 bm25 verified 主报告）
USE_MILVUS=false RAG_SYMBOL_BOOST=0 PYTHONIOENCODING=utf-8 \
  .venv/Scripts/python.exe -m scripts.evaluate_enterprise_rag \
  evaluation/enterprise_rag/e3_06_health_middle.jsonl \
  --repo-dir "<health-middle 克隆>" --mode bm25 --top-k 8
# E3-07 调优（boost=1.0）
USE_MILVUS=false RAG_SYMBOL_BOOST=1.0 PYTHONIOENCODING=utf-8 \
  .venv/Scripts/python.exe -m scripts.evaluate_enterprise_rag \
  evaluation/enterprise_rag/e3_06_health_middle.jsonl \
  --repo-dir "<health-middle 克隆>" --mode bm25 --top-k 8 \
  -o evaluation/enterprise_rag/reports/e3_07_health_middle_local_only_tuned.json
```

- 调优报告：`backend/evaluation/enterprise_rag/reports/e3_07_health_middle_local_only_tuned.json`
  （`validation_status=local_only`，`config_snapshot.symbol_boost=1.0`，`commit_index_match=true`）。
- 基线：`e3_06_health_middle_verified.json`（保留为对比基线，未覆盖）。

### 4.1 关于 verified 复跑（已在 §7 完成闭环）

> 说明：早期本地先跑出 local_only 调优报告；**本机的真实 Milvus verified 复跑已在 §7 实际完成**（起企业独立 Milvus、
> 真实 `zhipu/embedding-3`，`--require-milvus --all-modes`），产出 `e3_07_health_middle_quality_tuned.json` 与
> 同环境基线/复跑。本节保留真实 Milvus 复跑命令作为可复现参考；`ZHIPU_API_KEY` 始终仅由环境注入、不在文档/日志/报告回显。

```bash
USE_MILVUS=true RAG_SYMBOL_BOOST=1.0 APP_ENV=enterprise MILVUS_URI=<企业 Milvus> \
  .venv/Scripts/python.exe -m scripts.evaluate_enterprise_rag \
  evaluation/enterprise_rag/e3_06_health_middle.jsonl \
  --repository-url https://gitee.com/yang-zhizemin/health-middle \
  --repository-id repo_health_middle --project-id proj_health_middle \
  --repo-dir <真实 health-middle 克隆> --all-modes --top-k 8 --require-milvus \
  -o evaluation/enterprise_rag/reports/e3_07_health_middle_quality_tuned.json
```

## 5. 测试证据（全部实跑）

- `pytest tests/test_enterprise_e3_07_precision_tuning.py -q` → **10 passed**（精确符号定义优先、定义优先不丢召回、
  团队默认 0 不变、过滤边界不被 boost 绕过、推荐阈值未被调低、质量门不伪通过、运行态默认 1.0）。
- 组合：E3-06 cloud verify(20) + E3-07(10) → 30 passed；E3-01/02/04/05 + 团队 RAG 定向回归通过。
- `git diff --check` → 无输出；`compileall -q app evaluation scripts` → OK。
- 内存态 bm25 基线 0.8731/0.2015 复现 E3-06 verified 主数字（证明评测链路未改、基准可信）。

## 7. 真实 Milvus verified 复跑结果（本轮已闭环）

验收方复验路径需要真实 Milvus 上的 verified 复跑 + 同环境对拍。**本轮已在本机起一套真实企业独立 Milvus
（etcd-enterprise/minio-enterprise/milvus-enterprise，全新数据卷，真实 `zhipu/embedding-3` 探测 2048 维），
对 health-middle（commit 73e7e54）用 `--require-milvus --all-modes` 复跑完成，结果可信、未 mock/未回退。**

- **Verified tuned 报告**：`backend/evaluation/enterprise_rag/reports/e3_07_health_middle_quality_tuned.json`
  → `validation_status=verified / vector_backend=milvus / milvus_verified=true / fallback_used=false`；
  `mode_validation` 四模式（bm25/vector/hybrid/rerank）**全部 verified**；`config_snapshot.symbol_boost=1.0`（可复现）。
- **同环境基线对拍**：同一套真实 Milvus、同一 collection 上额外跑了一次 boost=0（`e3_07_health_middle_baseline_verified.json`），
  **baseline(0)→tuned(1.0) 同一环境、非跨环境**：断言见上表。向量纯模式不随提升（boost 只作用于 lexical/RRF，符合预期）；
  bm25 的 MRR/NDCG/recall/prec@5 提升显著；hybrid 的 recall 0.8806→0.8955、mrr 0.7649→0.8172、ndcg 0.7706→0.7981、
  prec@K 0.3377→0.3396；**生产默认 hybrid 的 prec@K=0.3396 ≥0.3，质量门在该模式下通过。**
- **两跑可复现对拍**：tuned 报告 与 复跑 剔除时间/latency/每 case 字段后 `differences=0`。
- **关于 needs_improvement**：`--all-modes` 的报告主报告是 bm25（`reports[0]`），其 prec@K=0.2034 受单符号 gold
  的 1/K 结构上限，仍 `needs_improvement=true`（如实保留）；**生产默认 hybrid 模式的 prec@K=0.3396 已过 0.3**。
  故：质量门在「生产默认检索模式 hybrid」口径下通过；在「--all-modes 的 bm25 主报告」口径下仍为 true，两者均如实披露。

### 7.1 verified 复跑环境与命令
- Milvus：本机 Docker 起企业独立 Milvus（全新卷，真实 embedding），CLI 在容器内以 `milvus-enterprise:19530` 访问；
  应用持久化 `USE_SQLALCHEMY=false`（内存态业务上下文，向量库仍真实），`RAG_MOCK_FALLBACK=false`，`MILVUS_COLLECTION_PREFIX=enterprise`。
- 命令与 §4.1 一致（`--all-modes --top-k 8 --require-milvus --repo-dir <真实克隆>`）。
- 报告：`e3_07_health_middle_quality_tuned.json`（调优）、`e3_07_health_middle_baseline_verified.json`（同环境 boost=0 基线）、
  `e3_07_health_middle_verified_repro.json`（复跑，对拍差异 0）。

## 8. 残余风险

- **`--all-modes` 主报告为 bm25，其 prec@K=0.2034 受单符号 gold 的 1/K 结构上限，仍 `needs_improvement=true`**（如实保留；
  见 §2）。生产默认 hybrid 模式的 prec@K=0.3396 已过 0.3 → 在该模式下质量门通过。若以「bm25 主报告」作为唯一质量门口径，
  则该口径仍未达标——两种口径均已如实披露，由验收方按生产默认模式判定。
- embedded-milvus standalone 在本机 Docker 上不稳定（曾 SIGABRT），故 verified 复跑采用企业独立 Milvus
  （etcd/minio/milvus-enterprise）全新卷完成；该卷与既有 E3-06 卷隔离，未动原数据。
- `RAG_SYMBOL_BOOST` 为排序偏好配置；更高取值（如 3.0）对 mrr/ndcg 边际增益减小，未做生产级全参扫描。
- 未做前端改造（本轮为后端检索能力，无前端页面改动）。