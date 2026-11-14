"""Производный текст файлов переписки; не источник проектных знаний."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class ChatFileText(Base):
    """Один кэш на неизменяемый оригинал; lease разделяет конкурентные чтения."""

    __tablename__ = "chat_file_texts"
    __table_args__ = (
        CheckConstraint(
            "num_nonnulls(agent_file_id, chat_attachment_id) = 1", name="ck_chat_file_text_source"
        ),
        CheckConstraint(
            "status IN ('pending', 'processing', 'ready', 'empty', 'failed', 'unsupported', 'disabled')",
            name="ck_chat_file_text_status",
        ),
        CheckConstraint("total_chars >= 0", name="ck_chat_file_text_chars"),
        UniqueConstraint("agent_file_id", name="uq_chat_file_text_agent_file"),
        UniqueConstraint("chat_attachment_id", name="uq_chat_file_text_chat_attachment"),
        Index(
            "ix_chat_file_text_pending",
            "updated_at",
            "id",
            postgresql_where=text("status = 'pending'"),
        ),
        Index(
            "ix_chat_file_text_lease", "lease_until", postgresql_where=text("status = 'processing'")
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, comment="ID производного текста.")
    agent_file_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_files.id", ondelete="CASCADE"), comment="Оригинал личного диалога."
    )
    chat_attachment_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("chat_attachments.id", ondelete="CASCADE"), comment="Оригинал общего чата."
    )
    status: Mapped[str] = mapped_column(String(16), comment="Состояние извлечения текста.")
    detail: Mapped[str | None] = mapped_column(Text, comment="Безопасное пояснение состояния.")
    content: Mapped[str] = mapped_column(Text, server_default="", comment="Извлечённый текст.")
    original_hash: Mapped[str | None] = mapped_column(
        String(64), comment="SHA256 исходных байтов оригинала."
    )
    content_hash: Mapped[str | None] = mapped_column(
        String(64), comment="SHA256 UTF-8 доступного извлечённого текста."
    )
    total_chars: Mapped[int] = mapped_column(
        server_default="0", comment="Доступное число Unicode codepoints."
    )
    original_chars: Mapped[int] = mapped_column(
        server_default="0", comment="Число Unicode codepoints до ресурсного ограничения."
    )
    truncated: Mapped[bool] = mapped_column(
        server_default="false", comment="Извлечённый текст ограничен серверным пределом."
    )
    lease_token: Mapped[UUID | None] = mapped_column(comment="Владелец текущего извлечения.")
    lease_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), comment="Срок конкурентного lease."
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), comment="Обновление кэша."
    )
