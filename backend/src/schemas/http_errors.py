"""Существующие формы HTTP-ошибок для документации OpenAPI."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ValidationIssueSchema(BaseModel):
    """Ошибка параметра, которую возвращает стандартная валидация FastAPI."""

    model_config = ConfigDict(extra="allow")

    loc: list[str | int] = Field(
        description="Путь до невалидного параметра.", examples=[["body", "title"]]
    )
    msg: str = Field(description="Сообщение валидации.", examples=["Field required"])
    type: str = Field(description="Тип ошибки валидации.", examples=["missing"])
    input: Any = Field(default=None, description="Значение, вызвавшее ошибку.", examples=[None])
    ctx: dict[str, Any] | None = Field(
        default=None, description="Дополнительные параметры правила валидации.", examples=[None]
    )
    url: str | None = Field(
        default=None, description="Ссылка на описание ошибки валидатора.", examples=[None]
    )


class HttpErrorResponseSchema(BaseModel):
    """Безопасное сообщение ожидаемой HTTP-ошибки."""

    detail: str = Field(description="Причина отклонения запроса.", examples=["Объект не найден."])


class HttpValidationErrorResponseSchema(BaseModel):
    """Доменная ошибка 422 либо ошибки валидации параметров FastAPI."""

    detail: str | list[ValidationIssueSchema] = Field(
        description="Сообщение доменной валидации или список ошибок параметров.",
        examples=[
            "Ошибка валидации.",
            [{"loc": ["body", "title"], "msg": "Field required", "type": "missing"}],
        ],
    )
