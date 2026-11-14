"""Производный кэш файлов переписки без изменения оригиналов и истории.

Revision ID: f01b96d24c8a
Revises: e92a74c610bf
"""

import sqlalchemy as sa
from alembic import op

revision = "f01b96d24c8a"
down_revision = "e92a74c610bf"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Добавляет только кэш с каскадным удалением вслед за исходным файлом."""
    op.create_table(
        "chat_file_texts",
        sa.Column("id", sa.Integer(), primary_key=True, comment="ID производного текста."),
        sa.Column(
            "agent_file_id",
            sa.Uuid(),
            sa.ForeignKey("agent_files.id", ondelete="CASCADE"),
            nullable=True,
            comment="Оригинал личного диалога.",
        ),
        sa.Column(
            "chat_attachment_id",
            sa.Uuid(),
            sa.ForeignKey("chat_attachments.id", ondelete="CASCADE"),
            nullable=True,
            comment="Оригинал общего чата.",
        ),
        sa.Column("status", sa.String(16), nullable=False, comment="Состояние извлечения текста."),
        sa.Column("detail", sa.Text(), nullable=True, comment="Безопасное пояснение состояния."),
        sa.Column(
            "content", sa.Text(), nullable=False, server_default="", comment="Извлечённый текст."
        ),
        sa.Column(
            "original_hash",
            sa.String(64),
            nullable=True,
            comment="SHA256 исходных байтов оригинала.",
        ),
        sa.Column(
            "content_hash",
            sa.String(64),
            nullable=True,
            comment="SHA256 UTF-8 доступного извлечённого текста.",
        ),
        sa.Column(
            "total_chars",
            sa.Integer(),
            nullable=False,
            server_default="0",
            comment="Доступное число Unicode codepoints.",
        ),
        sa.Column(
            "original_chars",
            sa.Integer(),
            nullable=False,
            server_default="0",
            comment="Число Unicode codepoints до ресурсного ограничения.",
        ),
        sa.Column(
            "truncated",
            sa.Boolean(),
            nullable=False,
            server_default="false",
            comment="Извлечённый текст ограничен серверным пределом.",
        ),
        sa.Column("lease_token", sa.Uuid(), nullable=True, comment="Владелец текущего извлечения."),
        sa.Column(
            "lease_until",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Срок конкурентного lease.",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
            comment="Обновление кэша.",
        ),
        sa.CheckConstraint(
            "num_nonnulls(agent_file_id, chat_attachment_id) = 1", name="ck_chat_file_text_source"
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'ready', 'empty', 'failed', 'unsupported', 'disabled')",
            name="ck_chat_file_text_status",
        ),
        sa.CheckConstraint("total_chars >= 0", name="ck_chat_file_text_chars"),
        sa.UniqueConstraint("agent_file_id", name="uq_chat_file_text_agent_file"),
        sa.UniqueConstraint("chat_attachment_id", name="uq_chat_file_text_chat_attachment"),
    )
    op.create_index(
        "ix_chat_file_text_pending",
        "chat_file_texts",
        ["updated_at", "id"],
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index(
        "ix_chat_file_text_lease",
        "chat_file_texts",
        ["lease_until"],
        postgresql_where=sa.text("status = 'processing'"),
    )


def downgrade() -> None:
    """Удаляет только воспроизводимый кэш; файлы, сообщения и диалоги сохраняются."""
    op.drop_table("chat_file_texts")
