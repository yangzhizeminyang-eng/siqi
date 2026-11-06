"""add task pr_* columns (real Gitee PR creation)

Revision ID: b7c3d51a9e02
Revises: 4486135ff281
Create Date: 2026-09-08

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'b7c3d51a9e02'
down_revision: Union[str, Sequence[str], None] = '4486135ff281'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """给 tasks 表加 pr_number/pr_url/pr_status 列（真实 Gitee PR 创建结果）。"""
    from sqlalchemy import inspect

    existing_columns = {
        column["name"] for column in inspect(op.get_bind()).get_columns("tasks")
    }
    with op.batch_alter_table('tasks', schema=None) as batch_op:
        if "pr_number" not in existing_columns:
            batch_op.add_column(sa.Column('pr_number', sa.String(length=80), nullable=True))
        if "pr_url" not in existing_columns:
            batch_op.add_column(sa.Column('pr_url', sa.String(length=500), nullable=True))
        if "pr_status" not in existing_columns:
            batch_op.add_column(sa.Column('pr_status', sa.String(length=40), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('tasks', schema=None) as batch_op:
        batch_op.drop_column('pr_status')
        batch_op.drop_column('pr_url')
        batch_op.drop_column('pr_number')
