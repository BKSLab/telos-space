"""Общий контракт HTTP и WebSocket; автор всегда определяется сервером."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.schemas.agent_tools import AgentToolRunSchema
from src.schemas.knowledge import KnowledgeSourceSchema
from src.utils.document_refs import decode_document_ref

EntityType = Literal["TASK", "DOCUMENT", "RISK", "MILESTONE", "WBS_NODE"]
Reaction = Literal["👍", "❤️", "👀", "✅", "🎉"]
PositiveId = Annotated[int, Field(gt=0)]


class ChatInput(BaseModel):
    """Запрещает неизвестные поля и подмену автора/проекта."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ChatEntityRef(ChatInput):
    """Структурированная ссылка текущего проекта."""

    entity_type: EntityType
    entity_id: PositiveId | None = None
    document_ref: str | None = None

    @model_validator(mode="after")
    def validate_identity(self):
        if self.entity_type == "DOCUMENT":
            if self.entity_id is not None or self.document_ref is None:
                raise ValueError("Для документа нужна только document_ref.")
            decode_document_ref(self.document_ref)
        elif self.entity_id is None or self.document_ref is not None:
            raise ValueError("Для объекта нужен entity_id без document_ref.")
        return self

    @property
    def identity(self) -> tuple[str, str | int]:
        return self.entity_type, self.document_ref if self.entity_type == "DOCUMENT" else self.entity_id


class ChatMessageBody(ChatInput):
    """Редактируемое содержимое без HTML и двоичных данных."""

    content: str = Field(default="", max_length=8000)
    mentions: list[PositiveId] = Field(default_factory=list, max_length=20)
    entities: list[ChatEntityRef] = Field(default_factory=list, max_length=10)
    attachment_ids: list[UUID] = Field(default_factory=list, max_length=5)

    @model_validator(mode="after")
    def validate_content(self):
        """Отклоняет пустые сообщения, NUL и повторяющиеся связи."""
        if not (self.content or self.entities or self.attachment_ids) or "\x00" in self.content:
            raise ValueError("Добавьте текст, ссылку или файл.")
        if len(set(self.mentions)) != len(self.mentions) or len(set(self.attachment_ids)) != len(
            self.attachment_ids
        ):
            raise ValueError("Повторяющиеся упоминания или файлы.")
        refs = [ref.identity for ref in self.entities]
        if len(set(refs)) != len(refs):
            raise ValueError("Повторяющиеся ссылки.")
        return self


class ChatMessageCreate(ChatMessageBody):
    """Повторная отправка использует тот же UUID."""

    client_message_id: UUID
    reply_to_message_id: PositiveId | None = None


class ChatMessageEdit(ChatMessageBody):
    """Правка применяется только к увиденной участником версии."""

    expected_revision: int = Field(ge=1)


class ChatMessageDelete(ChatInput):
    """Защита удаления от гонки с редактированием."""

    expected_revision: int = Field(ge=1)


class ChatReactionChange(ChatInput):
    """Явные set/unset остаются идемпотентными при повторе."""

    reaction: Reaction
    active: bool


class ChatReadUpdate(ChatInput):
    """Последнее фактически показанное сообщение."""

    message_id: PositiveId


class ChatUser(BaseModel):
    """Публичное представление участника без контактных данных."""

    id: int
    username: str
    display_name: str


class ChatAttachmentView(BaseModel):
    """Метаданные; storage key никогда не покидает backend."""

    model_config = ConfigDict(from_attributes=True)
    id: UUID
    original_name: str
    content_type: str
    size_bytes: int
    message_id: int | None
    created_at: datetime


class ChatEntityView(ChatEntityRef):
    """Актуальная карточка сущности либо tombstone."""

    title: str
    available: bool = True
    status: str | None = None
    subtitle: str | None = None
    href: str | None = None


class ChatReplyView(BaseModel):
    """Ответ сохраняет ссылку даже после удаления исходного сообщения."""

    id: int
    seq: int
    author_type: Literal["USER", "AGENT"] = "USER"
    author: ChatUser | None
    content: str
    deleted: bool


class ChatAgentView(BaseModel):
    """Состояние ответа Ариса: печатает, готов или ошибка, и его карточки."""

    name: str = Field(description="Имя агента для людей — «Арис».")
    status: Literal["queued", "processing", "completed", "failed"] = Field(
        description="queued/processing — Арис печатает; completed — ответ; failed — ошибка."
    )
    error: str | None = Field(description="Причина ошибки; текст ошибки уже в content.")
    progress: str | None = Field(
        None,
        description=(
            "Что Арис делает сейчас, пока готовит ответ («Ищу задачи…»); "
            "null — статуса нет или ответ завершён."
        ),
    )
    requester: ChatUser | None = Field(description="Кто позвал Ариса; его права действуют.")
    request_message_id: int = Field(description="Сообщение участника, на которое отвечает Арис.")
    sources: list[KnowledgeSourceSchema] = Field(
        description="Карточки сущностей проекта, подтверждающие ответ."
    )
    actions: list[AgentToolRunSchema] = Field(
        description="Действия ответа; pending ждёт решения в карточке."
    )


class ChatReactionView(BaseModel):
    """Агрегированная реакция одинакова для всех участников."""

    reaction: str
    user_ids: list[int]


class ChatMessageView(BaseModel):
    """Единая нормализованная модель сообщения для HTTP и fan-out."""

    id: int
    chat_id: int
    project_id: int
    seq: int
    author_type: Literal["USER", "AGENT"] = "USER"
    author: ChatUser | None
    client_message_id: UUID
    content: str
    revision: int
    created_at: datetime
    edited_at: datetime | None
    deleted_at: datetime | None
    reply: ChatReplyView | None
    mentions: list[ChatUser]
    entities: list[ChatEntityView]
    attachments: list[ChatAttachmentView]
    reactions: list[ChatReactionView]
    agent: ChatAgentView | None = None


class ProjectChatView(BaseModel):
    """Состояние общего чата для текущего участника."""

    id: int
    project_id: int
    event_cursor: int
    last_read_seq: int
    unread_count: int
    members: list[ChatUser]


class ChatMessagePage(BaseModel):
    """История в прямом порядке; курсоры не зависят от клиентского времени."""

    messages: list[ChatMessageView]
    has_more: bool
    event_cursor: int


class ChatEventView(BaseModel):
    """Порядковый номер обязателен только для долговечных событий."""

    type: str
    project_id: int
    seq: int
    data: dict


class ChatEventPage(BaseModel):
    """Дельта после отключения, включая правки и удаления старых сообщений."""

    events: list[ChatEventView]
    cursor: int
    has_more: bool


class ChatCommand(ChatInput):
    """Конверт команд; тело повторно проверяется соответствующей схемой."""

    type: Literal[
        "message.send",
        "message.edit",
        "message.delete",
        "reaction.set",
        "read.set",
        "typing.started",
        "typing.stopped",
        "ping",
    ]
    request_id: UUID
    message_id: PositiveId | None = None
    data: dict = Field(default_factory=dict)
