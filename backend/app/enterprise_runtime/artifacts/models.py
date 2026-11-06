"""企业版 E4-02 Artifact 产物协议：版本化、租户隔离、引用图、内容指纹。

Agent 之间只通过 ArtifactStore 与 input_refs/output_refs 交换结果；不直接把上一个
Agent 的完整自然语言输出塞入下一个。content 只存结构化 payload 或 content_ref。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def content_hash_for(content: dict | None, content_ref: str | None) -> str:
    """内容指纹：对结构化 payload（规范 JSON）或内容引用算 sha256。"""
    if content is None and content_ref is None:
        return ""
    raw = content_ref or json.dumps(content, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class ArtifactType(str, Enum):
    REQUIREMENT = "RequirementArtifact"
    RESEARCH = "ResearchArtifact"
    CODE_CONTEXT = "CodeContextArtifact"
    PLAN = "PlanArtifact"
    PATCH = "PatchArtifact"
    DIFF = "DiffArtifact"
    REVIEW = "ReviewArtifact"
    SECURITY_RISK = "SecurityRiskArtifact"
    TEST_REPORT = "TestReportArtifact"
    PR_DESCRIPTION = "PRDescriptionArtifact"
    RELEASE_EVIDENCE = "ReleaseEvidenceArtifact"


class AgentArtifact(BaseModel):
    """一次 Agent 产出版本化产物。artifact_id 全局唯一（跨修复轮次不复用）。"""

    artifact_id: str
    task_id: str
    tenant_id: str
    agent_type: str
    artifact_type: ArtifactType
    run_id: str | None = None
    version: int = 1
    input_refs: list[str] = Field(default_factory=list)
    output_refs: list[str] = Field(default_factory=list)
    status: str = "active"  # active | superseded
    schema_version: int = 1
    content: dict | None = None
    content_ref: str | None = None  # 当内容过大时指向持久化引用（如工作区路径 hash）
    content_hash: str = ""
    idempotency_key: str = ""
    created_at: datetime = Field(default_factory=_utcnow)

    def __init__(self, **data: Any) -> None:
        data.setdefault("content_hash", content_hash_for(data.get("content"), data.get("content_ref")))
        super().__init__(**data)