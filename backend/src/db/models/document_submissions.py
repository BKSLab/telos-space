"""Квитанции создания документов, не владеющие первичными данными."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class DocumentSubmission(Base):
    __tablename__ = "document_submissions"

    actor_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
        doc="Автор запроса.",
        comment="Локальный пользователь, выполнивший создание.",
    )
    request_id: Mapped[UUID] = mapped_column(
        Uuid,
        primary_key=True,
        doc="ID повтора.",
        comment="Стабильный UUID одной пользовательской отправки.",
    )
    arguments_hash: Mapped[str] = mapped_column(
        String(64),
        doc="Отпечаток запроса.",
        comment="SHA-256 аргументов для проверки безопасного повтора.",
    )
    document_ref: Mapped[str] = mapped_column(
        String(128),
        doc="Результат создания.",
        comment="Ссылка-квитанция сохраняется и после удаления документа.",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        doc="Время создания.",
        comment="Время фиксации результата запроса.",
    )
