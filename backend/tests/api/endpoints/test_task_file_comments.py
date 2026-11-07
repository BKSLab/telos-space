"""Multipart, bounded read и защищённые карточки файлов в комментариях."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from starlette.datastructures import UploadFile

from main import app
from src.core.settings import get_settings
from src.dependencies.file_comments import (
    get_file_comments_service,
    get_file_previews_service,
    get_task_file_mutations_service,
)
from src.exceptions.file_comments import FileCommentSubmissionConflictError
from src.schemas.task_attachments import TaskAttachmentSchema
from src.schemas.task_comments import CommentSchema
from src.services.attachment_download import AttachmentDownload
from src.services.file_comments import FileCommentsService
from src.services.file_metadata import FilePreviewsService, TaskFileMutationsService


@pytest.fixture
def comment_files_service():
    service = AsyncMock(spec=FileCommentsService)
    service.max_file_size, service.max_files = 4, 10
    service.submit.return_value = CommentSchema(
        id=3,
        task_id=2,
        body_md="",
        created_at=datetime.now(UTC),
        attachments=[
            TaskAttachmentSchema(
                id=4,
                task_id=2,
                original_name="screen.png",
                caption="Макет",
                uploaded_by_user_id=1,
                content_type="image/png",
                size=4,
                created_at=datetime.now(UTC),
            )
        ],
    )
    app.dependency_overrides[get_file_comments_service] = lambda: service
    return service


async def test_multipart_forwards_stable_uuid_files_captions_existing_ids_and_returns_nested_cards(
    api_client, comment_files_service
):
    request_id = uuid4()
    api_client.cookies.set(get_settings().auth.session_cookie_name, "synthetic-cookie")
    response = await api_client.post(
        "/api/v1/tasks/2/comments/with-files",
        data={
            "request_id": str(request_id),
            "body_md": "",
            "captions": '["Макет"]',
            "attachment_ids": "[7]",
        },
        files=[("files", ("screen.png", b"file", "image/png"))],
        headers={"Authorization": "Bearer synthetic-key"},
    )
    assert response.status_code == 201
    attachment = response.json()["attachments"][0]
    assert attachment["caption"] == "Макет" and attachment["uploaded_by_user_id"] == 1
    assert (
        attachment["previewable"]
        and attachment["preview_url"] == "/api/v1/tasks/2/attachments/4/preview"
    )
    args = comment_files_service.submit.await_args.kwargs
    assert args["request_id"] == request_id and args["attachment_ids"] == [7]
    assert args["session_token"] == "synthetic-cookie" and args["bearer_secret"] == "synthetic-key"
    assert (args["uploads"][0].content, args["uploads"][0].caption) == (b"file", "Макет")
    comment_files_service.submit.side_effect = FileCommentSubmissionConflictError()
    repeated = await api_client.post(
        "/api/v1/tasks/2/comments/with-files",
        data={"request_id": str(request_id), "body_md": "Иное содержимое"},
    )
    assert repeated.status_code == 409


async def test_multipart_rejects_bad_json_misaligned_captions_ids_limit_and_closes_uploads(
    api_client, comment_files_service, monkeypatch
):
    closed = []
    sizes = []
    original_close, original_read = UploadFile.close, UploadFile.read

    async def close(upload):
        await original_close(upload)
        closed.append(upload)

    async def read(upload, size=-1):
        sizes.append(size)
        return await original_read(upload, size)

    monkeypatch.setattr(UploadFile, "close", close)
    monkeypatch.setattr(UploadFile, "read", read)
    for data in (
        {"captions": "invalid"},
        {"captions": "{}"},
        {"captions": "[]"},
        {"captions": "[8]"},
        {"attachment_ids": "[true]"},
        {"attachment_ids": "[-1]"},
        {"attachment_ids": str(list(range(1, 11)))},
    ):
        closed.clear()
        response = await api_client.post(
            "/api/v1/tasks/2/comments/with-files",
            data={"request_id": str(uuid4()), **data},
            files={"files": ("file.txt", b"file", "text/plain")},
        )
        assert response.status_code == 422, data
        assert closed and all(upload.file.closed for upload in closed)
    oversized = await api_client.post(
        "/api/v1/tasks/2/comments/with-files",
        data={"request_id": str(uuid4())},
        files={"files": ("file.txt", b"large", "text/plain")},
    )
    assert oversized.status_code == 413
    assert sizes == [5]
    assert (
        await api_client.post("/api/v1/tasks/2/comments/with-files", data={"body_md": "Текст"})
    ).status_code == 422
    assert (
        await api_client.post(
            "/api/v1/tasks/2/comments/with-files",
            data={"request_id": "not-a-uuid", "body_md": "Текст"},
        )
    ).status_code == 422
    comment_files_service.submit.assert_not_awaited()


async def test_task_caption_patch_distinguishes_null_and_omitted_and_preview_is_private(
    api_client, tmp_path
):
    mutations = AsyncMock(spec=TaskFileMutationsService)
    mutations.update_caption.return_value = TaskAttachmentSchema(
        id=4,
        task_id=2,
        original_name="screen.png",
        content_type="image/png",
        size=4,
        created_at=datetime.now(UTC),
    )
    app.dependency_overrides[get_task_file_mutations_service] = lambda: mutations
    assert (await api_client.patch("/api/v1/tasks/2/attachments/4", json={})).status_code == 422
    assert (
        await api_client.patch("/api/v1/tasks/2/attachments/4", json={"caption": "x" * 4001})
    ).status_code == 422
    cleared = await api_client.patch("/api/v1/tasks/2/attachments/4", json={"caption": None})
    assert cleared.status_code == 200 and cleared.json()["caption"] is None
    mutations.update_caption.assert_awaited_once_with(
        task_id=2, attachment_id=4, caption=None, session_token=None, bearer_secret=None
    )
    mutations.retry.return_value = mutations.update_caption.return_value
    retried = await api_client.post("/api/v1/tasks/2/attachments/4/retry")
    assert retried.status_code == 202 and retried.json()["id"] == 4
    mutations.retry.assert_awaited_once_with(
        task_id=2, attachment_id=4, session_token=None, bearer_secret=None
    )
    preview = tmp_path / "preview.jpg"
    preview.write_bytes(b"synthetic bounded jpeg")
    service = AsyncMock(spec=FilePreviewsService)
    service.prepare_task.return_value = AttachmentDownload(
        preview, "image/jpeg", "preview.jpg", True
    )
    service.prepare_project.return_value = service.prepare_task.return_value
    app.dependency_overrides[get_file_previews_service] = lambda: service
    for path in ("/api/v1/tasks/2/attachments/4/preview", "/api/v1/projects/2/files/4/preview"):
        response = await api_client.get(path)
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("image/jpeg")
        assert response.headers["content-disposition"].startswith("inline")
        assert response.headers["cache-control"] == "private, no-store"
        assert response.headers["x-content-type-options"] == "nosniff"
