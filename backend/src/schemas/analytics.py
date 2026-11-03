import enum
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from src.schemas.project_risks import ProjectRiskSummarySchema

MAX_FINDINGS = 8
MAX_PROGRESS = 6
MAX_RECOMMENDATIONS = 6
MAX_LINKED_TASKS = 6


class AnalyticsScope(str, enum.Enum):
    """Область анализа: весь портфель пользователя или один проект."""

    PORTFOLIO = "PORTFOLIO"
    PROJECT = "PROJECT"


class AnalyticsHealth(str, enum.Enum):
    """Общая оценка состояния работ в выбранной области."""

    STABLE = "STABLE"
    WATCH = "WATCH"
    RISK = "RISK"
    CRITICAL = "CRITICAL"


class AnalyticsSeverity(str, enum.Enum):
    """Значимость находки для руководителя проекта."""

    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class AnalyticsFindingKind(str, enum.Enum):
    """Тип находки: определяет иконку и группировку в интерфейсе."""

    OVERDUE = "OVERDUE"
    RISK = "RISK"
    BLOCKER = "BLOCKER"
    PROCESS = "PROCESS"
    DATA_GAP = "DATA_GAP"


class AnalyticsHorizon(str, enum.Enum):
    """Срок, на который рассчитано организационное действие."""

    TODAY = "TODAY"
    WEEK = "WEEK"
    LATER = "LATER"


# Блок черновика модели.
#
# Черновые схемы отличаются от публичных составом полей: модель адресует
# задачи ключами (`PROJ-142`), а наружу уходят проверенные ссылки с
# идентификаторами. Ключ, которого нет в проекте, отбрасывается сервисом,
# поэтому в ответ API не может попасть выдуманная задача.


class AnalyticsFindingDraftSchema(BaseModel):
    """Находка в ответе модели: что не так и где именно."""

    kind: AnalyticsFindingKind = Field(..., description="Тип находки.", examples=["OVERDUE"])
    severity: AnalyticsSeverity = Field(
        ...,
        description="Значимость находки.",
        examples=["HIGH"],
    )
    title: str = Field(
        ...,
        min_length=1,
        max_length=140,
        description="Суть находки одной строкой.",
        examples=["Интеграция с 1С просрочена на две недели"],
    )
    detail: str = Field(
        ...,
        min_length=1,
        max_length=700,
        description="Чем подтверждается находка: факты из задач и комментариев.",
        examples=["Срок 20.08, последний комментарий 22.08: ждём доступы от подрядчика."],
    )
    project_key: str | None = Field(
        None,
        max_length=32,
        description="Код проекта, к которому относится находка.",
        examples=["PROJ"],
    )
    task_keys: list[str] = Field(
        default_factory=list,
        max_length=MAX_LINKED_TASKS,
        description="Ключи задач-подтверждений.",
        examples=[["PROJ-142", "PROJ-151"]],
    )


class AnalyticsProgressDraftSchema(BaseModel):
    """Достигнутый результат в ответе модели."""

    title: str = Field(
        ...,
        min_length=1,
        max_length=140,
        description="Что сделано.",
        examples=["Закрыт контур авторизации"],
    )
    detail: str = Field(
        ...,
        min_length=1,
        max_length=700,
        description="Как это сделано — по комментариям и истории задач.",
        examples=["Пять задач блока закрыты за неделю, ревью прошло без замечаний."],
    )
    project_key: str | None = Field(
        None,
        max_length=32,
        description="Код проекта результата.",
        examples=["PROJ"],
    )
    task_keys: list[str] = Field(
        default_factory=list,
        max_length=MAX_LINKED_TASKS,
        description="Ключи задач, из которых собран результат.",
        examples=[["PROJ-101"]],
    )


class AnalyticsRecommendationDraftSchema(BaseModel):
    """Организационное действие, предложенное моделью."""

    horizon: AnalyticsHorizon = Field(
        ...,
        description="Когда действие нужно выполнить.",
        examples=["TODAY"],
    )
    title: str = Field(
        ...,
        min_length=1,
        max_length=140,
        description="Действие в повелительном наклонении.",
        examples=["Назначить владельца блока интеграций"],
    )
    detail: str = Field(
        ...,
        min_length=1,
        max_length=700,
        description="Что именно сделать и что это изменит.",
        examples=["Четыре задачи блока без исполнителя — сроки не за кем закрепить."],
    )
    project_key: str | None = Field(
        None,
        max_length=32,
        description="Код проекта, которого касается действие.",
        examples=["PROJ"],
    )
    task_keys: list[str] = Field(
        default_factory=list,
        max_length=MAX_LINKED_TASKS,
        description="Ключи задач, которых касается действие.",
        examples=[["PROJ-142"]],
    )


