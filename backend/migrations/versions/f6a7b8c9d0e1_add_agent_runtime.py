"""enterprise E4-02: multi-agent collaboration — agent_runs + agent_artifacts tables + repair fields.

链：e5f6071a2b3c (E4-01) -> f6a7b8c9d0e1 (本迁移)。绝不复用既有 revision。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f6a7b8c9d0e1"
down_revision: Union[str, Sequence[str], None] = "e5f6071a2b3c"
branch_labels = None
depends_on = None


def _table_exists(name: str) -> bool:
    return name in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    bind = op.get_bind()
    # C4: enterprise_runtime_tasks 增补修复轮次字段（旧库无此列时补，兼容 SQLite/MySQL）
    if _table_exists("enterprise_runtime_tasks"):
        columns = {c["name"] for c in sa.inspect(bind).get_columns("enterprise_runtime_tasks")}
        if "repair_round" not in columns:
            with op.batch_alter_table("enterprise_runtime_tasks") as b:
                b.add_column(sa.Column("repair_round", sa.Integer(), nullable=False, server_default="0"))
        columns = {c["name"] for c in sa.inspect(bind).get_columns("enterprise_runtime_tasks")}
        if "max_repair_rounds" not in columns:
            with op.batch_alter_table("enterprise_runtime_tasks") as b:
                b.add_column(sa.Column("max_repair_rounds", sa.Integer(), nullable=False, server_default="2"))

    if not _table_exists("enterprise_agent_runs"):
        op.create_table(
            "enterprise_agent_runs",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("run_id", sa.String(length=64), nullable=False),
            sa.Column("task_id", sa.String(length=120), nullable=False),
            sa.Column("tenant_id", sa.String(length=80), nullable=False),
            sa.Column("agent_type", sa.String(length=64), nullable=False),
            sa.Column("step_id", sa.String(length=160), nullable=True),
            sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("repair_round", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("status", sa.String(length=24), nullable=False),
            sa.Column("prompt_version", sa.String(length=40), nullable=False, server_default="1"),
            sa.Column("input_refs", sa.JSON(), nullable=False),
            sa.Column("output_refs", sa.JSON(), nullable=False),
            sa.Column("policy_context", sa.JSON(), nullable=True),
            sa.Column("model", sa.String(length=200), nullable=False, server_default=""),
            sa.Column("usage", sa.JSON(), nullable=True),
            sa.Column("error_reason", sa.Text(), nullable=True),
            sa.Column("idempotency_key", sa.String(length=200), nullable=False, server_default=""),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
            sa.Index("ix_agent_runs_tenant_id", "tenant_id"),
            sa.Index("ix_agent_runs_task_id", "task_id"),
            sa.Index("ix_agent_runs_agent_type", "agent_type"),
            sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_agent_run_tenant_idem"),
        )

    if not _table_exists("enterprise_agent_artifacts"):
        op.create_table(
            "enterprise_agent_artifacts",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("artifact_id", sa.String(length=120), nullable=False),
            sa.Column("task_id", sa.String(length=120), nullable=False),
            sa.Column("run_id", sa.String(length=64), nullable=True),
            sa.Column("tenant_id", sa.String(length=80), nullable=False),
            sa.Column("agent_type", sa.String(length=64), nullable=False),
            sa.Column("artifact_type", sa.String(length=80), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("status", sa.String(length=24), nullable=False, server_default="active"),
            sa.Column("input_refs", sa.JSON(), nullable=False),
            sa.Column("output_refs", sa.JSON(), nullable=False),
            sa.Column("content", sa.JSON(), nullable=True),
            sa.Column("content_ref", sa.Text(), nullable=True),
            sa.Column("content_hash", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("idempotency_key", sa.String(length=200), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Index("ix_agent_artifacts_tenant_id", "tenant_id"),
            sa.Index("ix_agent_artifacts_task_id", "task_id"),
            sa.Index("ix_agent_artifacts_run_id", "run_id"),
            sa.Index("ix_agent_artifacts_agent_type", "agent_type"),
            sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_agent_artifact_tenant_idem"),
        )


def downgrade() -> None:
    if _table_exists("enterprise_agent_artifacts"):
        op.drop_table("enterprise_agent_artifacts")
    if _table_exists("enterprise_agent_runs"):
        op.drop_table("enterprise_agent_runs")
    # 回滚不再反向移除 task 列（向前兼容；如需可手动 batch 移除）