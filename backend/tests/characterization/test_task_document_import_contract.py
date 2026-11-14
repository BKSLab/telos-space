"""Контракт импорта документа в задачу: успех, отказ и отсутствие остатков.

Этап 5 делает импорт одной транзакцией вместо трёх независимых commit с
best-effort компенсацией. Механизм меняется, наблюдаемый инвариант — нет:
клиент получает тот же ответ, а после неуспешного импорта в системе не
остаётся ни одной частично созданной сущности.
"""

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.clients.vision import DisabledVisionCapability
from src.exceptions.documents import DocumentsServiceError
from src.exceptions.task_attachments import TaskAttachmentTooLargeError
from src.exceptions.task_documents import (
    TaskDocumentImportServiceError,
    TaskDocumentStepFailedError,
    TaskDocumentTooLargeError,
    TaskDocumentUnsupportedFormatError,
)
from src.repositories.tasks import TasksRepository
from src.repositories.unit_of_work import UnitOfWork
from src.schemas.document_links import DocumentLinkSchema
from src.schemas.documents import DocumentDetailSchema
from src.schemas.task_attachments import TaskAttachmentSchema
from src.services.db_scope import TaskDocumentImportScope
from src.services.document_links import DocumentLinksService
from src.services.documents import DocumentsService
from src.services.task_attachments import StoredAttachment, TaskAttachmentsService
from src.services.task_documents import TaskDocumentImportService
from src.storage.task_attachments import TaskAttachmentStorage

IMPORT_PATH = "/api/v1/tasks/8/documents/import"


def _attachment() -> TaskAttachmentSchema:
    """Метаданные сохранённого оригинала."""
    return TaskAttachmentSchema(
        id=10,
        task_id=8,
        original_name="brief.txt",
        content_type="text/plain",
        size=5,
        created_at=datetime(2026, 9, 1, 10, 0, tzinfo=UTC),
        content_url="/api/v1/tasks/8/attachments/10/content",
        previewable=False,
    )


def _document() -> DocumentDetailSchema:
    """Документ проекта, полученный из файла."""
    return DocumentDetailSchema(
        id=11,
        project_id=2,
        slug="brief-txt",
        title="brief.txt",
        content_md="# brief.txt\n\nhello",
        created_at=datetime(2026, 9, 1, 10, 0, tzinfo=UTC),
        updated_at=datetime(2026, 9, 1, 10, 0, tzinfo=UTC),
        search_match_source=None,
        search_title=None,
        search_excerpt=None,
    )


def _link() -> DocumentLinkSchema:
    """Связь документа с задачей."""
    return DocumentLinkSchema(id=12, document_id=11, task_id=8)


FAILED_IMPORTS = (
    (
        TaskDocumentUnsupportedFormatError("Этот формат нельзя преобразовать в документ проекта."),
        422,
        "Этот формат нельзя преобразовать в документ проекта.",
    ),
    (
        TaskDocumentTooLargeError(max_size_mb=10),
        413,
        "Размер файла превышает допустимые 10 МБ.",
    ),
    (
        TaskDocumentStepFailedError(DocumentsServiceError("сбой создания документа")),
        500,
        DocumentsServiceError("сбой создания документа").detail,
    ),
)


def test_nested_failure_keeps_the_cause_visible_to_the_client() -> None:
    """Обёртка верхнего сервиса переносит наружу причину отказа.

    Иначе «файл слишком большой» и «конфликт slug» слились бы в одно
    невнятное сообщение, и пользователь не понял бы, что исправлять.
    """
    cause = TaskAttachmentTooLargeError(max_size_mb=10)

    wrapped = TaskDocumentStepFailedError(cause)

    assert wrapped.status_code == cause.status_code
    assert wrapped.detail == cause.detail
    assert isinstance(wrapped, TaskDocumentImportServiceError)


