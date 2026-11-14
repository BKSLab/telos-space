from dataclasses import dataclass
from datetime import date, datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.db.models.task_participants import TaskParticipantRole
from src.schemas.enums import TaskKind, TaskPriority, TaskRole, TaskStatus
from src.schemas.task_checklists import TaskChecklistSchema
from src.schemas.task_reports import TaskReportSchema
from src.schemas.users import UserSummarySchema


class TaskParticipantSchema(BaseModel):
    """Член команды и его роль в конкретной задаче."""

    id: int = Field(..., description="Идентификатор назначения.", examples=[18])
    role: TaskParticipantRole = Field(
        ...,
        description="Исполнитель, постановщик или наблюдатель.",
        examples=["EXECUTOR"],
    )
    user: UserSummarySchema = Field(..., description="Участник проектной команды.")


class TaskSchema(BaseModel):
    """Задача проекта с агрегированным контекстом."""

    revision: int = Field(
        1, ge=1, description="Текущая версия общих полей и назначений задачи.", examples=[3]
    )
    checklist: TaskChecklistSchema | None = Field(None, description="Чек-лист задачи или null.")
    checklist_revision: int = Field(
        0, ge=0, description="Текущая версия чек-листа для записи.", examples=[2]
    )

    model_config = ConfigDict(from_attributes=True)

    id: int = Field(..., description="Уникальный идентификатор задачи.", examples=[142])
    kind: TaskKind = Field(TaskKind.PROJECT, description="Вид задачи.")
    status: TaskStatus | None = Field(None, description="Статус разовой задачи.")
    project_id: int | None = Field(..., description="Идентификатор проекта.", examples=[1])
    stage_id: int | None = Field(..., description="Идентификатор текущей стадии.", examples=[2])
    wbs_node_id: int | None = Field(
        None,
        description="Раздел ИСР или null, если задача не распределена.",
        examples=[32],
    )
    number: int = Field(..., description="Номер задачи внутри проекта.", examples=[142])
    key: str = Field(
        ...,
        description="Отображаемый идентификатор задачи.",
        examples=["PROJ-142"],
    )
    title: str = Field(
        ...,
        description="Заголовок задачи.",
        examples=["Реализовать фильтрацию проектов"],
    )
    description_md: str | None = Field(
        None,
        description="Описание задачи в Markdown.",
        examples=["Добавить фильтрацию по статусу и исполнителю."],
    )
    priority: TaskPriority = Field(..., description="Приоритет задачи.", examples=["HIGH"])
    role: TaskRole | None = Field(None, description="Ответственная роль.", examples=["BE"])
    assignee: str | None = Field(
        None,
        description="Подпись исполнителя.",
        examples=["Иван"],
    )
    participants: list[TaskParticipantSchema] = Field(
        default_factory=list,
        description="Ролевые назначения участников команды.",
    )
    start_date: date | None = Field(
        None,
        description="Плановая дата начала.",
        examples=["2026-09-01"],
    )
    due_date: date | None = Field(
        None,
        description="Плановая дата завершения.",
        examples=["2026-09-08"],
    )
    baseline_start_date: date | None = Field(None, description="Зафиксированное начало baseline.")
    baseline_due_date: date | None = Field(None, description="Зафиксированное завершение baseline.")
    completed_at: datetime | None = Field(None, description="Фактическое время завершения.")
    position: float = Field(..., description="Позиция задачи внутри стадии.", examples=[1000.0])
    created_at: datetime = Field(
        ...,
        description="Дата создания задачи.",
        examples=["2026-09-01T10:00:00Z"],
    )
    updated_at: datetime = Field(
        ...,
        description="Дата последнего обновления задачи.",
        examples=["2026-09-02T12:00:00Z"],
    )
    comments_count: int = Field(
        0,
        ge=0,
        description="Количество комментариев задачи.",
        examples=[4],
    )
    last_comment: str | None = Field(
        None,
        description="Текст последнего комментария.",
        examples=["Готово к проверке."],
    )
    search_match_source: str | None = Field(
        None,
        description="Источник поискового совпадения.",
        examples=["title"],
    )
    search_title: str | None = Field(
        None,
        description="Заголовок с маркерами подсветки.",
        examples=["__FTS_START__Фильтрация__FTS_END__ проектов"],
    )
    search_excerpt: str | None = Field(
        None,
        description="Фрагмент совпадения с маркерами подсветки.",
        examples=["Добавить __FTS_START__фильтрацию__FTS_END__ по статусу."],
    )


