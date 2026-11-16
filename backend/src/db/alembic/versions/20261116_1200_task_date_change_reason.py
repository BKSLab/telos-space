"""Единая история плановых дат с причиной без изменения прежних записей.

Revision ID: f23d8b410ce2
Revises: f12c7a309bd1
"""

import sqlalchemy as sa
from alembic import op

revision = "f23d8b410ce2"
down_revision = "f12c7a309bd1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Добавляет необязательную причину и тип события изменения обеих дат."""
    op.add_column(
        "task_activity",
        sa.Column(
            "reason",
            sa.Text(),
            nullable=True,
            comment="Общая причина изменения или снятия назначенных дат задачи.",
        ),
    )
    op.execute("ALTER TYPE task_activity_event_type ADD VALUE IF NOT EXISTS 'DATES_CHANGED'")


def downgrade() -> None:
    """Запрещает откат с новыми событиями или причинами без потери данных.

    Значение enum сохраняется, как и существующие события истории.
    """
    op.execute(
        """DO $$ BEGIN
        IF EXISTS (
            SELECT 1 FROM task_activity
            WHERE reason IS NOT NULL OR event_type::text = 'DATES_CHANGED'
        ) THEN
            RAISE EXCEPTION 'Cannot downgrade: task date change history must be preserved';
        END IF;
        END $$"""
    )
    op.drop_column("task_activity", "reason")
