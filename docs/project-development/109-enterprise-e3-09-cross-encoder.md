# E3-09 企业 RAG Cross-Encoder 模型级精排对比

> 阶段：E3（企业级 RAG）
> 前置：E3-01..E3-08（企业 RAG 检索 + 离线评测 + 真实 Milvus verified + 控制台）

## 1. 目标与非目标

目标：在不改变召回边界的前提下，新增 Cross-Encoder 精排 provider，并在**同一真实评测集/commit/collection/embedding/top-k/过滤/权限**下对比
原始 Hybrid、Heuristic Rerank、LLM 摘要 Rerank、Cross-Encoder Rerank，产出真实、可复现的模型级指标对比。

非目标：不做「企业 RAG 已彻底完成 / 已达生产标准」的宣称；CE 只作为新增 provider 对比，结果不直接等同于生产达标。

## 2. 安全与隐私约束（硬约束，全部满足）

1. CE 只处理**已经通过** tenant_id/project_id/repository_id/commit|ref/source_type/index + `rag:search` 过滤的候选
   （由 `enterprise_rag_search` 返回，CE 只是对其重排，绝不重新检索/扩大全库）。
2. auditor / 无 rag:search 用户 → 路由不显示、直达重定向、后端 `require_tenant_permission(ctx, RAG_SEARCH)` 403（复用既有门禁）。
3. 请求体 tenant_id 不作为权限来源（沿用 `context_for_current_user` 服务端解析）。
4. **默认禁止发送企业代码正文到外部模型服务**：重排输入是 `EnterpriseRetrievedChunk`（不含 content 字段），
   CE 打分只使用 `file_path / symbol_name / language / 行区间` 摘要（`_candidate_text`），天然不含正文/token/凭据。
5. 优先本地/私有模型运行时（本机安装 `sentence-transformers` + CPU torch，模型从 hf-mirror 拉取并缓存）。
6. 模型不可用/超时/加载失败/输出异常/长度不匹配 → **稳定回退 heuristic**，`rerank_trace` 记录 `provider_attempted=cross_encoder`
   + `fallback_used` + `fallback_reason`；**fallback 绝不冒充 CE executed**。
7. 不改原始 score/metadata/租户边界，只新增 `rerank_score/rerank_reason` 与可解释 trace（无 token/密码/Cookie/JWT/正文）。
8. 未删除候选（超过 max_candidates 的结果原序保留）、未调低推荐阈值、未改真值——不刷指标。
9. 团队版 RAG 行为不变（CE 仅在 enterprise_rag_search 内按配置启用，默认关）。

## 3. 主链路（Mermaid）

```mermaid
flowchart LR
    Q[查询] --> F[enterprise_rag_search<br/>tenant/project/repo/commit/ref/source_type/index + rag:search]
    F --> R[enterprise_rerank<br/>provider=heuristic|llm|cross_encoder]
    R --> CE{provider==cross_encoder?}
    CE -->|是| L[load_cross_encoder<br/>本地/私有模型 · 缓存]
    L -->|可用| B[batch 打分 file/symbol 摘要]
    B --> O[只重排 top_n，其余原序保留]
    L -->|不可用/超时/异常| H[回退 heuristic + fallback_reason]
    O --> C[统一契约：rerank_score/reason/trace + provider/fallback/latency]
```

## 4. Cross-Encoder 与既有 Rerank 的边界

- 与 heuristic/LLM 一样，CE 只对 `enterprise_rag_search` 已返回的候选重排（同一过滤链），不产生新候选。
- heuristic 是确定性符号/路径/fresh 规则；LLM 是外部模型摘要重排（可回退）；CE 是本地模型对 `(query, file/symbol 摘要)`
  打分后按分数重排——三者都只重排 top_n、不删除、不改召回边界。
- CE 打分输入脱敏（file/symbol 摘要，非代码正文）；模型输出仅用于排序。

## 5. 模型与配置

| 配置 | 默认 | 说明 |
| --- | --- | --- |
| `RAG_CROSS_ENCODER_ENABLED` | `false` | 默认关闭（安全默认） |
| `RAG_CROSS_ENCODER_MODEL` | 空 | 本地/私有模型名或路径；`cross-encoder/ms-marco-MiniLM-L-6-v2` |
| `RAG_CROSS_ENCODER_DEVICE` | `cpu` | 推理设备 |
| `RAG_CROSS_ENCODER_TOP_N` | `16` | 重排头部数量 |
| `RAG_CROSS_ENCODER_BATCH_SIZE` | `8` | batch 推理 |
| `RAG_CROSS_ENCODER_TIMEOUT_SECONDS` | `5` | 推理超时 → 回退 |
| `RAG_CROSS_ENCODER_MAX_CANDIDATES` | `32` | 参与打分的候选上限（其余原序保留） |
| `RAG_CROSS_ENCODER_FALLBACK` | `heuristic` | 回退 provider |

