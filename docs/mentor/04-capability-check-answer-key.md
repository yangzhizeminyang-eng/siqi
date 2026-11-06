# 第 8 站 · 能力体检答案卡（12 问）

> 用途：小泽先自己答，我按此卡判分。
> 原则：**每条答案都标注证据等级** —— `[码]`=我读过源码/实测过，`[推]`=机制已确认但设计意图是我的解读，`[待补]`=尚未逐行核对。
> 判分口径：**能不能讲清机制**，不是能不能背出路径。

---

## A 档 · 基础（必须全对）

### Q1 一个任务从 Gitee Issue 到 COMPLETED，中间经过哪几个状态？在哪改写？

**30 秒参考答案**

状态字面量共 **15 个**，定义在 `backend/app/main.py:312-328` 的 `TaskStatus = Literal[...]`
（注意它不是 `class`，是 `Literal`）：

```text
CREATED → ISSUE_FETCHED → CONTEXT_INDEXED → ANALYSIS_QUEUED → ANALYZED
→ PLAN_GENERATED → WAITING_APPROVAL → APPROVED → DIFF_GENERATED
→ TEST_RUNNING → TESTED → COMPLETED           （终态另有 FAILED / CANCELLED）
```

**改写地点分两层**：`agent/workflow.py` 的节点负责**触发**，真正的字段改写与落库在 `main.py` 的
服务函数里。对应关系：

| 状态跃迁 | 触发节点（`workflow.py`）| 实际执行 |
| --- | --- | --- |
| → `CONTEXT_INDEXED` / `ANALYSIS_QUEUED` | `node_prepare_context:76` | `main._prepare_context` |
| → `ANALYZED` | `node_ai_analyze:183` | `main._run_analysis_stage` |
| → `PLAN_GENERATED` / `WAITING_APPROVAL` | `node_generate_plan:227` | `main.attach_plan` + `approval_policy.evaluate_and_apply` |
| → `APPROVED` | `node_wait_approval:243`（暂停）| 人工审批 或 `apply_auto_approval` |
| → `DIFF_GENERATED` | `node_generate_diff:262` | `diff_service.execute_diff` + `store_diff_artifact` |
| → `TEST_RUNNING` / `TESTED` / `TEST_FAILED` | `node_run_test:289` | `main.execute_task_tests_worker` |
| → `COMPLETED` | `node_finish:319` | PR 描述生成后收口 |

**`main.py` 内的逐行改写点（已全部核对）**：

| 状态 | 行号 | 状态 | 行号 |
| --- | --- | --- | --- |
| `ISSUE_FETCHED` | `:3857`、`:3910` | `DIFF_GENERATED` | `:3348`、`:8442`、`:8689` |
| `CONTEXT_INDEXED` | `:3814` | `TEST_RUNNING` | `:3319`、`:8665` |
| `ANALYSIS_QUEUED` | `:4013` | `TESTED` | `:3310`、`:3361`、`:8700` |
| `ANALYZED` | `:2639` | `TEST_FAILED` | `:3383`、`:8718` |
| `WAITING_APPROVAL` | `:2994` | `FAILED` | `:2573`（`mark_task_failed`）|
| `APPROVED` | `:8133` | `CANCELLED` | `:7247` |

`[码]` 状态字面量、节点行号、索引阶段、上表行号**均已核对**。

> **值得注意**：测试执行的状态改写有**两套**（`:3310-3383` 与 `:8665-8718`），
> 各自都写了 `record.status = skipped/passed/failed` + `task.status = TESTED/TEST_FAILED`。
> 这大概率是「HTTP 内联路径」与「Worker 路径」的两份实现——**是重复逻辑，属可维护性风险**
> （改一处漏一处）。`[待补]` 下一轮我去确认两个函数名再定性。

**真实证据**：`TASK-8817` 的事件时间线（`agent_team` DB，15 条）完整覆盖了这条链，
从 `TASK_CREATED` 到 `PR_DESC_GENERATED`，含 `PLAN_GENERATED` 时风险从 `medium` **重算为 `high`**。

**追问点**：`TEST_RUNNING` 为什么不常出现在列表里？（瞬态，很快就落到 `TESTED`/`TEST_FAILED`）

---