class TaskCompactSchema(BaseModel):
    """Компактное представление задачи для пула ИСР и canvas."""

    checklist: TaskChecklistSchema | None = Field(None, description="Чек-лист задачи, если создан.")

    model_config = ConfigDict(from_attributes=True)

    id: int = Field(..., description="Уникальный идентификатор задачи.", examples=[142])
    key: str = Field(..., description="Отображаемый идентификатор.", examples=["PROJ-142"])
    title: str = Field(
        ...,
        description="Заголовок задачи.",
        examples=["Реализовать фильтрацию проектов"],
    )
    stage_id: int = Field(..., description="Идентификатор текущей стадии.", examples=[2])
    wbs_node_id: int | None = Field(None, description="Раздел ИСР.", examples=[32])
    wbs_position: float | None = Field(
        None,
        description="Позиция задачи среди задач своего раздела ИСР.",
        examples=[2000.0],
    )
    canvas_x: float | None = Field(
        None,
        description="Координата X карточки на холсте ИСР; null — задача в списке-пуле.",
        examples=[420.0],
    )
    canvas_y: float | None = Field(
        None,
        description="Координата Y карточки на холсте ИСР; null — задача в списке-пуле.",
        examples=[180.0],
    )
    priority: TaskPriority = Field(..., description="Приоритет задачи.", examples=["HIGH"])
    assignee: str | None = Field(None, description="Подпись исполнителя.", examples=["Иван"])
    start_date: date | None = Field(None, description="Плановая дата начала.")
    due_date: date | None = Field(
        None,
        description="Плановая дата завершения.",
        examples=["2026-09-08"],
    )
    is_done: bool = Field(
        ...,
        description="Признак задачи в завершающей стадии.",
        examples=[False],
    )


class TaskCreateSchema(BaseModel):
    """Тело запроса для создания задачи."""

    checklist: TaskChecklistSchema | None = Field(
        None, description="Чек-лист, сохраняемый атомарно с задачей."
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "title": "Реализовать фильтрацию проектов",
                "description_md": "Добавить фильтрацию по статусу и исполнителю.",
                "priority": "HIGH",
                "due_date": "2026-09-08",
            }
        }
    )

    title: str = Field(
        ...,
        min_length=1,
        max_length=512,
        description="Заголовок задачи.",
        examples=["Реализовать фильтрацию проектов"],
    )
    description_md: str | None = Field(
        None,
        description="Описание задачи в Markdown.",
        examples=["Добавить фильтрацию по статусу и исполнителю."],
    )
    stage_id: int | None = Field(
        None,
        gt=0,
        description=(
            "Стадия задачи. Без значения берётся начальная стадия проекта — "
            "крайняя левая колонка доски, обычно «Бэклог»."
        ),
        examples=[1],
    )
    wbs_node_id: int | None = Field(
        None,
        gt=0,
        description="Раздел ИСР, в который сразу помещается задача.",
        examples=[32],
    )
    priority: TaskPriority = Field(
        TaskPriority.MEDIUM,
        description="Приоритет задачи.",
        examples=["HIGH"],
    )
    role: TaskRole | None = Field(None, description="Ответственная роль.", examples=["BE"])
    assignee: str | None = Field(
        None,
        max_length=255,
        description="Подпись исполнителя.",
        examples=["Иван"],
    )
    executor_id: int | None = Field(
        None,
        gt=0,
        description="Пользователь-исполнитель из команды проекта.",
        examples=[7],
    )
    reporter_id: int | None = Field(
        None,
        gt=0,
        description="Пользователь-постановщик; по умолчанию автор запроса.",
        examples=[3],
    )
    observer_ids: list[int] = Field(
        default_factory=list,
        max_length=50,
        description="Пользователи-наблюдатели из команды проекта.",
        examples=[[4, 9]],
    )
    start_date: date | None = Field(
        None,
        description="Плановая дата начала.",
        examples=["2026-09-01"],
    )
    due_date: date | None = Field(
        None,
        description="Плановая дата завершения.",
        examples=["2026-09-08"],
    )


