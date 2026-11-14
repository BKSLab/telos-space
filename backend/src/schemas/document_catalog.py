"""Общий пользовательский контракт текстовых и файловых документов."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

DocumentSourceKind = Literal["text", "upload"]
DocumentScope = Literal["all", "project", "tasks"]
DocumentProcessingFilter = Literal["all", "pending", "ready", "failed"]


class DocumentAuthorSchema(BaseModel):
    id: int = Field(description="Локальный автор документа.", examples=[1])
    name: str = Field(description="Публичное имя автора.", examples=["Иван Иванов"])


class DocumentOwnershipSchema(BaseModel):
    kind: Literal["project", "task"] = Field(
        description="Владелец, определяющий доступ и жизненный цикл.", examples=["project"]
    )
    project_id: int | None = Field(
        description="Текущий проект, если он существует.", examples=[1, None]
    )
    owner_task_id: int | None = Field(
        description="Задача-владелец; связь с задачей не меняет владельца.", examples=[42, None]
    )


class DocumentTaskSchema(BaseModel):
    id: int = Field(description="Связанная задача.", examples=[42])
    key: str = Field(description="Текущий ключ задачи.", examples=["TELOS-10"])
    title: str = Field(description="Название задачи.", examples=["Подготовить спецификацию"])
    relation: Literal["owner", "linked"] = Field(
        description="Владение задачей либо независимая связь.", examples=["linked"]
    )


class DocumentStageSchema(BaseModel):
    status: str = Field(description="Фактическое состояние стадии обработки.", examples=["ready"])
    detail: str | None = Field(
        default=None, description="Безопасная диагностика стадии.", examples=[None]
    )


class DocumentStatesSchema(BaseModel):
    extraction: DocumentStageSchema = Field(description="Извлечение текста из оригинала.")
    fts: DocumentStageSchema = Field(description="Полнотекстовый поиск PostgreSQL.")
    semantic: DocumentStageSchema = Field(description="Индекс знаний проекта.")


class DocumentOriginalSchema(BaseModel):
    filename: str = Field(
        description="Исходное имя неизменяемого файла.", examples=["Требования.pdf"]
    )
    content_type: str = Field(description="MIME-тип оригинала.", examples=["application/pdf"])
    size: int = Field(description="Размер оригинала в байтах.", examples=[1024])
    content_url: str = Field(
        description="Защищённый канонический URL оригинала.",
        examples=["/api/v1/documents/d1_ZG9jdW1lbnQ6MQ/content"],
    )
    preview_url: str | None = Field(
        description="Защищённая локальная миниатюра, если доступна.", examples=[None]
    )


class DocumentTextMetadataSchema(BaseModel):
    available: bool = Field(
        description="Можно ли прочитать текстовое представление.", examples=[True]
    )
    total_chars: int = Field(
        description="Полная длина представления в Unicode code points.", examples=[1234]
    )
    rendition: Literal["markdown", "extracted_text"] = Field(
        description="Вид читаемого представления.", examples=["markdown"]
    )
    content_hash: str = Field(description="SHA-256 полного текста UTF-8.", examples=["a" * 64])
    url: str = Field(
        description="Постраничное чтение текста.",
        examples=["/api/v1/documents/d1_ZG9jdW1lbnQ6MQ/text"],
    )


class DocumentCapabilitiesSchema(BaseModel):
    can_read: bool = Field(description="Чтение при текущих полномочиях.")
    can_edit: bool = Field(description="Изменение текста или метаданных.")
    can_delete: bool = Field(description="Удаление самого документа.")
    can_retry: bool = Field(description="Повтор обработки оригинала.")
    can_link: bool = Field(description="Создание связи с задачей без изменения доступа.")
    can_detach: bool = Field(description="Удаление независимой связи с задачей.")


class DocumentCatalogItemSchema(BaseModel):
    ref: str = Field(description="Непрозрачный стабильный идентификатор документа.")
    source_kind: DocumentSourceKind = Field(
        description="Текстовый документ либо загруженный оригинал."
    )
    title: str = Field(description="Редактируемое название документа.")
    caption: str | None = Field(default=None, description="Примечание пользователя.")
    author: DocumentAuthorSchema | None = Field(
        default=None, description="Автор; неизвестен у прежних данных."
    )
    ownership: DocumentOwnershipSchema = Field(description="Владелец и текущий контекст доступа.")
    tasks: list[DocumentTaskSchema] = Field(
        default_factory=list, description="Доступные связанные задачи."
    )
    created_at: datetime = Field(description="Дата создания документа.")
    updated_at: datetime = Field(description="Дата изменения метаданных.")
    revision: int = Field(description="Версия для optimistic concurrency.", ge=1)
    format: str = Field(description="Формат без точки в нижнем регистре.", examples=["md", "pdf"])
    states: DocumentStatesSchema = Field(description="Независимые стадии обработки.")
    original: DocumentOriginalSchema | None = Field(
        description="Неизменяемый оригинал файлового документа."
    )
    text: DocumentTextMetadataSchema = Field(
        description="Метаданные полного текстового представления."
    )
    capabilities: DocumentCapabilitiesSchema = Field(
        description="Действия при текущем доступе и scopes ключа."
    )


class DocumentCatalogPageSchema(BaseModel):
    items: list[DocumentCatalogItemSchema] = Field(description="Страница доступных документов.")
    total: int = Field(description="Общее число результатов после фильтров.")
    limit: int = Field(description="Размер страницы.")
    offset: int = Field(description="Смещение страницы.")
    next_offset: int | None = Field(description="Смещение следующей страницы либо null.")


class DocumentCreateRequestSchema(BaseModel):
    request_id: UUID = Field(
        description="Стабильный UUID одного создания; повтор не создаёт дубль."
    )
    project_id: int | None = Field(
        default=None, gt=0, description="Проект-владелец, исключает owner_task_id."
    )
    owner_task_id: int | None = Field(
        default=None, gt=0, description="Задача-владелец, включая личную."
    )
    title: str = Field(min_length=1, max_length=255, description="Название документа.")
    caption: str | None = Field(default=None, max_length=2000, description="Примечание документа.")
    content_md: str = Field(default="", description="Редактируемый текст Markdown.")

    @model_validator(mode="after")
    def one_owner(self):
        if (self.project_id is None) == (self.owner_task_id is None):
            raise ValueError("Нужен ровно один владелец: project_id или owner_task_id.")
        return self


class DocumentUpdateRequestSchema(BaseModel):
    expected_revision: int = Field(ge=1, description="Прочитанная версия документа.")
    title: str | None = Field(
        default=None, min_length=1, max_length=255, description="Новое название."
    )
    caption: str | None = Field(
        default=None, max_length=2000, description="Новое примечание; null очищает его."
    )
    content_md: str | None = Field(
        default=None, description="Новый текст; доступен текстовым документам."
    )


class DocumentTextPageSchema(BaseModel):
    ref: str = Field(description="Стабильная ссылка документа.")
    text: str = Field(description="Фрагмент исходного текстового представления.")
    rendition: Literal["markdown", "extracted_text"] = Field(
        description="Вид текстового представления."
    )
    content_hash: str = Field(description="SHA-256 полного представления UTF-8.")
    offset: int = Field(description="Начало страницы в Unicode code points.")
    next_offset: int | None = Field(description="Продолжение либо null.")
    total_chars: int = Field(description="Полная длина текстового представления.")
    offset_unit: Literal["unicode_codepoints"] = Field(
        default="unicode_codepoints", description="Единица смещений; не UTF-16 и не байты."
    )