### Q2 索引的五个阶段各自做什么？失败时怎么落到 FAILED 并保留 `error_stage`？

**30 秒参考答案**

阶段序列定义在 `main.py:380`：

```python
INDEX_STAGE_ORDER = ("PENDING", "SYNCING", "PARSING", "EMBEDDING", "STORING", "COMPLETED")
```

| 阶段 | 做什么 |
| --- | --- |
| `PENDING` | 已建索引记录，等待调度 |
| `SYNCING` | 拉取仓库快照（`sync_repository_snapshot`），记录 `total_files` |
| `PARSING` | `retrieval/parsing.py` 解析 + 切片 + 回填行号/符号/校验值 |
| `EMBEDDING` | 向量化并写入 Milvus collection `code_{index_id}` |
| `STORING` | 索引元数据版本收尾 |
| `COMPLETED` | 发布索引 + 触发恢复 |

**驱动方式**（`main.py:3155-3199`，`progress_single_repository_index`）：

- 终态直接返回：`if record.status in {"COMPLETED","FAILED"}: return False`（**幂等**）；
- `while record.status in INDEX_RUNNING_STAGES:` 在**一次调用内连续推进多个阶段**；
- **节流**：`if not force and (now - record.updated_at) < INDEX_STAGE_SECONDS: break`，
  `INDEX_STAGE_SECONDS = 2`（`main.py:378`）→ 所以一次请求最多推进到「距上次 ≥2 秒」的位置；
- 每步 `next_stage = INDEX_STAGE_ORDER[index(stage) + 1]`（线性推进）。

**失败处理**（`fail_repository_index`，`main.py:3038-3053`）：

```python
record.status = "FAILED"
record.error_stage = stage          # ← 保留失败在哪个阶段
record.error_message = message[:400]
record.active_key = None            # ← 释放占位，允许重建
```
并写 `INDEX_FAILED` 事件，摘要里带**阶段名**（`f"仓库索引在「{stage}」阶段失败"`）。

捕获分两层（`:3164-3170`）：`HTTPException` 取 `detail.message`；
其他 `Exception` 统一包成 `索引阶段执行异常：{error}` —— **两级都落到 FAILED，不留静默失败**。

完成时（`:3175-3195`）反向清理：`error_message=None`、`error_stage=None`、`active_key=None`，
写 `INDEX_COMPLETED`（含 id/commit/文件数/切片数/collection），
然后 **`resume_tasks_waiting_for_index(record)`** —— 这就是 `TASK-8817` 时间线里
`INDEX_RESUMED`（「索引完成，恢复任务主链路」）的来源。

`[码]` 全部已核对。

---

### Q3 `code_contexts` 在哪一步注入？为空时为什么不判任务失败？

**30 秒参考答案**

注入点在 **`main._prepare_context`**（`workflow.py:89-92` 调用），
命中后置 `CONTEXT_INDEXED`，事件 `CONTEXT_INDEXED` 的摘要会写「检索结果：N 段（Top-K K）」。

门槛常量（`main.py:383-384`）：

```python
MAX_CONTEXT_CHUNKS = 8      # 最多注入 8 段
MIN_CONTEXT_SCORE = 0.3     # 低于此分视为未命中
```

**为空不判失败的原因**：上下文缺失是**信息不足**，不是**执行错误**。
系统的处理是**转人工补充**而不是失败——`manual_reason = "context_missing"`，
由人在任务详情页补齐后从同一张图恢复。

**这个设计的正确性**：判失败会把「可恢复的信息缺口」变成「不可恢复的终态」，
逼用户重建任务并从零重跑索引/分析；转人工只消耗一次人工介入。
**代价要如实说**：任务会停在 `ISSUE_FETCHED` 之类的前序态等人工，
如果没人处理，它会一直挂着（`APPROVAL_OVERDUE_SECONDS = 86400` 这类逾期阈值是给审批用的，不是给它的）。

**落库点（已核对）**：`main.py:3867-3872`

```python
mark_task_manual_required(
    task,
    node="上下文聚合",
    reason=empty_reason,
    manual_reason="context_missing",
)
return False          # ← 注意：返回 False，不抛错、不置 FAILED
```

