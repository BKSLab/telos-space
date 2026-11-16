from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, computed_field

from src.utils.action_effects import action_effect


class AgentToolRunSchema(BaseModel):
    """Проверяемое действие и результат в личном диалоге."""

    model_config = ConfigDict(from_attributes=True)
    id: UUID = Field(description="ID действия.")
    tool_name: str = Field(description="Имя инструмента.")
    title: str = Field(description="Название действия.")
    arguments: dict[str, Any] = Field(description="Конкретные параметры действия.")
    status: Literal["pending", "completed", "rejected", "failed"] = Field(
        description="Состояние действия."
    )
    result: dict[str, Any] = Field(description="Сохранённый результат.")
    created_at: datetime = Field(description="Время запроса действия.")

    @computed_field
    @property
    def effect(self) -> Literal["write", "external_release"]:
        return action_effect(self.tool_name)


class AgentToolDecisionSchema(BaseModel):
    """Решение участника по конкретным сохранённым параметрам."""

    model_config = ConfigDict(extra="forbid")
    decision: Literal["approve", "reject"] = Field(description="Выполнить или отклонить действие.")


class AgentPendingProjectSchema(BaseModel):
    """Проект, в котором выполнится действие."""

    id: int = Field(description="ID проекта.")
    key: str = Field(description="Ключ проекта.")
    name: str = Field(description="Название проекта.")


class AgentPendingActionSchema(BaseModel):
    """Предложенное агентом действие для страницы подтверждения по ссылке."""

    id: UUID = Field(description="ID действия.")
    tool_name: str = Field(description="Имя инструмента.")
    title: str = Field(description="Что будет сделано, кратко.")
    status: Literal["pending", "completed", "rejected", "failed", "expired"] = Field(
        description="Состояние; expired — срок подтверждения истёк, действие не выполнено."
    )
    arguments: dict[str, Any] = Field(description="Сохранённые параметры действия.")
    preview: dict[str, Any] = Field(
        description="Объекты, которые затронет действие, на момент предложения."
    )
    result: dict[str, Any] = Field(description="Результат решения; пусто до решения.")
    project: AgentPendingProjectSchema = Field(description="Проект действия.")
    created_at: datetime = Field(description="Время предложения.")
    expires_at: datetime = Field(description="До какого момента можно подтвердить.")

    @computed_field
    @property
    def effect(self) -> Literal["write", "external_release"]:
        return action_effect(self.tool_name)