class AnalyticsDraftSchema(BaseModel):
    """Свод целиком в том виде, в каком его возвращает модель."""

    headline: str = Field(
        ...,
        min_length=1,
        max_length=280,
        description="Главное о состоянии работ одним предложением.",
        examples=["Портфель идёт по плану, но интеграционный блок тормозит два проекта."],
    )
    health: AnalyticsHealth = Field(
        ...,
        description="Общая оценка состояния.",
        examples=["WATCH"],
    )
    health_note: str = Field(
        ...,
        min_length=1,
        max_length=400,
        description="Почему выставлена именно такая оценка.",
        examples=["Просрочек немного, но все они в одном критическом блоке."],
    )
    findings: list[AnalyticsFindingDraftSchema] = Field(
        default_factory=list,
        max_length=MAX_FINDINGS,
        description="Находки: просрочки, риски, блокеры, пробелы в данных.",
    )
    progress: list[AnalyticsProgressDraftSchema] = Field(
        default_factory=list,
        max_length=MAX_PROGRESS,
        description="Что сделано за последнее время и как.",
    )
    recommendations: list[AnalyticsRecommendationDraftSchema] = Field(
        default_factory=list,
        max_length=MAX_RECOMMENDATIONS,
        description="Организационные решения и действия.",
    )


# Блок публичного контракта.


class AnalyticsTaskRefSchema(BaseModel):
    """Проверенная ссылка на задачу: карточка открывается прямо из свода."""

    id: int = Field(..., description="Идентификатор задачи.", examples=[142])
    key: str = Field(..., description="Отображаемый ключ задачи.", examples=["PROJ-142"])
    title: str = Field(
        ...,
        description="Заголовок задачи.",
        examples=["Интеграция с 1С"],
    )
    project_key: str = Field(..., description="Код проекта задачи.", examples=["PROJ"])
    due_date: date | None = Field(
        None,
        description="Плановая дата завершения.",
        examples=["2026-08-20"],
    )
    is_overdue: bool = Field(..., description="Признак просроченной задачи.", examples=[True])


class AnalyticsFindingSchema(BaseModel):
    """Находка с проверенными ссылками на задачи."""

    kind: AnalyticsFindingKind = Field(..., description="Тип находки.", examples=["OVERDUE"])
    severity: AnalyticsSeverity = Field(..., description="Значимость.", examples=["HIGH"])
    title: str = Field(
        ...,
        description="Суть находки.",
        examples=["Интеграция с 1С просрочена на две недели"],
    )
    detail: str = Field(
        ...,
        description="Подтверждение находки фактами.",
        examples=["Срок 20.08, последний комментарий 22.08: ждём доступы."],
    )
    project_key: str | None = Field(None, description="Код проекта.", examples=["PROJ"])
    project_name: str | None = Field(
        None, description="Название проекта.", examples=["TELOS SPACE"]
    )
    tasks: list[AnalyticsTaskRefSchema] = Field(
        default_factory=list,
        description="Задачи-подтверждения.",
    )


class AnalyticsProgressSchema(BaseModel):
    """Достигнутый результат с проверенными ссылками на задачи."""

    title: str = Field(..., description="Что сделано.", examples=["Закрыт контур авторизации"])
    detail: str = Field(
        ...,
        description="Как это сделано.",
        examples=["Пять задач блока закрыты за неделю."],
    )
    project_key: str | None = Field(None, description="Код проекта.", examples=["PROJ"])
    project_name: str | None = Field(
        None, description="Название проекта.", examples=["TELOS SPACE"]
    )
    tasks: list[AnalyticsTaskRefSchema] = Field(
        default_factory=list,
        description="Задачи результата.",
    )


class AnalyticsRecommendationSchema(BaseModel):
    """Организационное действие с проверенными ссылками на задачи."""

    horizon: AnalyticsHorizon = Field(..., description="Срок действия.", examples=["TODAY"])
    title: str = Field(
        ...,
        description="Действие.",
        examples=["Назначить владельца блока интеграций"],
    )
    detail: str = Field(
        ...,
        description="Что сделать и что это изменит.",
        examples=["Четыре задачи блока без исполнителя."],
    )
    project_key: str | None = Field(None, description="Код проекта.", examples=["PROJ"])
    project_name: str | None = Field(
        None, description="Название проекта.", examples=["TELOS SPACE"]
    )
    tasks: list[AnalyticsTaskRefSchema] = Field(
        default_factory=list,
        description="Задачи действия.",
    )


class AnalyticsSignalsSchema(ProjectRiskSummarySchema):
    """Факты, посчитанные по базе, а не моделью.

    Эти числа показываются рядом с текстом свода: они позволяют проверить
    выводы модели и остаются верными, даже если модель ошиблась.
    """

    total_tasks: int = Field(..., ge=0, description="Всего задач в области.", examples=[86])
    done_tasks: int = Field(..., ge=0, description="Завершённых задач.", examples=[41])
    overdue_tasks: int = Field(..., ge=0, description="Просроченных задач.", examples=[5])
    due_soon_tasks: int = Field(
        ...,
        ge=0,
        description="Незавершённых задач со сроком в ближайшую неделю.",
        examples=[7],
    )
    no_due_date_tasks: int = Field(
        ...,
        ge=0,
        description="Незавершённых задач без срока.",
        examples=[12],
    )
    unassigned_tasks: int = Field(
        ...,
        ge=0,
        description="Незавершённых задач без исполнителя.",
        examples=[9],
    )
    stale_tasks: int = Field(
        ...,
        ge=0,
        description="Незавершённых задач без изменений дольше двух недель.",
        examples=[6],
    )
    blocked_tasks: int = Field(
        ...,
        ge=0,
        description="Незавершённых задач с незакрытым предшественником.",
        examples=[3],
    )
    unplaced_tasks: int = Field(
        ...,
        ge=0,
        description="Задач вне структуры работ.",
        examples=[4],
    )
    milestones_at_risk: int = Field(
        ...,
        ge=0,
        description="Вех со сроком в прошлом, но без отметки достижения.",
        examples=[1],
    )


