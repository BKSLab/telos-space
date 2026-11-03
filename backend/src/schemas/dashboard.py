from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from src.db.models.projects import ProjectStatus
from src.schemas.enums import TaskPriority
from src.schemas.project_risks import ProjectRiskSummarySchema


class DashboardTotalsSchema(BaseModel):
    """Суммарные показатели по всем проектам."""

    total_projects: int = Field(..., ge=0, description="Всего проектов.", examples=[4])
    active_projects: int = Field(
        ...,
        ge=0,
        description="Проектов в статусе ACTIVE.",
        examples=[2],
    )
    total_tasks: int = Field(..., ge=0, description="Всего задач.", examples=[86])
    done_tasks: int = Field(..., ge=0, description="Выполненных задач.", examples=[41])
    in_progress_tasks: int = Field(..., ge=0, description="Задач в работе.", examples=[19])
    overdue_tasks: int = Field(..., ge=0, description="Просроченных задач.", examples=[5])
    completion_rate: float = Field(
        ...,
        ge=0,
        le=1,
        description="Доля выполненных задач по всем проектам.",
        examples=[0.48],
    )


class DashboardProjectSchema(BaseModel):
    """Карточка проекта на общем дашборде."""

    model_config = ConfigDict(from_attributes=True)
    risks: ProjectRiskSummarySchema = Field(
        default_factory=ProjectRiskSummarySchema, description="Сигналы проектного реестра рисков."
    )

    id: int = Field(..., description="Идентификатор проекта.", examples=[1])
    key: str = Field(..., description="Короткий код проекта.", examples=["PROJ"])
    name: str = Field(..., description="Название проекта.", examples=["TELOS SPACE"])
    description_md: str | None = Field(
        None,
        description="Описание проекта в Markdown.",
        examples=["Персональный ассистент для подбора работы."],
    )
    status: ProjectStatus = Field(..., description="Статус проекта.", examples=["ACTIVE"])
    color: str = Field(..., description="HEX-цвет проекта.", examples=["#58a6ff"])
    icon: str | None = Field(None, description="Эмодзи-иконка проекта.", examples=["🚀"])
    icon_image_version: str | None = Field(None, description="Версия локальной картинки проекта.")
    total_tasks: int = Field(..., ge=0, description="Всего задач в проекте.", examples=[18])
    done_tasks: int = Field(..., ge=0, description="Выполненных задач.", examples=[9])
    in_progress_tasks: int = Field(..., ge=0, description="Задач в работе.", examples=[5])
    overdue_tasks: int = Field(..., ge=0, description="Просроченных задач.", examples=[2])
    completion_rate: float = Field(
        ...,
        ge=0,
        le=1,
        description="Доля выполненных задач проекта.",
        examples=[0.5],
    )
    next_due_date: date | None = Field(
        None,
        description="Ближайший срок среди незавершённых задач.",
        examples=["2026-09-08"],
    )
    updated_at: datetime = Field(
        ...,
        description="Дата последнего обновления проекта.",
        examples=["2026-09-02T12:00:00Z"],
    )


class DashboardTaskSchema(BaseModel):
    """Задача, требующая внимания, в сводке дашборда."""

    id: int = Field(..., description="Идентификатор задачи.", examples=[142])
    key: str = Field(..., description="Отображаемый идентификатор.", examples=["PROJ-142"])
    title: str = Field(
        ...,
        description="Заголовок задачи.",
        examples=["Реализовать фильтрацию проектов"],
    )
    project_id: int = Field(..., description="Идентификатор проекта.", examples=[1])
    project_key: str = Field(..., description="Код проекта.", examples=["PROJ"])
    project_name: str = Field(..., description="Название проекта.", examples=["TELOS SPACE"])
    project_color: str = Field(..., description="HEX-цвет проекта.", examples=["#58a6ff"])
    stage_id: int = Field(..., description="Идентификатор стадии.", examples=[2])
    stage_name: str = Field(..., description="Название стадии.", examples=["В работе"])
    priority: TaskPriority = Field(..., description="Приоритет задачи.", examples=["HIGH"])
    due_date: date | None = Field(
        None,
        description="Плановая дата завершения.",
        examples=["2026-09-08"],
    )
    is_overdue: bool = Field(..., description="Признак просроченной задачи.", examples=[True])
    updated_at: datetime = Field(
        ...,
        description="Дата последнего обновления задачи.",
        examples=["2026-09-02T12:00:00Z"],
    )


class DashboardSchema(BaseModel):
    """Сводка состояния всех проектов."""

    has_outside_tasks: bool = Field(False, description="Есть открытые разовые задачи пользователя.")
    totals: DashboardTotalsSchema = Field(..., description="Суммарные показатели портфеля.")
    projects: list[DashboardProjectSchema] = Field(
        default_factory=list,
        description="Проекты с прогрессом и проблемными признаками.",
    )
    attention_tasks: list[DashboardTaskSchema] = Field(
        default_factory=list,
        description="Просроченные задачи и ближайшие сроки.",
    )
    recent_tasks: list[DashboardTaskSchema] = Field(
        default_factory=list,
        description="Недавно изменённые задачи.",
    )
