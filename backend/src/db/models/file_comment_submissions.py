"""Устойчивая квитанция атомарной отправки комментария с файлами."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class FileCommentSubmission(Base):
    """Квитанция живёт вместе с задачей и не позволяет повторить бизнес-факт."""

    __tablename__ = "file_comment_submissions"
    __table_args__ = (
        UniqueConstraint(
            "actor_user_id", "request_id", name="uq_file_comment_submission_actor_request"
        ),
    )

    id: Mapped[int] = mapped_column(
        primary_key=True, doc="ID квитанции.", comment="Идентификатор отправки комментария."
    )
    actor_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        doc="Автор отправки.",
        comment="Серверный локальный автор идемпотентного запроса.",
    )
    task_id: Mapped[int] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"),
        index=True,
        doc="Задача отправки.",
        comment="Задача, права на которую проверяются при повторе.",
    )
    request_id: Mapped[UUID] = mapped_column(
        doc="Ключ повтора.", comment="Стабильный UUID одной отправки клиента."
    )
    arguments_hash: Mapped[str] = mapped_column(
        String(64), doc="Хэш аргументов.", comment="SHA-256 нормализованных аргументов и файлов."
    )
    result: Mapped[dict] = mapped_column(
        JSONB, doc="Результат отправки.", comment="Исходный публичный результат успешной отправки."
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        doc="Время фиксации.",
        comment="Время фиксации комментария, файлов и квитанции.",
    )

    def __repr__(self) -> str:
        return f"<FileCommentSubmission(id={self.id}, task_id={self.task_id})>"
