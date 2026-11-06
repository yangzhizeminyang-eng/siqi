"""企业版 E4-02 Agent 定义与工具白名单（服务端强制边界，非仅 Prompt）。

每个 Agent 拥有独立实例：system_prompt / input·output schema / 工具白名单 / 上下文域 /
允许读写 artifact 类型。Agent 之间不直接调用，只经 ArtifactStore 与 refs 交换。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar


# 工具名统一常量（白名单以这些名字为准；真实包装见 tools.py）
TOOL_READ_ISSUE = "read_issue"
TOOL_READ_COMMENTS = "read_comments"
TOOL_READ_HISTORY = "read_history"
TOOL_REPO_READ = "repo_read"
TOOL_RAG_SEARCH = "rag_search"
TOOL_WORKSPACE_READ = "workspace_read"
TOOL_WORKSPACE_WRITE = "workspace_write"
TOOL_BUILD_DIFF = "build_diff"
TOOL_READ_PLAN = "read_plan"
TOOL_READ_DIFF = "read_diff"
TOOL_READ_DEPS = "read_deps"
TOOL_READ_SECURITY_POLICY = "read_security_policy"
TOOL_RUN_TEST = "run_test"
TOOL_DRAFT_PR = "draft_pr"
TOOL_COLLECT_EVIDENCE = "collect_evidence"
TOOL_SEMGREP_SCAN = "semgrep_scan"


class ToolPolicyRule:
    """单条工具策略：允许哪些工具、是否只读、是否需测试命令白名单。"""

    def __init__(self, tool_names: set[str], *, readonly: set[str] | None = None,
                 write_allowed: set[str] | None = None, test_whitelist_only: set[str] | None = None):
        self.tool_names = set(tool_names)
        self.readonly = set(readonly or set())
        self.write_allowed = set(write_allowed or set())
        self.test_whitelist_only = set(test_whitelist_only or set())


@dataclass(frozen=True)
class AgentDefinition:
    """六个专业 Agent 的静态定义。policy（工具白名单与作用域）由定义固定，不随模型自扩。"""

    agent_type: str
    name: str
    system_prompt: str
    input_schema: type
    output_schema: type
    tool_whitelist: frozenset[str] = field(default_factory=frozenset)
    readonly_tools: frozenset[str] = field(default_factory=frozenset)
    test_whitelist_only_tools: frozenset[str] = field(default_factory=frozenset)
    context_scope: str = ""  # 允许读取上下文来源（issue/repository/security）
    allowed_read_artifact_types: tuple[str, ...] = ()
    allowed_write_artifact_types: tuple[str, ...] = ()
    provider: str = "langchain"  # "langchain" | "deep"

    def tool_names(self) -> list[str]:
        return sorted(self.tool_whitelist)

    def allows_write(self) -> bool:
        return any(t not in self.readonly_tools for t in self.tool_whitelist)


# 上下文来源常量
CTX_ISSUE = "issue"
CTX_REPOSITORY = "repository"
CTX_SECURITY = "security"
