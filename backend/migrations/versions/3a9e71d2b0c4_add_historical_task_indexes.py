"""add historical_task_summaries and historical_task_indexes (DEV-021)

Revision ID: 3a9e71d2b0c4
Revises: 8f3d21c7a0b1
Create Date: 2026-09-09
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '3a9e71d2b0c4'
down_revision: Union[str, Sequence[str], None] = '8f3d21c7a0b1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from sqlalchemy import inspect

    existing = set(inspect(op.get_bind()).get_table_names())

    if "historical_task_summaries" not in existing:
        op.create_table(
            'historical_task_summaries',
            sa.Column('id', sa.String(length=80), primary_key=True),
            sa.Column('task_id', sa.String(length=80), nullable=False),
            sa.Column('project_id', sa.String(length=80), nullable=False),
            sa.Column('repository_id', sa.String(length=80), nullable=True),
            # 注意：TEXT 列在 MySQL 8 严格模式不允许 DEFAULT（1101），故只 NOT NULL、不留 server_default；
            # 取值由代码层默认提供（ORM/仓储写入）。
            sa.Column('source_task_title', sa.Text(), nullable=False),
            sa.Column('issue_summary', sa.Text(), nullable=False),
            sa.Column('context_summary', sa.Text(), nullable=False),
            sa.Column('analysis_summary', sa.Text(), nullable=False),
            sa.Column('plan_summary', sa.Text(), nullable=False),
            sa.Column('diff_summary', sa.Text(), nullable=False),
            sa.Column('test_summary', sa.Text(), nullable=False),
            sa.Column('pr_summary', sa.Text(), nullable=False),
            sa.Column('replay_summary', sa.Text(), nullable=False),
            sa.Column('searchable_text', sa.Text(), nullable=False),
            sa.Column('summary_version', sa.Integer(), nullable=False, server_default='1'),
            sa.Column('completed_at', sa.DateTime(timezone=True), nullable=False),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index('ix_historical_task_summaries_task_id', 'historical_task_summaries', ['task_id'])
        op.create_index('ix_historical_task_summaries_project_id', 'historical_task_summaries', ['project_id'])

    if "historical_task_indexes" not in existing:
        op.create_table(
            'historical_task_indexes',
            sa.Column('id', sa.String(length=80), primary_key=True),
            sa.Column('summary_id', sa.String(length=80), nullable=False),
            sa.Column('task_id', sa.String(length=80), nullable=False),
            sa.Column('project_id', sa.String(length=80), nullable=False),
            sa.Column('embedding_provider', sa.String(length=40), nullable=False, server_default=''),
            sa.Column('embedding_model', sa.String(length=80), nullable=False, server_default=''),
            sa.Column('vector_document_id', sa.String(length=120), nullable=True),
            sa.Column('index_status', sa.String(length=20), nullable=False, server_default='PENDING'),
            sa.Column('failure_reason', sa.Text(), nullable=True),
            sa.Column('failure_context', sa.Text(), nullable=True),
            sa.Column('indexed_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('retry_count', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index('ix_historical_task_indexes_summary_id', 'historical_task_indexes', ['summary_id'])
        op.create_index('ix_historical_task_indexes_task_id', 'historical_task_indexes', ['task_id'])
        op.create_index('ix_historical_task_indexes_project_id', 'historical_task_indexes', ['project_id'])


def downgrade() -> None:
    op.drop_table('historical_task_indexes')
    op.drop_table('historical_task_summaries')