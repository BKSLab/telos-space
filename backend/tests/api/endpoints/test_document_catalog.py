"""Канонический HTTP: единый DTO, multipart, Unicode и защищённый оригинал."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from main import app
from src.dependencies.document_catalog import (
    get_document_catalog_principal,
    get_document_catalog_service,
)
from src.exceptions.api_keys import ApiKeyScopeError
from src.exceptions.document_catalog import (
    CatalogDocumentConflictError,
    CatalogDocumentNotFoundError,
)
from src.schemas.document_catalog import (
    DocumentCatalogItemSchema,
    DocumentCatalogPageSchema,
    DocumentTextPageSchema,
)
from src.services.attachment_download import AttachmentDownload
from src.services.document_catalog import DocumentCatalogService
from src.utils.document_refs import encode_document_ref

REF = encode_document_ref("attachment", 4)
BASE = f"/api/v1/documents/{REF}"


def document():
    return DocumentCatalogItemSchema.model_validate(
        {
            "ref": REF,
            "source_kind": "upload",
            "title": "Требования",
            "caption": None,
            "author": {"id": 1, "name": "Тест"},
            "ownership": {"kind": "task", "project_id": 1, "owner_task_id": 2},
            "tasks": [{"id": 2, "key": "TELOS-2", "title": "Задача", "relation": "owner"}],
            "created_at": datetime.now(UTC),
            "updated_at": datetime.now(UTC),
            "revision": 1,
            "format": "txt",
            "states": {
                "extraction": {"status": "pending"},
                "fts": {"status": "ready"},
                "semantic": {"status": "pending"},
            },
            "original": {
                "filename": "Требования.txt",
                "content_type": "text/plain",
                "size": 4,
                "content_url": f"{BASE}/content",
                "preview_url": None,
            },
            "text": {
                "available": False,
                "total_chars": 0,
                "rendition": "extracted_text",
                "content_hash": "a" * 64,
                "url": f"{BASE}/text",
            },
            "capabilities": {
                "can_read": True,
                "can_edit": True,
                "can_delete": True,
                "can_retry": True,
                "can_link": False,
                "can_detach": False,
            },
        }
    )


@pytest.fixture
def catalog_service(current_principal):
    service = AsyncMock(spec=DocumentCatalogService)
    service.max_file_size = 10 * 1024 * 1024
    service.get.return_value = document()
    service.create.return_value = document()
    service.upload.return_value = document()
    service.update.return_value = document()
    service.retry.return_value = document()
    service.link.return_value = document()
    service.unlink.return_value = document()
    service.list.return_value = DocumentCatalogPageSchema(
        items=[document()], total=1, limit=50, offset=0, next_offset=None
    )
    service.read_text.return_value = DocumentTextPageSchema(
        ref=REF,
        text="😀 аб",
        rendition="extracted_text",
        content_hash="a" * 64,
        offset=4,
        next_offset=8,
        total_chars=12,
    )
    app.dependency_overrides[get_document_catalog_service] = lambda: service
    app.dependency_overrides[get_document_catalog_principal] = lambda: current_principal
    return service


async def test_catalog_upload_and_filters_use_one_public_contract(api_client, catalog_service):
    request_id = uuid4()
    response = await api_client.post(
        "/api/v1/documents/upload",
        data={"request_id": str(request_id), "owner_task_id": "2", "caption": "К файлу"},
        files={"file": ("Требования.txt", b"data", "text/plain")},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["ref"] == REF and body["source_kind"] == "upload"
    assert set(body["states"]) == {"extraction", "fts", "semantic"}
    assert "content_md" not in body and "storage_key" not in body
    assert catalog_service.upload.await_args.kwargs["request_id"] == request_id
    listed = await api_client.get(
        "/api/v1/documents",
        params={
            "project_id": 1,
            "q": "требования",
            "scope": "tasks",
            "source_kind": "upload",
            "format": "txt",
            "processing": "pending",
            "limit": 15,
            "offset": 3,
        },
    )
    assert listed.status_code == 200
    assert listed.json()["items"][0]["ref"] == REF
    assert catalog_service.list.await_args.kwargs["scope"] == "tasks"
    assert catalog_service.list.await_args.kwargs["q"] == "требования"


async def test_text_paging_revision_errors_and_detach(api_client, catalog_service):
    response = await api_client.get(f"{BASE}/text", params={"offset": 4, "max_chars": 4})
    assert response.status_code == 200
    assert response.json()["offset_unit"] == "unicode_codepoints"
    assert response.json()["text"] == "😀 аб"
    updated = await api_client.patch(BASE, json={"expected_revision": 1, "caption": None})
    assert updated.status_code == 200
    assert "caption" in catalog_service.update.await_args.kwargs["data"].model_fields_set
    catalog_service.update.side_effect = CatalogDocumentConflictError()
    assert (
        await api_client.patch(BASE, json={"expected_revision": 1, "title": "Новое"})
    ).status_code == 409
    assert (await api_client.post(f"{BASE}/retry")).status_code == 202
    assert (await api_client.post(f"{BASE}/tasks/3")).status_code == 200
    assert (await api_client.delete(f"{BASE}/tasks/3")).status_code == 200
    catalog_service.delete.assert_not_awaited()
    assert (await api_client.delete(BASE)).status_code == 204
    catalog_service.get.side_effect = CatalogDocumentNotFoundError()
    assert (await api_client.get(BASE)).status_code == 404
    catalog_service.retry.side_effect = ApiKeyScopeError("tasks:write", [])
    assert (await api_client.post(f"{BASE}/retry")).status_code == 403


async def test_original_preview_headers_and_no_silent_page_truncation(
    api_client, catalog_service, tmp_path
):
    original = tmp_path / "sample.txt"
    original.write_bytes(b"data")
    catalog_service.prepare_download.return_value = AttachmentDownload(
        path=original, media_type="text/plain", filename="sample.txt", previewable=False
    )
    response = await api_client.get(f"{BASE}/content")
    assert response.status_code == 200 and response.content == b"data"
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-disposition"].startswith("attachment")
    response = await api_client.get(f"{BASE}/preview")
    assert response.status_code == 200
    assert response.headers["content-disposition"].startswith("inline")
    catalog_service.prepare_download.side_effect = CatalogDocumentNotFoundError()
    assert (await api_client.get(f"{BASE}/content")).status_code == 404
    for url in (
        "/api/v1/documents?limit=0",
        "/api/v1/documents?scope=owned",
        f"{BASE}/text?offset=-1",
        f"{BASE}/text?max_chars=8001",
    ):
        assert (await api_client.get(url)).status_code == 422
    assert (
        await api_client.post(
            "/api/v1/documents",
            json={
                "request_id": str(uuid4()),
                "project_id": 1,
                "owner_task_id": 2,
                "title": "Неверно",
            },
        )
    ).status_code == 422


async def test_old_channel_urls_are_not_aliases(api_client, catalog_service):
    for method, path in (
        ("GET", "/api/v1/projects/1/documents"),
        ("GET", "/api/v1/projects/1/files"),
        ("GET", "/api/v1/tasks/2/attachments"),
        ("POST", "/api/v1/tasks/2/documents/import"),
        ("POST", "/api/v1/document-links"),
        ("GET", "/api/v1/projects/1/files/search"),
        ("GET", "/api/v1/tasks/2/files/search"),
    ):
        assert (await api_client.request(method, path)).status_code == 404


async def test_credentials_reach_short_scope_auth_before_original(
    api_client, catalog_service, current_principal, tmp_path
):
    from src.core.settings import get_settings

    app.dependency_overrides.pop(get_document_catalog_principal)
    catalog_service.resolve_principal.return_value = current_principal
    path = tmp_path / "original.txt"
    path.write_bytes(b"data")
    catalog_service.prepare_download.return_value = AttachmentDownload(
        path=path, media_type="text/plain", filename="original.txt", previewable=False
    )
    api_client.cookies.set(get_settings().auth.session_cookie_name, "synthetic-session")
    response = await api_client.get(
        f"{BASE}/content", headers={"Authorization": "Bearer synthetic-bearer"}
    )
    assert response.status_code == 200
    catalog_service.resolve_principal.assert_awaited_once_with(
        session_token="synthetic-session", bearer_secret="synthetic-bearer"
    )
