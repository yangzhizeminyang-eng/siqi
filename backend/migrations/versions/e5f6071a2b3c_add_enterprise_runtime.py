"""enterprise E4-01: runtime task + checkpoint tables (tenant-scoped, versioned)"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e5f6071a2b3c"
down_revision: Union[str, Sequence[str], None] = "c4d5e6f7089a"
branch_labels = None
depends_on = None


def _table_exists(name: str) -> bool:
    return name in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    bind = op.get_bind()
    if not _table_exists("enterprise_runtime_tasks"):
        op.create_table(
            "enterprise_runtime_tasks",
            sa.Column("id", sa.String(length=120), primary_key=True),
            sa.Column("tenant_id", sa.String(length=80), nullable=False),
            sa.Column("project_id", sa.String(length=120), nullable=False, server_default=""),
            sa.Column("repository_id", sa.String(length=120), nullable=False, server_default=""),
            sa.Column("title", sa.String(length=200), nullable=False, server_default=""),
            sa.Column("status", sa.String(length=40), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("workflow_version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("checkpoint_version", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("current_step", sa.String(length=120), nullable=True),
            sa.Column("completed_steps", sa.JSON(), nullable=False),
            sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("approval_state", sa.String(length=40), nullable=False, server_default="not_required"),
            sa.Column("execution_state", sa.String(length=40), nullable=False, server_default="idle"),
            sa.Column("idempotency_key", sa.String(length=200), nullable=False),
            sa.Column("wait_human_reason", sa.Text(), nullable=True),
            sa.Column("takeover", sa.JSON(), nullable=True),
            sa.Column("lease_worker", sa.String(length=120), nullable=True),
            sa.Column("lease_version", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("steps", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Index("ix_enterprise_runtime_tasks_tenant_id", "tenant_id"),
            sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_runtime_tenant_idem"),
        )
    # 兼容：早期建表未含 steps/lease_expires_at 时补列
    columns = {c["name"] for c in sa.inspect(bind).get_columns("enterprise_runtime_tasks")}
    if "steps" not in columns:
        with op.batch_alter_table("enterprise_runtime_tasks") as batch_op:
            batch_op.add_column(sa.Column("steps", sa.JSON(), nullable=False))
    columns = {c["name"] for c in sa.inspect(bind).get_columns("enterprise_runtime_tasks")}
    if "lease_expires_at" not in columns:
        with op.batch_alter_table("enterprise_runtime_tasks") as batch_op:
            batch_op.add_column(sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True))
    if not _table_exists("enterprise_runtime_checkpoints"):
        op.create_table(
            "enterprise_runtime_checkpoints",
            sa.Column("id", sa.String(length=120), primary_key=True),
            sa.Column("task_id", sa.String(length=120), nullable=False),
            sa.Column("tenant_id", sa.String(length=80), nullable=False),
            sa.Column("step_id", sa.String(length=160), nullable=False),
            sa.Column("workflow_version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("checkpoint_version", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("completed_steps", sa.JSON(), nullable=False),
            sa.Column("current_step", sa.String(length=120), nullable=True),
            sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("approval_state", sa.String(length=40), nullable=False, server_default="not_required"),
            sa.Column("execution_state", sa.String(length=40), nullable=False, server_default="idle"),
            sa.Column("idempotency_keys", sa.JSON(), nullable=False),
            sa.Column("output_summary", sa.Text(), nullable=False),
            sa.Column("worker_id", sa.String(length=120), nullable=True),
            sa.Column("lease_version", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Index("ix_enterprise_runtime_checkpoints_task_id", "task_id"),
            sa.Index("ix_enterprise_runtime_checkpoints_tenant_id", "tenant_id"),
        )


def downgrade() -> None:
    if _table_exists("enterprise_runtime_checkpoints"):
        op.drop_table("enterprise_runtime_checkpoints")
    if _table_exists("enterprise_runtime_tasks"):
        op.drop_table("enterprise_runtime_tasks")