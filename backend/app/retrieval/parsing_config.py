"""LlamaIndex 代码解析/切片的集中参数（独立配置模块，解耦 main 与 parsing）。

这些参数只影响「代码解析 + 切片」这一步；embedding / 检索 / 存储参数不在此列。

OPT-10 消融：支持环境变量覆盖（import 时读取一次，供子进程隔离的切片参数对比；
默认值与历史完全一致，不设置环境变量时行为不变）：
- RAG_CHUNK_LINES          行窗口目标（CodeSplitter chunk_lines；当前 llama-index 版本仅校验、切分实际由 max_chars 驱动）
- RAG_CHUNK_OVERLAP_LINES  相邻切片重叠行（CodeSplitter chunk_lines_overlap，须 > 0）
- RAG_CHUNK_MAX_CHARS      单切片字符上限（CodeSplitter max_chars / 切片内容截断）
非法值回退默认；低于下限钳制（行/重叠/字符均 ≥ 1）。
"""

from __future__ import annotations

import os


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(minimum, value)


# 文件级过滤/截断
INDEX_FILE_SIZE_LIMIT = 200_000   # 单文件字节上限
INDEX_FILE_LINE_LIMIT = 2_000     # 单文件行数上限

# LlamaIndex CodeSplitter 行参数
INDEX_TARGET_WINDOW_LINES = _env_int("RAG_CHUNK_LINES", 40)     # CodeSplitter 目标行数
INDEX_WINDOW_OVERLAP_LINES = _env_int("RAG_CHUNK_OVERLAP_LINES", 10)  # CodeSplitter 相邻切片重叠行数

# 单 chunk 内容截断字符数
MAX_CHUNK_CHARS = _env_int("RAG_CHUNK_MAX_CHARS", 4_000)
