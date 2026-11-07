"""Постоянная очередь извлечения оригиналов, независимая от RAG."""

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class FileProcessingJob(Base):
    """Одна коалесцируемая запись на оригинал; lease защищает внешнюю фазу."""

    __tablename__ = "file_processing_jobs"
    __table_args__ = (
        CheckConstraint(
            "num_nonnulls(attachment_id, project_file_id) = 1", name="ck_file_processing_owner"
        ),
        CheckConstraint(
            "status IN ('pending', 'processing', 'ready', 'failed')",
            name="ck_file_processing_status",
        ),
        Index(
            "uq_file_processing_attachment",
            "attachment_id",
            unique=True,
            postgresql_where=text("attachment_id IS NOT NULL"),
        ),
        Index(
            "uq_file_processing_project_file",
            "project_file_id",
            unique=True,
            postgresql_where=text("project_file_id IS NOT NULL"),
        ),
        Index(
            "ix_file_processing_pending",
            "available_at",
            "id",
            postgresql_where=text("status = 'pending'"),
        ),
        Index(
            "ix_file_processing_lease",
            "lease_until",
            postgresql_where=text("status = 'processing'"),
        ),
    )

    id: Mapped[int] = mapped_column(
        primary_key=True, doc="ID задания.", comment="Идентификатор обработки оригинала."
    )
    attachment_id: Mapped[int | None] = mapped_column(
        ForeignKey("task_attachments.id", ondelete="CASCADE"),
        doc="Вложение задачи.",
        comment="Ровно один исходный оригинал: вложение задачи.",
    )
    project_file_id: Mapped[int | None] = mapped_column(
        ForeignKey("project_files.id", ondelete="CASCADE"),
        doc="Файл проекта.",
        comment="Ровно один исходный оригинал: файл проекта.",
    )
    status: Mapped[str] = mapped_column(
        String(16),
        server_default="pending",
        doc="Состояние очереди.",
        comment="pending, processing, ready или failed.",
    )
    requested_version: Mapped[int] = mapped_column(
        Integer,
        server_default="1",
        doc="Требуемая версия.",
        comment="Монотонная версия запроса; обновление во время обработки не теряется.",
    )
    claimed_version: Mapped[int | None] = mapped_column(
        Integer, doc="Захваченная версия.", comment="Версия запроса в начале текущего lease."
    )
    lease_token: Mapped[str | None] = mapped_column(
        String(36), doc="Владелец lease.", comment="Случайная квитанция владельца обработки."
    )
    lease_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        doc="Срок lease.",
        comment="После этого времени обработку можно безопасно перехватить.",
    )
    attempts: Mapped[int] = mapped_column(
        Integer, server_default="0", doc="Число попыток.", comment="Число попыток текущего запроса."
    )
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        doc="Не раньше.",
        comment="Время следующей попытки обработки.",
    )
    detail: Mapped[str | None] = mapped_column(
        Text, doc="Диагностика.", comment="Безопасное пояснение ошибки обработки."
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        doc="Обновление.",
        comment="Время последнего изменения очереди.",
    )

    def __repr__(self) -> str:
        return f"<FileProcessingJob(id={self.id}, status={self.status!r})>"
