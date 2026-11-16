from datetime import date, datetime

from pydantic import BaseModel, Field

from src.db.models.project_milestones import ProjectMilestoneStatus
from src.db.models.task_dependencies import TaskDependencyType
from src.schemas.enums import TaskPriority
from src.schemas.users import UserSummarySchema


class CalendarRangeSchema(BaseModel):
    """Фактический диапазон ответа календаря."""

    date_from: date = Field(..., description="Первый день диапазона включительно.")
    date_to: date = Field(..., description="Последний день диапазона включительно.")
    today: date = Field(..., description="Локальная текущая дата клиента для сигналов срока.")


class CalendarProjectSchema(BaseModel):
    """Границы времени проекта."""

    start_date: date | None = Field(None, description="Плановое начало проекта.")
    due_date: date | None = Field(None, description="Системный milestone завершения проекта.")


class CalendarStageSchema(BaseModel):
    """Стадия для отображения и фильтра календаря."""

    id: int
    name: str
    color: str
    order_index: int
    is_done_stage: bool


class CalendarWbsNodeSchema(BaseModel):
    """Узел ИСР для группировки календаря."""

    id: int
    parent_id: int | None
    title: str
    position: float


class CalendarRiskReasonSchema(BaseModel):
    """Объяснимая причина сигнала риска задачи."""

    code: str
    message: str
    days: int | None = None
    task_key: str | None = None
    milestone_title: str | None = None


class CalendarTaskSchema(BaseModel):
    """Компактная задача временного представления."""

    id: int
    revision: int = Field(
        default=1,
        ge=1,
        description="Текущая версия задачи для expected_revision при изменении её дат.",
        examples=[3],
    )
    key: str
    title: str
    start_date: date | None
    due_date: date | None
    baseline_start_date: date | None
    baseline_due_date: date | None
    drift_days: int | None
    stage_id: int
    wbs_node_id: int | None
    priority: TaskPriority
    assignee: str | None
    executor: UserSummarySchema | None = Field(
        default=None,
        description="Назначенный исполнитель; свободная подпись assignee не определяет identity.",
        examples=[None],
    )
    is_done: bool
    is_overdue: bool
    is_due_soon: bool
    risk_level: str | None
    risk_reasons: list[CalendarRiskReasonSchema]
    updated_at: datetime

    progress: int | None = Field(
        None,
        ge=0,
        le=100,
        description=(
            "Процент выполнения: 100 у выполненной задачи, иначе доля отмеченных пунктов "
            "чек-листа; null — чек-листа нет, прогресс неизвестен."
        ),
    )

    is_critical: bool = Field(
        False,
        description="Задача на критическом пути проекта: запас по цепочке FS не больше нуля.",
    )


class CalendarDateChangeSchema(BaseModel):
    """Недавнее изменение дедлайна задачи."""

    id: int
    task_id: int
    task_key: str
    task_title: str
    from_date: date | None
    to_date: date | None
    changed_at: datetime


class CalendarMilestoneSchema(BaseModel):
    """Пользовательская или системная веха временной карты."""

    id: int | None
    title: str
    due_date: date
    status: ProjectMilestoneStatus
    wbs_node_id: int | None
    description_md: str | None
    is_system: bool = False


class CalendarDependencySchema(BaseModel):
    """Связь задач для временной карты и SVG-overlay."""

    id: int
    predecessor_task_id: int
    successor_task_id: int
    dependency_type: TaskDependencyType
    lag_days: int

    is_critical: bool = Field(
        False,
        description="Связь критического пути: обе задачи критичны и у связи нет запаса.",
    )


class CalendarSummarySchema(BaseModel):
    """Счётчики задач, соответствующих фильтрам календаря."""

    overdue: int = Field(..., ge=0)
    due_soon: int = Field(..., ge=0)
    unscheduled: int = Field(..., ge=0)
    drifted: int = Field(..., ge=0)
    dependency_risks: int = Field(0, ge=0)


class CalendarResponseSchema(BaseModel):
    """Read model выбранного диапазона календаря."""

    range: CalendarRangeSchema
    project: CalendarProjectSchema
    tasks: list[CalendarTaskSchema]
    stages: list[CalendarStageSchema]
    wbs_nodes: list[CalendarWbsNodeSchema]
    assignees: list[str]
    summary: CalendarSummarySchema
    recent_changes: list[CalendarDateChangeSchema]
    milestones: list[CalendarMilestoneSchema]
    dependencies: list[CalendarDependencySchema]


class UnscheduledTasksPageSchema(BaseModel):
    """Страница задач проекта без срока."""

    items: list[CalendarTaskSchema]
    next_cursor: int | None = None
