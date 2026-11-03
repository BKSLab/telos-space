"""Сообщения; удаление сохраняет место в истории и цепочку ответов."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin


class ChatMessage(TimestampMixin, Base):
    """Сообщение участника или Ариса с серверным порядком и ключом повторной отправки."""

    __tablename__ = "chat_messages"
    __table_args__ = (
        ForeignKeyConstraint(
            ["chat_id", "project_id"],
            ["project_chats.id", "project_chats.project_id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("id", "chat_id"),
        UniqueConstraint("chat_id", "seq"),
        UniqueConstraint("chat_id", "author_user_id", "client_message_id"),
        ForeignKeyConstraint(
            ["reply_to_message_id", "chat_id"],
            ["chat_messages.id", "chat_messages.chat_id"],
            name="fk_chat_reply_same_chat",
        ),
        Index("ix_chat_messages_search", "search_vector", postgresql_using="gin"),
        CheckConstraint("author_kind IN ('user', 'agent')", name="ck_chat_message_author_kind"),
        CheckConstraint(
            "author_kind = 'user' OR author_user_id IS NULL", name="ck_chat_agent_message_author"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, doc="ID сообщения.", comment="ID сообщения.")
    chat_id: Mapped[int] = mapped_column(doc="Чат сообщения.", comment="Чат сообщения.")
    project_id: Mapped[int] = mapped_column(doc="Проект сообщения.", comment="Проект сообщения.")
    seq: Mapped[int] = mapped_column(
        BigInteger, doc="Порядок внутри чата.", comment="Порядок внутри чата."
    )
    author_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), doc="Автор.", comment="Автор."
    )
    author_kind: Mapped[str] = mapped_column(
        String(16),
        server_default="user",
        doc="Автор: user — участник, agent — Арис.",
        comment="Автор: user — участник, agent — Арис.",
    )
    content: Mapped[str] = mapped_column(
        Text, doc="Безопасный текст сообщения.", comment="Безопасный текст сообщения."
    )
    client_message_id: Mapped[UUID] = mapped_column(
        Uuid, doc="Ключ повторной отправки.", comment="Ключ повторной отправки."
    )
    reply_to_message_id: Mapped[int | None] = mapped_column(
        doc="Ответ в том же чате.", comment="Ответ в том же чате."
    )
    revision: Mapped[int] = mapped_column(
        server_default="1", doc="Версия для защиты правок.", comment="Версия для защиты правок."
    )
    edited_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), doc="Время правки.", comment="Время правки."
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), doc="Время удаления.", comment="Время удаления."
    )
    search_vector: Mapped[object] = mapped_column(
        TSVECTOR,
        Computed(
            "to_tsvector('russian', coalesce(content, '')) || to_tsvector('simple', coalesce(content, ''))",
            persisted=True,
        ),
        doc="Полнотекстовый индекс русского и английского текста.",
        comment="Полнотекстовый индекс русского и английского текста.",
    )
