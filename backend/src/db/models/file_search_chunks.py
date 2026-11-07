"""Локальные оконные FTS-векторы оригиналов; это не RAG chunks."""

from sqlalchemy import CheckConstraint, Computed, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class FileSearchChunk(Base):
    """Ограниченное текстовое окно с полными смещениями в сохранённом тексте."""

    __tablename__ = "file_search_chunks"
    __table_args__ = (
        CheckConstraint(
            "num_nonnulls(attachment_id, project_file_id) = 1", name="ck_file_search_owner"
        ),
        CheckConstraint(
            "chunk_index >= 0 AND start_offset >= 0 AND end_offset >= start_offset",
            name="ck_file_search_offsets",
        ),
        CheckConstraint(
            "char_length(body) <= 12000 AND char_length(caption) <= 4000",
            name="ck_file_search_bounded",
        ),
        Index(
            "uq_file_search_attachment_chunk",
            "attachment_id",
            "chunk_index",
            unique=True,
            postgresql_where=text("attachment_id IS NOT NULL"),
        ),
        Index(
            "uq_file_search_project_chunk",
            "project_file_id",
            "chunk_index",
            unique=True,
            postgresql_where=text("project_file_id IS NOT NULL"),
        ),
        Index("ix_file_search_vector", "search_vector", postgresql_using="gin"),
    )

    id: Mapped[int] = mapped_column(
        primary_key=True, doc="ID окна.", comment="Идентификатор поискового окна."
    )
    attachment_id: Mapped[int | None] = mapped_column(
        ForeignKey("task_attachments.id", ondelete="CASCADE"),
        doc="Вложение.",
        comment="Исходное вложение задачи.",
    )
    project_file_id: Mapped[int | None] = mapped_column(
        ForeignKey("project_files.id", ondelete="CASCADE"),
        doc="Файл проекта.",
        comment="Исходный самостоятельный файл проекта.",
    )
    chunk_index: Mapped[int] = mapped_column(
        Integer, doc="Номер окна.", comment="Порядковый номер окна оригинала, начиная с нуля."
    )
    start_offset: Mapped[int] = mapped_column(
        Integer, doc="Начало.", comment="Начальное смещение в полном тексте, начиная с нуля."
    )
    end_offset: Mapped[int] = mapped_column(
        Integer, doc="Конец.", comment="Исключающая правая граница окна в полном тексте."
    )
    title: Mapped[str] = mapped_column(
        String(512), doc="Название.", comment="Название оригинала для немедленного поиска."
    )
    caption: Mapped[str] = mapped_column(
        Text,
        server_default="",
        doc="Подпись.",
        comment="Ограниченная пользовательская подпись для поиска.",
    )
    body: Mapped[str] = mapped_column(
        Text,
        server_default="",
        doc="Текст окна.",
        comment="Ограниченное окно полного извлечённого текста.",
    )
    search_vector: Mapped[object] = mapped_column(
        TSVECTOR,
        Computed(
            "setweight(to_tsvector('russian', coalesce(title, '')), 'A') || "
            "setweight(to_tsvector('russian', coalesce(caption, '')), 'B') || "
            "setweight(to_tsvector('russian', coalesce(body, '')), 'C')",
            persisted=True,
        ),
        doc="GIN FTS-вектор.",
        comment="Взвешенный хранимый вектор: название, подпись, содержимое окна.",
    )

    def __repr__(self) -> str:
        return f"<FileSearchChunk(id={self.id}, chunk_index={self.chunk_index})>"
