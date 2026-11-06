"""add comments and notifications tables (DEV-019)

Revision ID: 8f3d21c7a0b1
Revises: c9a4e71f2b50
Create Date: 2026-09-09
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '8f3d21c7a0b1'
down_revision: Union[str, Sequence[str], None] = 'c9a4e71f2b50'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from sqlalchemy import inspect

    existing_tables = set(inspect(op.get_bind()).get_table_names())
    if "comments" not in existing_tables:
        op.create_table(
            'comments',
            sa.Column('id', sa.String(length=80), primary_key=True),
            sa.Column('task_id', sa.String(length=80), nullable=False),
            sa.Column('project_id', sa.String(length=80), nullable=False),
            sa.Column('repository_id', sa.String(length=80), nullable=False),
            sa.Column('author_id', sa.String(length=80), nullable=False),
            sa.Column('author_name', sa.String(length=120), nullable=False),
            sa.Column('body', sa.Text(), nullable=False),
            sa.Column('mentioned_user_ids', sa.JSON(), nullable=True),
            sa.Column('reference_type', sa.String(length=40), nullable=True),
            sa.Column('reference_id', sa.String(length=80), nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
            sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index('ix_comments_task_id', 'comments', ['task_id'])
        op.create_index('ix_comments_project_id', 'comments', ['project_id'])
        op.create_index('ix_comments_repository_id', 'comments', ['repository_id'])
        op.create_index('ix_comments_created_at', 'comments', ['created_at'])

    if "notifications" not in existing_tables:
        op.create_table(
            'notifications',
            sa.Column('id', sa.String(length=80), primary_key=True),
            sa.Column('recipient_id', sa.String(length=80), nullable=False),
            sa.Column('task_id', sa.String(length=80), nullable=False),
            sa.Column('type', sa.String(length=40), nullable=False),
            sa.Column('title', sa.String(length=200), nullable=False),
            sa.Column('body', sa.Text(), nullable=False),
            sa.Column('actor_id', sa.String(length=80), nullable=False),
            sa.Column('actor_name', sa.String(length=120), nullable=False),
            sa.Column('priority', sa.String(length=20), nullable=False, server_default='normal'),
            sa.Column('read_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index('ix_notifications_recipient_id', 'notifications', ['recipient_id'])
        op.create_index('ix_notifications_task_id', 'notifications', ['task_id'])
        op.create_index('ix_notifications_type', 'notifications', ['type'])
        op.create_index('ix_notifications_created_at', 'notifications', ['created_at'])


def downgrade() -> None:
    op.drop_table('notifications')
    op.drop_table('comments')