@dataclass(frozen=True, slots=True)
class TaskRephraseFile:
    """Прочитанный файл контекста, ещё не сохранённый в задаче.

    Объявлен рядом со схемой запроса: это часть контракта сценария, а не
    деталь его реализации, и транспорт не должен импортировать его из
    модуля сервиса.
    """

    name: str
    content: bytes


class TaskRephraseRequestSchema(BaseModel):
    """Черновик и выбранный контекст для переформулирования описания."""

    checklist: TaskChecklistSchema | None = Field(
        None, description="Текущий черновик чек-листа задачи."
    )
    title: str = Field("", max_length=512, description="Название новой или существующей задачи.")
    description_md: str = Field(
        ...,
        min_length=1,
        max_length=50_000,
        description="Пользовательский черновик, который нужно сделать понятнее.",
    )
    task_id: int | None = Field(
        None,
        gt=0,
        description="Существующая задача, если запрос выполняется из её карточки.",
    )
    document_refs: list[Annotated[str, Field(min_length=7, max_length=110)]] = Field(
        default_factory=list,
        max_length=50,
        description="Стабильные ссылки документов этого проекта для дополнительного контекста.",
    )


class TaskRephraseResultSchema(BaseModel):
    """Переформулированный, но ещё не сохранённый черновик описания."""

    description_md: str = Field(..., min_length=1, max_length=12_000)


class TaskUpdateSchema(BaseModel):
    """Тело запроса для частичного обновления задачи."""

    expected_revision: int | None = Field(
        None,
        ge=1,
        strict=True,
        description="Версия из чтения задачи; при несовпадении 409 без изменений. Без неё сохраняется прежнее поведение.",
        examples=[3],
    )
    checklist: TaskChecklistSchema | None = Field(
        None,
        description="Полный чек-лист в новом порядке; null удаляет, отсутствие поля сохраняет.",
    )
    checklist_revision: int | None = Field(
        None,
        ge=0,
        strict=True,
        description="Ожидаемая версия; обязательна при любом изменении чек-листа.",
        examples=[2],
    )

    @model_validator(mode="after")
    def validate_checklist_revision(self):
        changes = "checklist" in self.model_fields_set
        if changes and self.checklist_revision is None:
            raise ValueError("Для изменения чек-листа укажите checklist_revision.")
        if not changes and "checklist_revision" in self.model_fields_set:
            raise ValueError("Версию чек-листа нельзя изменять отдельно.")
        return self

    model_config = ConfigDict(json_schema_extra={"example": {"priority": "URGENT"}})

    title: str | None = Field(
        None,
        min_length=1,
        max_length=512,
        description="Новый заголовок.",
        examples=["Реализовать фильтрацию и сортировку"],
    )
    description_md: str | None = Field(
        None,
        description="Новое описание в Markdown.",
        examples=["Добавить фильтрацию, сортировку и сохранение пресетов."],
    )
    priority: TaskPriority | None = Field(
        None,
        description="Новый приоритет.",
        examples=["URGENT"],
    )
    role: TaskRole | None = Field(None, description="Новая ответственная роль.", examples=["FE"])
    assignee: str | None = Field(
        None,
        max_length=255,
        description="Новая подпись исполнителя или null для очистки.",
        examples=["Мария"],
    )
    executor_id: int | None = Field(
        None,
        gt=0,
        description="Новый исполнитель или null для снятия назначения.",
        examples=[7],
    )
    reporter_id: int | None = Field(
        None,
        gt=0,
        description="Новый постановщик или null для снятия назначения.",
        examples=[3],
    )
    observer_ids: list[int] | None = Field(
        None,
        max_length=50,
        description="Полный новый список наблюдателей; пустой список очищает его.",
        examples=[[4, 9]],
    )
    start_date: date | None = Field(
        None,
        description="Новая дата начала или null для очистки.",
        examples=["2026-09-03"],
    )
    due_date: date | None = Field(
        None,
        description="Новая дата завершения или null для очистки.",
        examples=["2026-09-12"],
    )


