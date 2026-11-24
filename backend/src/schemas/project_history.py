import re
from datetime import UTC, date, datetime, time
from typing import Literal, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, field_validator, model_validator

ProjectHistoryKind = Literal[
    "task_activity",
    "task_comment",
    "task_report",
    "project_deadline",
    "project_context",
    "risk_created",
    "document_created",
    "project_activity",
    "project_member",
    "project_stage",
    "risk_activity",
    "document_activity",
    "project_plan",
    "project_board",
]


class ProjectHistoryQuery(BaseModel):
    limit: int = Field(20, ge=1, le=100)
    offset: int = Field(0, ge=0, le=2**63 - 1)
    kind: ProjectHistoryKind | None = None
    q: str | None = Field(None, max_length=200, description="Подстрока заголовка или деталей.")
    actor: str | None = Field(None, max_length=200, description="Подстрока имени автора.")
    actor_user_id: int | None = Field(
        None, gt=0, le=2**31 - 1, description="Точный ID автора события."
    )
    task_id: int | None = Field(
        None, gt=0, le=2**31 - 1, description="Задача, события которой нужны."
    )
    event_id: str | None = Field(
        None,
        max_length=160,
        pattern=r"^[a-z_]+:[0-9]+(?::[0-9]+)?$",
        description="Точный ID события для прямой ссылки.",
    )
    timezone: str = Field(
        "UTC", max_length=100, description="Часовой пояс IANA для границ периода."
    )
    since: date | None = Field(None, description="Первый местный день периода включительно.")
    until: date | None = Field(None, description="Последний местный день периода включительно.")
    include_previous: bool = Field(
        False,
        description="На первой странице вернуть ближайший день с событиями до since с теми же фильтрами.",
    )

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("Неизвестный часовой пояс IANA.") from None
        return value

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
        try:
            for day, boundary in ((self.since, time.min), (self.until, time.max)):
                if day:
                    datetime.combine(day, boundary, ZoneInfo(self.timezone)).astimezone(UTC)
        except OverflowError:
            raise ValueError("Граница периода выходит за допустимый диапазон UTC.") from None
        return self


class ProjectHistoryItem(BaseModel):
    id: str
    kind: ProjectHistoryKind
    title: str
    detail: str | None
    created_at: datetime
    actor_name: str | None
    actor_user_id: int | None = None
    task_id: int | None
    document_ref: str | None = None
    event_type: str | None = None
    from_value: str | None = None
    to_value: str | None = None
    comment_id: int | None = None
    report_id: int | None = None
    report_revision: int | None = None
    source_available: bool = True


class ProjectHistoryPage(BaseModel):
    items: list[ProjectHistoryItem]
    next_offset: int | None
    previous_day: date | None = None
    revision: str = "0"


class ProjectHistoryVersion(BaseModel):
    revision: str
