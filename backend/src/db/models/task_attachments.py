from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

if TYPE_CHECKING:
    from .tasks import Task


class TaskAttachment(Base):
    """Метаданные файла, прикреплённого к задаче канбана."""

    __tablename__ = "task_attachments"
    __table_args__ = (
        UniqueConstraint("storage_key", name="uq_task_attachments_storage_key"),
        UniqueConstraint("id", "task_id", name="uq_task_attachments_id_task"),
        CheckConstraint("size > 0", name="ck_task_attachments_size_positive"),
    )

    id: Mapped[int] = mapped_column(
        primary_key=True,
        doc="Идентификатор файла задачи.",
        comment="Уникальный идентификатор файла задачи.",
    )
    task_id: Mapped[int] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        doc="Задача-владелец файла.",
        comment="Идентификатор задачи, к которой прикреплён файл.",
    )
    original_name: Mapped[str] = mapped_column(
        String(length=512),
        nullable=False,
        doc="Исходное имя файла для отображения пользователю.",
        comment="Исходное имя файла без компонентов пути.",
    )
    caption: Mapped[str | None] = mapped_column(
        Text, doc="Подпись пользователя.", comment="Необязательная подпись к оригиналу файла."
    )
    display_title: Mapped[str | None] = mapped_column(
        String(255),
        doc="Название документа.",
        comment="Редактируемое название без изменения оригинала.",
    )
    version: Mapped[int] = mapped_column(
        Integer,
        server_default="1",
        doc="Версия записи.",
        comment="Монотонная версия метаданных оригинала задачи.",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
        doc="Время изменения.",
        comment="Версия пользовательских метаданных оригинала.",
    )
    uploaded_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        doc="Автор загрузки.",
        comment="Локальный пользователь; NULL у прежних загрузок.",
    )
    storage_key: Mapped[str] = mapped_column(
        String(length=255),
        nullable=False,
        doc="Относительный уникальный ключ файла в локальном хранилище.",
        comment="Относительный путь файла внутри каталога uploads.",
    )
    content_type: Mapped[str] = mapped_column(
        String(length=255),
        nullable=False,
        doc="Нормализованный MIME-тип файла.",
        comment="MIME-тип, используемый при выдаче содержимого файла.",
    )
    size: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        doc="Размер файла в байтах.",
        comment="Положительный размер файла в байтах.",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        doc="Дата и время загрузки файла.",
        comment="Дата и время добавления файла к задаче.",
    )

    task: Mapped[Task] = relationship("Task", back_populates="attachments")

    def __repr__(self) -> str:
        return (
            f"<TaskAttachment(id={self.id}, task_id={self.task_id}, "
            f"original_name={self.original_name!r})>"
        )
