# E3-05 真实评测集与质量基线报告

> 阶段：E3（企业级 RAG）
> 前置：E3-01..E3-04（强过滤/Hybrid、Rewrite/Expansion、Rerank、离线评测框架+加固）
> 相关设计：`docs/enterprise/04-enterprise-rag-design.md`、`docs/enterprise/01-enterprise-prd.md §9`

## 1. 功能目标与范围

补一份可复现、脱敏、项目级真实研发问题评测集，并在本地/内存态复用 `enterprise_rag_search` 跑出
Recall@K / MRR / NDCG@K / Precision@K / Freshness / Empty / Unauthorized 等基线分数，输出质量基线报告。

**范围内**
- 评测集（≥30 条）覆盖：精确函数/类名、错误码、API 路径、文件路径、自然语言、跨文件、stale/fresh、
  403/422 边界、无答案、source_type 过滤；字段含 case_id/query/project_id/repository_id/commit_sha|ref/
  expected_chunk_ids|expected_file_paths/source_types/tags/difficulty/rationale/expected_status。
- JSON/JSONL 可加载；case_id 唯一；脱敏不含真实商业代码正文/token/凭据。
- 评测集 + 脱敏合成 demo 仓库种子（内存态），可复现；复用 E3-04 评价主链路（不新写检索）。
- 基线报告：summary / metrics_by_tag / failed_cases / config_snapshot / dataset_size /
  recommended_thresholds / needs_improvement。
- 质量门槛：推荐阈值只标记 `needs_improvement`，不硬说达标、不让脚本伪通过。
- CLI：`--dataset/--output/--top-k/--memory-seed/--dry-run`。

**范围外（不宣称完成）**
- 云端真实 Milvus 端到端 / 多副本并发：未验证。
- 前端评测控制台：未实现。
- Cross-Encoder 指标对比：未实现。
- 真实生产代码评测集的 Recall 绝对达标分数：本轮为**脱敏合成 demo 语料**基线，不代表生产项目达标。

## 2. 主链路（Mermaid）

```mermaid
flowchart LR
    DS[e3_05_baseline.jsonl 32条] --> LB[load_cases 字段/唯一/合法性校验]
    SEED[seed.py 脱敏 demo 仓库<br/>内存态] --> SR[enterprise_rag_search 复用授权+过滤+Hybrid]
    LB --> SR
    SR --> MET[Recall@K/MRR/NDCG/Precision/Freshness/Empty/Unauthorized]
    MET --> GATE{recommended_thresholds<br/>needs_improvement}
    GATE --> RPT[基线报告 summary/metrics_by_tag/failed_cases/config_snapshot]
```

## 3. 关键代码说明

- `evaluation/enterprise_rag/seed.py`：构建脱敏"支付/退款/订单/网关"合成仓库（14 个 code 切片）+ 跨租户仓库，
  仅通用占位代码，无真实敏感；用于让检索真实命中与 403 边界成立。
- `evaluation/enterprise_rag/e3_05_baseline.jsonl`：32 条样本，case_id 唯一，覆盖上文各类别。
- CLI `scripts.evaluate_enterprise_rag`：`--memory-seed` 先种种子再评测；`--dry-run` 显式声明本地内存态；
  `-o` 写报告（UTF-8）。输出 summary + needs_improvement。
- 质量门 `quality_gate`：对 `recall_at_k/mrr/ndcg_at_k` 与推荐阈值比较，低于阈值只进 `below` + `needs_improvement`。
- 指标/结构沿用 E3-04（case_id/rationale 字段、`retrieval_passed`、真值去重、tag 增补 precision 均已收口）。

## 4. 源码位置

| 职责 | 文件 |
| --- | --- |
| 评测集/指标/复用搜索/质量门 | `backend/app/enterprise_rag/evaluation.py` |
| 评测集数据 | `backend/evaluation/enterprise_rag/e3_05_baseline.jsonl` |
| 脱敏种子 | `backend/evaluation/enterprise_rag/seed.py` |
| 基线报告（生成产物） | `backend/evaluation/enterprise_rag/e3_05_baseline_report.json` |
| 目录说明 | `backend/evaluation/enterprise_rag/README.md` |
| CLI | `backend/scripts/evaluate_enterprise_rag.py` |
| 测试 | `backend/tests/test_enterprise_e3_05_quality_baseline.py` |

## 5. 基线结果（真实执行，内存态/脱敏合成语料）

报告 `e3_05_baseline_report.json`（CLI `--memory-seed --top-k 8` 实跑）：

| 指标 | 值 |
| --- | --- |
| dataset_size | 32 |
| recall@K (K=8) | 0.8750 |
| precision@K | 0.1172 |
| mrr | 0.8281 |
| ndcg@K | 0.8519 |
| freshness_rate | 0.9667 |
| empty_result_rate | 0.0667 |
| unauthorized_pass_rate | 1.0000 |
| needs_improvement | false（均高于推荐阈值） |
| failed_cases | 3 |

> 说明：这是**脱敏合成 demo 语料**的基线，用于验证评测框架、指标与质量门可复现运行；**不代表任何真实生产
> 项目的 Recall/MRR 达标**。真实生产评测集（≥50 条 + 在目标仓库上的达标分数）仍需后续以真实代码跑出并提供证据。

## 6. 复现命令

```bash
cd backend
USE_SQLALCHEMY=false MODEL_MOCK_FALLBACK=true USE_MILVUS=false \
  .venv/Scripts/python.exe -m scripts.evaluate_enterprise_rag \
  evaluation/enterprise_rag/e3_05_baseline.jsonl \
  -o evaluation/enterprise_rag/e3_05_baseline_report.json --memory-seed --top-k 8
```

## 7. 真实测试证据（全部实跑）

- `pytest tests/test_enterprise_e3_05_quality_baseline.py -q` → **8 passed**
  （数据集≥30 且 case_id 唯一、字段完整、tags/difficulty 合法、数据集无敏感/代码正文、CLI 产出报告、
  报告含 summary/metrics_by_tag/failed_cases/config_snapshot + dataset_size/thresholds/needs_improvement 且无敏感、
  quality_gate 逻辑、403/422 计入 unauthorized_pass_rate、复用 E3-04 主链路 recall>0）
- E3-01..E3-05 + RAG + E1 + E2 + E2-hardening 定向回归 → **122 passed**
- 后端全量 `pytest -q` → **662 passed, 16 skipped**
- `git diff --check` → 无输出

## 8. 已知限制与残余风险

- **云端真实 Milvus 端到端 / 多副本并发：未验证**（本轮内存态；真实向量腿以 mock fallback/自测桩确定）。
- **前端评测控制台、Cross-Encoder 指标对比：未实现**。
- **真实生产评测集与达标分数：未提供**——当前基线上脱敏合成语料，不是生产达标证据。
- `USE_MILVUS=false` 下本地评测走关键词/内存降级；要反映生产向量检索请在真实 Milvus 上复跑并向此报告补证据。