def _build_service(*, link_error: Exception | None = None):
    """Собирает сервис импорта на двойниках вложенных сервисов."""
    tasks = AsyncMock(spec=TasksRepository)
    tasks.get_by_id.return_value = SimpleNamespace(id=8, project_id=2)

    attachments = AsyncMock(spec=TaskAttachmentsService)
    attachments.max_file_size = 10 * 1024 * 1024
    attachments.save_in_transaction.return_value = StoredAttachment(
        attachment=_attachment(),
        storage_key="tasks/8/stored.txt",
    )

    documents = AsyncMock(spec=DocumentsService)
    documents.create_document.return_value = _document()

    links = AsyncMock(spec=DocumentLinksService)
    if link_error is not None:
        links.create_link.side_effect = link_error
    else:
        links.create_link.return_value = _link()

    unit_of_work = AsyncMock(spec=UnitOfWork)
    storage = AsyncMock(spec=TaskAttachmentStorage)
    db = TaskDocumentImportScope(
        knowledge_sources=AsyncMock(),
        tasks=tasks,
        attachments=attachments,
        documents=documents,
        links=links,
        unit_of_work=unit_of_work,
    )

    @asynccontextmanager
    async def scope():
        yield db

    service = TaskDocumentImportService(
        scope=scope,
        attachment_storage=storage,
        vision=DisabledVisionCapability(),
        max_file_size=10 * 1024 * 1024,
    )
    return service, attachments, documents, links, unit_of_work, storage


async def test_import_leaves_nothing_behind_when_last_step_fails() -> None:
    """Инвариант: после неуспешного импорта не остаётся частичных сущностей.

    Сейчас это достигается компенсацией уже сохранённых записей, после
    этапа 5 — единой транзакцией. Проверяется результат, а не механизм.
    """
    service, attachments, documents, _, unit_of_work, storage = _build_service(
        link_error=DocumentsServiceError("связь не создана")
    )

    with pytest.raises(TaskDocumentStepFailedError):
        await service.import_file(
            task_id=8,
            user_id=1,
            file_name="brief.txt",
            content_type="text/plain",
            content=b"hello",
        )

    # Инвариант тот же, механизм другой: вместо компенсации уже
    # закоммиченных строк — откат единственной транзакции.
    unit_of_work.rollback.assert_awaited_once_with()
    unit_of_work.commit.assert_not_awaited()
    documents.delete_document.assert_not_awaited()
    attachments.delete_attachment.assert_not_awaited()
    storage.delete.assert_awaited_once_with("tasks/8/stored.txt")


async def test_import_creates_attachment_document_and_link_in_order() -> None:
    """Успешный сценарий создаёт все три сущности и ничего не удаляет."""
    service, attachments, documents, links, unit_of_work, storage = _build_service()

    result = await service.import_file(
        task_id=8,
        user_id=1,
        file_name="brief.txt",
        content_type="text/plain",
        content=b"hello",
    )

    assert result.attachment.id == 10
    assert result.document.id == 11
    assert result.link.id == 12
    attachments.save_in_transaction.assert_awaited_once()
    documents.create_document.assert_awaited_once()
    links.create_link.assert_awaited_once()
    unit_of_work.commit.assert_awaited_once_with()
    documents.delete_document.assert_not_awaited()
    attachments.delete_attachment.assert_not_awaited()


async def test_unsupported_extension_is_rejected_before_any_write() -> None:
    """Неподдерживаемый формат отсекается до создания чего-либо."""
    service, attachments, documents, links, unit_of_work, storage = _build_service()

    with pytest.raises(TaskDocumentUnsupportedFormatError):
        await service.import_file(
            task_id=8,
            user_id=1,
            file_name="archive.zip",
            content_type="application/zip",
            content=b"PK\x03\x04",
        )

    attachments.save_in_transaction.assert_not_awaited()
    documents.create_document.assert_not_awaited()
    links.create_link.assert_not_awaited()
    unit_of_work.commit.assert_not_awaited()
