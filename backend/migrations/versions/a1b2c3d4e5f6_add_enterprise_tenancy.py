"""enterprise E1-01: multi-tenant tables + backfill tenant_id on business data"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "3274ebaca315"
branch_labels = None
depends_on = None

DEFAULT_TENANT = "tenant_default"

# 业务表：补 tenant_id 并回填默认租户（企业版数据隔离；历史团队版数据归属 default）。
BUSINESS_TABLES = [
    "projects",
    "repositories",
    "tasks",
    "team_requests",
    "repository_indexes",
    "retrieval_records",
    "audit_events",
    "code_chunks",
]


def _add_tenant_id_indempotently(table: str) -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {c["name"] for c in inspector.get_columns(table)}
    if "tenant_id" not in columns:
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.add_column(sa.Column("tenant_id", sa.String(length=80), nullable=True))
    bind.execute(
        sa.text(f"UPDATE {table} SET tenant_id=:tenant WHERE tenant_id IS NULL"),
        {"tenant": DEFAULT_TENANT},
    )


def _create_tenant_index_indempotently(table: str) -> None:
    inspector = sa.inspect(op.get_bind())
    current = {i["name"] for i in inspector.get_indexes(table)}
    index_name = f"ix_{table}_tenant_id"
    if index_name not in current:
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.create_index(index_name, ["tenant_id"], unique=False)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = set(inspector.get_table_names())

    if "tenants" not in existing:
        op.create_table(
            "tenants",
            sa.Column("id", sa.String(length=80), primary_key=True),
            sa.Column("name", sa.String(length=120), nullable=False),
            sa.Column("description", sa.Text(), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Index("ix_tenants_name", "name"),
        )
    if "organizations" not in existing:
        op.create_table(
            "organizations",
            sa.Column("id", sa.String(length=80), primary_key=True),
            sa.Column("tenant_id", sa.String(length=80), nullable=False),
            sa.Column("name", sa.String(length=120), nullable=False),
            sa.Column("description", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Index("ix_organizations_tenant_id", "tenant_id"),
        )
    if "tenant_memberships" not in existing:
        op.create_table(
            "tenant_memberships",
            sa.Column("id", sa.String(length=80), primary_key=True),
            sa.Column("tenant_id", sa.String(length=80), nullable=False),
            sa.Column("user_id", sa.String(length=80), nullable=False),
            sa.Column("user_name", sa.String(length=120), nullable=False, server_default=""),
            sa.Column("organization_id", sa.String(length=80), nullable=True),
            sa.Column("role", sa.String(length=40), nullable=False, server_default="developer"),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Index("ix_tenant_memberships_tenant_id", "tenant_id"),
            sa.Index("ix_tenant_memberships_user_id", "user_id"),
            sa.Index("ix_tenant_memberships_status", "status"),
            sa.UniqueConstraint("tenant_id", "user_id", name="uq_membership_tenant_user"),
        )

    # 业务资源补 tenant_id 并回填默认租户
    for table in BUSINESS_TABLES:
        inspector = sa.inspect(bind)
        if table in inspector.get_table_names():
            _add_tenant_id_indempotently(table)
    # 给部分关键表补 tenant_id 索引（可选，提升隔离查询性能）
    for table in ("projects", "tasks", "team_requests", "audit_events"):
        inspector = sa.inspect(bind)
        if table in inspector.get_table_names():
            _create_tenant_index_indempotently(table)


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    existing = set(inspector.get_table_names())
    for table in BUSINESS_TABLES:
        if table not in existing:
            continue
        columns = {c["name"] for c in inspector.get_columns(table)}
        current = {i["name"] for i in inspector.get_indexes(table)}
        if f"ix_{table}_tenant_id" in current:
            with op.batch_alter_table(table, schema=None) as batch_op:
                batch_op.drop_index(f"ix_{table}_tenant_id")
        if "tenant_id" in columns:
            with op.batch_alter_table(table, schema=None) as batch_op:
                batch_op.drop_column("tenant_id")

    for table in ("tenant_memberships", "organizations", "tenants"):
        if table in existing:
            op.drop_table(table)