"""Восстанавливаемый текст и прогресс обработки файла проекта."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class KnowledgeProjectFileText(Base):
    """Результат worker; его обновление не создаёт новые задания outbox."""

    __tablename__ = "knowledge_project_file_texts"

    project_file_id: Mapped[int] = mapped_column(
        ForeignKey("project_files.id", ondelete="CASCADE"),
        primary_key=True,
        doc="Исходный файл.",
        comment="Идентификатор исходного файла проекта.",
    )
    text: Mapped[str] = mapped_column(
        Text, default="", doc="Полный текст.", comment="Полный извлечённый текст оригинала."
    )
    status: Mapped[str] = mapped_column(
        String(32), default="pending", doc="Извлечение.", comment="Состояние извлечения текста."
    )
    detail: Mapped[str | None] = mapped_column(
        Text, doc="Пояснение.", comment="Безопасное пояснение результата извлечения."
    )
    original_chars: Mapped[int] = mapped_column(
        Integer,
        default=0,
        doc="Длина текста.",
        comment="Число символов полного извлечённого текста.",
    )
    content_hash: Mapped[str] = mapped_column(
        String(64), doc="Версия оригинала.", comment="SHA-256 оригинала обработанной версии."
    )
    index_status: Mapped[str] = mapped_column(
        String(32),
        default="pending",
        doc="Индексация.",
        comment="Состояние векторного индекса файла.",
    )
    index_detail: Mapped[str | None] = mapped_column(
        Text, doc="Ошибка индексации.", comment="Безопасное пояснение состояния индекса."
    )
    indexed_hash: Mapped[str | None] = mapped_column(
        String(64),
        doc="Индексированная версия.",
        comment="SHA-256 успешно проиндексированного оригинала.",
    )
    embedding_signature: Mapped[str | None] = mapped_column(
        String(64),
        doc="Версия embeddings.",
        comment="Отпечаток модели и размерности успешного индекса.",
    )
    indexed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        doc="Время индексации.",
        comment="Время успешной индексации версии.",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        doc="Обновление результата.",
        comment="Время последнего изменения результата обработки.",
    )