前面还写了一条**执行步骤，状态是 `blocked` 而非 `failed`**（`:3860-3866`，
`blocked_reason=empty_reason`）——**「阻塞」与「失败」在数据层就是两个不同的状态**，
这是「为空不判失败」最硬的证据。

**恢复路径**（`main.py:7568` 起）：`context_missing` 有专门的补上下文接口，
重新检索命中则解除并推进到分析队列；**仍为空则保持人工补充态**（`:7585-7586` 强调
与 `answer_required` **严格区分**——分析阻塞问题不走这个接口）。

`[码]` 常量、调用点、落库点、恢复路径**均已核对**。

> **顺带发现（F14）**：`main.py:2571` 的 `mark_task_failed(task, *, node, reason, manual_reason="context_missing")`
> ——**失败辅助函数的 `manual_reason` 默认值居然是 `context_missing`**。
> 而 `workflow.py:330 node_fail` 调用时**没传** `manual_reason`：
> ```python
> mark_task_failed(task, node=state.get("node") or "unknown", reason=state.get("reason") or "Agent 节点异常")
> ```
> → **任何 Agent 节点异常失败的任务，都会被标成「上下文缺失」**。这是误导性诊断，见日志 §15。

---

## B 档 · RAG 核心（主战场）

### Q4 一个 chunk 的 `chunk_id` 怎么算出来的？为什么用这个组合？

**30 秒参考答案**（`retrieval/parsing.py:212`）：

```python
chunk_id = f"chunk_{hashlib.md5(f'{repository_id}:{commit_sha}:{file.path}:{start_line}'.encode()).hexdigest()[:12]}"
```

即 **`chunk_` + md5(`repository_id:commit_sha:file.path:start_line`)[:12]**。

**为什么是这四个字段**：

| 字段 | 作用 |
| --- | --- |
| `repository_id` | 隔离仓库（不同仓库同路径不能撞） |
| `commit_sha` | **版本绑定** —— 同一文件在不同 commit 下 id 不同，跨版本不会串 |
| `file.path` | 定位文件 |
| `start_line` | **用行号而非 `chunk_index` 做锚点** —— 切片顺序会随解析器/参数变化，行号是文件里的真实位置 |

**必须知道的两个后果**：

1. **不含 `index_id`**：同一 `(repo, commit, path, start_line)` 在不同索引里 **id 相同**。
   这是刻意的（跨索引版本 id 稳定，便于对比），但因为 `milvus_pk = chunk_id`（`:227`），
   **同一 commit 在同一 collection 内重复索引会主键相同 → upsert 语义**。
2. 另有独立的**内容哈希** `checksum = sha256(text)[:16]`（`:211`）——
   **身份（chunk_id）与内容（checksum）分离**：位置没变但内容变了，id 不变、checksum 变。

`[码]` 已核对。

**追问点**：行号是怎么来的？`_assign_line_ranges`（`:138-162`）是**近似映射**——
按「前序消费」指针在原文行里**向前搜索首个非空行**（带前缀容错），
所以它是**近似而非 AST 精确偏移**（代码注释自述「保证行锚定且近似准确」）。**这条要主动承认。**

---

### Q5 `MAX_CONTEXT_CHUNKS=8` 和单文件配额 2 分别防什么？

**30 秒参考答案**

| 常量 | 值 | 防什么 |
| --- | --- | --- |
| `MAX_CONTEXT_CHUNKS` | 8（`main.py:383`）| **总量失控**：上下文窗口有限，塞太多既贵又稀释注意力 |
| `DEFAULT_MAX_CHUNKS_PER_FILE` | 2（`llamaindex_backend.py:49`）| **单文件挤占**：一个巨型文件若占满 8 个名额，模型只看到一个文件，**看不到跨文件的调用关系** |

实现是 `_dedupe_and_diversify`（`llamaindex_backend.py:119`），在三条检索路径上都被调用
（`:293` / `:402` / `:508`）——**去重 + 单文件配额在融合之后、返回之前统一执行**。

**内部实现（`llamaindex_backend.py:119-143`，已核对）**：

