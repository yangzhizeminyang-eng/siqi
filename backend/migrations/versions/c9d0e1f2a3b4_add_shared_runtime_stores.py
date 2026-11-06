"""fix: shared persistent stores for RAG evaluation runs, index lifecycle and trace spans

FIX-01/02/03：把原先的进程内字典真源迁移到数据库（跨 API/Worker/重启一致）。
迁移保持前向-only；表已存在时跳过，兼容 create_all 建出的测试库。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c9d0e1f2a3b4"
down_revision: Union[str, Sequence[str], None] = "a7b8c9d0e1f2"
branch_labels = None
depends_on = None


def _table_exists(name: str) -> bool:
    return name in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    if not _table_exists("enterprise_rag_evaluation_runs"):
        op.create_table(
            "enterprise_rag_evaluation_runs",
            sa.Column("run_id", sa.String(length=80), primary_key=True),
            sa.Column("tenant_id", sa.String(length=80), nullable=False),
            sa.Column("dataset", sa.String(length=200), nullable=False),
            sa.Column("retrieval_mode", sa.String(length=20), nullable=False, server_default="hybrid"),
            sa.Column("top_k", sa.Integer(), nullable=False, server_default="8"),
            sa.Column("execution_scope", sa.String(length=20), nullable=False, server_default="isolated"),
            sa.Column("status", sa.String(length=20), nullable=False),
            sa.Column("validation_status", sa.String(length=20), nullable=False, server_default=""),
            sa.Column("report_name", sa.String(length=200), nullable=True),
            sa.Column("summary", sa.JSON(), nullable=False),
            # MySQL 不允许 TEXT 带 DEFAULT；应用层 default="" 已足够，避免 1101。
            sa.Column("error", sa.Text(), nullable=False),
            sa.Column("requested_by", sa.String(length=120), nullable=False, server_default=""),
            sa.Column("requested_role", sa.String(length=40), nullable=False, server_default="developer"),
            sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Index("ix_enterprise_rag_evaluation_runs_tenant_id", "tenant_id"),
            sa.Index("ix_enterprise_rag_evaluation_runs_status", "status"),
            sa.Index("ix_enterprise_rag_evaluation_runs_created_at", "created_at"),
        )

    if not _table_exists("enterprise_rag_index_lifecycles"):
        op.create_table(
            "enterprise_rag_index_lifecycles",
            sa.Column("index_id", sa.String(length=80), primary_key=True),
            sa.Column("tenant_id", sa.String(length=80), nullable=False),
            sa.Column("repository_id", sa.String(length=80), nullable=False, server_default=""),
            sa.Column("project_id", sa.String(length=80), nullable=False, server_default=""),
            sa.Column("base_branch", sa.String(length=120), nullable=False, server_default=""),
            sa.Column("commit_sha", sa.String(length=80), nullable=False, server_default=""),
            sa.Column("collection_name", sa.String(length=200), nullable=False, server_default=""),
            sa.Column("embedding_provider", sa.String(length=80), nullable=False, server_default=""),
            sa.Column("embedding_model", sa.String(length=200), nullable=False, server_default=""),
            sa.Column("embedding_dimension", sa.Integer(), nullable=True),
            sa.Column("embedding_source", sa.String(length=40), nullable=False, server_default=""),
            sa.Column("embedding_fingerprint", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("status", sa.String(length=20), nullable=False),
            sa.Column("chunk_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("error_stage", sa.String(length=80), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("superseded_by", sa.String(length=80), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Index("ix_enterprise_rag_index_lifecycles_tenant_id", "tenant_id"),
            sa.Index("ix_enterprise_rag_index_lifecycles_repository_id", "repository_id"),
            sa.Index("ix_enterprise_rag_index_lifecycles_project_id", "project_id"),
            sa.Index("ix_enterprise_rag_index_lifecycles_status", "status"),
            sa.Index("ix_enterprise_rag_index_lifecycles_created_at", "created_at"),
        )

    if not _table_exists("enterprise_runtime_trace_spans"):
        op.create_table(
            "enterprise_runtime_trace_spans",
            sa.Column("seq", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("span_id", sa.String(length=80), nullable=False),
            sa.Column("trace_id", sa.String(length=120), nullable=False, server_default=""),
            sa.Column("tenant_id", sa.String(length=80), nullable=False, server_default=""),
            sa.Column("category", sa.String(length=20), nullable=False),
            sa.Column("name", sa.String(length=200), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="ok"),
            sa.Column("error_kind", sa.String(length=120), nullable=False, server_default=""),
            sa.Column("duration_ms", sa.Float(), nullable=True),
            sa.Column("attributes", sa.JSON(), nullable=False),
            sa.Column("tokens", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("span_id", name="uq_trace_span_id"),
            sa.Index("ix_enterprise_runtime_trace_spans_trace_id", "trace_id"),
            sa.Index("ix_enterprise_runtime_trace_spans_tenant_id", "tenant_id"),
            sa.Index("ix_enterprise_runtime_trace_spans_category", "category"),
            sa.Index("ix_enterprise_runtime_trace_spans_created_at", "created_at"),
        )


def downgrade() -> None:
    for table in (
        "enterprise_runtime_trace_spans",
        "enterprise_rag_index_lifecycles",
        "enterprise_rag_evaluation_runs",
    ):
        if _table_exists(table):
            op.drop_table(table)
