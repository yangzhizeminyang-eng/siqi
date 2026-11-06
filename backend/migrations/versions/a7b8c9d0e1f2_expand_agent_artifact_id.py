"""E4-02: allow versioned artifact primary keys to contain agent/type names.

Chain: f6a7b8c9d0e1 -> a7b8c9d0e1f2.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a7b8c9d0e1f2"
down_revision: Union[str, Sequence[str], None] = "f6a7b8c9d0e1"
branch_labels = None
depends_on = None


def _table_exists(name: str) -> bool:
    return name in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    if not _table_exists("enterprise_agent_artifacts"):
        return
    with op.batch_alter_table("enterprise_agent_artifacts") as batch:
        batch.alter_column(
            "id",
            existing_type=sa.String(length=64),
            type_=sa.String(length=120),
            existing_nullable=False,
        )


def downgrade() -> None:
    if not _table_exists("enterprise_agent_artifacts"):
        return
    with op.batch_alter_table("enterprise_agent_artifacts") as batch:
        batch.alter_column(
            "id",
            existing_type=sa.String(length=120),
            type_=sa.String(length=64),
            existing_nullable=False,
        )
