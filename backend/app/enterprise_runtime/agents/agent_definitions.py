"""企业版 E4-02 六个专业 Agent 的静态定义（独立 system prompt / schema / 工具白名单 / 上下文域 / 产物权限）。"""

from __future__ import annotations

from app.enterprise_runtime.artifacts.models import ArtifactType
from app.enterprise_runtime.agents.definition import (
    CTX_ISSUE,
    CTX_REPOSITORY,
    CTX_SECURITY,
    TOOL_BUILD_DIFF,
    TOOL_COLLECT_EVIDENCE,
    TOOL_DRAFT_PR,
    TOOL_RAG_SEARCH,
    TOOL_READ_COMMENTS,
    TOOL_READ_DEPS,
    TOOL_READ_DIFF,
    TOOL_READ_HISTORY,
    TOOL_READ_ISSUE,
    TOOL_READ_PLAN,
    TOOL_READ_SECURITY_POLICY,
    TOOL_REPO_READ,
    TOOL_RUN_TEST,
    TOOL_SEMGREP_SCAN,
    TOOL_WORKSPACE_READ,
    TOOL_WORKSPACE_WRITE,
    AgentDefinition,
)
from app.enterprise_runtime.agents.schema import (
    CodeChangeInput,
    CodeChangeOutput,
    PlanInput,
    PlanOutput,
    RequirementInput,
    RequirementOutput,
    ResearchInput,
    ResearchOutput,
    ReviewInput,
    ReviewOutput,
    TestReleaseInput,
    TestReleaseOutput,
)

# RequirementAgent：需求理解、问题补全；只读 Issue/评论/历史；只写 RequirementArtifact。
def _requirement() -> AgentDefinition:
    return AgentDefinition(
        agent_type="requirement_agent",
        name="RequirementAgent",
        system_prompt=(
            "你是需求分析 Agent。基于给定 Issue、评论与历史摘要，产出结构化的需求清单、验收标准与开放问题。"
            "只能读取 Issue/评论/历史，绝不访问仓库代码、不修改任何文件、不写库。"
        ),
        input_schema=RequirementInput,
        output_schema=RequirementOutput,
        tool_whitelist=frozenset({TOOL_READ_ISSUE, TOOL_READ_COMMENTS, TOOL_READ_HISTORY}),
        readonly_tools=frozenset({TOOL_READ_ISSUE, TOOL_READ_COMMENTS, TOOL_READ_HISTORY}),
        context_scope=CTX_ISSUE,
        allowed_read_artifact_types=(),
        allowed_write_artifact_types=(ArtifactType.REQUIREMENT.value,),
        provider="langchain",
    )


def _research() -> AgentDefinition:
    return AgentDefinition(
        agent_type="repository_research_agent",
        name="RepositoryResearchAgent",
        system_prompt=(
            "你是代码研究 Agent。基于已批准的需求与授权仓库，阅读仓库结构与 RAG 检索结果，整理代码上下文。"
            "只读授权仓库与 RAG 索引，不可访问其他租户或未授权项目；只写 ResearchArtifact / CodeContextArtifact。"
            "工具使用要收敛：先做一次相关 RAG 检索，再读取最多 3 个最相关文件或符号；"
            "不要重复相同路径或查询，不要全仓扫描。已有足够文件、符号和代码片段支撑结构化结论后，立即停止工具调用并返回 JSON。"
        ),
        input_schema=ResearchInput,
        output_schema=ResearchOutput,
        tool_whitelist=frozenset({TOOL_REPO_READ, TOOL_RAG_SEARCH}),
        readonly_tools=frozenset({TOOL_REPO_READ, TOOL_RAG_SEARCH}),
        context_scope=CTX_REPOSITORY,
        allowed_read_artifact_types=(ArtifactType.REQUIREMENT.value,),
        allowed_write_artifact_types=(ArtifactType.RESEARCH.value, ArtifactType.CODE_CONTEXT.value),
        provider="langchain",
    )


def _planner() -> AgentDefinition:
    return AgentDefinition(
        agent_type="planner_agent",
        name="PlannerAgent",
        system_prompt=(
            "你是方案规划 Agent。只读取 RequirementArtifact / ResearchArtifact / CodeContextArtifact，产出结构化修改方案。"
            "绝对禁止修改代码、禁止调用任何代码/文件/执行工具；只写 PlanArtifact。"
        ),
        input_schema=PlanInput,
        output_schema=PlanOutput,
        tool_whitelist=frozenset(),  # 无工具：服务端不发放任何工具（C7）
        readonly_tools=frozenset(),
        context_scope="",
        allowed_read_artifact_types=(ArtifactType.REQUIREMENT.value, ArtifactType.RESEARCH.value,
                                     ArtifactType.CODE_CONTEXT.value),
        allowed_write_artifact_types=(ArtifactType.PLAN.value,),
        provider="langchain",
    )