```python
per_file_limit = max_chunks_per_file if max_chunks_per_file is None else max(1, max_chunks_per_file)
seen_chunk_ids, file_counts, selected = set(), {}, []
for result in results:                    # results 已按分数排好序
    if result.chunk_id in seen_chunk_ids: continue
    seen_chunk_ids.add(result.chunk_id)
    file_path = result.file_path or result.chunk_id
    if per_file_limit is not None and file_counts.get(file_path, 0) >= per_file_limit:
        continue                          # ← 单文件超额就跳过，继续看下一个
    file_counts[file_path] = file_counts.get(file_path, 0) + 1
    selected.append(result)
    if len(selected) >= top_k: break
return selected
```

**三个必须讲清的点**：

1. **它是「贪心顺序过滤」，不是重排** —— 按传入顺序（已按分数降序）遍历，
   够 `top_k` 就停。所以**它不会把后面的高分片段提前**，只会**丢掉**超额片段。
   含义：配额是「宁可少给，也不让一个文件占满」。
2. **`max_chunks_per_file=None` 意味着「不限制」而不是「用默认值 2」** ——
   `per_file_limit` 会是 `None`，后面 `if per_file_limit is not None` 直接跳过检查。
   默认值 2 来自**调用方**（`DEFAULT_MAX_CHUNKS_PER_FILE`），不是这里兜底。**这是个容易记错的点。**
3. **去重按 `chunk_id`**（`seen_chunk_ids`）—— 同一 chunk 被向量路和词法路各召回一次时只留一条。

**配套的两个机制**（同一文件）：
- `_candidate_top_k`（`:146-150`）：`min(max(top_k, top_k × MULTIPLIER), MAX)` ——
  **先多召回候选，再去重降配到 `top_k`**，避免过滤后不够数；
- `_stable_sort_retrieved`（`:153-154`，注释 "Keep equal-score vector hits deterministic
  across Milvus query runs"）—— **让同分向量命中在多次查询间顺序稳定**，
  这是「两次对拍差异 0」的另一半保证（另一半是 `hybrid.py:135` 的确定性排序键）。

`[码]` 实现、`None` 语义、两处确定性机制**均已核对**。

**追问点**：这两个是「防模型看到太多」和「防模型只看到一个文件」——**方向相反的两个约束**，
要能同时说清，别只背数字。

---

### Q6 RRF 为什么用 `1/(K+rank)` 而不是直接加权求和？`K=60` 大了小了会怎样？

**30 秒参考答案**

`RRF_K = 60`（`llamaindex_backend.py:50`），企业版累加式（`enterprise_rag/hybrid.py:113`）：

```python
state["rrf"] += weight / (RRF_K + result.rank)
```

**为什么不用加权求和**：两路的**分数尺度不可比**。
我实测过：同一个查询下 `bm25` 最高分 **0.9501**（词法匹配），`vector` 最高分 **0.6452**（向量相似度），
`hybrid` 是 **0.2488**（RRF 尺度）。直接把 0.95 和 0.64 加权相加，
**等于让分数尺度大的那一路主导融合**，权重形同虚设。

RRF 只用**排名**，天然消除尺度差异——这是它被广泛采用的核心原因。

**`K` 的作用**：`K` 是平滑项，控制「名次差异的衰减速度」。

| `K` | 效果 |
| --- | --- |
| 偏小 | 第 1 名与第 2 名差距被放大 → **头部主导**，对单路的高排名更敏感 |
| 偏大 | 各名次趋近 → **更平均**，长尾更容易进结果，但区分度下降 |
| `K=60` | 原论文经验值，业界默认；本项目未做 `K` 的消融实验（`[推]`） |

`[码]` `RRF_K` 值与累加式已核对；`K` 的取值影响是**通用原理 + 我的解读**，项目未做该实验。

---

### Q7 团队版和企业版的 hybrid 融合公式差在哪？企业版为什么多一个 `support` 放大项？

**30 秒参考答案**

**企业版公式**（`enterprise_rag/hybrid.py:113 + 124-133`，实测读通）：

```python
rrf      = Σ_variants  weight_v / (RRF_K + rank_v)          # 变体加权 RRF
support  = max(dense_score, bm25_confidence)                 # 两路置信度取大
score    = rrf * (1.0 + support * 0.5)                       # ← support 放大
if symbol_boost > 0 and symbol_name matches any variant:
    score *= (1.0 + symbol_boost)                            # ← 符号定义加权
```