class AnalyticsEntityCountSchema(BaseModel):
    """Полное и переданное число записей одного вида источников."""

    total: int = Field(..., ge=0, description="Записей в проекте или портфеле.", examples=[32])
    included: int = Field(..., ge=0, description="Записей в контексте модели.", examples=[20])


class AnalyticsContextSchema(BaseModel):
    """Что именно увидела модель: границы анализа видны пользователю."""

    projects: int = Field(..., ge=0, description="Проектов в контексте.", examples=[3])
    risks_total: int = Field(0, ge=0, description="Всего зарегистрированных рисков.", examples=[8])
    risks_included: int = Field(
        0, ge=0, description="Рисков в содержательном контексте модели.", examples=[8]
    )
    tasks_total: int = Field(..., ge=0, description="Всего задач в области.", examples=[340])
    tasks_included: int = Field(
        ...,
        ge=0,
        description="Задач, попавших в контекст модели.",
        examples=[150],
    )
    comments_included: int = Field(..., ge=0, description="Комментариев.", examples=[48])
    documents_included: int = Field(..., ge=0, description="Документов.", examples=[6])
    stickers_included: int = Field(..., ge=0, description="Стикеров доски.", examples=[11])
    wbs_nodes_included: int = Field(..., ge=0, description="Разделов ИСР.", examples=[18])
    milestones_included: int = Field(..., ge=0, description="Вех.", examples=[4])
    activity_included: int = Field(..., ge=0, description="Событий истории задач.", examples=[60])
    entity_counts: dict[str, AnalyticsEntityCountSchema] = Field(
        default_factory=dict,
        description="Охват каждого вида источников, включая команду, назначения, вложения и связи.",
        examples=[{"attachments": {"total": 12, "included": 10}}],
    )
    truncated: bool = Field(
        ...,
        description="Признак, что часть данных не поместилась в контекст.",
        examples=[True],
    )
    omitted: list[str] = Field(
        default_factory=list,
        description="Что осталось за пределами анализа.",
        examples=[["в анализ вошли 150 из 340 задач: остальные отсечены лимитом контекста"]],
    )


class AnalyticsReportSchema(BaseModel):
    """Сохранённый аналитический свод дашборда."""

    id: int = Field(..., description="Идентификатор свода.", examples=[12])
    scope: AnalyticsScope = Field(..., description="Область анализа.", examples=["PROJECT"])
    project_id: int | None = Field(None, description="Идентификатор проекта.", examples=[1])
    project_key: str | None = Field(None, description="Код проекта.", examples=["PROJ"])
    project_name: str | None = Field(
        None, description="Название проекта.", examples=["TELOS SPACE"]
    )
    created_at: datetime = Field(
        ...,
        description="Момент формирования свода.",
        examples=["2026-09-04T12:00:00Z"],
    )
    created_by: str = Field(..., description="Кто запросил анализ.", examples=["Иванов Иван"])
    llm_model: str = Field(
        ...,
        description="Модель, сформировавшая свод.",
        examples=["google/gemini-3.7-flash"],
    )
    duration_ms: int = Field(..., ge=0, description="Время формирования, мс.", examples=[8400])
    headline: str = Field(
        ...,
        description="Главное о состоянии работ.",
        examples=["Портфель идёт по плану, но интеграционный блок тормозит два проекта."],
    )
    health: AnalyticsHealth = Field(..., description="Оценка состояния.", examples=["WATCH"])
    health_note: str = Field(
        ...,
        description="Обоснование оценки.",
        examples=["Просрочек немного, но все они в одном критическом блоке."],
    )
    findings: list[AnalyticsFindingSchema] = Field(
        default_factory=list,
        description="Находки свода.",
    )
    progress: list[AnalyticsProgressSchema] = Field(
        default_factory=list,
        description="Достигнутые результаты.",
    )
    recommendations: list[AnalyticsRecommendationSchema] = Field(
        default_factory=list,
        description="Организационные действия.",
    )
    signals: AnalyticsSignalsSchema = Field(..., description="Проверяемые факты по базе.")
    context: AnalyticsContextSchema = Field(..., description="Границы анализа.")


class AnalyticsGenerateRequest(BaseModel):
    """Тело запроса на формирование свода."""

    model_config = ConfigDict(json_schema_extra={"example": {"project_id": 1}})

    project_id: int | None = Field(
        None,
        gt=0,
        description="Проект для анализа; null — анализ всего портфеля пользователя.",
        examples=[1],
    )