def _code_change() -> AgentDefinition:
    return AgentDefinition(
        agent_type="code_change_agent",
        name="CodeChangeAgent",
        system_prompt=(
            "你是代码变更 Agent。仅当 PlanArtifact 已审批通过后方可执行：在隔离工作区写入修改并生成 patch/diff。"
            "禁止写主分支、禁止改动生产配置、禁止直接发布；只写 PatchArtifact / DiffArtifact。DeepAgent 时关闭子 Agent 委派。"
            "严格按一次性执行协议：优先使用输入中的代码上下文，最多读取 3 个相关文件；每个目标文件最多写入一次，"
            "完成计划中的修改后只调用一次 build_diff。拿到 diff 后立即停止所有工具调用并返回 JSON，禁止继续探索、重复读写或全仓扫描。"
        ),
        input_schema=CodeChangeInput,
        output_schema=CodeChangeOutput,
        tool_whitelist=frozenset({TOOL_WORKSPACE_READ, TOOL_WORKSPACE_WRITE, TOOL_BUILD_DIFF}),
        readonly_tools=frozenset({TOOL_WORKSPACE_READ, TOOL_BUILD_DIFF}),
        context_scope=CTX_REPOSITORY,
        allowed_read_artifact_types=(ArtifactType.PLAN.value, ArtifactType.CODE_CONTEXT.value),
        allowed_write_artifact_types=(ArtifactType.PATCH.value, ArtifactType.DIFF.value),
        provider="langchain",
    )


def _review() -> AgentDefinition:
    return AgentDefinition(
        agent_type="review_security_agent",
        name="ReviewSecurityAgent",
        system_prompt=(
            "你是代码审查与安全 Agent。只读取 PlanArtifact / DiffArtifact / 工作区 / 依赖与安全策略，判定是否通过。"
            "如果不能直接修改代码；发现问题时给出问题与修复建议，让流程回到 CodeChangeAgent。"
            "严格按一次性审查协议：先读取一次 PlanArtifact、DiffArtifact、依赖和安全策略；"
            "再按 diff 中的文件最多读取 3 个工作区文件，每个文件最多一次。完成审查结论后立即返回 JSON，"
            "可对授权隔离工作区中的单个相对路径调用 semgrep_scan 做本地规则检查；禁止重复读取同一路径、继续探索或修改文件。"
        ),
        input_schema=ReviewInput,
        output_schema=ReviewOutput,
        tool_whitelist=frozenset({TOOL_READ_PLAN, TOOL_READ_DIFF, TOOL_WORKSPACE_READ, TOOL_READ_DEPS, TOOL_READ_SECURITY_POLICY, TOOL_SEMGREP_SCAN}),
        readonly_tools=frozenset({TOOL_READ_PLAN, TOOL_READ_DIFF, TOOL_WORKSPACE_READ, TOOL_READ_DEPS, TOOL_READ_SECURITY_POLICY, TOOL_SEMGREP_SCAN}),
        context_scope=CTX_SECURITY,
        allowed_read_artifact_types=(ArtifactType.PLAN.value, ArtifactType.DIFF.value, ArtifactType.PATCH.value),
        allowed_write_artifact_types=(ArtifactType.REVIEW.value, ArtifactType.SECURITY_RISK.value),
        provider="langchain",
    )


def _test_release() -> AgentDefinition:
    return AgentDefinition(
        agent_type="test_release_agent",
        name="TestReleaseAgent",
        system_prompt=(
            "你是测试与发布 Agent。基于 DiffArtifact 与审查结论，在隔离环境执行白名单测试命令，产出测试报告、PR 描述与发布证据。"
            "禁止绕过审批合并或发布；只写 TestReportArtifact / PRDescriptionArtifact / ReleaseEvidenceArtifact。"
            "严格按一次性收敛协议执行：read_diff、workspace_read、run_test、draft_pr、collect_evidence "
            "每个工具最多调用一次；工具返回 BLOCKED、不可用或测试未执行时不得重试、不得换命令绕过，"
            "把阻塞原因写入 test_cases/summary/release_evidence 后立即返回 JSON。"
            "run_test 的 command 只能使用 pytest、python 或 echo 开头的白名单命令；"
            "文档/代码检查优先使用 `python -c \"...\"`，不要使用 test、grep、find、cd、shell 管道或其他未授权命令。"
            "完成一次测试结果、一次 PR 草稿和一次证据收集后立即返回 JSON，禁止继续探索或重复调用工具。"
        ),
        input_schema=TestReleaseInput,
        output_schema=TestReleaseOutput,
        tool_whitelist=frozenset({TOOL_READ_DIFF, TOOL_WORKSPACE_READ, TOOL_RUN_TEST, TOOL_DRAFT_PR, TOOL_COLLECT_EVIDENCE}),
        readonly_tools=frozenset({TOOL_READ_DIFF, TOOL_WORKSPACE_READ}),
        test_whitelist_only_tools=frozenset({TOOL_RUN_TEST}),
        context_scope=CTX_REPOSITORY,
        allowed_read_artifact_types=(ArtifactType.DIFF.value, ArtifactType.REVIEW.value),
        allowed_write_artifact_types=(ArtifactType.TEST_REPORT.value, ArtifactType.PR_DESCRIPTION.value,
                                      ArtifactType.RELEASE_EVIDENCE.value),
        provider="langchain",
    )


AGENT_DEFINITIONS: dict[str, AgentDefinition] = {
    d.agent_type: d
    for d in (_requirement(), _research(), _planner(), _code_change(), _review(), _test_release())
}
