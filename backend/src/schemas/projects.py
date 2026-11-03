import re
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.db.models.projects import ProjectStatus
from src.schemas.project_stages import StageCreateSchema
from src.schemas.users import USERNAME_PATTERN

KEY_PATTERN = r"^[A-Za-z][A-Za-z0-9]{1,9}$"
LUCIDE_ICON_PATTERN = r"^lucide:[a-z0-9]+(?:-[a-z0-9]+)*$"
ICON_DESCRIPTION = (
    "Иконка проекта: `lucide:<имя>` — иконка из набора интерфейса (например "
    "`lucide:rocket`), либо эмодзи до 8 символов (старый формат). Своя картинка "
    "загружается отдельно (POST /projects/{id}/icon-image) и показывается вместо иконки."
)


def validate_project_icon(value: str | None) -> str | None:
    """Проверяет формат иконки: lucide:<имя> либо короткое эмодзи."""
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    if value.startswith("lucide:"):
        if len(value) > 64 or not re.fullmatch(LUCIDE_ICON_PATTERN, value):
            raise ValueError("Иконка из набора задаётся как lucide:<имя-через-дефис>.")
        return value
    if len(value) > 8:
        raise ValueError("Эмодзи-иконка — не длиннее 8 символов.")
    return value


COLOR_PATTERN = r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$"


class ProjectDescriptionSchema(BaseModel):
    """Логические блоки единого описания проекта."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    problem: str = Field("", description="Какую проблему решает проект.")
    goal: str = Field("", description="Что должно измениться благодаря проекту.")
    expected_result: str = Field("", description="Что будет готово и как проверить завершение.")
    additional: str = Field("", description="Дополнительные сведения и договорённости.")


class ProjectDeadlineChangeSchema(BaseModel):
    """Запись истории назначения и изменения срока окончания."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    previous_due_date: date | None
    new_due_date: date | None
    changed_by_user_id: int | None
    changed_by_name: str
    comment: str | None
    created_at: datetime


class ProjectDefaultsSchema(BaseModel):
    """Начальный набор стадий для формы создания проекта."""

    stages: list[StageCreateSchema]


class ProjectSchema(BaseModel):
    """Проект трекера."""

    model_config = ConfigDict(from_attributes=True)

    id: int = Field(..., description="Уникальный идентификатор проекта.", examples=[1])
    owner_id: int | None = Field(None, description="Руководитель проекта — его создатель.")
    description_sections: ProjectDescriptionSchema | None = Field(
        None, description="Блоки паспорта проекта."
    )
    due_date_has_been_set: bool = Field(
        False, description="Срок окончания уже назначался; пересмотр требует причины."
    )
    key: str = Field(..., description="Короткий код проекта.", examples=["PROJ"])
    name: str = Field(..., description="Название проекта.", examples=["TELOS SPACE"])
    description_md: str | None = Field(
        None,
        description="Описание проекта в Markdown.",
        examples=["Персональный ассистент для подбора работы."],
    )
    status: ProjectStatus = Field(..., description="Статус проекта.", examples=["ACTIVE"])
    color: str = Field(..., description="HEX-цвет проекта.", examples=["#58a6ff"])
    icon: str | None = Field(None, description=ICON_DESCRIPTION, examples=["🚀"])
    icon_image_version: str | None = Field(None, description="Версия локальной картинки проекта.")
    start_date: date | None = Field(
        None,
        description="Плановая дата начала.",
        examples=["2026-09-01"],
    )
    due_date: date | None = Field(
        None,
        description="Плановая дата завершения.",
        examples=["2026-12-20"],
    )
    order_index: int = Field(..., description="Порядок проекта в списке.", examples=[0])
    created_at: datetime = Field(
        ...,
        description="Дата создания проекта.",
        examples=["2026-09-01T10:00:00Z"],
    )
    updated_at: datetime = Field(
        ...,
        description="Дата последнего обновления проекта.",
        examples=["2026-09-02T12:00:00Z"],
    )


