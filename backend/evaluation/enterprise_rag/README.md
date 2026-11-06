# 企业 RAG 离线评测集（E3-05）

本目录提供可复现、脱敏、项目级真实研发问题评测集与质量基线报告。

- `e3_05_baseline.jsonl`：32 条评测样本（≥30），覆盖精确符号/错误码/API 路径/文件路径/自然语言/跨文件/
  stale/fresh/403/422 边界/无答案/source_type 过滤；字段含 case_id/query/project_id/repository_id/commit_sha|ref/
  expected_chunk_ids|expected_file_paths/tags/difficulty/rationale/expected_status。
- `e3_05_baseline_report.json`：由 CLI 生成的基线报告（summary/metrics_by_tag/failed_cases/config_snapshot）。
- `seed.py`：脱敏合成 demo 仓库（内存态），供检索命中与可复现。
- 种子与评测集**不含真实商业代码正文/token/密码/JWT/恢复码**。

复现：`python -m scripts.evaluate_enterprise_rag e3_05_baseline.jsonl -o e3_05_baseline_report.json --memory-seed --top-k 8`
（本地内存态；云端真实 Milvus 未验证）。
