from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.utils.document_refs import encode_document_ref
from src.utils.file_previews import is_raster_previewable


class TaskAttachmentSchema(BaseModel):
    """Метаданные прикреплённого к задаче файла."""

    model_config = ConfigDict(from_attributes=True)

    id: int = Field(..., description="Уникальный идентификатор файла.", examples=[4])
    task_id: int = Field(..., description="Идентификатор задачи.", examples=[12])
    document_ref: str = Field("", description="Каноническая стабильная ссылка документа.")
    caption: str | None = Field(
        None, description="Пользовательская подпись файла.", examples=[None]
    )
    uploaded_by_user_id: int | None = Field(
        None, description="Автор загрузки; null у прежних файлов.", examples=[1]
    )
    original_name: str = Field(
        ...,
        description="Исходное имя файла.",
        examples=["Отчёт за август.pdf"],
    )
    content_type: str = Field(
        ...,
        description="MIME-тип файла.",
        examples=["application/pdf"],
    )
    size: int = Field(..., gt=0, description="Размер файла в байтах.", examples=[204800])
    created_at: datetime = Field(
        ...,
        description="Дата и время загрузки файла.",
        examples=["2026-08-02T12:00:00Z"],
    )
    content_url: str = Field(
        "",
        description="Относительный URL просмотра или скачивания файла.",
        examples=["/api/v1/tasks/12/attachments/4/content"],
    )
    previewable: bool = Field(
        False,
        description="Можно ли безопасно показать файл как изображение.",
        examples=[False],
    )
    preview_url: str | None = Field(
        None, description="Защищённый URL локальной миниатюры.", examples=[None]
    )
    extraction_status: str = Field(
        "pending", description="Состояние сохранённого извлечения текста.", examples=["pending"]
    )
    extraction_detail: str | None = Field(
        None, description="Пояснение извлечения текста.", examples=[None]
    )
    fts_status: str = Field(
        "pending", description="Состояние сохранённого PostgreSQL FTS.", examples=["pending"]
    )
    fts_detail: str | None = Field(None, description="Безопасная диагностика FTS.", examples=[None])
    index_status: str = Field(
        "pending",
        description="Семантическая индексация; общая диагностика в знаниях проекта.",
        examples=["pending"],
    )
    index_detail: str | None = Field(
        None, description="Пояснение семантической индексации.", examples=[None]
    )

    @model_validator(mode="after")
    def protected_urls(self) -> "TaskAttachmentSchema":
        """Дополняет и вложенные ORM-файлы тем же защищённым контрактом."""
        self.document_ref = encode_document_ref("attachment", self.id)
        self.content_url = f"/api/v1/documents/{self.document_ref}/content"
        self.previewable = is_raster_previewable(self.original_name, self.content_type)
        self.preview_url = (
            f"/api/v1/documents/{self.document_ref}/preview"
            if self.previewable
            else None
        )
        return self
