"""enterprise E2-01 (hardening): execution tracking columns for multi-replica approval execution"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c4d5e6f7089a"
down_revision: Union[str, Sequence[str], None] = "b3c4d5e6f708"
branch_labels = None
depends_on = None


def _add_column(table: str, column: sa.Column) -> None:
    inspector = sa.inspect(op.get_bind())
    if any(c["name"] == column.name for c in inspector.get_columns(table)):
        return
    with op.batch_alter_table(table) as batch_op:
        batch_op.add_column(column)


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "approval_applications" not in inspector.get_table_names():
        return
    _add_column("approval_applications", sa.Column("execution_attempt_id", sa.String(length=120), nullable=True))
    _add_column("approval_applications", sa.Column("async_result_id", sa.String(length=120), nullable=True))
    _add_column("approval_applications", sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"))
    _add_column("approval_applications", sa.Column("started_at", sa.DateTime(timezone=True), nullable=True))
    _add_column("approval_applications", sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True))
    _add_column("approval_applications", sa.Column("execution_error", sa.Text(), nullable=True))


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "approval_applications" not in inspector.get_table_names():
        return
    with op.batch_alter_table("approval_applications") as batch_op:
        for name in ("execution_attempt_id", "async_result_id", "retry_count",
                     "started_at", "finished_at", "execution_error"):
            if any(c["name"] == name for c in inspector.get_columns("approval_applications")):
                batch_op.drop_column(name)