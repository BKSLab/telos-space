import re
from datetime import date, datetime
from typing import Literal, Self

from pydantic import BaseModel, Field, field_validator, model_validator

ProjectHistoryKind = Literal[
    "task_activity",
    "task_comment",
    "task_report",
    "project_deadline",
    "project_context",
    "risk_created",
    "document_created",
]


class ProjectHistoryQuery(BaseModel):
    limit: int = Field(20, ge=1, le=100)
    offset: int = Field(0, ge=0, le=2**63 - 1)
    kind: ProjectHistoryKind | None = None
    q: str | None = Field(None, max_length=200, description="Подстрока заголовка или деталей.")
    actor: str | None = Field(None, max_length=200, description="Подстрока имени автора.")
    since: date | None = Field(None, description="Первый день периода UTC включительно.")
    until: date | None = Field(None, description="Последний день периода UTC включительно.")

    @field_validator("since", "until", mode="before")
    @classmethod
    def validate_date_format(cls, value: object) -> object:
        if isinstance(value, str) and not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
            raise ValueError("Ожидается дата в формате YYYY-MM-DD.")
        return value

    @field_validator("q", "actor")
    @classmethod
    def normalize_search(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if "\x00" in value:
            raise ValueError("Строка содержит нулевой символ.")
        return value.strip() or None

    @model_validator(mode="after")
    def validate_date_range(self) -> Self:
        if self.since and self.until and self.since > self.until:
            raise ValueError("Дата начала периода позже даты окончания.")
        return self


class ProjectHistoryItem(BaseModel):
    id: str
    kind: ProjectHistoryKind
    title: str
    detail: str | None
    created_at: datetime
    actor_name: str | None
    task_id: int | None
    document_ref: str | None = None


class ProjectHistoryPage(BaseModel):
    items: list[ProjectHistoryItem]
    next_offset: int | None
