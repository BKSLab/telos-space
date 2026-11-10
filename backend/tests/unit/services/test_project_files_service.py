"""Границы БД, компенсация оригинала и доступ к файлам проекта."""

import asyncio
import hashlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from src.db.models.api_tokens import ApiTokenScope
from src.exceptions.access import ResourceNotAvailableError, ResourceWriteRequiredError
from src.exceptions.api_keys import ApiKeyScopeError
from src.exceptions.auth import NotAuthenticatedError
from src.exceptions.project_files import (
    ProjectFileNotFoundError,
    ProjectFilesRepositoryError,
    ProjectFilesServiceError,
    ProjectFileTooLargeError,
    ProjectFileUnsupportedError,
    ProjectFileValidationError,
)
from src.exceptions.unit_of_work import UnitOfWorkRepositoryError
from src.repositories.knowledge_sources import KnowledgeSourcesRepository
from src.repositories.project_files import ProjectFilesRepository
from src.repositories.unit_of_work import UnitOfWork
from src.services.access import AccessGrant, AccessService
from src.services.auth import ApiKeyGrant, AuthService, Principal
from src.services.project_files import ProjectFilesScope, ProjectFilesService
from src.storage.task_attachments import TaskAttachmentStorage
from src.utils.api_scopes import SCOPE_PROJECTS_READ


def file_row(**changes: object) -> dict:
    """Возвращает данные оригинала без зависимости от обработки знаний."""
    return {
        "id": 7,
        "project_id": 2,
        "uploaded_by_user_id": 1,
        "original_name": "plan.txt",
        "storage_key": "projects/2/files/original.txt",
        "content_type": "text/plain",
        "content_hash": hashlib.sha256(b"plan").hexdigest(),
        "size": 4,
        "created_at": datetime(2026, 11, 6, tzinfo=UTC),
        **changes,
    }


def create_service(
    storage: TaskAttachmentStorage | None = None,
    *,
    max_file_size: int = 100,
    knowledge_enabled: bool = True,
) -> tuple[ProjectFilesService, ProjectFilesScope, list[str]]:
    """Собирает короткие области с явными дублёрами зависимостей."""
    db = ProjectFilesScope(
        files=AsyncMock(spec=ProjectFilesRepository),
        sources=AsyncMock(spec=KnowledgeSourcesRepository),
        access=AsyncMock(spec=AccessService),
        auth=AsyncMock(spec=AuthService),
        unit_of_work=AsyncMock(spec=UnitOfWork),
    )
    events: list[str] = []

    @asynccontextmanager
    async def scope() -> AsyncIterator[ProjectFilesScope]:
        events.append("open")
        try:
            yield db
        finally:
            events.append("closed")

    service = ProjectFilesService(
        scope=scope,
        storage=storage if storage is not None else AsyncMock(spec=TaskAttachmentStorage),
        max_file_size=max_file_size,
        knowledge_enabled=knowledge_enabled,
    )
    return service, db, events


async def test_upload_saves_original_outside_scope_before_metadata_and_returns_pending(
    tmp_path: Path,
) -> None:
    storage = AsyncMock(spec=TaskAttachmentStorage)
    disk = TaskAttachmentStorage(tmp_path)
    service, db, events = create_service(storage)

    async def save_original(directory: PurePosixPath, extension: str, content: bytes) -> str:
        assert events == ["open", "closed"]
        assert directory == PurePosixPath("projects/2/files")
        return await disk.save_in(directory, extension, content)

    async def save_metadata(data: dict) -> dict:
        assert disk.resolve(data["storage_key"]).read_bytes() == b"plan"
        assert data["content_hash"] == hashlib.sha256(b"plan").hexdigest()
        return file_row(**data)

    storage.save_in.side_effect = save_original
    db.files.save.side_effect = save_metadata

    result = await service.upload(
        project_id=2,
        user_id=1,
        file_name="folder\\PLAN.TXT",
        content=b"plan",
    )

    assert result.original_name == "PLAN.TXT"
    assert result.content_type == "text/plain"
    assert result.size == 4
    assert result.extraction_status == result.index_status == "pending"
    assert result.summary is None
    assert result.content_url == "/api/v1/projects/2/files/7/content"
    assert "storage_key" not in result.model_dump()
    assert db.access.ensure_project_access.await_count == 2
    assert events == ["open", "closed", "open", "closed"]
    db.unit_of_work.commit.assert_awaited_once()
    storage.delete_if_exists.assert_not_awaited()
    assert len(list(tmp_path.rglob("*.txt"))) == 1


