from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class DocumentSchema(BaseModel):
    """Краткое представление документа для списка и результатов поиска."""

    model_config = ConfigDict(from_attributes=True)

    id: int = Field(..., description="Уникальный идентификатор документа.", examples=[1])
    project_id: int = Field(..., description="Идентификатор проекта.", examples=[1])
    slug: str = Field(..., description="URL-идентификатор документа.", examples=["design-guide"])
    title: str = Field(..., description="Заголовок документа.", examples=["Дизайн-гайд"])
    updated_at: datetime = Field(
        ...,
        description="Дата последнего обновления документа.",
        examples=["2026-08-02T12:00:00Z"],
    )
    search_match_source: str | None = Field(
        None,
        description="Поле, в котором найдено поисковое совпадение.",
        examples=["content"],
    )
    search_title: str | None = Field(
        None,
        description="Заголовок с безопасными маркерами подсветки.",
        examples=["__FTS_START__Проект__FTS_END__ TELOS SPACE"],
    )
    search_excerpt: str | None = Field(
        None,
        description="Фрагмент совпавшего содержимого с безопасными маркерами подсветки.",
        examples=["Описание __FTS_START__проекта__FTS_END__."],
    )


class DocumentDetailSchema(DocumentSchema):
    """Полное представление документа с Markdown-содержимым."""

    content_md: str = Field(
        ...,
        description="Содержимое документа в формате Markdown.",
        examples=["# План проекта"],
    )
    created_at: datetime = Field(
        ...,
        description="Дата создания документа.",
        examples=["2026-08-01T10:00:00Z"],
    )


class DocumentCreateSchema(BaseModel):
    """Тело запроса для создания документа."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "title": "План проекта",
                "slug": "project-plan",
                "content_md": "# План проекта",
            }
        }
    )

    title: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Заголовок документа.",
        examples=["План проекта"],
    )
    slug: str | None = Field(
        None,
        min_length=1,
        max_length=255,
        description="Желаемый slug; при отсутствии формируется из заголовка.",
        examples=["project-plan"],
    )
    content_md: str = Field(
        "",
        description="Начальное содержимое документа в формате Markdown.",
        examples=["# План проекта"],
    )


class DocumentUpdateSchema(BaseModel):
    """Тело запроса для частичного обновления документа."""

    model_config = ConfigDict(
        json_schema_extra={"example": {"title": "Актуальный план", "content_md": "# План"}}
    )

    title: str | None = Field(
        None,
        min_length=1,
        max_length=255,
        description="Новый заголовок документа.",
        examples=["Актуальный план"],
    )
    content_md: str | None = Field(
        None,
        description="Новое Markdown-содержимое документа.",
        examples=["# Актуальный план"],
    )
