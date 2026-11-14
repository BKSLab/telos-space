"""Ссылки хранят идентификаторы; карточки собираются из текущих данных."""

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class ChatMessageEntity(Base):
    """Ссылка на объект того же проекта."""

    __tablename__ = "chat_message_entities"
    __table_args__ = (
        UniqueConstraint("message_id", "entity_type", "entity_id"),
        UniqueConstraint("message_id", "document_ref", name="uq_chat_message_document_ref"),
        CheckConstraint(
            "(entity_type = 'DOCUMENT' AND document_ref IS NOT NULL AND entity_id IS NULL) OR "
            "(entity_type <> 'DOCUMENT' AND document_ref IS NULL AND entity_id IS NOT NULL)",
            name="ck_chat_message_entity_identity",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, doc="ID ссылки.", comment="ID ссылки.")
    message_id: Mapped[int] = mapped_column(
        ForeignKey("chat_messages.id", ondelete="CASCADE"),
        index=True,
        doc="Сообщение.",
        comment="Сообщение.",
    )
    entity_type: Mapped[str] = mapped_column(String(16), doc="Тип объекта.", comment="Тип объекта.")
    entity_id: Mapped[int | None] = mapped_column(doc="ID объекта.", comment="ID объекта, кроме документа.")
    document_ref: Mapped[str | None] = mapped_column(String(128), doc="Стабильная ссылка документа.")
    position: Mapped[int] = mapped_column(doc="Положение карточки.", comment="Положение карточки.")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        doc="Время добавления ссылки.",
        comment="Время добавления ссылки.",
    )
