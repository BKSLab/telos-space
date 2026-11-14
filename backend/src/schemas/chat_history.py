"""Ограниченная история одной переписки и проверенные источники сообщений."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from src.schemas.chat_files import ChatFileSchema
from src.schemas.chat_references import ChatMessageReferenceSchema


class ChatHistoryEntityReferenceSchema(BaseModel):
    """Историческая identity-подсказка; актуальные поля и права читает инструмент объекта."""

    model_config = ConfigDict(extra="forbid")

    entity_type: str = Field(
        max_length=32, description="Тип сохранённой сущности в нижнем регистре.", examples=["task"]
    )
    entity_id: int | None = Field(
        default=None,
        gt=0,
        description="Числовой ID сущности; документы используют document_ref.",
        examples=[501],
    )
    document_ref: str | None = Field(
        default=None,
        max_length=128,
        description="Канонический ref документа без прав доступа и содержимого.",
        examples=["d1_ZG9jdW1lbnQ6MQ"],
    )
    source_id: str | None = Field(
        default=None,
        max_length=96,
        description="Структурный ID сохранённого источника, без handles прежних запусков.",
        examples=["task:501"],
    )
    task_id: int | None = Field(
        default=None,
        gt=0,
        description="Связанная задача, если её ID был явно сохранён в источнике.",
        examples=[501],
    )
    key: str | None = Field(
        default=None,
        max_length=64,
        description="Ограниченный исторический ключ карточки, не её актуальное состояние.",
        examples=["TELOS-501"],
    )
    title: str | None = Field(
        default=None,
        max_length=160,
        description="Ограниченный исторический заголовок; без excerpt, цитат и полей карточки.",
        examples=["Проверить результат"],
    )


class ChatHistoryMessageSchema(BaseModel):
    """Сообщение из текущей доступной области; файлы всегда вложены в сообщение."""

    model_config = ConfigDict(extra="forbid")

    id: int = Field(gt=0, description="ID исходного сообщения.", examples=[42])
    chat_id: int = Field(
        gt=0, description="ID чата проекта или личного диалога, выбранного сервером.", examples=[1]
    )
    kind: Literal["project_chat", "conversation"] = Field(
        description="Вид переписки, различающий одинаковые числовые ID.", examples=["project_chat"]
    )
    project_id: int | None = Field(
        description="Фактический проект переписки; null у общего личного диалога.",
        examples=[1, None],
    )
    project_key: str | None = Field(
        description="Текущий ключ фактического проекта для навигации.", examples=["TELOS", None]
    )
    author_id: int | None = Field(
        description="Локальный автор; null у Ариса или удалённого участника.", examples=[1, None]
    )
    author_name: str = Field(
        description="Имя автора из текущих серверных данных.", examples=["Иван Петров", "Арис"]
    )
    author_type: Literal["USER", "AGENT"] = Field(
        description="Автор сообщения: пользователь или Арис.", examples=["USER"]
    )
    created_at: datetime = Field(
        description="Время отправки с часовым поясом.", examples=["2026-11-14T09:30:00Z"]
    )
    revision: int = Field(
        ge=1,
        description="Версия исходного сообщения; завершённая личная реплика неизменяема и имеет версию 1.",
        examples=[1],
    )
    content: str = Field(
        description="Страница исходного текста без добавления метаданных и файлов в тело.",
        examples=["Исправлю завтра."],
    )
    text_truncated: bool = Field(
        description="Текущая страница не содержит весь исходный текст; продолжение доступно по content offsets.",
        examples=[False],
    )
    content_offset: int = Field(
        ge=0, description="Начало страницы исходного текста в Unicode codepoints.", examples=[0]
    )
    content_total_chars: int = Field(
        ge=0, description="Полная длина текста сообщения в Unicode codepoints.", examples=[14000]
    )
    next_content_offset: int | None = Field(
        description="Начало следующей страницы текста; null в конце сообщения.",
        examples=[8000, None],
    )
    reply_to_message_id: int | None = Field(
        description="ID сообщения, на которое отвечает исходная реплика; доступ цели проверяется отдельно.",
        examples=[35, None],
    )
    attachments: list[ChatFileSchema] = Field(
        default_factory=list,
        description="Только отправленные файлы этого сообщения; bounded ready prefixes не запускают извлечение.",
        examples=[[]],
    )
    entity_references: list[ChatHistoryEntityReferenceSchema] = Field(
        default_factory=list,
        max_length=20,
        description="До 20 структурных подсказок из сохранённых карточек и ссылок сообщения, включая ответы без текста. Для актуальных данных нужен read-инструмент сущности.",
        examples=[
            [{"entity_type": "task", "entity_id": 501, "source_id": "task:501", "key": "TELOS-501"}]
        ],
    )
    reference: ChatMessageReferenceSchema = Field(
        description="Доверенная ссылка с SHA256 полного исходного текста и точной Unicode-цитатой.",
        examples=[
            {
                "kind": "project_chat",
                "chat_id": 1,
                "message_id": 42,
                "project_id": 1,
                "project_key": "TELOS",
                "author_id": 1,
                "author_name": "Иван Петров",
                "created_at": "2026-11-14T09:30:00Z",
                "revision": 1,
                "content_hash": "a" * 64,
                "quote": "Исправлю завтра.",
                "start": 0,
                "end": 16,
                "offset_unit": "unicode_codepoints",
                "state": "available",
            }
        ],
    )


class ChatHistoryFiltersSchema(BaseModel):
    """Применённые фильтры, включая фактическую область и календарный часовой пояс."""

    chat_id: int = Field(description="Проверенная сервером область чтения.", examples=[1])
    kind: Literal["project_chat", "conversation"] = Field(
        description="Вид проверенной переписки.", examples=["project_chat"]
    )
    query: str | None = Field(
        default=None,
        description="FTS-запрос по русскому/simple словарям; не более 200 символов.",
        examples=["проверка отчёта", None],
    )
    author_user_id: int | None = Field(
        default=None,
        description="Применённый локальный ID автора: me нормализован сервером в ID инициатора; имена не определяют identity.",
        examples=[1, None],
    )
    author_type: Literal["USER", "AGENT"] | None = Field(
        default=None,
        description="Применённый тип автора; USER и AGENT фильтруются до LIMIT совместно с ID автора.",
        examples=["USER", "AGENT", None],
    )
    date_from: date | None = Field(
        default=None,
        description="Первый включительный календарный день в timezone.",
        examples=["2026-11-01", None],
    )
    date_to: date | None = Field(
        default=None,
        description="Последний включительный календарный день в timezone; SQL использует начало следующего дня.",
        examples=["2026-11-14", None],
    )
    timezone: str = Field(
        description="Проверенный IANA timezone серверного контекста.", examples=["Europe/Samara"]
    )
    before_message_id: int | None = Field(
        default=None,
        description="Исключительная верхняя граница, проверенная в этой переписке.",
        examples=[42, None],
    )
    after_message_id: int | None = Field(
        default=None,
        description="Исключительная нижняя граница, проверенная в этой переписке.",
        examples=[35, None],
    )


class ChatHistoryPageSchema(BaseModel):
    """Страница подходящих сообщений в хронологическом порядке."""

    items: list[ChatHistoryMessageSchema] = Field(
        description="Подходящие живые сообщения; удалённые и незавершённые ответы исключены до LIMIT.",
        examples=[[]],
    )
    has_more: bool = Field(
        description="Есть ещё подходящие сообщения в выбранном направлении.", examples=[True]
    )
    next_before_message_id: int | None = Field(
        description="Курсор следующей страницы к ранней истории при default/before чтении.",
        examples=[35, None],
    )
    next_after_message_id: int | None = Field(
        description="Курсор следующей страницы к новым сообщениям при after чтении.",
        examples=[42, None],
    )
    applied_filters: ChatHistoryFiltersSchema = Field(
        description="Фактические scope и фильтры этой страницы.",
        examples=[{"chat_id": 1, "kind": "project_chat", "timezone": "UTC"}],
    )
