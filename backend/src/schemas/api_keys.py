"""Схемы личных и сервисных API-ключей локального пользователя."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.utils.api_scopes import API_SCOPES, unknown_scopes

_SCOPES_HELP = (
    "Права ключа. Допустимые значения: "
    + ", ".join(API_SCOPES)
    + ". Пустой список означает «ключ не открывает ничего»."
)


def _known_scopes(value: list[str]) -> list[str]:
    """Отсекает выдуманные права до записи в базу и убирает повторы."""
    unknown = unknown_scopes(value)
    if unknown:
        raise ValueError(
            "Неизвестные права: " + ", ".join(unknown) + ". Допустимые: " + ", ".join(API_SCOPES)
        )
    return list(dict.fromkeys(value))


class ApiKeyCreateSchema(BaseModel):
    """Тело запроса на выдачу ключа."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "label": "Моя интеграция",
                "owner_user_id": 12,
                "scopes": ["projects:read", "tasks:read", "tasks:write", "comments:write"],
                "expires_at": "2027-01-01T00:00:00Z",
            }
        }
    )

    label: str = Field(
        ..., min_length=1, max_length=160, description="Имя ключа.", examples=["Мой агент"]
    )
    owner_user_id: int = Field(
        ...,
        ge=1,
        description=(
            "Локальный пользователь, на которого выдан ключ: ключ работает с его правами в проектах "
            "(суженными правами ключа), и к нему идут вопросы по интеграции."
        ),
        examples=[12],
    )
    scopes: list[str] = Field(default_factory=list, description=_SCOPES_HELP)
    expires_at: datetime | None = Field(
        None,
        description="Момент, после которого ключ перестаёт приниматься. Не задан — бессрочный.",
        examples=["2027-01-01T00:00:00Z"],
    )

    @field_validator("scopes")
    @classmethod
    def known_scopes_only(cls, value: list[str]) -> list[str]:
        """Не даёт выдать ключ с несуществующим правом."""
        return _known_scopes(value)


class ApiKeyUpdateSchema(BaseModel):
    """Частичное изменение ключа."""

    model_config = ConfigDict(
        json_schema_extra={"example": {"label": "Мой агент (прод)", "is_active": False}}
    )

    label: str | None = Field(None, min_length=1, max_length=160, description="Новое имя ключа.")
    scopes: list[str] | None = Field(
        None, description="Новый набор прав — заменяет прежний целиком."
    )
    expires_at: datetime | None = Field(
        None, description="Новый срок действия. Явный null делает ключ бессрочным."
    )
    is_active: bool | None = Field(
        None, description="false — отозвать ключ (пишется кто и когда), true — вернуть в работу."
    )

    @field_validator("scopes")
    @classmethod
    def known_scopes_only(cls, value: list[str] | None) -> list[str] | None:
        """Не даёт записать несуществующее право при изменении."""
        return None if value is None else _known_scopes(value)






