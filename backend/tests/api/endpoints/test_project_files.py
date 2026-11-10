"""HTTP-контракт файлов проекта: multipart, scopes и защищённый оригинал."""

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from httpx import AsyncClient
from starlette.datastructures import UploadFile

from main import app
from src.core.settings import get_settings
from src.db.models.api_tokens import ApiTokenScope
from src.dependencies.auth import get_principal
from src.dependencies.scopes import get_project_files_service
from src.exceptions.access import ResourceNotAvailableError
from src.exceptions.api_keys import ApiKeyScopeError
from src.exceptions.auth import NotAuthenticatedError
from src.exceptions.project_files import (
    ProjectFileNotFoundError,
    ProjectFileUnsupportedError,
    ProjectFileValidationError,
)
from src.schemas.project_files import ProjectFileDetailSchema, ProjectFileSchema
from src.services.attachment_download import AttachmentDownload
from src.services.auth import ApiKeyGrant, Principal
from src.services.project_files import ProjectFilesService


def file_schema() -> ProjectFileSchema:
    """Создаёт ответ сразу после сохранения оригинала."""
    return ProjectFileSchema(
        id=7,
        project_id=2,
        uploaded_by_user_id=1,
        original_name="plan.txt",
        content_type="text/plain",
        size=4,
        created_at=datetime(2026, 11, 6, tzinfo=UTC),
        content_url="/api/v1/projects/2/files/7/content",
        extraction_status="pending",
        extraction_detail=None,
        index_status="pending",
        index_detail=None,
        indexed_at=None,
        summary=None,
    )


@pytest.fixture
def files_service() -> AsyncMock:
    """Подменяет только сервис, оставляя транспорт и write-guard настоящими."""
    service = AsyncMock(spec=ProjectFilesService)
    service.max_file_size = 4
    service.upload.return_value = file_schema()
    service.list_files.return_value = [file_schema()]
    service.get_file.return_value = ProjectFileDetailSchema(
        **file_schema().model_dump(), can_edit=True, text="", text_total_chars=0, text_offset=0, next_offset=None
    )
    service.retry.return_value = file_schema()
    app.dependency_overrides[get_project_files_service] = lambda: service
    return service


async def test_file_routes_return_pending_upload_text_page_and_retry_status(
    api_client: AsyncClient,
    files_service: AsyncMock,
) -> None:
    uploaded = await api_client.post(
        "/api/v1/projects/2/files",
        files={"file": ("plan.txt", b"plan", "application/x-untrusted")},
    )
    assert uploaded.status_code == 201
    assert uploaded.json()["extraction_status"] == uploaded.json()["index_status"] == "pending"
    assert uploaded.json()["content_url"] == "/api/v1/projects/2/files/7/content"
    assert "text" not in uploaded.json()
    assert "storage_key" not in uploaded.json()
    files_service.upload.assert_awaited_once_with(
        project_id=2, file_name="plan.txt", content=b"plan", caption=None,
        session_token=None, bearer_secret=None,
    )

    listed = await api_client.get(
        "/api/v1/projects/2/files", params={"search": "план", "limit": 10, "offset": 20}
    )
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [7]
    files_service.list_files.assert_awaited_once_with(
        project_id=2, user_id=1, search="план", limit=10, offset=20
    )

    detail = await api_client.get(
        "/api/v1/projects/2/files/7", params={"offset": 0, "max_chars": 4}
    )
    assert detail.status_code == 200
    assert detail.json()["text"] == ""
    assert detail.json()["text_total_chars"] == 0
    assert detail.json()["next_offset"] is None
    assert detail.json()["can_edit"] is True
    files_service.get_file.assert_awaited_once_with(
        project_id=2, file_id=7, user_id=1, offset=0, max_chars=4
    )

    retried = await api_client.post("/api/v1/projects/2/files/7/retry")
    assert retried.status_code == 202
    assert retried.json()["index_status"] == "pending"

    deleted = await api_client.delete("/api/v1/projects/2/files/7")
    assert deleted.status_code == 204
    assert deleted.content == b""