async def test_upload_compensates_disk_after_database_failure_revocation_and_cancellation(
    tmp_path: Path,
) -> None:
    failures = [
        ("metadata", ProjectFilesRepositoryError("metadata failed"), ProjectFilesServiceError),
        ("commit", UnitOfWorkRepositoryError("commit failed"), ProjectFilesServiceError),
        (
            "access",
            ResourceNotAvailableError(resource="project", resource_id=2),
            ResourceNotAvailableError,
        ),
        ("cancelled", asyncio.CancelledError(), asyncio.CancelledError),
    ]
    for stage, failure, expected_error in failures:
        storage = TaskAttachmentStorage(tmp_path / stage)
        service, db, events = create_service(storage)
        db.files.save.return_value = file_row()
        if stage in {"metadata", "cancelled"}:
            db.files.save.side_effect = failure
        elif stage == "commit":
            db.unit_of_work.commit.side_effect = failure
        else:
            db.access.ensure_project_access.side_effect = [None, failure]

        with pytest.raises(expected_error) as caught:
            await service.upload(project_id=2, user_id=1, file_name="plan.txt", content=b"plan")

        assert events == ["open", "closed", "open", "closed"]
        assert not list((tmp_path / stage).rglob("*.txt")), stage
        if stage in {"metadata", "commit"}:
            assert type(caught.value) is ProjectFilesServiceError
            assert caught.value.status_code == 500
            assert caught.value.__cause__ is failure
        elif stage == "access":
            assert caught.value.status_code == 404
            db.files.save.assert_not_awaited()


async def test_invalid_upload_is_rejected_before_database_or_disk() -> None:
    service, db, events = create_service(max_file_size=4)
    cases = [
        ("plan.txt", b"12345", ProjectFileTooLargeError, 413),
        ("run.exe", b"x", ProjectFileUnsupportedError, 415),
        ("plan.txt", b"", ProjectFileValidationError, 422),
        (" ", b"x", ProjectFileValidationError, 422),
        ("folder/", b"x", ProjectFileValidationError, 422),
        ("bad\x00.txt", b"x", ProjectFileValidationError, 422),
        ("a" * 509 + ".txt", b"x", ProjectFileValidationError, 422),
    ]
    for name, content, expected_error, status in cases:
        with pytest.raises(expected_error) as caught:
            await service.upload(project_id=2, user_id=1, file_name=name, content=content)
        assert caught.value.status_code == status

    assert events == []
    service.storage.save_in.assert_not_awaited()
    db.files.save.assert_not_awaited()


async def test_download_resolves_disk_only_after_auth_access_and_scope_close(
    current_principal: Principal,
    tmp_path: Path,
) -> None:
    service, db, events = create_service()
    db.auth.resolve_principal.return_value = replace(
        current_principal, scope=ApiTokenScope.READ, via_api_token=True
    )
    db.files.get_by_id.return_value = file_row()
    target = tmp_path / "plan.txt"

    def resolve(storage_key: str) -> Path:
        assert storage_key == "projects/2/files/original.txt"
        assert events == ["open", "closed"]
        return target

    service.storage.resolve.side_effect = resolve

    result = await service.prepare_download(
        project_id=2, file_id=7, session_token="session", bearer_secret="read-token"
    )

    assert result.path == target
    assert result.filename == "plan.txt"
    assert result.previewable is False
    db.auth.resolve_principal.assert_awaited_once_with(
        session_token="session", bearer_secret="read-token"
    )
    db.access.ensure_project_access.assert_awaited_once_with(project_id=2, user_id=1)
    db.files.get_by_id.assert_awaited_once_with(project_id=2, file_id=7, max_chars=0)


