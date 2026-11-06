"""add schema compat columns

Revision ID: 3274ebaca315
Revises: d1b7c8e9f012
Create Date: 2026-09-11 11:16:12.841278

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3274ebaca315'
down_revision: Union[str, Sequence[str], None] = 'd1b7c8e9f012'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """为既有库补上新增列（幂等：已存在则跳过），兼容 create_all 新建库与 Alembic 增量升级。"""
    bind = op.get_bind()
    insp = sa.inspect(bind)

    def add_if_missing(table: str, cols: dict[str, str]) -> None:
        existing = {c["name"] for c in insp.get_columns(table)}
        for name, ddl in cols.items():
            if name not in existing:
                op.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")

    add_if_missing("diffs", {"real_change_source": "VARCHAR(40) NULL"})
    add_if_missing("repository_indexes", {"active_key": "VARCHAR(180) NULL"})
    add_if_missing("repositories", {
        "is_active": "TINYINT(1) NOT NULL DEFAULT 1",
        "archived_at": "DATETIME NULL",
        "superseded_by_id": "VARCHAR(80) NULL",
    })
    try:  # active_key 唯一索引（幂等）
        ix = {i["name"] for i in insp.get_indexes("repository_indexes")}
        if "uq_repository_indexes_active_key" not in ix:
            op.create_index("uq_repository_indexes_active_key", "repository_indexes", ["active_key"], unique=True)
    except Exception:
        pass


def downgrade() -> None:
    """回退：逐个移除新增列（容忍部分库缺列）。"""
    for table, cols in [
        ("diffs", ["real_change_source"]),
        ("repository_indexes", ["active_key"]),
        ("repositories", ["archived_at", "superseded_by_id", "is_active"]),
    ]:
        for c in cols:
            try:
                op.execute(f"ALTER TABLE {table} DROP COLUMN {c}")
            except Exception:
                pass
