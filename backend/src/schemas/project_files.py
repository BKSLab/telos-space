"""Публичные контракты самостоятельных файлов проекта."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

ExtractionStatus = Literal[
    "pending", "processing", "ready", "empty", "unsupported", "failed", "disabled"
]
IndexStatus = Literal["pending", "processing", "ready", "failed", "disabled"]


class ProjectFileSchema(BaseModel):
    """Оригинал и независимые состояния извлечения и индексации."""

    id: int = Field(description="Идентификатор файла.", examples=[5])
    project_id: int = Field(description="Проект-владелец файла.", examples=[2])
    uploaded_by_user_id: int | None = Field(description="Автор загрузки.", examples=[1])
    caption: str | None = Field(
        None, description="Пользовательская подпись оригинала.", examples=[None]
    )
    previewable: bool = Field(
        False, description="Поддерживается локальная растровая миниатюра.", examples=[False]
    )
    preview_url: str | None = Field(
        None, description="Защищённый URL локальной миниатюры.", examples=[None]
    )
    original_name: str = Field(description="Исходное имя файла.", examples=["План.docx"])
    content_type: str = Field(
        description="Определённый сервером MIME-тип.", examples=["text/plain"]
    )
    size: int = Field(gt=0, description="Размер оригинала в байтах.", examples=[1024])
    created_at: datetime = Field(description="Время загрузки.", examples=["2026-11-06T10:00:00Z"])
    content_url: str = Field(
        description="Защищённый URL оригинала.", examples=["/api/v1/projects/2/files/5/content"]
    )
    extraction_status: ExtractionStatus = Field(
        description="Состояние извлечения текста.", examples=["pending"]
    )
    extraction_detail: str | None = Field(
        description="Пояснение результата извлечения.", examples=[None]
    )
    fts_status: Literal["pending", "ready", "failed"] = Field(
        "pending", description="Состояние индекса PostgreSQL FTS.", examples=["pending"]
    )
    fts_detail: str | None = Field(None, description="Безопасная диагностика FTS.", examples=[None])
    index_status: IndexStatus = Field(
        description="Состояние векторного индекса файла.", examples=["pending"]
    )
    index_detail: str | None = Field(description="Пояснение состояния индексации.", examples=[None])
    indexed_at: datetime | None = Field(
        description="Время успешной индексации версии.", examples=[None]
    )
    summary: str | None = Field(description="Краткое описание текущего текста.", examples=[None])


class ProjectFileDetailSchema(ProjectFileSchema):
    """Одна страница извлечённого текста вместе с метаданными."""

    text: str = Field(description="Фрагмент извлечённого текста.", examples=["Цель проекта…"])
    text_total_chars: int = Field(description="Полная длина извлечённого текста.", examples=[12000])
    text_offset: int = Field(description="Начальная позиция фрагмента.", examples=[0])
    next_offset: int | None = Field(
        description="Позиция следующего фрагмента или null.", examples=[8000]
    )
