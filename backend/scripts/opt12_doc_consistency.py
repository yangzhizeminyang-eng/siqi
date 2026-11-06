#!/usr/bin/env python
"""OPT-12 文档一致性检查：`docs/project-development` 语料的引用防腐。

背景：OPT-11 将 `main.py` 从 9177 行拆到 7651 行后，历史文档中的
`main.py:<行号>` 引用进一步失效。本脚本把「行号引用」列为禁用形态，
改用「文件路径（符号名）」锚点，并校验锚点仍然可解析。

检查项（仅扫描 `docs/project-development/**.md`，排除 `assets/`：
历史证据文件不得改写）：
- A `stale_line_ref`：正文不得出现 `main.py:<数字>` 行号引用；
- B `missing_path`：正文引用的带路径 `.py` 文件必须真实存在
  （按 仓库根 / backend/ / frontend/ / backend/app/ 四个前缀解析；
  `../` 前缀按「从 backend/ 出发」归一化，兼容命令示例的 cwd 相对写法；
  合成演示仓库路径白名单见 `_SYNTHETIC_DEMO_PATHS`，本就不存在于本仓库）；
- C `missing_symbol`：`路径.py（符号…）` 锚点中的标识符必须能在该文件中
  找到（防文件搬走后锚点悬空）。

退出码：0 = 无违规；1 = 有违规。`--json` 输出结构化结果（供测试断言）。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from functools import lru_cache
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CORPUS_ROOT = REPO_ROOT / "docs" / "project-development"

_MAIN_LINE_REF = re.compile(r"main\.py:\d+(?:-\d+)?")
_PATH_TOKEN = re.compile(r"(?<![\w/.-])((?:backend/|frontend/)?[\w.]+(?:/[\w.-]+)+\.py)")
_CITATION = re.compile(r"\.py[）)]?\s*（([^（）]{1,80}?)）")
_TOKEN_SPLIT = re.compile(r"[\s/、，,&·()（）`“”]+")
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{2,}$")
# 合成演示仓库路径：评测/演示文档中脱敏虚构的被检仓库文件，本就不存在于本仓库，
# 不作为缺失处理（其真实性由对应评测报告的合成语料 SHA 绑定，而非文件系统）。
_SYNTHETIC_DEMO_PATHS = frozenset({
    "src/refund.py",
    "src/payment/refund.py",
    # dev_demo_dev018.py 演示种子语料中的伪仓库文件（检索命中的代码片段）
    "services/callback/timeout_handler.py",
})
_EXCLUDED_DIRS = frozenset({
    "__pycache__", ".venv", "venv", ".data", "node_modules", ".git",
    "dist", "build", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".idea",
})
_STOPWORDS = {
    "POST", "GET", "PUT", "PATCH", "DELETE", "API", "HTTP", "JSON", "OPT", "DEV",
    "MVP", "LLM", "RAG", "APP", "SQL", "RUN", "URL", "ID", "IP", "CORS", "SSE",
    "CSS", "HTML", "TSV", "CSV", "CLI", "SDK", "DB", "ORM", "E2E", "KPI", "MFA",
    "OIDC", "RBAC", "Agent", "LangGraph", "Redis", "Celery", "Docker", "Milvus",
    "MySQL", "Gitee", "GitHub", "GitLab", "Python", "FastAPI", "Vue", "Optional",
    "None", "True", "False", "String", "Integer", "Boolean", "Query", "Path",
    "class", "def", "import", "from", "return", "async", "await", "lambda",
    "side", "tools", "backend", "frontend", "tests", "scripts", "assets",
    "session", "services", "view", "views", "models", "stores", "types",
}


def iter_docs(corpus_root: Path | None = None) -> list[Path]:
    root = corpus_root or CORPUS_ROOT
    if not root.exists():
        return []
    docs: list[Path] = []
    for path in sorted(root.rglob("*.md")):
        if "assets" in path.parts:
            continue
        docs.append(path)
    return docs


def _doc_label(doc: Path, corpus_root: Path) -> str:
    for base in (REPO_ROOT, corpus_root):
        try:
            return doc.relative_to(base).as_posix()
        except ValueError:
            continue
    return doc.as_posix()


@lru_cache(maxsize=1)
def _repo_py_index() -> tuple[str, ...]:
    found: list[str] = []
    for dirpath, dirnames, filenames in os.walk(REPO_ROOT):
        dirnames[:] = [d for d in dirnames if d not in _EXCLUDED_DIRS]
        for name in filenames:
            if name.endswith(".py"):
                rel = Path(dirpath, name).relative_to(REPO_ROOT).as_posix()
                found.append(rel)
    return tuple(sorted(found))


def resolve_path(token: str) -> Path | None:
    prefixes = (
        REPO_ROOT,
        REPO_ROOT / "backend",
        REPO_ROOT / "frontend",
        REPO_ROOT / "backend" / "app",
    )
    tokens = [token]
    if token.startswith("../"):
        tokens.append(token[3:])
    for prefix in prefixes:
        for name in tokens:
            candidate = prefix / name
            if candidate.is_file():
                return candidate
    # 后缀唯一匹配：兼容「包内相对写法」（例如企业运行时文档中的 agents/tools.py）；
    # 仅当全仓唯一命中才接受，避免掩盖歧义引用。
    for name in tokens:
        suffix = "/" + name
        matches = [rel for rel in _repo_py_index() if rel.endswith(suffix)]
        if len(matches) == 1:
            return REPO_ROOT / matches[0]
    return None


def _check_line_refs(label: str, text: str) -> list[dict]:
    issues = []
    for match in _MAIN_LINE_REF.finditer(text):
        line_no = text[: match.start()].count("\n") + 1
        issues.append({
            "check": "stale_line_ref",
            "doc": label,
            "line": line_no,
            "detail": f"发现行号引用 `{match.group(0)}`；请改用「文件（符号名）」锚点",
        })
    return issues


def _check_paths(label: str, text: str) -> list[dict]:
    issues = []
    for match in _PATH_TOKEN.finditer(text):
        token = match.group(1)
        if token in _SYNTHETIC_DEMO_PATHS:
            continue
        if resolve_path(token) is None:
            line_no = text[: match.start()].count("\n") + 1
            issues.append({
                "check": "missing_path",
                "doc": label,
                "line": line_no,
                "detail": f"引用的文件不存在：{token}",
            })
    return issues


def _check_symbols(label: str, text: str) -> list[dict]:
    issues = []
    for match in _CITATION.finditer(text):
        anchor = match.group(1)
        # 锚点路径必须「紧邻」引用：窗口取到 `.py` 末尾，并要求路径 token 恰好止于此，
        # 避免把同一行/相邻行的其它路径误配给本引用。
        adjacent_end = match.start() + 3
        path_match = None
        for candidate in _PATH_TOKEN.finditer(text[:adjacent_end]):
            path_match = candidate
        if path_match is None or path_match.end() != adjacent_end:
            continue
        path = resolve_path(path_match.group(1))
        if path is None or path.suffix != ".py":
            continue
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for token in _TOKEN_SPLIT.split(anchor):
            if not _IDENTIFIER.match(token) or token in _STOPWORDS:
                continue
            if not re.search(rf"\b{re.escape(token)}\b", source):
                line_no = text[: match.start()].count("\n") + 1
                issues.append({
                    "check": "missing_symbol",
                    "doc": label,
                    "line": line_no,
                    "detail": f"锚点符号 `{token}` 在 {path_match.group(1)} 中找不到",
                })
    return issues


def collect_violations(corpus_root: Path | None = None) -> list[dict]:
    root = corpus_root or CORPUS_ROOT
    violations: list[dict] = []
    for doc in iter_docs(root):
        text = doc.read_text(encoding="utf-8", errors="replace")
        label = _doc_label(doc, root)
        violations.extend(_check_line_refs(label, text))
        violations.extend(_check_paths(label, text))
        violations.extend(_check_symbols(label, text))
    return violations


def main() -> int:
    parser = argparse.ArgumentParser(description="OPT-12 文档一致性检查")
    parser.add_argument("--json", action="store_true", help="输出 JSON 结果")
    parser.add_argument(
        "--corpus",
        type=Path,
        default=None,
        help="自定义语料根目录（用于历史基线对比/回归实验；默认 docs/project-development）",
    )
    args = parser.parse_args()

    corpus_root = (args.corpus or CORPUS_ROOT).resolve()
    violations = collect_violations(corpus_root)
    docs_count = len(iter_docs(corpus_root))
    try:
        corpus_label = corpus_root.relative_to(REPO_ROOT).as_posix() + " (assets 除外)"
    except ValueError:
        corpus_label = corpus_root.as_posix() + " (assets 除外)"
    result = {
        "schema": "opt12-doc-consistency-v1",
        "corpus": corpus_label,
        "docs_scanned": docs_count,
        "violations": violations,
        "violation_count": len(violations),
    }

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"扫描 {docs_count} 份文档，发现 {len(violations)} 条违规")
        for item in violations:
            print(f"[{item['check']}] {item['doc']}:{item['line']} {item['detail']}")
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