排序稳定性：`scored.sort(key=(-score, file_path, start_line))`（`:135-136`）——
**用文件路径 + 起始行做确定性 tie-break**，这是「两次对拍差异 0」可复现的前提。

`retrieval_source` 判定（`:140-145`）：两路都命中 → `hybrid`；只向量 → `vector`；只词法 → `bm25`。
**所以 `hybrid` 不是「用了混合策略」，而是「这条结果确实被两路同时命中」** ——
这解释了我实测为什么 8 条结果里只有 2 条标 `hybrid`。

**团队版差异**：团队链是固定权重（0.6 / 0.4）的加权融合，不走变体 RRF、不加 support/符号项。

**企业版为什么要 `support`（`[推]`，设计意图是我的解读）**：
纯 RRF **只用排名、丢掉分数幅度**——两路都排第 1 的两条不同 chunk 会得到**完全相同的 RRF**。
企业链的候选集被强过滤（`strict_metadata_filters`）压得很小，
**排名信号在小子集上区分度不足**，所以用 `support`（两路置信度取大）把「分数量级」重新注入，
让「高相似度但勉强进候选」排在「低相似度但排名靠前」之前。

`[码]` 公式、排序键、`retrieval_source` 判定、团队/企业差异已核对；**support 的设计动机未在代码中写明**，属我的解读。

---

### Q8 `strict_metadata_filters=True` 时过滤不可用会怎样？为什么这里不能降级？

**30 秒参考答案**（`retrieval/llamaindex_backend.py:218-238`，实测读通）

带 `filters` 的检索抛错时，分支逻辑是：

```python
if "filters" not in retriever_kwargs: raise            # 本来就没过滤 → 原样抛
if strict_metadata_filters:
    if not _fallback_allowed():
        raise RuntimeError("企业 RAG 元数据过滤不可用，拒绝无过滤向量查询")
    return _keyword_results(scoped_chunks, ...)         # 显式降级到「受限」关键词检索
# 团队版：pop 掉 filters，改应用层范围过滤后重试
```

**关键在 `scoped_chunks`**：降级后走的 `_keyword_results` **仍在受限子集内检索**，
**不是退回全量**。所以「降级」降的是**检索算法**，**不降安全边界**。

**为什么不能退回无过滤全量查询**：过滤条件的核心是
`tenant_id / project_id / repository_id / commit_sha / index_id`（企业 `RAGMetadataFilters`）。
一旦退回无过滤，**跨租户、跨仓库、跨 commit 的片段会进入候选**——
这不是「质量下降」，是**越权与串版本**。所以正确做法是**宁可失败**。

而且 `RAG_MOCK_FALLBACK=false` 时连关键词降级都不允许（`_fallback_allowed()` 为假）→ **直接抛错**。
这正是我让你回答的那个引子题：**`RAG_MOCK_FALLBACK=true` 的危险不是「结果变差」，
而是「你以为在做受控检索，实际在无边界召回，而且系统不会告诉你是降级的」。**

`[码]` 全部分支已核对。**这道题答对等于 A/B 档满分。**

---

## C 档 · 质量与诚实性（最加分）

### Q9 为什么 LLM rerank 在这份数据上比不重排还差？你从中学到什么？

**30 秒参考答案**

真实数据（`health-middle @ 73e7e54`，67 条评测集，top-k 8，真实 Milvus verified）：

| 档位 | MRR | NDCG@K | p50 延迟 |
| --- | ---: | ---: | ---: |
| baseline（hybrid 无重排） | **0.8172** | 0.7981 | 290ms |
| heuristic rerank | 0.8116 | 0.7934 | 227ms |
| **llm rerank（DeepSeek 摘要级）** | **0.7668–0.7873** | 0.7812–0.7961 | **4425ms** |
| cross-encoder（ms-marco-MiniLM-L-6-v2） | **0.8507** | **0.8090** | 331ms |

三个反直觉结论：

1. **LLM rerank 比不重排还差**，慢 **15 倍**，而且**两次跑结果不同**（0.7873 → 0.7668）→ **不可复现**；
2. **heuristic rerank 也略低于 baseline** → 「加了 rerank」不等于「变好了」；
3. **Cross-Encoder 才是赢家**，且确定性可复现（两次对拍差异 0）。

