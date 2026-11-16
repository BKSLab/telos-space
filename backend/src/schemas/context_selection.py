"""Запрос отбора знаний: существенные аспекты и честные ограничения поиска."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

ContextAspect = Literal[
    "goal", "current_state", "desired_change", "boundaries", "constraints", "verification"
]
RequestQuote = Annotated[str, Field(min_length=1, max_length=1200)]


class ContextSelectionInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    question: str = Field(
        min_length=2,
        max_length=2000,
        description="Конкретный вопрос для поиска подходящих внутренних источников.",
    )
    aspects: list[ContextAspect] = Field(
        default_factory=list,
        max_length=6,
        description="Существенные аспекты текущего вопроса; пустые поля проекта сами по себе не блокируют простую задачу.",
    )
    provided: dict[ContextAspect, RequestQuote] = Field(
        default_factory=dict,
        description="Дословные фрагменты текущего вопроса человека, а не предположения модели.",
    )
    limit: int = Field(
        default=5, ge=1, le=10, description="Предел источников/утверждений на первом шаге."
    )
    map_offset: int = Field(
        default=0, ge=0, description="Продолжение карты по ранее полученному next_offset."
    )
    task_ids: list[Annotated[int, Field(gt=0)]] = Field(
        default_factory=list,
        max_length=3,
        description="До трёх задач текущего вопроса для отдельной проверки связанных PR/CI/deploy. Кандидаты task из поиска также проверяются в этом пределе.",
    )


class KnowledgeMapReadInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    limit: int = Field(default=10, ge=1, le=20, description="Ограниченная страница карты.")
    offset: int = Field(default=0, ge=0, description="Курсор страницы карты.")
    category: str | None = Field(
        default=None,
        max_length=40,
        description="Раздел паспорта, facts, relations или task_results.",
    )


class KnowledgeClaimReadInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: UUID = Field(description="UUID из страницы карты; не выдумывать.")