class TaskStagePlacementSchema(BaseModel):
    """Тело запроса для перемещения задачи по доске."""

    model_config = ConfigDict(json_schema_extra={"example": {"stage_id": 3}})

    stage_id: int = Field(..., gt=0, description="Целевая стадия.", examples=[3])
    position: float | None = Field(
        None,
        ge=0,
        description="Новая позиция внутри стадии; без значения задача ставится в конец.",
        examples=[2000.0],
    )


class TaskDetailSchema(TaskSchema):
    """Полная карточка, в отличие от списка, содержит актуальный отчёт."""

    report: TaskReportSchema | None = Field(
        None, description="Актуальная опубликованная версия отчёта; null, если публикаций нет."
    )


class PersonalTaskCreateSchema(TaskCreateSchema):
    """Тело запроса для создания разовой задачи.

    Та же форма, что у проектной задачи, но без стадии и роли: исполнитель
    и наблюдатели — локальные пользователи, известные трекеру.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "title": "Подготовить отчёт по складу",
                "executor_id": 7,
                "due_date": "2026-10-03",
                "priority": "HIGH",
            }
        }
    )

    stage_id: None = Field(None, exclude=True, description="У разовой задачи стадий нет.")
    wbs_node_id: None = Field(None, exclude=True, description="У разовой задачи ИСР нет.")
    role: None = Field(None, exclude=True, description="У разовой задачи нет роли проекта.")


class TaskStatusUpdateSchema(BaseModel):
    """Смена статуса задачи вне проекта."""

    model_config = ConfigDict(json_schema_extra={"example": {"status": "IN_PROGRESS"}})

    status: TaskStatus = Field(..., description="Новый статус.", examples=["IN_PROGRESS"])


class TaskContextSchema(BaseModel):
    """Проект, которому принадлежит задача."""

    kind: TaskKind = Field(..., description="PROJECT.", examples=["PROJECT"])
    id: int = Field(..., description="Идентификатор проекта.", examples=[3])
    key: str = Field(..., description="Ключ проекта.", examples=["ДОСТ"])
    name: str = Field(..., description="Название.", examples=["Заявки на доступы"])
    color: str | None = Field(None, description="Цвет проекта (#RRGGBB).", examples=["#8a1538"])
    icon: str | None = Field(
        None,
        description="Иконка проекта: `lucide:<имя>` или эмодзи.",
        examples=["lucide:rocket"],
    )
    icon_image_version: str | None = Field(
        None,
        description="Версия своей картинки проекта для адреса иконки; null — картинки нет.",
    )


class MyTaskSchema(TaskSchema):
    """Задача в списке «Мои задачи»: общий вид для задач всех видов."""

    context: TaskContextSchema | None = Field(None, description="Проект задачи; null у разовой.")
    state: TaskStatus = Field(
        ...,
        description=(
            "Состояние для общего списка. У проектной выводится из стадии: первая "
            "стадия — NEW, завершающая — DONE, прочие — IN_PROGRESS."
        ),
        examples=["IN_PROGRESS"],
    )
    state_label: str = Field(
        ..., description="Подпись состояния: статус или имя стадии проекта.", examples=["В работе"]
    )
    my_roles: list[TaskParticipantRole] = Field(
        default_factory=list, description="Мои роли в задаче.", examples=[["EXECUTOR"]]
    )


class ProjectBaselineResultSchema(BaseModel):
    """Итог фиксации плана (baseline) всех задач проекта с датами."""

    model_config = ConfigDict(
        json_schema_extra={"example": {"dry_run": False, "scheduled_count": 12, "changed_count": 4}}
    )

    dry_run: bool = Field(..., description="Только подсчёт: план не записан.", examples=[False])
    scheduled_count: int = Field(
        ..., ge=0, description="Задач проекта хотя бы с одной плановой датой.", examples=[12]
    )
    changed_count: int = Field(
        ...,
        ge=0,
        description="У скольких задач план отличался от текущих дат и был (будет) перезаписан.",
        examples=[4],
    )
    task_ids: list[int] = Field(
        default_factory=list, description="Задачи, у которых план изменился.", examples=[[7, 9]]
    )
