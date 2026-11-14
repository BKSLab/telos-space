"""Аргументы инструментов: серверная область проекта не задаётся моделью."""

import math
from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.schemas.board_stickers import (
    BoardStickerCreateSchema,
    BoardStickerPositionUpdateSchema,
    BoardStickerUpdateSchema,
)
from src.schemas.calendar_scenarios import ScenarioApplyChangeSchema, ScenarioChangeInputSchema
from src.schemas.document_catalog import DocumentUpdateRequestSchema
from src.schemas.enums import TaskStatus
from src.schemas.milestones import MilestoneCreateSchema, MilestoneUpdateSchema
from src.schemas.project_risks import (
    ProjectRiskCreateSchema,
    ProjectRiskFilters,
    ProjectRiskUpdateSchema,
)
from src.schemas.project_stages import StageCreateSchema, StageUpdateSchema
from src.schemas.projects import ProjectUpdateSchema
from src.schemas.task_checklists import TaskChecklistSchema
from src.schemas.task_dependencies import TaskDependencyCreateSchema
from src.schemas.tasks import PersonalTaskCreateSchema, TaskCreateSchema, TaskUpdateSchema


class ToolInput(BaseModel):
    """Строгие аргументы с конечными числами и ограниченным текстом."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    @model_validator(mode="before")
    @classmethod
    def bounded_values(cls, value: Any) -> Any:
        """Отсекает непригодные для БД и чрезмерно большие аргументы модели."""

        def check(item: Any, depth: int = 0) -> None:
            if depth > 12:
                raise ValueError("Слишком большая вложенность аргументов.")
            if isinstance(item, str) and ("\x00" in item or len(item) > 50_000):
                raise ValueError(
                    "Текст ограничен 50 000 символов и не должен содержать нулевые байты."
                )
            if isinstance(item, float) and not math.isfinite(item):
                raise ValueError("Числа должны быть конечными.")
            if isinstance(item, dict):
                for child in item.values():
                    check(child, depth + 1)
            if isinstance(item, list):
                if len(item) > 250:
                    raise ValueError("Список ограничен 250 элементами.")
                for child in item:
                    check(child, depth + 1)

        check(value)
        return value


class EmptyInput(ToolInput):
    """Инструмент без аргументов."""


class ToolSchemasInput(ToolInput):
    names: list[str] = Field(
        min_length=1, max_length=8, description="Имена инструментов, чьи точные схемы нужны."
    )


class PageInput(ToolInput):
    offset: int = Field(0, ge=0, description="Смещение страницы.")
    limit: int = Field(30, ge=1, le=100, description="Максимум элементов.")


class TaskInput(ToolInput):
    task_id: int = Field(gt=0, description="ID задачи из результатов поиска текущего проекта.")


class TaskLookupInput(ToolInput):
    """Задача по ID или по ключу вида KEY-12 — ключ избавляет от шага поиска."""

    task_id: int | None = Field(None, gt=0, description="ID задачи текущего проекта.")
    task_key: str | None = Field(
        None,
        pattern=r"^[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё0-9_]{0,31}-\d{1,9}$",
        description="Ключ задачи текущего проекта, например PROJ-12.",
    )

    @model_validator(mode="after")
    def one_reference(self) -> "TaskLookupInput":
        """Нужен ровно один способ указать задачу."""
        if (self.task_id is None) == (self.task_key is None):
            raise ValueError("Укажи task_id или task_key — ровно одно из двух.")
        return self


class TaskListInput(PageInput):
    search: str | None = Field(
        None, max_length=200, description="Название, слова описания или ключ задачи."
    )
    stage_id: int | None = Field(None, gt=0, description="Стадия текущего проекта.")


class OverdueTasksInput(ToolInput):
    limit: int = Field(30, ge=1, le=30, description="Максимум задач, самые ранние сроки первыми.")


class RecentActivityInput(ToolInput):
    limit: int = Field(30, ge=1, le=50, description="Максимум событий, свежие первыми.")


class TaskPageInput(TaskInput, PageInput):
    """Страница объектов задачи."""






class RiskListInput(ProjectRiskFilters, ToolInput):
    page: int = Field(1, ge=1, description="Номер страницы реестра.")
    page_size: int = Field(30, ge=1, le=100, description="Размер страницы реестра.")


class TaskCreateInput(TaskCreateSchema, ToolInput):
    """Новая задача с ролевыми назначениями и необязательным чек-листом."""

    stage_name: str | None = Field(
        None, description="Название стадии вместо stage_id; разрешается только при первом исполнении."
    )
    executor_username: str | None = Field(
        None, description="Логин исполнителя вместо executor_id; пустая строка снимает назначение."
    )
    observer_usernames: list[str] | None = Field(
        None, max_length=50, description="Логины наблюдателей вместо observer_ids."
    )

    @model_validator(mode="after")
    def single_assignment_reference(self) -> "TaskCreateInput":
        """Назначение указывается именем либо ID, без неоднозначного смешения."""
        for name, identity in (
            ("stage_name", "stage_id"),
            ("executor_username", "executor_id"),
            ("observer_usernames", "observer_ids"),
        ):
            if getattr(self, name) is not None and identity in self.model_fields_set:
                raise ValueError(f"Укажите только одно из полей {name} и {identity}.")
        return self


class TaskChangesInput(TaskUpdateSchema, ToolInput):
    """Изменения задачи с необязательной expected_revision из свежего telos_get_task."""


class TaskUpdateInput(TaskInput):
    changes: TaskChangesInput = Field(
        description="Только изменяемые поля задачи; null очищает допустимое поле."
    )


class TaskTransferInput(TaskInput):
    stage_id: int = Field(gt=0, description="Целевая стадия текущего проекта.")
    position: float | None = Field(
        None, description="Позиция внутри стадии; без значения — в конец."
    )


class TaskDeleteInput(TaskInput):
    expected_updated_at: datetime = Field(
        description="updated_at из свежего telos_get_task; удаление требует решения участника."
    )


class ChecklistInput(TaskInput):
    checklist_revision: int = Field(ge=0, description="Текущая версия чек-листа из telos_get_task.")
    checklist: TaskChecklistSchema | None = Field(
        description="Полный чек-лист с ID пунктов; null удаляет его."
    )


class CommentInput(ToolInput):
    comment_id: int = Field(gt=0, description="ID комментария текущего проекта.")


class CommentCreateInput(TaskInput):
    body_md: str = Field(
        min_length=1, max_length=50_000, description="Комментарий; автор определяется сервером."
    )


class CommentUpdateInput(CommentInput):
    body_md: str = Field(min_length=1, max_length=50_000, description="Новый текст комментария.")
    expected_body_md: str = Field(
        description="Текст прочитанной версии для защиты от потери чужой правки."
    )


class MemberAddInput(ToolInput):
    username: str = Field(
        min_length=1, max_length=100, description="Точный логин существующего пользователя."
    )


class MemberInput(ToolInput):
    user_id: int = Field(gt=0, description="ID участника текущего проекта.")


class ProjectChangesInput(ProjectUpdateSchema, ToolInput):
    """Паспорт и настройки текущего проекта; перенос срока требует причины и expected_due_date."""


class StageCreateInput(StageCreateSchema, ToolInput):
    """Создание стадии канбана."""


class StageInput(ToolInput):
    stage_id: int = Field(gt=0, description="ID стадии текущего проекта.")


class StageChangesInput(StageUpdateSchema, ToolInput):
    """Имя, цвет, признак завершения или порядок стадии."""


class StageUpdateInput(StageInput):
    changes: StageChangesInput = Field(description="Изменяемые параметры стадии.")


BOARD_ID_DESCRIPTION = "ID доски проекта из telos_list_boards; не указан — первая доска проекта."


class StickerListInput(PageInput):
    board_id: int | None = Field(None, gt=0, description=BOARD_ID_DESCRIPTION)


class StickerCreateInput(BoardStickerCreateSchema, ToolInput):
    """Новый стикер с привязками к задачам и координатами."""

    board_id: int | None = Field(None, gt=0, description=BOARD_ID_DESCRIPTION)


class StickerInput(ToolInput):
    sticker_id: int = Field(gt=0, description="ID стикера текущего проекта.")
    board_id: int | None = Field(
        None,
        gt=0,
        description="ID доски стикера; не указан — стикер ищется на любой доске проекта.",
    )


class StickerUpdateInput(StickerInput):
    changes: BoardStickerUpdateSchema = Field(
        description="Текст, цвет и задачи вместе с текущей revision."
    )


class StickerMoveInput(StickerInput):
    position: BoardStickerPositionUpdateSchema = Field(
        description="Координаты, необязательный размер и слой z_index."
    )


class StickerDeleteInput(StickerInput):
    revision: int = Field(ge=1, description="Текущая revision стикера.")


class DocumentListInput(PageInput):
    """Документы проекта и его задач с общими фильтрами каталога."""

    q: str | None = Field(None, max_length=200, description="Название, примечание или содержимое.")
    scope: Literal["all", "project", "tasks"] = Field("all", description="Все, без задач или связанные с задачами.")
    task_id: int | None = Field(None, gt=0, description="Документы конкретной задачи текущего проекта.")
    source_kind: Literal["text", "upload"] | None = Field(None, description="Созданный текст или загруженный оригинал.")
    format: str | None = Field(None, max_length=20, description="Формат без точки, например md или pdf.")
    processing: Literal["all", "pending", "ready", "failed"] = Field("all", description="Состояние обработки документа.")


class DocumentCreateInput(ToolInput):
    """Новый Markdown в области проекта или принадлежащей ему задачи."""

    title: str = Field(min_length=1, max_length=255, description="Название документа.")
    content_md: str = Field(default="", max_length=50_000, description="Текст Markdown.")
    caption: str | None = Field(None, max_length=2000, description="Примечание документа.")
    owner_task_id: int | None = Field(None, gt=0, description="Задача-владелец текущего проекта; без значения владелец — проект.")


class DocumentInput(ToolInput):
    document_ref: str = Field(min_length=7, max_length=110, description="Стабильная ссылка ref из каталога документов текущего проекта.")


class DocumentReadInput(DocumentInput):
    offset: int = Field(default=0, ge=0, description="Начало страницы в символах content_md.")
    max_chars: int = Field(
        default=4000, ge=500, le=8000, description="Размер страницы оригинального текста."
    )


class DocumentChangesInput(DocumentUpdateRequestSchema, ToolInput):
    """Название и содержимое документа."""


class DocumentUpdateInput(DocumentInput):
    changes: DocumentChangesInput = Field(description="Изменяемые поля документа.")


class DocumentLinkInput(DocumentInput):
    task_id: int = Field(gt=0, description="Задача того же проекта.")


class DocumentCopyInput(DocumentInput):
    target_task_id: int = Field(
        gt=0, description="Задача текущего проекта, для которой создать самостоятельную копию документа."
    )


class TextAttachmentInput(TaskInput):
    file_name: str = Field(
        min_length=1, max_length=255, description="Имя файла .txt, .md, .csv или .log."
    )
    content: str = Field(max_length=50_000, description="Точное текстовое содержимое нового файла.")


class UploadedAttachmentInput(TaskInput):
    file_id: UUID = Field(description="ID файла из uploaded_files, ранее отправленного участником.")


class MilestoneCreateInput(MilestoneCreateSchema, ToolInput):
    """Новая веха текущего проекта."""


class MilestoneInput(ToolInput):
    milestone_id: int = Field(gt=0, description="ID вехи текущего проекта.")


class MilestoneChangesInput(MilestoneUpdateSchema, ToolInput):
    """Поля изменения вехи."""


class MilestoneUpdateInput(MilestoneInput):
    changes: MilestoneChangesInput = Field(description="Изменяемые поля вехи.")


class DependencyCreateInput(TaskDependencyCreateSchema, ToolInput):
    """Зависимость между двумя задачами текущего проекта."""


class DependencyInput(ToolInput):
    dependency_id: int = Field(gt=0, description="ID зависимости текущего проекта.")


class CalendarInput(ToolInput):
    date_from: date = Field(description="Начало диапазона.")
    date_to: date = Field(description="Конец диапазона.")


class ScenarioChangeInput(ScenarioChangeInputSchema, ToolInput):
    """Строгая дата изменения задачи."""


class ScenarioApplyChangeInput(ScenarioApplyChangeSchema, ToolInput):
    """Строгая версия применения даты."""


class ScenarioPreviewInput(ToolInput):
    """Расчёт последствий изменений сроков без записи."""

    changes: list[ScenarioChangeInput] = Field(
        min_length=1, max_length=100, description="Исходные изменения для расчёта последствий."
    )


class ScenarioApplyInput(ToolInput):
    """Применение конкретного preview с проверкой версий задач."""

    changes: list[ScenarioApplyChangeInput] = Field(
        min_length=1, max_length=250, description="Изменения и версии из результата preview."
    )


class RiskCreateInput(ProjectRiskCreateSchema, ToolInput):
    """Новый зарегистрированный риск."""


class RiskInput(ToolInput):
    risk_id: int = Field(gt=0, description="ID риска текущего проекта.")


class RiskChangesInput(ProjectRiskUpdateSchema, ToolInput):
    """Поля риска, включая статус и планы реагирования."""


class RiskUpdateInput(RiskInput):
    changes: RiskChangesInput = Field(description="Изменяемые поля риска.")


# Личные действия Ариса вне проекта: мои задачи и разовые задачи.


class MyTasksInput(PageInput):
    role: Literal["all", "doing", "assigned", "watching"] = Field(
        "all",
        description="all — все мои; doing — я исполнитель; assigned — я постановщик; watching — наблюдаю.",
    )
    include_closed: bool = Field(False, description="Показывать ли закрытые задачи.")
    search: str | None = Field(
        None, max_length=200, description="Слова названия или ключ задачи для фильтра списка."
    )


class TaskSearchInput(PageInput):
    search: str = Field(
        min_length=1, max_length=200, description="Слова названия, описания или ключ задачи."
    )


class TaskRefInput(ToolInput):
    task_id: int | None = Field(None, gt=0, description="ID задачи, если известен.")
    task_key: str | None = Field(
        None,
        min_length=3,
        max_length=64,
        description="Ключ проектной задачи PROJ-142 или разовой задачи T-15.",
    )

    @model_validator(mode="after")
    def one_ref(self) -> "TaskRefInput":
        if (self.task_id is None) == (self.task_key is None):
            raise ValueError("Укажите ровно одно: task_id или task_key.")
        return self


class PersonalTaskCreateInput(PersonalTaskCreateSchema, ToolInput):
    """Разовая задача вне проекта; постановщик — пользователь."""








class TaskStatusInput(ToolInput):
    task_id: int = Field(gt=0, description="ID разовой задачи.")
    status: TaskStatus = Field(description="Новый статус: NEW, IN_PROGRESS, DONE и др.")


class ProjectActionInput(ToolInput):
    project_key: str = Field(
        min_length=1, max_length=32, description="Ключ проекта из telos_list_my_projects, например PROJ."
    )
    name: str = Field(min_length=1, max_length=64, description="Имя проектного действия.")
    arguments: dict[str, Any] = Field(
        default_factory=dict, description="Аргументы по схеме проектного действия."
    )


class TaskReportReadInput(TaskLookupInput):
    """Постраничное чтение актуального текста, включая большие отчёты."""

    offset: int = Field(0, ge=0)
    max_chars: int = Field(8000, ge=500, le=16000)


class TaskReportPublishInput(TaskInput):
    """Полная новая версия с защитой от потери изменений."""

    body_md: str = Field(max_length=50000)
    expected_revision: int = Field(ge=0, strict=True)


class WbsCreateInput(ToolInput):
    title: str = Field(
        min_length=1, max_length=255, description="Название результата или раздела ИСР."
    )
    parent_id: int | None = Field(None, gt=0, description="Родительский раздел; null — корень.")


class WbsInput(ToolInput):
    node_id: int = Field(gt=0, description="ID раздела ИСР текущего проекта.")


class WbsUpdateInput(WbsInput):
    title: str = Field(min_length=1, max_length=255, description="Новое название раздела.")


class WbsMoveInput(WbsInput):
    parent_id: int | None = Field(description="Новый родитель; null — корень.")
    before_id: int | None = Field(
        None, gt=0, description="Поставить перед соседним разделом; null — в конец."
    )


class TaskPlacementInput(TaskInput):
    wbs_node_id: int | None = Field(description="Раздел ИСР; null возвращает задачу в пул.")
    before_task_id: int | None = Field(
        None, gt=0, description="Поставить перед задачей того же раздела."
    )
    canvas_x: float | None = Field(None, description="Координата в свободном пуле.")
    canvas_y: float | None = Field(None, description="Координата в свободном пуле.")