async def test_multipart_reads_only_limit_plus_one_and_closes_file_on_all_outcomes(
    api_client: AsyncClient,
    files_service: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested_sizes: list[int] = []
    closed: list[UploadFile] = []
    original_read, original_close = UploadFile.read, UploadFile.close

    async def read(upload: UploadFile, size: int = -1) -> bytes:
        requested_sizes.append(size)
        return await original_read(upload, size)

    async def close(upload: UploadFile) -> None:
        await original_close(upload)
        closed.append(upload)

    monkeypatch.setattr(UploadFile, "read", read)
    monkeypatch.setattr(UploadFile, "close", close)
    cases = [
        (b"plan", None, 201),
        (b"12345", None, 413),
        (b"plan", ProjectFileUnsupportedError(".exe"), 415),
        (b"", ProjectFileValidationError("empty"), 422),
    ]
    for content, failure, status in cases:
        files_service.upload.reset_mock()
        files_service.upload.side_effect = failure
        closed.clear()
        response = await api_client.post(
            "/api/v1/projects/2/files",
            files={"file": ("plan.txt", content, "text/plain")},
        )
        assert response.status_code == status
        assert closed and all(upload.file.closed for upload in closed)
        if status == 413:
            files_service.upload.assert_not_awaited()

    assert requested_sizes == [5, 5, 5, 5]


async def test_read_token_reads_files_but_cannot_upload_retry_or_delete(
    api_client: AsyncClient,
    files_service: AsyncMock,
    current_principal: Principal,
) -> None:
    app.dependency_overrides[get_principal] = lambda: replace(
        current_principal, scope=ApiTokenScope.READ, via_api_token=True
    )
    for url in ["/api/v1/projects/2/files", "/api/v1/projects/2/files/7"]:
        assert (await api_client.get(url)).status_code == 200
    assert (await api_client.get("/api/v1/projects/2/files/7")).json()["can_edit"] is False

    # Upload performs auth inside its short service scope; the transport forwards the refusal.
    files_service.upload.side_effect = ApiKeyScopeError("projects:write", [])
    uploaded = await api_client.post(
        "/api/v1/projects/2/files", files={"file": ("plan.txt", b"plan", "text/plain")}
    )
    retried = await api_client.post("/api/v1/projects/2/files/7/retry")
    deleted = await api_client.delete("/api/v1/projects/2/files/7")
    assert [response.status_code for response in (uploaded, retried, deleted)] == [403, 403, 403]
    files_service.upload.assert_awaited_once()
    files_service.retry.assert_not_awaited()
    files_service.delete_file.assert_not_awaited()


async def test_invalid_path_pagination_and_missing_multipart_never_reach_service(
    api_client: AsyncClient,
    files_service: AsyncMock,
) -> None:
    invalid_urls = [
        "/api/v1/projects/0/files",
        "/api/v1/projects/2/files/0",
        "/api/v1/projects/2/files?limit=0",
        "/api/v1/projects/2/files?limit=201",
        "/api/v1/projects/2/files?offset=-1",
        "/api/v1/projects/2/files?search=" + "a" * 2001,
        "/api/v1/projects/2/files/7?offset=-1",
        "/api/v1/projects/2/files/7?max_chars=0",
        "/api/v1/projects/2/files/7?max_chars=32001",
    ]
    for url in invalid_urls:
        response = await api_client.get(url)
        assert response.status_code == 422, url
    assert (await api_client.post("/api/v1/projects/2/files")).status_code == 422
    files_service.list_files.assert_not_awaited()
    files_service.get_file.assert_not_awaited()
    files_service.upload.assert_not_awaited()


async def test_detail_edit_capability_combines_api_key_scopes_and_current_domain_grant(
    api_client: AsyncClient, files_service: AsyncMock, current_principal: Principal,
) -> None:
    for writable_scope, domain_write, expected in ((False, True, False), (True, False, False), (True, True, True)):
        scopes = {"projects:read", "projects:write"} if writable_scope else {"projects:read"}
        key = ApiKeyGrant(
            key_id=UUID(int=1), label="Проверка", key_prefix="12345678", owner_user_id=1,
            scopes=frozenset(scopes),
        )
        app.dependency_overrides[get_principal] = lambda key=key: replace(current_principal, api_key=key)
        files_service.get_file.return_value.can_edit = domain_write
        response = await api_client.get("/api/v1/projects/2/files/7")
        assert response.status_code == 200
        assert response.json()["can_edit"] is expected


async def test_download_returns_attachment_with_private_headers_and_safe_errors(
    api_client: AsyncClient,
    files_service: AsyncMock,
    tmp_path: Path,
) -> None:
    original = tmp_path / "page.html"
    original.write_bytes(b"<script>alert('original')</script>")
    files_service.prepare_download.return_value = AttachmentDownload(
        path=original, media_type="text/html", filename="page.html", previewable=False
    )
    api_client.cookies.set(get_settings().auth.session_cookie_name, "session")
    response = await api_client.get(
        "/api/v1/projects/2/files/7/content", headers={"Authorization": "Bearer read-token"}
    )
    assert response.status_code == 200
    assert response.content == original.read_bytes()
    assert response.headers["content-disposition"].startswith("attachment;")
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-security-policy"] == "default-src 'none'; sandbox"
    files_service.prepare_download.assert_awaited_once_with(
        project_id=2, file_id=7, session_token="session", bearer_secret="read-token"
    )

    for failure in [
        NotAuthenticatedError(),
        ResourceNotAvailableError(resource="project", resource_id=2),
        ProjectFileNotFoundError("private/path/to/original"),
    ]:
        files_service.prepare_download.side_effect = failure
        response = await api_client.get("/api/v1/projects/2/files/7/content")
        assert response.status_code == failure.status_code
        assert response.json() == {"detail": failure.detail}
        assert "private/path" not in response.text


async def test_missing_or_inaccessible_file_uses_domain_http_error(
    api_client: AsyncClient,
    files_service: AsyncMock,
) -> None:
    failure = ProjectFileNotFoundError("7")
    files_service.get_file.side_effect = failure
    files_service.retry.side_effect = failure
    files_service.delete_file.side_effect = failure
    responses = [
        await api_client.get("/api/v1/projects/2/files/7"),
        await api_client.post("/api/v1/projects/2/files/7/retry"),
        await api_client.delete("/api/v1/projects/2/files/7"),
    ]
    assert [response.status_code for response in responses] == [404, 404, 404]
    assert all(response.json() == {"detail": failure.detail} for response in responses)
