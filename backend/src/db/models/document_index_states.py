"""Производные квитанции фактической индексации конкретных документов."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class DocumentIndexState(Base):
    __tablename__ = "document_index_states"

    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"),
        primary_key=True,
        doc="Проект индекса.",
        comment="Проектная коллекция производного индекса.",
    )
    source_id: Mapped[str] = mapped_column(
        String(128),
        primary_key=True,
        doc="Источник.",
        comment="Восстанавливаемый источник каталога знаний.",
    )
    source_hash: Mapped[str] = mapped_column(
        String(64),
        doc="Версия источника.",
        comment="Хэш полного проверенного снимка источника.",
    )
    source_version: Mapped[int] = mapped_column(
        Integer,
        doc="Версия первичной записи.",
        comment="Версия редактируемых метаданных в снимке индексации.",
    )
    text_hash: Mapped[str] = mapped_column(
        String(64),
        doc="Версия текста.",
        comment="SHA-256 полного текстового представления UTF-8.",
    )
    embedding_signature: Mapped[str] = mapped_column(
        String(64),
        doc="Версия embeddings.",
        comment="Отпечаток модели и размерности векторов.",
    )
    status: Mapped[str] = mapped_column(
        String(32),
        doc="Результат индексации.",
        comment="ready, pending или failed для сохранённой версии.",
    )
    detail: Mapped[str | None] = mapped_column(
        Text,
        doc="Причина ожидания.",
        comment="Безопасное пояснение сбоя семантического этапа.",
    )
    indexed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        doc="Время результата.",
        comment="Время подтверждения результата worker-ом.",
    )
