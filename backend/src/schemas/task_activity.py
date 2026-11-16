from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from src.db.models.task_activity import TaskActivityEventType


class ActivitySchema(BaseModel):
    """Неизменяемое событие истории задачи."""

    model_config = ConfigDict(from_attributes=True)

    id: int = Field(..., description="Уникальный идентификатор события.", examples=[7])
    task_id: int = Field(..., description="Идентификатор задачи.", examples=[3])
    event_type: TaskActivityEventType = Field(
        ...,
        description="Тип изменения задачи.",
        examples=["STAGE_CHANGED"],
    )
    from_value: str | None = Field(
        None,
        description="Предыдущее значение.",
        examples=["Бэклог"],
    )
    to_value: str | None = Field(
        None,
        description="Новое значение.",
        examples=["В работе"],
    )
    reason: str | None = Field(
        None,
        description="Причина изменения дат; null при первом назначении или в прежних событиях.",
        examples=["Перенос поставки оборудования."],
    )
    actor_user_id: int | None = Field(
        None,
        description="Кто выполнил изменение; null — автор не записан (старые события).",
        examples=[5],
    )
    actor_name: str | None = Field(
        None,
        description="Имя автора изменения на момент события.",
        examples=["Иванов Иван"],
    )
    via_api_key_label: str | None = Field(
        None,
        description="Имя сервисного API-ключа, через который агент сделал изменение.",
        examples=["Проектный агент"],
    )
    created_at: datetime = Field(
        ...,
        description="Дата фиксации события.",
        examples=["2026-08-02T12:00:00Z"],
    )
