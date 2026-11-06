"""企业版 E4-02 每个 Agent 独立的输入/输出 Schema（结构化，经 ArtifactStore 传递）。"""

from __future__ import annotations

from pydantic import BaseModel, Field


# ---------------- RequirementAgent ----------------
class RequirementInput(BaseModel):
    task_id: str
    project_id: str = ""
    repository_id: str = ""
    issue: str = ""
    comments: list[str] = Field(default_factory=list)
    history_summary: str = ""


class RequirementOutput(BaseModel):
    summary: str = ""
    requirements: list[dict] = Field(default_factory=list)  # [{id,description,acceptance}]
    open_questions: list[str] = Field(default_factory=list)
    completeness: str = "complete"  # complete | partial


# ---------------- RepositoryResearchAgent ----------------
class ResearchInput(BaseModel):
    task_id: str
    requirement_ref: str  # RequirementArtifact artifact_id
    repository_id: str = ""
    project_id: str = ""


class ResearchOutput(BaseModel):
    findings: list[dict] = Field(default_factory=list)  # [{file,summary,relevance}]
    files: list[str] = Field(default_factory=list)
    symbols: list[str] = Field(default_factory=list)
    code_context: list[dict] = Field(default_factory=list)


# ---------------- PlannerAgent ----------------
class PlanInput(BaseModel):
    task_id: str
    requirement_ref: str
    research_ref: str
    context_ref: str


class PlanOutput(BaseModel):
    objectives: list[str] = Field(default_factory=list)
    steps: list[dict] = Field(default_factory=list)  # [{file,change,reason,risk}]
    test_command: str = ""
    approval_request_note: str = ""


# ---------------- CodeChangeAgent ----------------
class CodeChangeInput(BaseModel):
    task_id: str
    plan_ref: str  # 已审批 PlanArtifact
    context_ref: str


class CodeChangeOutput(BaseModel):
    workspace_path: str = ""
    files_changed: list[str] = Field(default_factory=list)
    patch: str = ""
    diff_ref: str = ""
    test_command: str = ""


# ---------------- ReviewSecurityAgent ----------------
class ReviewInput(BaseModel):
    task_id: str
    plan_ref: str
    diff_ref: str


class ReviewOutput(BaseModel):
    approved: bool = False
    conclusions: list[str] = Field(default_factory=list)
    issues: list[dict] = Field(default_factory=list)  # [{severity,file,desc,fix_hint}]
    security_risks: list[dict] = Field(default_factory=list)


# ---------------- TestReleaseAgent ----------------
class TestReleaseInput(BaseModel):
    task_id: str
    diff_ref: str
    review_ref: str


class TestReleaseOutput(BaseModel):
    passed: bool = False
    summary: str = ""
    test_cases: list[dict] = Field(default_factory=list)
    pr_description: str = ""
    release_evidence: dict = Field(default_factory=dict)


INPUT_MODELS = {
    "requirement_agent": RequirementInput,
    "repository_research_agent": ResearchInput,
    "planner_agent": PlanInput,
    "code_change_agent": CodeChangeInput,
    "review_security_agent": ReviewInput,
    "test_release_agent": TestReleaseInput,
}

OUTPUT_MODELS = {
    "requirement_agent": RequirementOutput,
    "repository_research_agent": ResearchOutput,
    "planner_agent": PlanOutput,
    "code_change_agent": CodeChangeOutput,
    "review_security_agent": ReviewOutput,
    "test_release_agent": TestReleaseOutput,
}