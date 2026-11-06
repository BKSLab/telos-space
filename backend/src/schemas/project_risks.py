from datetime import date, datetime
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.schemas.enums import (
    RiskRating,
    RiskReasonCode,
    RiskResponseStrategy,
    RiskSource,
    RiskStatus,
)

RISK_EXAMPLE = {
    "title": "Задержка интеграции CRM",
    "description": "Документация поставщика может задержаться.",
    "probability": "HIGH",
    "impact": "HIGH",
    "response_strategy": "MITIGATE",
    "mitigation_plan": "Согласовать контракт API заранее.",
    "response_plan": "Перейти на тестовый адаптер.",
    "owner_user_id": None,
    "task_id": None,
    "review_date": "2026-09-12",
}


class ProjectRiskCreateSchema(BaseModel):
    """Поля риска, который пользователь решил зарегистрировать."""

    model_config = ConfigDict(
        extra="forbid", str_strip_whitespace=True, json_schema_extra={"example": RISK_EXAMPLE}
    )

    title: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Название рискового события.",
        examples=[RISK_EXAMPLE["title"]],
    )
    description: str = Field(
        ...,
        min_length=1,
        max_length=20000,
        description="Описание события, причин и последствий в Markdown.",
        examples=[RISK_EXAMPLE["description"]],
    )
    probability: RiskRating = Field(..., description="Вероятность события.", examples=["HIGH"])
    impact: RiskRating = Field(..., description="Влияние на проект.", examples=["HIGH"])
    status: RiskStatus = Field(
        RiskStatus.OPEN, description="Состояние риска, независимо от канбана.", examples=["OPEN"]
    )
    response_strategy: RiskResponseStrategy = Field(
        ..., description="Выбранная стратегия реагирования.", examples=["MITIGATE"]
    )
    mitigation_plan: str = Field(
        "",
        max_length=20000,
        description="Превентивные меры в Markdown.",
        examples=[RISK_EXAMPLE["mitigation_plan"]],
    )
    response_plan: str = Field(
        "",
        max_length=20000,
        description="Действия при реализации события в Markdown.",
        examples=[RISK_EXAMPLE["response_plan"]],
    )
    owner_user_id: int | None = Field(
        None, gt=0, description="Ответственный участник проекта.", examples=[15]
    )
    task_id: int | None = Field(
        None, gt=0, description="Необязательная задача текущего проекта.", examples=[142]
    )
    review_date: date | None = Field(
        None, description="Дата следующего контроля включительно.", examples=["2026-09-12"]
    )
    source: RiskSource = Field(
        RiskSource.MANUAL,
        description="Происхождение риска; AI-предложение регистрируется после подтверждения человеком.",
        examples=["MANUAL"],
    )

    @field_validator("title", "description", "mitigation_plan", "response_plan")
    @classmethod
    def reject_null_bytes(cls, value: str) -> str:
        """Не пропускает символы, которые PostgreSQL не может сохранить."""
        if "\x00" in value:
            raise ValueError("Текст не может содержать нулевой символ.")
        return value


class ProjectRiskUpdateSchema(BaseModel):
    """Частичное изменение риска; вычисляемая оценка и источник неизменяемы клиентом."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        json_schema_extra={"example": {"probability": "MEDIUM", "status": "MITIGATING"}},
    )

    title: str | None = Field(
        None,
        min_length=1,
        max_length=255,
        description="Новое название.",
        examples=["Задержка интеграции CRM"],
    )
    description: str | None = Field(
        None,
        min_length=1,
        max_length=20000,
        description="Markdown-описание.",
        examples=["Уточнены последствия задержки."],
    )
    probability: RiskRating | None = Field(
        None, description="Новая вероятность.", examples=["MEDIUM"]
    )
    impact: RiskRating | None = Field(None, description="Новое влияние.", examples=["HIGH"])
    status: RiskStatus | None = Field(
        None,
        description="Новое состояние; закрытый риск можно открыть повторно.",
        examples=["MITIGATING"],
    )
    response_strategy: RiskResponseStrategy | None = Field(
        None, description="Стратегия реагирования.", examples=["ACCEPT"]
    )
    mitigation_plan: str | None = Field(
        None,
        max_length=20000,
        description="Превентивные меры; пустая строка очищает поле.",
        examples=["Согласовать контракт API."],
    )
    response_plan: str | None = Field(
        None,
        max_length=20000,
        description="План реагирования; пустая строка очищает поле.",
        examples=["Использовать резервного поставщика."],
    )
    owner_user_id: int | None = Field(
        None, gt=0, description="Ответственный; null снимает назначение.", examples=[15]
    )
    task_id: int | None = Field(
        None, gt=0, description="Задача проекта; null удаляет связь.", examples=[142]
    )
    review_date: date | None = Field(
        None, description="Дата контроля; null очищает дату.", examples=["2026-09-12"]
    )

    @model_validator(mode="after")
    def validate_changes(self) -> Self:
        """Различает пропущенное поле и явный null, запрещает пустое изменение."""
        if not self.model_fields_set:
            raise ValueError("Передайте хотя бы одно изменение риска.")
        for name in self.model_fields_set:
            value = getattr(self, name)
            if value is None and name not in {"owner_user_id", "task_id", "review_date"}:
                raise ValueError(f"Поле {name} не может быть null.")
            if isinstance(value, str) and "\x00" in value:
                raise ValueError("Текст не может содержать нулевой символ.")
        return self


class ProjectRiskSchema(ProjectRiskCreateSchema):
    """Сохранённый риск с серверной оценкой и стабильным номером."""

    model_config = ConfigDict(from_attributes=True, extra="ignore")

    id: int = Field(..., description="Идентификатор риска.", examples=[12])
    key: str = Field(..., description="Стабильный номер риска.", examples=["RISK-12"])
    project_id: int = Field(..., description="Проект риска.", examples=[1])
    risk_level: RiskRating = Field(
        ..., description="Оценка по серверной матрице.", examples=["HIGH"]
    )
    created_at: datetime = Field(
        ..., description="Время регистрации риска.", examples=["2026-09-06T10:00:00Z"]
    )
    updated_at: datetime = Field(
        ..., description="Время последнего изменения.", examples=["2026-09-06T10:00:00Z"]
    )


class ProjectRiskFilters(BaseModel):
    """Общие фильтры реестра, матрицы и инструментов MCP."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    status: RiskStatus | None = Field(None, description="Точное состояние.", examples=["OPEN"])
    probability: RiskRating | None = Field(None, description="Вероятность.", examples=["HIGH"])
    impact: RiskRating | None = Field(None, description="Влияние.", examples=["HIGH"])
    risk_level: RiskRating | None = Field(None, description="Итоговый уровень.", examples=["HIGH"])
    owner_user_id: int | None = Field(None, gt=0, description="Ответственный.", examples=[15])
    task_id: int | None = Field(None, gt=0, description="Связанная задача.", examples=[142])
    search: str | None = Field(
        None, max_length=255, description="Поиск по номеру, названию и описанию.", examples=["CRM"]
    )
    active_only: bool = Field(
        False, description="Исключить CLOSED, сохранив OCCURRED.", examples=[True]
    )


