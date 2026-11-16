import enum
from datetime import date, datetime

from pydantic import BaseModel, Field, model_validator

from src.schemas.calendar import CalendarRiskReasonSchema


class ScenarioChangeSource(str, enum.Enum):
    """Источник изменения внутри preview."""

    DIRECT = "DIRECT"
    CASCADE = "CASCADE"


class ScenarioTaskDatesSchema(BaseModel):
    """Пара плановых дат задачи."""

    start_date: date | None
    due_date: date | None


class ScenarioChangeInputSchema(ScenarioTaskDatesSchema):
    """Локальное изменение, с которого начинается preview."""

    task_id: int = Field(..., gt=0)

    @model_validator(mode="after")
    def validate_date_order(self):
        if (
            self.start_date is not None
            and self.due_date is not None
            and self.start_date > self.due_date
        ):
            raise ValueError("Дата начала не может быть позже даты завершения.")
        return self


class ScenarioPreviewRequestSchema(BaseModel):
    """Набор локальных предложений для расчёта последствий."""

    changes: list[ScenarioChangeInputSchema] = Field(..., min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_unique_tasks(self):
        task_ids = [change.task_id for change in self.changes]
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("Одна задача указана в сценарии несколько раз.")
        return self


class ScenarioNormalizedChangeSchema(BaseModel):
    """Нормализованное прямое или каскадное изменение."""

    task_id: int
    task_key: str
    task_title: str
    current: ScenarioTaskDatesSchema
    proposed: ScenarioTaskDatesSchema
    expected_updated_at: datetime
    source: ScenarioChangeSource
    reasons: list[CalendarRiskReasonSchema]


class ScenarioConflictSchema(BaseModel):
    """Объяснимый конфликт, мешающий применению сценария."""

    code: str
    message: str
    task_id: int
    task_key: str


class ScenarioPreviewResponseSchema(BaseModel):
    """Read-only результат вычисления сценария."""

    changes: list[ScenarioNormalizedChangeSchema]
    conflicts: list[ScenarioConflictSchema]
    consequences_count: int = Field(..., ge=0)
    can_apply: bool


class ScenarioApplyChangeSchema(ScenarioTaskDatesSchema):
    """Изменение с версией, подтверждаемое пользователем."""

    task_id: int = Field(..., gt=0)
    expected_updated_at: datetime

    @model_validator(mode="after")
    def validate_date_order(self):
        if (
            self.start_date is not None
            and self.due_date is not None
            and self.start_date > self.due_date
        ):
            raise ValueError("Дата начала не может быть позже даты завершения.")
        return self


class ScenarioApplyRequestSchema(BaseModel):
    """Подтверждённый результат preview для атомарного применения."""

    changes: list[ScenarioApplyChangeSchema] = Field(..., min_length=1, max_length=250)
    date_change_reason: str | None = Field(
        None,
        max_length=2000,
        description="Общая причина изменения или снятия назначенных дат всех задач сценария.",
        examples=["Перенос поставки оборудования и зависимых задач."],
    )

    @model_validator(mode="after")
    def validate_unique_tasks(self):
        task_ids = [change.task_id for change in self.changes]
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("Одна задача указана в сценарии несколько раз.")
        return self


class ScenarioApplyResponseSchema(BaseModel):
    """Результат атомарного применения сценария."""

    applied_count: int = Field(..., ge=0)
    task_ids: list[int]