- 配置同步：`backend/app/core/runtime_config.py`（`rag_cross_encoder_*`）与
  `deploy/env/enterprise.env.example`。运行时 trace 已记录模型身份、设备、候选数、打分数、耗时与回退状态；
  本轮提交报告的 `config_snapshot` 仍为空对象，`batch_size/timeout/max_candidates` 尚未完整进入报告顶层，
  作为后续报告契约加固项记录，不影响本轮 CE 实际执行与对比结果。
- 依赖：`backend/requirements-e3-09.txt` 固定 `sentence-transformers==3.3.1`（解析 torch/transformers/tokenizers/huggingface-hub）；
  模型 `cross-encoder/ms-marco-MiniLM-L-6-v2`（Apache-2.0），下载 `HF_ENDPOINT=https://hf-mirror.com`（HF 直连受限时）；
  模型首次下载后缓存于 `~/.cache/huggingface`，进程内二次加载走 `_MODEL_CACHE`（跨请求复用）。支持离线：模型预下载后置本地路径即可。
- 测试不联网：E3-09 单元测试用确定性桩模型验证逻辑；模型不可用路径验证 fallback。

## 6. 真实评测命令与报告

```bash
cd backend
# 起真实企业独立 Milvus（e309 卷，宿主机 19530）
export ENTERPRISE_MINIO_ROOT_USER=enterprise-minio ENTERPRISE_MINIO_ROOT_PASSWORD=<随机>
docker compose -f deploy/docker-compose.enterprise.yml -f deploy/docker-compose.milvus-e309.override.yml \
  up -d etcd-enterprise minio-enterprise milvus-enterprise
# 运行 E3-09 对比（真实 Milvus + 真实 CE 模型；需 ZHIPU_API_KEY 等环境注入）
export USE_MILVUS=true MILVUS_URI=http://127.0.0.1:19530 MILVUS_COLLECTION_PREFIX=enterprise \
  APP_ENV=enterprise USE_SQLALCHEMY=false RAG_MOCK_FALLBACK=false RAG_SYMBOL_BOOST=1.0 \
  RAG_CROSS_ENCODER_ENABLED=true RAG_CROSS_ENCODER_MODEL=cross-encoder/ms-marco-MiniLM-L-6-v2 RAG_CROSS_ENCODER_DEVICE=cpu \
  HF_ENDPOINT=https://hf-mirror.com
.venv/Scripts/python.exe -m scripts.evaluate_enterprise_rag evaluation/enterprise_rag/e3_06_health_middle.jsonl \
  --repository-url https://gitee.com/yang-zhizemin/health-middle \
  --repository-id repo_health_middle --project-id proj_health_middle \
  --top-k 8 --require-milvus --repo-dir <health-middle 克隆> \
  --rerank-compare --cross-encoder-model cross-encoder/ms-marco-MiniLM-L-6-v2 --cross-encoder-device cpu \
  -o evaluation/enterprise_rag/reports/e3_09_health_middle_rerank_comparison.json
```

- 报告：`backend/evaluation/enterprise_rag/reports/e3_09_health_middle_rerank_comparison.json`（首跑，CE 未缓存模型）
  与 `..._rerank_comparison_repro.json`（复跑，CE 走进程内模型缓存）。含 dataset/config hash、repository/commit/collection、
  embedding provider/model/dimension、baseline/heuristic/llm/cross_encoder_metrics、mode_comparison、per_tag、latency p50/p95、
  model_loading、fallback_used、needs_improvement、failed_cases、generated_at。

## 7. 真实指标对比（同一真实 Milvus + health-middle 73e7e54 + zhipu/embedding-3 2048 + top-k 8）

| 提供方 | recall@K | precision@K | MRR | NDCG@K | 状态 |
| --- | --- | --- | --- | --- | --- |
| baseline（hybrid 无重排） | 0.8955 | 0.3396 | 0.8172 | 0.7981 | verified |
| heuristic rerank | 0.8955 | 0.3396 | 0.8116 | 0.7934 | verified |
| llm rerank（deepseek 摘要） | 0.8955 | 0.3396 | 0.7668–0.7873 | 0.7812–0.7961 | verified（随机，跨次波动） |
| **cross_encoder rerank** | 0.8955 | 0.3396 | **0.8507** | **0.8090** | **verified / executed（真实模型）** |