class ProjectRiskPageSchema(BaseModel):
    """Страница отфильтрованного реестра."""

    total: int = Field(..., ge=0, description="Всего совпадений.", examples=[42])
    page: int = Field(..., ge=1, description="Номер страницы от единицы.", examples=[1])
    page_size: int = Field(..., ge=1, le=100, description="Размер страницы.", examples=[25])
    items: list[ProjectRiskSchema] = Field(..., description="Риски текущей страницы.")


class RiskMatrixCellSchema(BaseModel):
    """Количество рисков одной комбинации матрицы."""

    probability: RiskRating = Field(..., description="Строка матрицы.", examples=["HIGH"])
    impact: RiskRating = Field(..., description="Столбец матрицы.", examples=["HIGH"])
    count: int = Field(0, ge=0, description="Число рисков в ячейке.", examples=[2])


class RiskSignalSchema(BaseModel):
    """Объяснимый сигнал внимания для обзора проекта."""

    code: RiskReasonCode = Field(
        ..., description="Стабильный код причины.", examples=["HIGH_OPEN_RISK"]
    )
    count: int = Field(
        ..., ge=0, description="Число соответствующих активных рисков.", examples=[2]
    )


class ProjectRiskSummarySchema(BaseModel):
    """Счётчики по всему отфильтрованному набору, независимо от страницы.

    Уровни и пробелы управления считаются только среди активных рисков.
    OCCURRED остаётся активным до явного закрытия; HIGH_OPEN_RISK относится
    только к ещё не реализовавшимся OPEN/MITIGATING.
    """

    total_risks: int = Field(0, ge=0, description="Все зарегистрированные риски.", examples=[8])
    active_risks: int = Field(0, ge=0, description="Все риски, кроме CLOSED.", examples=[5])
    open_risks: int = Field(0, ge=0, description="Выявленные риски OPEN.", examples=[3])
    mitigating_risks: int = Field(
        0, ge=0, description="Риски со снижением MITIGATING.", examples=[1]
    )
    occurred_risks: int = Field(
        0, ge=0, description="Реализовавшиеся события OCCURRED.", examples=[1]
    )
    closed_risks: int = Field(0, ge=0, description="Закрытые риски.", examples=[3])
    high_risks: int = Field(0, ge=0, description="Активные HIGH.", examples=[2])
    medium_risks: int = Field(0, ge=0, description="Активные MEDIUM.", examples=[2])
    low_risks: int = Field(0, ge=0, description="Активные LOW.", examples=[1])
    risks_without_owner: int = Field(
        0, ge=0, description="Активные без ответственного.", examples=[1]
    )
    risks_without_mitigation: int = Field(
        0, ge=0, description="Активные без превентивного плана.", examples=[1]
    )
    risks_due_for_review: int = Field(
        0, ge=0, description="Активные с датой контроля сегодня или раньше.", examples=[2]
    )
    risks_review_overdue: int = Field(
        0, ge=0, description="Активные с прошедшей датой контроля.", examples=[1]
    )
    risks_linked_to_tasks: int = Field(
        0, ge=0, description="Все риски со связанной задачей.", examples=[4]
    )
    ai_suggested_risks: int = Field(
        0, ge=0, description="Все принятые человеком AI-предложения.", examples=[1]
    )
    latest_update: datetime | None = Field(
        None,
        description="Последнее изменение реестра для определения свежести свода.",
        examples=["2026-09-06T10:00:00Z"],
    )
    matrix: list[RiskMatrixCellSchema] = Field(
        default_factory=list,
        description="Все девять ячеек по текущим фильтрам, включая закрытые, если они не исключены.",
    )
    signals: list[RiskSignalSchema] = Field(
        default_factory=list, description="Причины внимания к активным рискам."
    )
