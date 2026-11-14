"""Multipart, bounded read и защищённые карточки файлов в комментариях."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from starlette.datastructures import UploadFile

from main import app
from src.core.settings import get_settings
from src.dependencies.file_comments import get_file_comments_service
from src.exceptions.file_comments import FileCommentSubmissionConflictError
from src.schemas.task_comments import CommentSchema
from src.services.file_comments import FileCommentsService
from src.utils.document_refs import encode_document_ref


@pytest.fixture
def comment_files_service():
    service = AsyncMock(spec=FileCommentsService)
    service.max_file_size, service.max_files = 4, 10
    service.submit.return_value = CommentSchema(
        id=3,
        task_id=2,
        body_md="",
        created_at=datetime.now(UTC),
        document_refs=[encode_document_ref("attachment", 4)],
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
            "document_refs": '["' + encode_document_ref("attachment", 7) + '"]',
        },
        files=[("files", ("screen.png", b"file", "image/png"))],
        headers={"Authorization": "Bearer synthetic-key"},
    )
    assert response.status_code == 201
    assert response.json()["document_refs"] == [encode_document_ref("attachment", 4)]
    args = comment_files_service.submit.await_args.kwargs
    assert args["request_id"] == request_id and args["document_refs"] == [
        encode_document_ref("attachment", 7)
    ]
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
        {"document_refs": "[true]"},
        {"document_refs": "[-1]"},
        {"document_refs": str(list(range(1, 11)))},
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