async def test_download_checks_api_key_read_scope_and_current_access(
    current_principal: Principal,
) -> None:
    grant = ApiKeyGrant(
        key_id=UUID(int=1),
        label="file-reader",
        key_prefix="ttk_reader",
        scopes=frozenset({SCOPE_PROJECTS_READ}),
        owner_user_id=current_principal.user_id,
    )
    for allowed, scopes in [(True, grant.scopes), (False, frozenset({"tasks:read"}))]:
        service, db, events = create_service()
        db.auth.resolve_principal.return_value = replace(
            current_principal, api_key=replace(grant, scopes=scopes)
        )
        db.files.get_by_id.return_value = file_row()
        service.storage.resolve.return_value = Path("/original.txt")
        if allowed:
            await service.prepare_download(
                project_id=2, file_id=7, session_token=None, bearer_secret="ttk_reader"
            )
            db.access.ensure_project_access.assert_awaited_once_with(project_id=2, user_id=1)
        else:
            with pytest.raises(ApiKeyScopeError) as caught:
                await service.prepare_download(
                    project_id=2, file_id=7, session_token=None, bearer_secret="ttk_reader"
                )
            assert caught.value.status_code == 403
            db.access.ensure_project_access.assert_not_awaited()
            db.files.get_by_id.assert_not_awaited()
            service.storage.resolve.assert_not_called()
        assert events == ["open", "closed"]

    for stage, failure in [
        ("auth", NotAuthenticatedError()),
        ("access", ResourceNotAvailableError(resource="project", resource_id=2)),
        ("file", ProjectFileNotFoundError("7")),
    ]:
        service, db, events = create_service()
        db.auth.resolve_principal.return_value = current_principal
        if stage == "auth":
            db.auth.resolve_principal.side_effect = failure
        elif stage == "access":
            db.access.ensure_project_access.side_effect = failure
        else:
            db.files.get_by_id.return_value = None
        with pytest.raises(type(failure)) as caught:
            await service.prepare_download(
                project_id=2, file_id=7, session_token=None, bearer_secret=None
            )
        assert caught.value.status_code == (401 if stage == "auth" else 404)
        service.storage.resolve.assert_not_called()
        assert events == ["open", "closed"]


async def test_detail_reports_text_pagination_and_disabled_index_without_disk_reads() -> None:
    service, db, events = create_service(knowledge_enabled=False)
    for offset, text, total, next_offset in [
        (0, "abcd", 6, 4),
        (4, "ef", 6, None),
        (9, "", 6, None),
    ]:
        db.files.get_by_id.return_value = file_row(
            text=text, text_total_chars=total, extraction_status="ready", index_status="failed"
        )
        result = await service.get_file(
            project_id=2, file_id=7, user_id=1, offset=offset, max_chars=4
        )
        assert (result.text, result.text_total_chars, result.text_offset, result.next_offset) == (
            text,
            total,
            offset,
            next_offset,
        )
        assert result.index_status == "disabled"
        assert result.extraction_status == "ready"

    db.files.get_by_id.return_value = None
    with pytest.raises(ProjectFileNotFoundError) as caught:
        await service.get_file(project_id=2, file_id=7, user_id=1, offset=0, max_chars=4)
    assert caught.value.status_code == 404
    assert events.count("open") == events.count("closed") == 4
    service.storage.resolve.assert_not_called()


async def test_detail_exposes_author_and_current_write_access_without_allowing_readonly_mutations() -> None:
    service, db, _ = create_service()
    db.files.get_by_id.return_value = file_row(
        text="Текст", text_total_chars=5, uploaded_by_name="Иванов Иван Петрович"
    )
    db.access.require_write.side_effect = AccessService.require_write
    for allowed in (True, False):
        db.access.ensure_project_access.return_value = AccessGrant(
            project_id=2, resource_id=2, is_owner=False, can_write=allowed
        )
        result = await service.get_file(
            project_id=2, file_id=7, user_id=1, offset=0, max_chars=8000
        )
        assert result.uploaded_by_name == "Иванов Иван Петрович"
        assert result.can_edit is allowed
    db.files.reset_mock()
    for mutation in (
        lambda: service.update_caption(project_id=2, file_id=7, user_id=1, caption="Текст"),
        lambda: service.retry(project_id=2, file_id=7, user_id=1),
        lambda: service.delete_file(project_id=2, file_id=7, user_id=1),
    ):
        with pytest.raises(ResourceWriteRequiredError):
            await mutation()
    db.files.get_by_id.assert_not_awaited()
    db.unit_of_work.commit.assert_not_awaited()
