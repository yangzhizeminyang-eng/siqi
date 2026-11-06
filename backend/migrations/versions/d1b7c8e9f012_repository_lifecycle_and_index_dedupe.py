"""isolate repository replacements and deduplicate running indexes"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d1b7c8e9f012"
down_revision: Union[str, Sequence[str], None] = "3a9e71d2b0c4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    repository_columns = {column["name"] for column in inspector.get_columns("repositories")}
    with op.batch_alter_table("repositories", schema=None) as batch_op:
        if "is_active" not in repository_columns:
            batch_op.add_column(sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()))
        if "archived_at" not in repository_columns:
            batch_op.add_column(sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))
        if "superseded_by_id" not in repository_columns:
            batch_op.add_column(sa.Column("superseded_by_id", sa.String(length=80), nullable=True))
        if "ix_repositories_is_active" not in {index["name"] for index in inspector.get_indexes("repositories")}:
            batch_op.create_index("ix_repositories_is_active", ["is_active"], unique=False)
        if "ix_repositories_superseded_by_id" not in {index["name"] for index in inspector.get_indexes("repositories")}:
            batch_op.create_index("ix_repositories_superseded_by_id", ["superseded_by_id"], unique=False)

    index_columns = {column["name"] for column in inspector.get_columns("repository_indexes")}
    with op.batch_alter_table("repository_indexes", schema=None) as batch_op:
        if "active_key" not in index_columns:
            batch_op.add_column(sa.Column("active_key", sa.String(length=180), nullable=True))

    # 老库可能已有重复的运行中记录。保留最新一条占用唯一键，其余标记失败，绝不删除历史行。
    running = ("PENDING", "SYNCING", "PARSING", "EMBEDDING", "STORING")
    rows = bind.execute(
        sa.text(
            "SELECT id, repository_id, commit_sha, status, created_at "
            "FROM repository_indexes WHERE status IN :statuses ORDER BY created_at DESC, id DESC"
        ).bindparams(sa.bindparam("statuses", expanding=True)),
        {"statuses": list(running)},
    ).mappings().all()
    seen: set[tuple[str, str]] = set()
    for row in rows:
        key = (str(row["repository_id"]), str(row["commit_sha"]))
        if key in seen:
            bind.execute(
                sa.text(
                    "UPDATE repository_indexes SET status='FAILED', "
                    "error_stage='DEDUPLICATION', error_message='历史重复索引记录，已保留为历史失败记录', active_key=NULL "
                    "WHERE id=:id"
                ),
                {"id": row["id"]},
            )
        else:
            seen.add(key)
            bind.execute(
                sa.text("UPDATE repository_indexes SET active_key=:active_key WHERE id=:id"),
                {"id": row["id"], "active_key": f"{key[0]}:{key[1]}"},
            )

    # 必须在历史重复记录处理完之后再建立唯一索引，否则老数据会阻断迁移。
    current_indexes = {index["name"] for index in sa.inspect(bind).get_indexes("repository_indexes")}
    if "uq_repository_indexes_active_key" not in current_indexes:
        with op.batch_alter_table("repository_indexes", schema=None) as batch_op:
            batch_op.create_index("uq_repository_indexes_active_key", ["active_key"], unique=True)


def downgrade() -> None:
    with op.batch_alter_table("repository_indexes", schema=None) as batch_op:
        batch_op.drop_index("uq_repository_indexes_active_key")
        batch_op.drop_column("active_key")
    with op.batch_alter_table("repositories", schema=None) as batch_op:
        batch_op.drop_index("ix_repositories_superseded_by_id")
        batch_op.drop_index("ix_repositories_is_active")
        batch_op.drop_column("superseded_by_id")
        batch_op.drop_column("archived_at")
        batch_op.drop_column("is_active")
