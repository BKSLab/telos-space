from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_serializer

from src.schemas.agent_files import AgentFileSchema
from src.schemas.agent_tools import AgentToolRunSchema
from src.schemas.knowledge import KnowledgeSourceSchema

MAX_BOARD_SELECTION = 200


class TimBoardSelectionRefSchema(BaseModel):
    """Ссылка выделения: пространство имён типа и положительный ID."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["shape", "sticker"]
    id: int = Field(gt=0, strict=True)


class TimPageContextSchema(BaseModel):
    """Страница, с которой пользователь пишет Арису в виджете.

    Контекст — подсказка для «этот проект», «эта задача»: права на объект
    сервер проверяет сам, недоступный объект Арису просто не показывается.
    """

    model_config = ConfigDict(extra="forbid")

    route: str = Field(
        max_length=500, description="Маршрут страницы фронта, например /projects/PROJ/board."
    )
    project_key: str | None = Field(
        None, max_length=32, description="Ключ проекта открытой страницы."
    )
    task_id: int | None = Field(None, gt=0, description="ID открытой задачи.")
    board_id: int | None = Field(
        None,
        gt=0,
        description="ID открытой личной или проектной доски; права проверяются сервером.",
    )

    board_selection: list[TimBoardSelectionRefSchema] | None = Field(
        None,
        max_length=MAX_BOARD_SELECTION,
        description="Выделенные фигуры и стикеры; сервер проверяет тип, доску и доступ.",
    )

    @model_serializer(mode="wrap")
    def optional_board_context(self, handler):
        data = handler(self)
        if self.board_id is None:
            data.pop("board_id", None)
        if self.board_id is None or self.board_selection is None:
            data.pop("board_selection", None)
        return data

    @field_validator("route")
    @classmethod
    def no_null_bytes(cls, value: str) -> str:
        """Маршрут уходит в контекст модели: без нулевых байтов."""
        if "\x00" in value:
            raise ValueError("Маршрут не должен содержать нулевые байты.")
        return value


class AgentConversationSchema(BaseModel):
    """Диалог пользователя с Арисом в виджете, без внутренней памяти модели."""

    model_config = ConfigDict(from_attributes=True)

    id: int = Field(description="ID диалога.")
    project_id: int | None = Field(None, description="Фиксированный проект диалога; null — общий помощник.")
    title: str = Field(description="Название по первому вопросу участника.")
    created_at: datetime = Field(description="Время создания диалога.")
    updated_at: datetime = Field(description="Время последнего изменения диалога.")


class AgentConversationListSchema(BaseModel):
    """Страница диалогов пользователя с Арисом; первый — текущий."""

    items: list[AgentConversationSchema] = Field(
        description="Диалоги от недавно изменённых к старым; первый — текущий диалог виджета."
    )
    next_offset: int | None = Field(description="Смещение следующей страницы; null в конце списка.")


class AgentMessageCreateSchema(BaseModel):
    """Новый вопрос: историю и автора определяет сервер."""

    model_config = ConfigDict(extra="forbid")

    request_id: UUID = Field(
        description="Стабильный UUID отправки; повтор не создаёт новый вопрос."
    )
    content: str = Field(
        min_length=2, max_length=2000, description="Вопрос или продолжение обсуждения."
    )
    file_ids: list[UUID] = Field(
        default_factory=list,
        max_length=5,
        description="До пяти собственных загрузок этого диалога.",
    )
    page_context: TimPageContextSchema | None = Field(
        None, description="Страница, с которой отправлено сообщение; Арис учитывает её."
    )

    @field_validator("content")
    @classmethod
    def normalize_content(cls, value: str) -> str:
        """Убирает внешние пробелы и запрещает пустой вопрос или нулевой байт."""
        normalized = value.strip()
        if len(normalized) < 2 or "\x00" in normalized:
            raise ValueError("Введите вопрос длиной от двух символов без нулевых байтов.")
        return normalized


class AgentMessageSchema(BaseModel):
    """Сохранённая реплика и наблюдаемое состояние ответа."""

    model_config = ConfigDict(from_attributes=True)

    id: int = Field(description="ID сохранённой реплики.")
    conversation_id: int = Field(description="Диалог реплики.")
    request_id: UUID = Field(description="Общий ключ отправки для вопроса и ответа.")
    role: Literal["user", "assistant"] = Field(description="Автор: участник или агент.")
    content: str = Field(description="Текст вопроса или готового ответа.")
    sources: list[KnowledgeSourceSchema] = Field(description="Проверенные источники ответа.")
    actions: list[AgentToolRunSchema] = Field(
        default_factory=list,
        description="Сохранённые действия и запросы подтверждения этого ответа.",
    )
    files: list[AgentFileSchema] = Field(
        default_factory=list, description="Файлы, выбранные участником для этой реплики."
    )
    page_context: TimPageContextSchema | None = Field(
        None, description="Контекст страницы у вопроса пользователя; у ответа null."
    )
    status: Literal["queued", "processing", "completed", "failed"] = Field(
        description="Состояние подготовки ответа; вопросы всегда completed."
    )
    error: str | None = Field(description="Безопасное описание неудачной попытки.")
    progress: str | None = Field(
        None,
        description=(
            "Что Арис делает сейчас, пока готовит ответ («Читаю задачу EVL-1…»); "
            "null — статуса нет или ответ завершён."
        ),
    )
    created_at: datetime = Field(description="Время отправки реплики.")


class AgentMessagePageSchema(BaseModel):
    """Реплики в хронологическом порядке с продолжением к ранним сообщениям."""

    items: list[AgentMessageSchema] = Field(description="Реплики от ранних к поздним.")
    next_before_id: int | None = Field(description="Курсор ранних реплик; null в начале истории.")


class AgentMessageAcceptedSchema(BaseModel):
    """Сохранённый вопрос и поставленный в очередь ответ."""

    user_message: AgentMessageSchema = Field(description="Сохранённый вопрос участника.")
    assistant_message: AgentMessageSchema = Field(description="Состояние связанного ответа.")
