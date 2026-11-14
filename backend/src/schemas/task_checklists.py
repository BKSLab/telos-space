"""Контракты единственного чек-листа задачи и его AI-черновика."""

from typing import Annotated
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ChecklistItemSchema(BaseModel):
    """Пункт со стабильным идентификатором и независимой отметкой выполнения."""

    model_config = ConfigDict(extra="forbid")

    id: UUID = Field(
        default_factory=uuid4,
        description="Стабильный ID пункта.",
        examples=["1ba0f9df-5af9-4d8c-bac9-d87ab9cddc91"],
    )
    text: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description="Проверяемое действие.",
        examples=["Согласовать критерии приёмки"],
    )
    is_completed: bool = Field(False, strict=True, description="Пункт выполнен.", examples=[False])

    @field_validator("text")
    @classmethod
    def validate_text(cls, value: str) -> str:
        value = value.strip()
        if not value or "\x00" in value:
            raise ValueError("Пункт должен содержать текст без нулевых символов.")
        return value


class TaskChecklistSchema(BaseModel):
    """Чек-лист как часть задачи; порядок массива задаёт порядок пунктов."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(
        "Чек-лист",
        min_length=1,
        max_length=120,
        description="Название чек-листа.",
        examples=["Проверка перед выпуском"],
    )
    items: list[ChecklistItemSchema] = Field(
        default_factory=list, max_length=100, description="Пункты в порядке выполнения."
    )

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        value = value.strip()
        if not value or "\x00" in value:
            raise ValueError("Название чек-листа не должно быть пустым.")
        return value

    @model_validator(mode="after")
    def unique_items(self):
        if len({item.id for item in self.items}) != len(self.items):
            raise ValueError("Идентификаторы пунктов не должны повторяться.")
        return self


class ChecklistSuggestionRequestSchema(BaseModel):
    """Текущий черновик задачи и выбранные документы для генерации."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(
        ...,
        min_length=1,
        max_length=512,
        description="Название задачи.",
        examples=["Подготовить запуск сервиса"],
    )
    description_md: str = Field(
        "",
        max_length=50_000,
        description="Текущий текст описания.",
        examples=["Запуск для первой группы клиентов."],
    )
    task_id: int | None = Field(
        None, gt=0, description="Существующая задача; null для создания.", examples=[12]
    )
    document_refs: list[Annotated[str, Field(min_length=7, max_length=110)]] = Field(
        default_factory=list,
        max_length=50,
        description="Стабильные ссылки документов проекта, выбранных в форме.",
        examples=[["d1_ZG9jdW1lbnQ6MQ"]],
    )
    checklist: TaskChecklistSchema | None = Field(
        None, description="Текущий черновик чек-листа, если есть."
    )

    @field_validator("title", "description_md")
    @classmethod
    def validate_text(cls, value: str, info) -> str:
        value = value.strip()
        if "\x00" in value or (info.field_name == "title" and not value):
            raise ValueError("Укажите корректный текст задачи.")
        return value


class ChecklistSuggestionDraftSchema(BaseModel):
    """Внутренний ответ модели: только формулировки, без отметок выполнения."""

    items: list[str] = Field(
        ...,
        min_length=3,
        max_length=5,
        description="От трёх до пяти конкретных пунктов.",
        examples=[["Согласовать требования", "Проверить сценарий", "Зафиксировать результат"]],
    )

    @field_validator("items")
    @classmethod
    def validate_items(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values]
        if any(not value or len(value) > 500 or "\x00" in value for value in normalized):
            raise ValueError("Некорректная формулировка пункта.")
        if len({value.casefold() for value in normalized}) != len(normalized):
            raise ValueError("Модель вернула повторяющиеся пункты.")
        return normalized


class ChecklistSuggestionSchema(BaseModel):
    """Редактируемое предложение, которое ещё не сохранено в задаче."""

    checklist: TaskChecklistSchema = Field(
        ..., description="Предлагаемый чек-лист, все пункты не отмечены."
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Какие источники не прочитаны или сокращены.",
        examples=[["Файл archive.zip: формат не поддерживается."]],
    )
