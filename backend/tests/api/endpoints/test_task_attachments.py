from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient

from main import app
from src.dependencies.file_comments import get_task_file_mutations_service
from src.dependencies.scopes import get_attachment_download_service
from src.dependencies.services import get_task_attachments_service
from src.exceptions.task_attachments import (
    TaskAttachmentNotFoundError,
    TaskAttachmentTooLargeError,
)
from src.schemas.task_attachments import TaskAttachmentSchema
from src.services.attachment_download import AttachmentDownload, AttachmentDownloadService
from src.services.file_metadata import TaskFileMutationsService
from src.services.task_attachments import TaskAttachmentsService


def attachment_schema() -> TaskAttachmentSchema:
    """Возвращает стабильный API-ответ файла задачи."""
    return TaskAttachmentSchema(
        id=4,
        task_id=2,
        original_name="report.pdf",
        content_type="application/pdf",
        size=3,
        created_at=datetime.now(UTC),
        content_url="/api/v1/tasks/2/attachments/4/content",
        previewable=False,
    )


@pytest.mark.asyncio
async def test_attachment_endpoints_handle_upload_stream_and_errors(api_client: AsyncClient, tmp_path: Path) -> None:
    """Загрузка multipart, слишком большой файл — 413, стрим картинки inline, пропавший файл — 404."""

    service = AsyncMock(spec=TaskFileMutationsService)
    service.max_file_size = 10 * 1024 * 1024
    service.upload.return_value = attachment_schema()
    app.dependency_overrides[get_task_file_mutations_service] = lambda: service

    response = await api_client.post(
        "/api/v1/tasks/2/attachments",
        files={"file": ("report.pdf", b"pdf", "application/pdf")},
    )

    assert response.status_code == 201
    assert response.json()["original_name"] == "report.pdf"
    service.upload.assert_awaited_once_with(
        task_id=2,
        file_name="report.pdf",
        content_type="application/pdf",
        content=b"pdf",
        caption=None,
        session_token=None,
        bearer_secret=None,
    )

    service = AsyncMock(spec=TaskFileMutationsService)
    service.max_file_size = 10 * 1024 * 1024
    service.upload.side_effect = TaskAttachmentTooLargeError(max_size_mb=10)
    app.dependency_overrides[get_task_file_mutations_service] = lambda: service

    response = await api_client.post(
        "/api/v1/tasks/2/attachments",
        files={"file": ("report.pdf", b"pdf", "application/pdf")},
    )

    assert response.status_code == 413

    image_path = tmp_path / "photo.png"
    image_path.write_bytes(b"image")
    # Подготовка выдачи живёт в отдельном сервисе: у маршрута нет сессии.
    service = AsyncMock(spec=AttachmentDownloadService)
    service.prepare.return_value = AttachmentDownload(
        path=image_path,
        media_type="image/png",
        filename="photo.png",
        previewable=True,
    )
    app.dependency_overrides[get_attachment_download_service] = lambda: service

    response = await api_client.get("/api/v1/tasks/2/attachments/4/content")

    assert response.status_code == 200
    assert response.content == b"image"
    assert response.headers["content-type"].startswith("image/png")
    assert response.headers["content-disposition"].startswith("inline")
    assert response.headers["x-content-type-options"] == "nosniff"

    service = AsyncMock(spec=TaskAttachmentsService)
    service.delete_attachment.side_effect = TaskAttachmentNotFoundError(attachment_id=999)
    app.dependency_overrides[get_task_attachments_service] = lambda: service

    response = await api_client.delete("/api/v1/tasks/2/attachments/999")

    assert response.status_code == 404
