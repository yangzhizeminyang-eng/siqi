"""enterprise E2-01: approval applications table for governance gating"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b3c4d5e6f708"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "approval_applications" in inspector.get_table_names():
        return
    op.create_table(
        "approval_applications",
        sa.Column("id", sa.String(length=80), primary_key=True),
        sa.Column("tenant_id", sa.String(length=80), nullable=False),
        sa.Column("actor_id", sa.String(length=80), nullable=False),
        sa.Column("actor_name", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("resource_type", sa.String(length=80), nullable=False),
        sa.Column("resource_id", sa.String(length=80), nullable=False),
        sa.Column("operation", sa.String(length=80), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("request_summary", sa.JSON(), nullable=True),
        sa.Column("risk_level", sa.String(length=4), nullable=False),
        sa.Column("decision", sa.String(length=20), nullable=False),
        sa.Column("approval_status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=120), nullable=False),
        sa.Column("created_by", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by_id", sa.String(length=80), nullable=True),
        sa.Column("approved_by_name", sa.String(length=120), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("executed_by_id", sa.String(length=80), nullable=True),
        sa.Column("executor_result", sa.JSON(), nullable=True),
        sa.Column("resource_version", sa.String(length=120), nullable=True),
        sa.Column("allowed_approvers", sa.JSON(), nullable=True),
        sa.Column("approval_mode", sa.String(length=20), nullable=False, server_default="single"),
        sa.Column("approval_meta", sa.JSON(), nullable=True),
        sa.Column("secondary_confirmation", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Index("ix_approval_applications_tenant_id", "tenant_id"),
        sa.Index("ix_approval_applications_actor_id", "actor_id"),
        sa.Index("ix_approval_applications_resource_type", "resource_type"),
        sa.Index("ix_approval_applications_resource_id", "resource_id"),
        sa.Index("ix_approval_applications_operation", "operation"),
        sa.Index("ix_approval_applications_approval_status", "approval_status"),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_approval_tenant_idem"),
    )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "approval_applications" in inspector.get_table_names():
        op.drop_table("approval_applications")