class ApiKeySchema(BaseModel):
    """Данные ключа без секрета."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(..., description="UUID ключа.")
    label: str = Field(..., description="Имя ключа.", examples=["Мой агент"])
    key_prefix: str = Field(
        ..., description="Публичный идентификатор ключа: восемь hex-символов.", examples=["1a2b3c4d"]
    )
    is_active: bool = Field(..., description="Принимается ли ключ.", examples=[True])
    owner_user_id: int | None = Field(None, description="Владелец ключа.", examples=[12])
    owner_name: str | None = Field(None, description="Имя владельца.", examples=["Иванов Иван"])
    scopes: list[str] = Field(default_factory=list, description="Права ключа.")
    expires_at: datetime | None = Field(None, description="Срок действия; null — бессрочный.")
    last_used_at: datetime | None = Field(None, description="Последнее использование.")
    revoked_at: datetime | None = Field(None, description="Когда ключ отозван.")
    revoked_by_user_id: int | None = Field(None, description="Кто отозвал ключ.")
    revoked_by_name: str | None = Field(None, description="Имя отозвавшего.")
    created_by_user_id: int | None = Field(None, description="Кто выдал ключ.")
    created_by_name: str | None = Field(None, description="Имя выдавшего.")
    created_at: datetime = Field(..., description="Дата выдачи.")
    updated_at: datetime = Field(..., description="Дата последнего изменения.")

    @field_validator("scopes", mode="before")
    @classmethod
    def scopes_as_list(cls, value: object) -> list[str]:
        """Страхует от NULL и мусора в JSON-колонке."""
        if isinstance(value, list):
            return [str(item) for item in value]
        return []


class ApiKeyCreatedSchema(ApiKeySchema):
    """Ответ на выдачу ключа: секрет показывается единственный раз."""

    raw_key: str = Field(
        ...,
        description="Полный ключ — показывается только при выдаче, затем недоступен.",
        examples=["telos_key_1a2b3c4d_0123456789abcdef0123456789abcdef"],
    )


class ApiScopeSchema(BaseModel):
    """Право из реестра — для формы выдачи ключа и справки."""

    code: str = Field(..., description="Техническое имя права.", examples=["tasks:read"])
    description: str = Field(..., description="Что право открывает.")


class ApiKeyUsageDaySchema(BaseModel):
    """Обращения за один день."""

    day: str = Field(..., description="День в часовом поясе сервиса (ГГГГ-ММ-ДД).")
    requests: int = Field(..., description="Всего обращений.")
    errors: int = Field(..., description="Из них с ошибкой (4xx/5xx).")
    response_bytes: int = Field(..., description="Объём ответов, байты.")


class ApiKeyUsageRouteSchema(BaseModel):
    """Один метод REST или инструмент MCP, которым пользуется ключ."""

    channel: str = Field(..., description="rest или mcp.", examples=["mcp"])
    method: str = Field(..., description="HTTP-метод или CALL.", examples=["CALL"])
    route: str = Field(..., description="Шаблон маршрута или имя инструмента.")
    requests: int = Field(..., description="Обращений за период.")
    errors: int = Field(..., description="Из них с ошибкой.")
    avg_duration_ms: int = Field(..., description="Средняя длительность, мс.")
    last_at: datetime | None = Field(None, description="Последнее обращение.")


class ApiKeyUsageRequestSchema(BaseModel):
    """Одно обращение — строка журнала."""

    model_config = ConfigDict(from_attributes=True)

    at: datetime = Field(..., description="Момент обращения.")
    channel: str = Field(..., description="rest или mcp.")
    method: str = Field(..., description="HTTP-метод или CALL.")
    route: str = Field(..., description="Шаблон маршрута или имя инструмента.")
    path: str = Field(..., description="Фактический путь или имя инструмента.")
    query: dict = Field(default_factory=dict, description="Параметры (усечены).")
    actor_login: str | None = Field(None, description="Логин локального владельца ключа.")
    status_code: int = Field(..., description="Код ответа.")
    duration_ms: int = Field(..., description="Длительность, мс.")
    response_bytes: int | None = Field(None, description="Объём ответа, байты.")
    client_ip: str | None = Field(None, description="Адрес источника.")


class ApiKeyUsageSchema(BaseModel):
    """Журнал и статистика обращений по ключу за период."""

    api_key_id: uuid.UUID = Field(..., description="Ключ.")
    days: int = Field(..., description="Глубина периода в днях.")
    requests: int = Field(..., description="Всего обращений за период.")
    errors: int = Field(..., description="Из них с ошибкой.")
    response_bytes: int = Field(..., description="Объём ответов, байты.")
    avg_duration_ms: int = Field(..., description="Средняя длительность, мс.")
    last_at: datetime | None = Field(None, description="Последнее обращение.")
    daily: list[ApiKeyUsageDaySchema] = Field(default_factory=list, description="По дням.")
    routes: list[ApiKeyUsageRouteSchema] = Field(
        default_factory=list, description="По методам и инструментам."
    )
    recent: list[ApiKeyUsageRequestSchema] = Field(
        default_factory=list, description="Последние обращения."
    )


class PersonalApiKeyCreateSchema(BaseModel):
    """Тело запроса на выпуск личного ключа: владелец — сам пользователь."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "label": "Мой скрипт сверки",
                "scopes": ["tasks:read", "comments:write"],
                "expires_at": "2027-01-01T00:00:00Z",
            }
        }
    )

    label: str = Field(
        ..., min_length=1, max_length=160, description="Имя ключа.", examples=["Мой скрипт"]
    )
    scopes: list[str] = Field(default_factory=list, description=_SCOPES_HELP)
    expires_at: datetime | None = Field(
        None,
        description="Момент, после которого ключ перестаёт приниматься. Не задан — бессрочный.",
        examples=["2027-01-01T00:00:00Z"],
    )

    @field_validator("scopes")
    @classmethod
    def personal_scopes_only(cls, value: list[str]) -> list[str]:
        """Права ключа должны присутствовать в реестре."""
        return _known_scopes(value)


class PersonalApiKeyUpdateSchema(BaseModel):
    """Частичное изменение личного ключа (права — только из белого списка)."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"label": "Мой скрипт (прод)", "is_active": False}}
    )

    label: str | None = Field(None, min_length=1, max_length=160, description="Новое имя ключа.")
    scopes: list[str] | None = Field(
        None, description="Новый набор прав — заменяет прежний целиком."
    )
    expires_at: datetime | None = Field(
        None, description="Новый срок действия. Явный null делает ключ бессрочным."
    )
    is_active: bool | None = Field(
        None, description="false — отозвать ключ, true — вернуть в работу."
    )

    @field_validator("scopes")
    @classmethod
    def personal_scopes_only(cls, value: list[str] | None) -> list[str] | None:
        """Права ключа должны присутствовать в реестре."""
        if value is None:
            return None
        return _known_scopes(value)
