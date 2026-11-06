"""add projects.default_test_command (project-level default test command)

Revision ID: c9a4e71f2b50
Revises: b7c3d51a9e02
Create Date: 2026-09-08

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'c9a4e71f2b50'
down_revision: Union[str, Sequence[str], None] = 'b7c3d51a9e02'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from sqlalchemy import inspect

    existing_columns = {
        column["name"] for column in inspect(op.get_bind()).get_columns("projects")
    }
    if "default_test_command" in existing_columns:
        return

    with op.batch_alter_table('projects', schema=None) as batch_op:
        batch_op.add_column(sa.Column('default_test_command', sa.String(length=160), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('projects', schema=None) as batch_op:
        batch_op.drop_column('default_test_command')
