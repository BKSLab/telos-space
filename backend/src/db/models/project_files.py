"""Первичные метаданные самостоятельного файла проекта."""

from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin


class ProjectFile(Base, TimestampMixin):
    """Локальный оригинал, принадлежащий проекту независимо от задач."""

    __tablename__ = "project_files"
    __table_args__ = (CheckConstraint("size > 0", name="ck_project_files_size_positive"),)

    id: Mapped[int] = mapped_column(
        primary_key=True, doc="ID файла.", comment="Идентификатор файла проекта."
    )
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"),
        index=True,
        doc="Проект-владелец.",
        comment="Проект-владелец файла.",
    )
    uploaded_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        doc="Автор загрузки.",
        comment="Локальный пользователь, загрузивший файл.",
    )
    original_name: Mapped[str] = mapped_column(
        String(512), doc="Исходное имя.", comment="Имя файла без компонентов пути."
    )
    caption: Mapped[str | None] = mapped_column(
        Text, doc="Подпись пользователя.", comment="Необязательная подпись к оригиналу файла."
    )
    content_type: Mapped[str] = mapped_column(
        String(255), doc="MIME-тип.", comment="MIME-тип, определённый сервером."
    )
    size: Mapped[int] = mapped_column(
        BigInteger, doc="Размер оригинала.", comment="Размер файла в байтах."
    )
    storage_key: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        doc="Локальный ключ.",
        comment="Внутренний относительный ключ в uploads.",
    )
    content_hash: Mapped[str] = mapped_column(
        String(64), doc="Хэш оригинала.", comment="SHA-256 неизменяемого оригинала."
    )
    retry_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        doc="Запрос повтора.",
        comment="Время явного запроса повторной обработки.",
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        doc="Логическое удаление.",
        comment="Скрывает источник до фоновой очистки оригинала.",
    )

    def __repr__(self) -> str:
        return f"<ProjectFile(id={self.id}, project_id={self.project_id})>"
