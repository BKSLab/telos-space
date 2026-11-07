from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.exceptions.task_attachments import (
    TaskAttachmentsRepositoryError,
    TaskAttachmentsServiceError,
    TaskAttachmentTooLargeError,
    TaskAttachmentUnsupportedTypeError,
)
from src.exceptions.tasks import TaskNotFoundError
from src.repositories.task_attachments import TaskAttachmentsRepository
from src.repositories.tasks import TasksRepository
from src.repositories.unit_of_work import UnitOfWork
from src.services.knowledge_events import KnowledgeEvents
from src.services.task_attachments import TaskAttachmentsService
from src.storage.task_attachments import TaskAttachmentStorage


def create_service() -> tuple[
    TaskAttachmentsService,
    AsyncMock,
    AsyncMock,
    AsyncMock,
]:
    """Создаёт сервис с типизированными дублёрами зависимостей."""
    attachments_repository = AsyncMock(spec=TaskAttachmentsRepository)
    tasks_repository = AsyncMock(spec=TasksRepository)
    storage = AsyncMock(spec=TaskAttachmentStorage)
    service = TaskAttachmentsService(
        attachments_repository=attachments_repository,
        tasks_repository=tasks_repository,
        storage=storage,
        unit_of_work=AsyncMock(spec=UnitOfWork),
        knowledge_events=AsyncMock(spec=KnowledgeEvents),
    )
    return service, attachments_repository, tasks_repository, storage


@pytest.mark.asyncio
async def test_upload_attachment_saves_safe_name_and_metadata() -> None:
    service, attachments_repository, tasks_repository, storage = create_service()
    tasks_repository.get_by_id.return_value = SimpleNamespace(id=7, project_id=1)
    attachments_repository.get_count_for_task.return_value = 0
    storage.save.return_value = "tasks/7/abc.pdf"
    attachments_repository.save.return_value = SimpleNamespace(
        id=3,
        task_id=7,
        original_name="report.pdf",
        storage_key="tasks/7/abc.pdf",
        content_type="application/pdf",
        size=3,
        created_at=datetime.now(UTC),
    )

    result = await service.upload_attachment(
        task_id=7,
        file_name="folder\\report.pdf",
        content_type="application/pdf",
        content=b"pdf",
    )

    assert result.original_name == "report.pdf"
    assert result.content_url.endswith("/tasks/7/attachments/3/content")
    assert result.previewable is False
    storage.save.assert_awaited_once_with(task_id=7, extension=".pdf", content=b"pdf")
    attachments_repository.save.assert_awaited_once()


@pytest.mark.asyncio
async def test_upload_attachment_deletes_file_when_metadata_save_fails() -> None:
    service, attachments_repository, tasks_repository, storage = create_service()
    tasks_repository.get_by_id.return_value = SimpleNamespace(id=1, project_id=1)
    attachments_repository.get_count_for_task.return_value = 0
    storage.save.return_value = "tasks/1/abc.pdf"
    attachments_repository.save.side_effect = TaskAttachmentsRepositoryError("БД недоступна")

    with pytest.raises(TaskAttachmentsServiceError):
        await service.upload_attachment(
            task_id=1,
            file_name="report.pdf",
            content_type="application/pdf",
            content=b"pdf",
        )

    storage.delete.assert_awaited_once_with("tasks/1/abc.pdf")


@pytest.mark.asyncio
async def test_upload_rejects_unusable_files_and_missing_task() -> None:
    """Отсутствующая задача, слишком большой файл и неподдерживаемое расширение отклоняются."""

    service, attachments_repository, tasks_repository, storage = create_service()
    tasks_repository.get_by_id.return_value = None

    with pytest.raises(TaskNotFoundError) as exc_info:
        await service.upload_attachment(
            task_id=999,
            file_name="report.pdf",
            content_type="application/pdf",
            content=b"pdf",
        )

    assert exc_info.value.status_code == 404
    attachments_repository.save.assert_not_awaited()
    storage.save.assert_not_awaited()

    service, attachments_repository, tasks_repository, storage = create_service()
    service.MAX_FILE_SIZE = 3
    tasks_repository.get_by_id.return_value = SimpleNamespace(id=1, project_id=1)
    attachments_repository.get_count_for_task.return_value = 0

    with pytest.raises(TaskAttachmentTooLargeError) as exc_info:
        await service.upload_attachment(
            task_id=1,
            file_name="large.pdf",
            content_type="application/pdf",
            content=b"1234",
        )

    assert exc_info.value.status_code == 413
    storage.save.assert_not_awaited()

    service, attachments_repository, tasks_repository, storage = create_service()
    tasks_repository.get_by_id.return_value = SimpleNamespace(id=1, project_id=1)
    attachments_repository.get_count_for_task.return_value = 0

    with pytest.raises(TaskAttachmentUnsupportedTypeError) as exc_info:
        await service.upload_attachment(
            task_id=1,
            file_name="script.exe",
            content_type="application/octet-stream",
            content=b"binary",
        )

    assert exc_info.value.status_code == 415
    storage.save.assert_not_awaited()
