"""Проектные и разовые задачи."""

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.schemas.enums import TaskKind, TaskStatus
from src.schemas.tasks import TaskSchema
from src.schemas.users import UserSummarySchema


class TaskTransferTargetSchema(BaseModel):
    """Цель переноса: проект или личный список."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"project_id": 3, "stage_id": 11},
                {"personal": True},
            ]
        }
    )

    project_id: int | None = Field(None, gt=0, description="Целевой проект.", examples=[3])
    stage_id: int | None = Field(
        None,
        gt=0,
        description=(
            "Стадия в целевом проекте; без неё — стадия по текущему состоянию задачи "
            "(новая — первая, готово — завершающая, в работе — следующая за первой)."
        ),
        examples=[11],
    )
    personal: bool = Field(False, description="Сделать задачу разовой.", examples=[True])
    remove_outsiders: bool = Field(
        False,
        description=(
            "Перенос в проект: убрать из задачи людей не из команды проекта. Без флага "
            "такой перенос отклоняется с 409 и списком людей."
        ),
        examples=[False],
    )

    @model_validator(mode="after")
    def validate_single_target(self):
        targets = [self.project_id is not None, self.personal]
        if sum(targets) != 1:
            raise ValueError("Укажите ровно одну цель: project_id или personal=true.")
        if self.stage_id is not None and self.project_id is None:
            raise ValueError("Стадию можно указать только вместе с project_id.")
        return self

    @property
    def kind(self) -> TaskKind:
        """Вид задачи после переноса."""
        if self.project_id is not None:
            return TaskKind.PROJECT
        return TaskKind.PERSONAL


class TaskTransferLossesSchema(BaseModel):
    """Связи со старым проектом, которые перенос снимет."""

    dependencies: int = Field(0, ge=0, description="Зависимостей с задачами проекта.")
    document_links: int = Field(0, ge=0, description="Связей с документами проекта.")
    risks: int = Field(0, ge=0, description="Рисков проекта, привязанных к задаче.")
    wbs_placements: int = Field(0, ge=0, description="Размещений задачи в ИСР или на холсте.")
    board_stickers: int = Field(0, ge=0, description="Стикеров доски проекта с задачей.")


class TaskTransferPreviewSchema(BaseModel):
    """Что произойдёт при переносе: для предупреждения перед подтверждением."""

    from_key: str = Field(..., description="Текущий номер задачи.", examples=["PROJ-15"])
    target_kind: TaskKind = Field(..., description="Вид задачи после переноса.")
    target_name: str = Field(..., description="Название цели.", examples=["Заявки на доступы"])
    target_key: str = Field(..., description="Префикс нового номера.", examples=["ДОСТ"])
    stage_id: int | None = Field(None, description="Стадия в целевом проекте.")
    stage_name: str | None = Field(None, description="Название стадии в целевом проекте.")
    status: TaskStatus | None = Field(None, description="Статус задачи вне проекта.")
    executor: UserSummarySchema | None = Field(None, description="Исполнитель после переноса.")
    losses: TaskTransferLossesSchema = Field(..., description="Что будет снято.")
    outsiders: list[UserSummarySchema] = Field(
        default_factory=list,
        description="Люди задачи не из команды целевого проекта.",
    )
    notes: list[str] = Field(
        default_factory=list,
        description="Пояснения человеческим языком: что изменится.",
    )


class TaskTransferResultSchema(BaseModel):
    """Итог переноса."""

    task: TaskSchema = Field(..., description="Задача на новом месте.")
    from_key: str = Field(..., description="Прежний номер.", examples=["PROJ-15"])
    to_key: str = Field(..., description="Новый номер.", examples=["ДОСТ-4"])
    losses: TaskTransferLossesSchema = Field(..., description="Что снято.")
    notes: list[str] = Field(default_factory=list, description="Что изменилось.")