**学到了什么**（这才是被追问的点）：
- **「上大模型一定更好」是错觉**：LLM 擅长生成与推理，不擅长**细粒度相关性打分**；
  摘要级输入丢掉了排序需要的细节。
- **不可复现的优化不是优化**：无法对拍 = 无法证明有效 = 无法回归。
- **精排该用小模型**：CE 参数量小、专为相关性训练、延迟低 13 倍、结果确定。
- **必须有 A/B 才敢说「优化」**：这个项目因此把 `RAG_QUERY_EXPANSION` 默认设为 `false`
  （实测与仅 rerank 三项指标完全相同，无额外收益）——**用数据把功能关掉**。

`[码]` 数字来自 `docs/project-development/109` 与 `PROJECT_PROGRESS.md` E3-09 行，均为真实 Milvus 执行记录。

---

### Q10 `prec@K` 的 `1/K` 结构上限是什么？为什么 bm25 那份报告永远过不了 0.3？

**30 秒参考答案**

**结构上限**：当评测集里每个 case 的「真值（gold）」只有 **1 个 chunk** 时，
Precision@K 的分子最多是 1，分母是 K，所以

```text
prec@K ≤ 1/K
```

K=8 时上限 **0.125**；即便把 gold 提到 K 个位置全命中，也只有 1/8。
而 bm25 报告实测 `prec@K = 0.2034` —— **已经在结构上限附近**，
**无论怎么调排序都突破不了这个天花板**，因为问题不在排序，在**评测集与指标的匹配**。

**为什么永远过不了 0.3**：生产门限是 `prec@K ≥ 0.3`，但 0.3 > 1/8 = 0.125 的理论上限——
**对单 gold 数据集，0.3 这个门槛在数学上不可达**（除非 K ≤ 3）。
所以 bm25 报告的 `needs_improvement=true` **是结构决定的，不是模型差**。

**关键结论**：`--all-modes` 的 `reports[0]` 是 bm25 → 看第一行会得出「没达标」，
而**生产默认模式是 hybrid**（`prec@K .3396 ≥ 0.3`，`needs_improvement=false`）。
**同一份报告，看第一行和看 hybrid 行会得出相反结论**——这就是全景图 §6 说「两者不可混用」的原因。

**项目怎么处理的**：**如实保留 `needs_improvement=true`，没有粉饰成达标**。
`docs/107` §8 写了「由验收方按生产默认模式判定」，但权威口径应放在报告首页。

`[码]` 数字来自 `PROJECT_PROGRESS.md` E3-07 行与 `docs/107`。

---

### Q11 `verified` 和 `needs_improvement=false` 分别代表什么？为什么不能混？

**30 秒参考答案**

**两个正交维度**：

| 字段 | 回答的问题 | 取值 |
| --- | --- | --- |
| `validation_status` / `milvus_verified` / `fallback_used` | **这次评测可信吗？**（证据链真不真）| `verified` / `local_only` / `blocked` |
| `needs_improvement` | **质量达标吗？**（指标过没过阈值）| `true` / `false` |

所以存在**四种组合**，「verified 但 needs_improvement=true」是最常见也最容易被误读的一种。

**实测例证**（生产 `/api/enterprise/rag/reports` 报告列表）：

| 报告 | validation_status | needs_improvement |
| --- | --- | --- |
| `e3_06_health_middle_cloud_evidence.json` | **verified** | null（未评质量）|
| `e3_06_health_middle_verified.json` | verified | **true** |
| `e3_07_..._quality_tuned_hybrid_gate.json` | verified | **false** |

**为什么不能混**：
- 说「verified」= **证据可信**，不代表**质量达标**；
- 说「needs_improvement=false」= **过了阈值**，不代表**用的是真实后端**
  （`local_only` 的报告也可能是 `false`，那就毫无意义）。

**正确说法**：「在**真实 Milvus verified** 口径下，**生产默认 hybrid 模式**
`prec@K=0.3396 ≥ 0.3`，`needs_improvement=false`」——**一句话里两个维度都要交代**。

`[码]` 报告字段为生产接口实测值。

---

## D 档 · 架构（决定能不能带团队）

### Q12 为什么 `decide_next_agent` 要做成图与 Orchestrator 的共享真源？两边各写一套会怎样？

