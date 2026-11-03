"""Аргументы инструментов: серверная область проекта не задаётся моделью."""

import math
from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.schemas.calendar_scenarios import ScenarioApplyChangeSchema, ScenarioChangeInputSchema
from src.schemas.documents import DocumentCreateSchema, DocumentUpdateSchema
from src.schemas.enums import TaskStatus
from src.schemas.milestones import MilestoneCreateSchema, MilestoneUpdateSchema
from src.schemas.project_risks import (
    ProjectRiskCreateSchema,
    ProjectRiskFilters,
    ProjectRiskUpdateSchema,
)
from src.schemas.project_stages import StageCreateSchema, StageUpdateSchema
from src.schemas.project_stickers import (
    ProjectStickerCreateSchema,
    ProjectStickerPositionUpdateSchema,
    ProjectStickerUpdateSchema,
)
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


class TaskChangesInput(TaskUpdateSchema, ToolInput):
    """Изменения задачи; отсутствие поля сохраняет прежнее значение."""


class TaskUpdateInput(TaskInput):
    changes: TaskChangesInput = Field(
        description="Только изменяемые поля задачи; null очищает допустимое поле."
    )


class TaskMoveInput(TaskInput):
    stage_id: int = Field(gt=0, description="Целевая стадия текущего проекта.")
    position: float | None = Field(
        None, description="Позиция внутри стадии; без значения — в конец."
    )


class TaskDeleteInput(TaskInput):
    expected_updated_at: datetime = Field(
        description="updated_at из свежего get_task; удаление требует решения участника."
    )


class ChecklistInput(TaskInput):
    checklist_revision: int = Field(ge=0, description="Текущая версия чек-листа из get_task.")
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


BOARD_ID_DESCRIPTION = "ID доски проекта из list_boards; не указан — первая доска проекта."


class StickerListInput(PageInput):
    board_id: int | None = Field(None, gt=0, description=BOARD_ID_DESCRIPTION)


class StickerCreateInput(ProjectStickerCreateSchema, ToolInput):
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
    changes: ProjectStickerUpdateSchema = Field(
        description="Текст, цвет и задачи вместе с текущей revision."
    )


class StickerMoveInput(StickerInput):
    position: ProjectStickerPositionUpdateSchema = Field(
        description="Координаты, необязательный размер и слой z_index."
    )


class StickerDeleteInput(StickerInput):
    revision: int = Field(ge=1, description="Текущая revision стикера.")


class DocumentCreateInput(DocumentCreateSchema, ToolInput):
    """Новый документ проекта."""


class DocumentInput(ToolInput):
    document_id: int = Field(gt=0, description="ID документа текущего проекта.")


class DocumentReadInput(DocumentInput):
    offset: int = Field(default=0, ge=0, description="Начало страницы в символах content_md.")
    max_chars: int = Field(
        default=4000, ge=500, le=8000, description="Размер страницы оригинального текста."
    )


class DocumentChangesInput(DocumentUpdateSchema, ToolInput):
    """Название и содержимое документа."""


class DocumentUpdateInput(DocumentInput):
    changes: DocumentChangesInput = Field(description="Изменяемые поля документа.")


class DocumentLinkInput(DocumentInput):
    task_id: int = Field(gt=0, description="Задача того же проекта.")


class LinkInput(ToolInput):
    link_id: int = Field(gt=0, description="ID связи документа с задачей текущего проекта.")


class AttachmentInput(TaskInput):
    attachment_id: int = Field(gt=0, description="ID файла указанной задачи.")


class AttachmentCopyInput(AttachmentInput):
    target_task_id: int = Field(
        gt=0, description="Задача текущего проекта, к которой прикрепить копию файла."
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
        description="Ключ задачи: PROJ-142, ключ потока или T-15.",
    )

    @model_validator(mode="after")
    def one_ref(self) -> "TaskRefInput":
        if (self.task_id is None) == (self.task_key is None):
            raise ValueError("Укажите ровно одно: task_id или task_key.")
        return self


class PersonalTaskCreateInput(PersonalTaskCreateSchema, ToolInput):
    """Разовая задача вне проектов и потоков; постановщик — пользователь."""








class TaskStatusInput(ToolInput):
    task_id: int = Field(gt=0, description="ID задачи потока или разовой задачи.")
    status: TaskStatus = Field(description="Новый статус: NEW, IN_PROGRESS, DONE и др.")


class ProjectActionInput(ToolInput):
    project_key: str = Field(
        min_length=1, max_length=32, description="Ключ проекта из list_my_projects, например PROJ."
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
