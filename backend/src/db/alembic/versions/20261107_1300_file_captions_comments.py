"""Подписи файлов и атомарные комментарии с существующими оригиналами.

Revision ID: a53c9e1f426b
Revises: f42b8d0e315a
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "a53c9e1f426b"
down_revision = "f42b8d0e315a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Расширяет метаданные; прежние оригиналы и комментарии сохраняются."""
    op.add_column(
        "project_files",
        sa.Column(
            "caption", sa.Text(), nullable=True, comment="Необязательная подпись к оригиналу файла."
        ),
    )
    op.add_column(
        "task_attachments",
        sa.Column(
            "caption", sa.Text(), nullable=True, comment="Необязательная подпись к оригиналу файла."
        ),
    )
    op.add_column(
        "task_attachments",
        sa.Column(
            "uploaded_by_user_id",
            sa.Integer(),
            nullable=True,
            comment="Локальный пользователь; NULL у прежних загрузок.",
        ),
    )
    op.create_foreign_key(
        "fk_task_attachments_uploader",
        "task_attachments",
        "users",
        ["uploaded_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_unique_constraint(
        "uq_task_attachments_id_task", "task_attachments", ["id", "task_id"]
    )
    op.create_unique_constraint("uq_task_comments_id_task", "task_comments", ["id", "task_id"])
    op.create_index("ix_task_comments_task_id", "task_comments", ["task_id"])
    op.create_table(
        "task_comment_attachments",
        sa.Column(
            "comment_id", sa.Integer(), primary_key=True, comment="Комментарий, показывающий файл."
        ),
        sa.Column(
            "attachment_id",
            sa.Integer(),
            primary_key=True,
            comment="Единый оригинал в файлах задачи.",
        ),
        sa.Column(
            "task_id", sa.Integer(), nullable=False, comment="Общая задача комментария и оригинала."
        ),
        sa.ForeignKeyConstraint(
            ["comment_id", "task_id"],
            ["task_comments.id", "task_comments.task_id"],
            name="fk_comment_attachments_comment_task",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["attachment_id", "task_id"],
            ["task_attachments.id", "task_attachments.task_id"],
            name="fk_comment_attachments_attachment_task",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_comment_attachments_attachment",
        "task_comment_attachments",
        ["attachment_id", "task_id"],
    )
    op.create_table(
        "file_comment_submissions",
        sa.Column(
            "id", sa.Integer(), primary_key=True, comment="Идентификатор отправки комментария."
        ),
        sa.Column(
            "actor_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            comment="Серверный локальный автор идемпотентного запроса.",
        ),
        sa.Column(
            "task_id",
            sa.Integer(),
            sa.ForeignKey("tasks.id", ondelete="CASCADE"),
            nullable=False,
            comment="Задача, права на которую проверяются при повторе.",
        ),
        sa.Column(
            "request_id",
            sa.Uuid(),
            nullable=False,
            comment="Стабильный UUID одной отправки клиента.",
        ),
        sa.Column(
            "arguments_hash",
            sa.String(64),
            nullable=False,
            comment="SHA-256 нормализованных аргументов и файлов.",
        ),
        sa.Column(
            "result",
            postgresql.JSONB(),
            nullable=False,
            comment="Исходный публичный результат успешной отправки.",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
            comment="Время фиксации комментария, файлов и квитанции.",
        ),
        sa.UniqueConstraint(
            "actor_user_id", "request_id", name="uq_file_comment_submission_actor_request"
        ),
    )
    op.create_index("ix_file_comment_submissions_task_id", "file_comment_submissions", ["task_id"])


def downgrade() -> None:
    """Удаляет новые подписи/связи/квитанции, сохраняя оригиналы и комментарии."""
    op.drop_table("file_comment_submissions")
    op.drop_table("task_comment_attachments")
    op.drop_index("ix_task_comments_task_id", table_name="task_comments")
    op.drop_constraint("uq_task_comments_id_task", "task_comments", type_="unique")
    op.drop_constraint("uq_task_attachments_id_task", "task_attachments", type_="unique")
    op.drop_constraint("fk_task_attachments_uploader", "task_attachments", type_="foreignkey")
    op.drop_column("task_attachments", "uploaded_by_user_id")
    op.drop_column("task_attachments", "caption")
    op.drop_column("project_files", "caption")