**30 秒参考答案**

`enterprise_runtime/graph.py:41 decide_next_agent` 的定位写在文件头 `:9`：
「**是 graph 与 orchestrator 共享的『单一路由真源』，防止两处状态机分歧**」。

**共享真源的机制**：两个调用方都只调它，不自己判：

| 调用方 | 位置 | 用途 |
| --- | --- | --- |
| 完整拓扑图的条件边 | `graph.py:92-112 _route()` | 声明式拓扑（`graph_for_workflow(2)`）|
| 每步运行时路由 | `graph.py:130-159 _router_node()` → `route_via_graph()` | 生产实际走这条（`orchestrator.py:643`）|

**两边各写一套会怎样**（这是真正要答的）：
1. **静默分叉**：图认为该回退 `CodeChange`，编排认为该进 `Test` —— 两边都「正常」，
   只有跑到某个特定 repair_round 才暴露，**这类 bug 极难复现**；
2. **修复只改一处**：改了图忘了编排，测试可能全绿（测试只覆盖一处），
   生产才炸；
3. **审计对不上**：回放时间线由编排写、拓扑由图标，两者矛盾时**无法判断谁对**，
   而「可回放、可追责」正是这个产品的卖点。

**约束 #6 的原文**（`graph.py:132-134`）：
「Orchestrator 不得再用 `workflow_steps()[index+1]` 手动模拟主路由」——
**这条约束就是为防分叉而立的**。

`[码]` 已核对；`decide_next_agent` 的四条分支（`planner_agent`→必审批 /
`review_security_agent` 不通过→回退 / `test_release_agent` 失败按 `repair_round` 分流 /
`code_change_agent`→proceed）与 `E4 专项 54 passed` 均已验证。

**加分回答（主动说出，不等问）**：
> 完整拓扑图 `build_agent_graph()` 在生产路径**没有调用者**（只有测试调 `graph_for_workflow`），
> 生产实际走单 `router` 节点图。两者共享 `decide_next_agent` 所以不会分叉，
> 但**改拓扑图的边不会改变行为**——这是可维护性风险，`graph.py:4` 的表述容易让人误解。

---

## 判分标准（我用来给你打分的口径）

| 档 | 题号 | 及格线 |
| --- | --- | --- |
| A 基础 | Q1–Q3 | 全部讲清主链与状态改写层次 |
| B RAG | Q4–Q8 | **Q7/Q8 必须答对**（企业版核心）；Q4 要主动承认行号是近似 |
| C 质量 | Q9–Q11 | 能讲出「LLM rerank 更差 + 不可复现」和两个正交维度 |
| D 架构 | Q12 | 能讲清分叉的三种后果，并**主动**提完整拓扑图未被生产调用 |

**达到 10/12 且 Q7、Q8、Q11 全对 → 可以对外讲这个项目。**
**Q3、Q4、Q10、Q12 的「易错点」都答出来 → 可以带人做。**

### 每题的「易错点」（背了但讲不深就会踩）

| 题 | 易错点 |
| --- | --- |
| Q1 | 把 `TaskStatus` 说成 `class`（实际是 `Literal`）；漏掉 `TEST_RUNNING`/`TEST_FAILED` |
| Q2 | 说成「失败就 FAILED」但讲不出 `error_stage` 与 `active_key=None` 的作用 |
| Q3 | 说「为空就失败」——**恰恰相反，是转人工** |
| Q4 | 说 `chunk_id` 含 `index_id`（**不含**）；不知道行号是近似映射 |
| Q5 | 只背数字，讲不出两个约束方向相反 |
| Q6 | 说「RRF 是为了更准」——真实原因是**分数尺度不可比** |
| Q7 | 把 `retrieval_source=hybrid` 理解成「用了混合策略」（真实含义是**两路都命中**）|
| Q8 | 说「降级到关键词检索」但漏掉**仍在受限子集内** |
| Q9 | 说「LLM rerank 更贵所以更差」——贵是结果，**根因是摘要级输入丢了排序细节** |
| Q10 | 把 `1/K` 天花板说成「模型不够好」 |
| Q11 | 把 `verified` 当质量达标 |
| Q12 | 只答「避免重复代码」，答不出**静默分叉**的三种后果 |