class ProjectCreateSchema(BaseModel):
    """Тело запроса для создания проекта."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "key": "PROJ",
                "name": "TELOS SPACE",
                "description_md": "Персональный ассистент для подбора работы.",
                "color": "#58a6ff",
                "icon": "🚀",
            }
        }
    )

    description_sections: ProjectDescriptionSchema | None = None
    member_usernames: list[str] = Field(
        default_factory=list,
        max_length=100,
        description="Точные логины участников; руководитель включается автоматически.",
    )
    stages: list[StageCreateSchema] | None = Field(
        None,
        min_length=1,
        max_length=30,
        description="Стадии в порядке колонок. При отсутствии используется стандартный набор.",
    )

    @field_validator("member_usernames")
    @classmethod
    def normalize_members(cls, values: list[str]) -> list[str]:
        """Нормализует и проверяет логины без дублирования участников."""
        result = list(dict.fromkeys(value.strip().lower() for value in values))
        if any(not re.fullmatch(USERNAME_PATTERN, value) for value in result):
            raise ValueError("Укажите корректные логины участников.")
        return result

    @field_validator("stages")
    @classmethod
    def validate_stages(cls, stages: list[StageCreateSchema] | None):
        """Имена стадий одного проекта должны различаться."""
        if stages is not None:
            names = [stage.name.strip().casefold() for stage in stages]
            if any(not name for name in names) or len(names) != len(set(names)):
                raise ValueError("Названия стадий должны быть непустыми и уникальными.")
        return stages

    @model_validator(mode="after")
    def validate_period(self):
        """Проверяет заданный период проекта."""
        if self.start_date and self.due_date and self.due_date < self.start_date:
            raise ValueError("Окончание проекта не может быть раньше начала.")
        return self

    key: str = Field(
        ...,
        pattern=KEY_PATTERN,
        description="Короткий код проекта: латиница и цифры, от 2 до 10 символов.",
        examples=["PROJ"],
    )
    name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Название проекта.",
        examples=["TELOS SPACE"],
    )
    description_md: str | None = Field(
        None,
        description="Описание проекта в Markdown.",
        examples=["Персональный ассистент для подбора работы."],
    )
    status: ProjectStatus = Field(
        ProjectStatus.PLANNING,
        description="Начальный статус проекта.",
        examples=["PLANNING"],
    )
    color: str = Field(
        "#58a6ff",
        pattern=COLOR_PATTERN,
        description="HEX-цвет проекта.",
        examples=["#58a6ff"],
    )
    icon: str | None = Field(
        None,
        max_length=64,
        description="Эмодзи-иконка проекта.",
        examples=["🚀"],
    )
    start_date: date | None = Field(
        None,
        description="Плановая дата начала.",
        examples=["2026-09-01"],
    )
    due_date: date | None = Field(
        None,
        description="Плановая дата завершения.",
        examples=["2026-12-20"],
    )

    @field_validator("key")
    @classmethod
    def normalize_key(cls, value: str) -> str:
        """Приводит код проекта к верхнему регистру."""
        return value.upper()

    @field_validator("icon")
    @classmethod
    def validate_icon(cls, value: str | None) -> str | None:
        return validate_project_icon(value)


class ProjectUpdateSchema(BaseModel):
    """Тело запроса для частичного обновления проекта."""

    model_config = ConfigDict(json_schema_extra={"example": {"status": "ACTIVE"}})

    description_sections: ProjectDescriptionSchema | None = None
    due_date_comment: str | None = Field(
        None, max_length=5000, description="Причина пересмотра уже назначенного срока окончания."
    )
    expected_due_date: date | None = Field(
        None,
        description="Предыдущий срок из открытой формы, для защиты от одновременного изменения.",
    )

    key: str | None = Field(
        None,
        pattern=KEY_PATTERN,
        description="Новый короткий код проекта.",
        examples=["PROJ"],
    )
    name: str | None = Field(
        None,
        min_length=1,
        max_length=255,
        description="Новое название проекта.",
        examples=["TELOS SPACE 2.0"],
    )
    description_md: str | None = Field(
        None,
        description="Новое описание в Markdown.",
        examples=["Обновлённое описание проекта."],
    )
    status: ProjectStatus | None = Field(
        None,
        description="Новый статус проекта.",
        examples=["ACTIVE"],
    )
    color: str | None = Field(
        None,
        pattern=COLOR_PATTERN,
        description="Новый HEX-цвет проекта.",
        examples=["#a371f7"],
    )
    icon: str | None = Field(
        None,
        max_length=64,
        description="Новая эмодзи-иконка проекта.",
        examples=["🛠"],
    )
    start_date: date | None = Field(
        None,
        description="Новая дата начала или null для очистки.",
        examples=["2026-09-01"],
    )
    due_date: date | None = Field(
        None,
        description="Новая дата завершения или null для очистки.",
        examples=["2026-12-20"],
    )
    order_index: int | None = Field(
        None,
        ge=0,
        description="Новый порядок проекта в списке.",
        examples=[2],
    )

    @field_validator("key")
    @classmethod
    def normalize_key(cls, value: str | None) -> str | None:
        """Приводит код проекта к верхнему регистру."""
        return value.upper() if value is not None else None

    @field_validator("icon")
    @classmethod
    def validate_icon(cls, value: str | None) -> str | None:
        return validate_project_icon(value)


class StageBreakdownSchema(BaseModel):
    """Количество задач в одной стадии проекта."""

    stage_id: int = Field(..., description="Идентификатор стадии.", examples=[3])
    stage_name: str = Field(..., description="Название стадии.", examples=["В работе"])
    color: str = Field(..., description="HEX-цвет стадии.", examples=["#58a6ff"])
    is_done_stage: bool = Field(..., description="Признак завершающей стадии.", examples=[False])
    tasks_count: int = Field(..., ge=0, description="Количество задач в стадии.", examples=[4])


class ProjectStatsSchema(BaseModel):
    """Показатели проекта для карточки и обзорного экрана."""

    project_id: int = Field(..., description="Идентификатор проекта.", examples=[1])
    total_tasks: int = Field(..., ge=0, description="Всего задач в проекте.", examples=[18])
    done_tasks: int = Field(..., ge=0, description="Задач в завершающих стадиях.", examples=[9])
    in_progress_tasks: int = Field(
        ...,
        ge=0,
        description="Задач в работе: не в первой и не в завершающей стадии.",
        examples=[5],
    )
    overdue_tasks: int = Field(
        ...,
        ge=0,
        description="Незавершённых задач с истёкшим сроком.",
        examples=[2],
    )
    due_soon_tasks: int = Field(
        ...,
        ge=0,
        description="Незавершённых задач со сроком в ближайшие 7 дней.",
        examples=[3],
    )
    unassigned_tasks: int = Field(
        ...,
        ge=0,
        description="Задач, не распределённых по разделам ИСР.",
        examples=[6],
    )
    completion_rate: float = Field(
        ...,
        ge=0,
        le=1,
        description="Доля выполненных задач от общего числа.",
        examples=[0.5],
    )
    next_due_date: date | None = Field(
        None,
        description="Ближайший срок среди незавершённых задач.",
        examples=["2026-09-08"],
    )
    stage_breakdown: list[StageBreakdownSchema] = Field(
        default_factory=list,
        description="Распределение задач по стадиям проекта.",
    )