- CE 为四档中 MRR/NDCG 最高；recall/prec@K 各档一致（重排只改 top-K 内部顺序，不改变召回/候选集——非刷指标）。
- CE 真实执行：`model_loading.status=executed`，`cross_encoder_metrics.fallback_used=false`，model=ms-marco-MiniLM-L-6-v2，device=cpu。
- latency（每 case p50/p95）：baseline 290/433ms、heuristic 227/255ms、llm 4425/17932ms、**CE（缓存后）331/892ms**；
  首跑未缓存模型时 CE p50≈7251ms（每次搜索重新加载模型），进程内模型缓存修复后降到 331ms（真实优化，指标不变）。
- **复现对拍**：两次对比报告剔除时间/latency 后，baseline/heuristic/CE 的指标差异=0；仅 LLM 因模型随机性在两次间波动
  （MRR 0.7873→0.7668）——如实记录：heuristic/CE 确定性可复现，LLM 非位级可复现。
- **报告契约观察项**：`config_snapshot={}`，尚未完整固化 CE 的 batch size、timeout、max candidates 等超参；
  后续应将这些字段纳入配置快照及其 hash，增强跨环境复现能力。

## 8. 源码文件位置

| 职责 | 文件 |
| --- | --- |
| CE provider（加载/缓存/batch/超时/回退/脱敏摘要） | `backend/app/enterprise_rag/cross_encoder.py` |
| rerank 集成（provider=cross_encoder + fallback trace） | `backend/app/enterprise_rag/reranker.py` |
| 服务接线 + 返回契约（rerank_* 新字段） | `backend/app/enterprise_rag/service.py` |
| 配置 | `backend/app/core/runtime_config.py`、`deploy/env/enterprise.env.example`、`backend/requirements-e3-09.txt` |
| 对比评测报告 | `backend/app/enterprise_rag/rerank_compare.py`、`backend/scripts/evaluate_enterprise_rag.py --rerank-compare` |
| 配置快照 | `backend/app/enterprise_rag/evaluation.py::config_snapshot` |
| 专项测试 | `backend/tests/test_enterprise_e3_09_cross_encoder.py` |
| Milvus 复跑叠加 | `deploy/docker-compose.milvus-e309.override.yml` |

## 9. 测试证据（全部本会话真实执行）

- E3-09 专项 `pytest tests/test_enterprise_e3_09_cross_encoder.py` → **17 passed**（只重排给定候选、不改原 score、
  batch 顺序稳定、max_candidates 封顶不丢候选、空/单候选、disabled/unavailable/timeout/inference_error 回退、
  trace 无正文/凭据、enterprise_rerank CE 执行与回退、默认关闭 heuristic 不变、可复现、报告区分 provider/fallback、
  auditor 403、运行态安全默认）。
- E3-01..E3-09 组合回归 → **78 passed**；E3-09 + E3-03 rerank → 26 passed。
- **后端全量 pytest → 758 passed, 16 skipped**；**前端全量 vitest → 250 passed**（本轮未改前端）；`vue-tsc -b` 0 错；
  `vite build` 通过；`git diff --check` 无输出。

## 10. 未验证项 / 诚实边界

- **未修改 E3-08 页面**（本轮为后端 provider + 对比报告；E3-09 对比报告可经 E3-08 控制台报告列表选中查看其元数据，
  但未做专用的四档对比 UI）。按要求「如修改 E3-08 页面才需真实浏览器查看 provider 对比」，本轮未改，故未产生新浏览器截图。
- CE 基于 CPU 小模型（ms-marco-MiniLM-L-6-v2）与脱敏 file/symbol 摘要；换用更大/专用代码 CE 模型或 GPU 的指标未测。
- LLM rerank 非位级可复现（随机性）如实记录；如需可复现需固定 seed/温度或限定。
- 本轮 `config_snapshot` 未完整包含 CE 超参（batch size、timeout、max candidates）；后续补齐报告契约。
- CE 模型许可证 Apache-2.0、经 hf-mirror 拉取；生产离线环境需预置模型路径（`RAG_CROSS_ENCODER_MODEL`）。
- **不宣称企业 RAG 已彻底完成 / 生产已达标准**：E3-09 只交付 CE provider + 同环境对比报告。
