from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ApiTokenSchema(BaseModel):
    """Токен доступа без секрета: то, что можно показывать в списке."""

    model_config = ConfigDict(from_attributes=True)

    id: int = Field(..., description="Идентификатор токена.", examples=[1])
    name: str = Field(..., description="Имя токена.", examples=["Ноутбук"])
    prefix: str = Field(
        ...,
        description="Первые символы секрета для узнавания токена.",
        examples=["Ab123456"],
    )
    scope: Literal["READ", "WRITE"] = Field(
        ...,
        description="Права токена.",
        examples=["READ"],
    )
    created_at: datetime = Field(..., description="Момент выпуска.")
    expires_at: datetime | None = Field(None, description="Момент истечения; null — бессрочный.")
    revoked_at: datetime | None = Field(None, description="Момент отзыва; null — действует.")
    last_used_at: datetime | None = Field(
        None,
        description="Приблизительное время последнего использования.",
    )


class ApiTokenCreateSchema(BaseModel):
    """Параметры выпуска токена."""

    name: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Имя, по которому владелец узнает токен.",
        examples=["Ноутбук"],
    )
    scope: Literal["READ", "WRITE"] = Field(
        "READ",
        description="Права токена: только чтение или чтение и запись.",
        examples=["READ"],
    )
    ttl_days: int | None = Field(
        None,
        ge=1,
        le=3650,
        description="Срок жизни в днях; null — бессрочный токен.",
        examples=[90],
    )

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        """Не пропускает имя из одних пробелов."""
        normalized = value.strip()
        if not normalized:
            raise ValueError("Имя токена не может быть пустым.")
        return normalized


class ApiTokenCreatedSchema(BaseModel):
    """Ответ на выпуск токена: единственный показ секрета."""

    token: ApiTokenSchema = Field(..., description="Карточка выпущенного токена.")
    secret: str = Field(
        ...,
        description="Секрет токена. Показывается один раз и больше не восстанавливается.",
        examples=["telos_token_AbCdEf..."],
    )
