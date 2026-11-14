from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.schemas.users import UserSummarySchema


class CommentUpdateSchema(BaseModel):
    """Правка комментария с проверкой прочитанной версии."""

    model_config = ConfigDict(extra="forbid")
    body_md: str = Field(min_length=1, max_length=50000, description="Новый текст комментария.")
    expected_body_md: str = Field(
        max_length=50000, description="Прочитанный текст для защиты от потери чужой правки."
    )


class CommentSchema(BaseModel):
    """Комментарий задачи канбана."""

    model_config = ConfigDict(from_attributes=True)

    id: int = Field(..., description="Уникальный идентификатор комментария.", examples=[5])
    task_id: int = Field(..., description="Идентификатор задачи.", examples=[3])
    author: UserSummarySchema | None = Field(
        None,
        description="Автор комментария; null — пользователь удалён из трекера.",
    )

    @field_validator("author", mode="before")
    @classmethod
    def serialize_author(cls, value):
        if value is None or isinstance(value, (dict, UserSummarySchema)):
            return value
        from src.services.auth import to_user_summary

        return to_user_summary(value)

    author_name: str | None = Field(
        None,
        description="Полное имя автора на момент написания (остаётся и у удалённого автора).",
        examples=["Борис Петров"],
    )
    body_md: str = Field(
        ...,
        description="Текст комментария в Markdown.",
        examples=["Готово к проверке."],
    )
    created_at: datetime = Field(
        ...,
        description="Дата создания комментария.",
        examples=["2026-08-02T12:00:00Z"],
    )
    document_refs: list[str] = Field(
        default_factory=list,
        description="Стабильные ссылки на документы комментария.",
        examples=[[]],
    )


class CommentCreateSchema(BaseModel):
    """Тело запроса для создания комментария.

    Автор не передаётся: им всегда становится текущий пользователь. Лишнее
    поле ``author_name`` от старого клиента игнорируется, а не подменяет автора.
    """

    model_config = ConfigDict(json_schema_extra={"example": {"body_md": "Готово к проверке."}})

    body_md: str = Field(
        ...,
        min_length=1,
        description="Текст комментария в Markdown.",
        examples=["Готово к проверке."],
    )
