"""Версия задачи для защиты общих полей от параллельных правок.

Revision ID: f42b8d0e315a
Revises: e31a7c9d204f
"""

import sqlalchemy as sa
from alembic import op

from src.db.task_attention_ddl import REVISION_SQL

revision = "f42b8d0e315a"
down_revision = "e31a7c9d204f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column(
            "revision",
            sa.Integer(),
            nullable=False,
            server_default="1",
            comment="Монотонная версия общих полей и назначений задачи.",
        ),
    )
    op.create_check_constraint("ck_tasks_revision_positive", "tasks", "revision >= 1")
    op.execute(sa.text(REVISION_SQL))


def downgrade() -> None:
    op.drop_constraint("ck_tasks_revision_positive", "tasks", type_="check")
    op.drop_column("tasks", "revision")
    # После удаления поля возвращаем прежнее сравнение содержимого задачи.
    op.execute(sa.text(REVISION_SQL.replace(",'revision'", "")))
