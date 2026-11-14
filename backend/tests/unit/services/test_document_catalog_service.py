"""Повторная авторизация после IO и PG-lock не снимает транзакцию."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.db.models.api_tokens import ApiTokenScope
from src.exceptions.api_keys import ApiKeyInvalidError, ApiKeyScopeError
from src.exceptions.auth import AuthServiceError, InactiveUserError
from src.repositories.document_catalog import DocumentCatalogRepository
from src.repositories.file_processing_jobs import FileProcessingJobsRepository
from src.repositories.tasks import TasksRepository
from src.repositories.unit_of_work import UnitOfWork
from src.services.access import AccessGrant, AccessService
from src.services.auth import ApiKeyGrant, AuthService
from src.services.document_catalog import DocumentCatalogScope, DocumentCatalogService
from src.services.knowledge_events import KnowledgeEvents
from src.storage.task_attachments import TaskAttachmentStorage
from src.utils.document_refs import decode_document_ref, encode_document_ref


def test_document_ref_bounds_match_database_keys() -> None:
    """Подделанная ссылка не отправляет переполненное число в asyncpg как 500."""
    assert decode_document_ref(encode_document_ref("document", 2_147_483_647)) == (
        "document",
        2_147_483_647,
    )
    with pytest.raises(ValueError):
        decode_document_ref("d1_ZG9jdW1lbnQ6MjE0NzQ4MzY0OA")
    for source_id in (0, -1, 2_147_483_648, True):
        with pytest.raises(ValueError):
            encode_document_ref("attachment", source_id)


@pytest.mark.parametrize("change", ["key_revoked", "inactive_user", "reduced_scopes"])
async def test_upload_rechecks_credentials_after_storage_and_lock_before_writes(
    tmp_path, current_principal, change
):
    principal = replace(
        current_principal,
        via_api_token=True,
        api_key=ApiKeyGrant(
            key_id=uuid4(),
            label="Synthetic",
            key_prefix="12345678",
            scopes=frozenset({"projects:read", "projects:write"}),
            owner_user_id=current_principal.user_id,
        ),
    )
    db = DocumentCatalogScope(
        catalog=AsyncMock(spec=DocumentCatalogRepository),
        access=AsyncMock(spec=AccessService),
        auth=AsyncMock(spec=AuthService),
        tasks=AsyncMock(spec=TasksRepository),
        processing_jobs=AsyncMock(spec=FileProcessingJobsRepository),
        knowledge_events=AsyncMock(spec=KnowledgeEvents),
        unit_of_work=AsyncMock(spec=UnitOfWork),
    )
    db.access.ensure_project_access.return_value = AccessGrant(
        project_id=2, resource_id=2, is_owner=True
    )
    db.catalog.get_submission.return_value = None
    db.auth.resolve_principal.return_value = principal
    active_scopes = 0

    @asynccontextmanager
    async def scope():
        nonlocal active_scopes
        active_scopes += 1
        try:
            yield db
        finally:
            active_scopes -= 1

    storage = TaskAttachmentStorage(tmp_path)
    service = DocumentCatalogService(
        scope=scope,
        storage=storage,
        knowledge_enabled=False,
        max_file_size=1024,
        embedding_signature="test-model",
    )
    initial = await service.resolve_principal(
        session_token=None, bearer_secret="synthetic-test-credential"
    )
    started, release = asyncio.Event(), asyncio.Event()
    original_save = storage.save_in

    async def delayed_save(*args, **kwargs):
        assert active_scopes == 0
        key = await original_save(*args, **kwargs)
        started.set()
        await release.wait()
        return key

    storage.save_in = delayed_save
    running = asyncio.create_task(
        service.upload(
            user_id=initial.user_id,
            request_id=uuid4(),
            project_id=2,
            file_name="brief.txt",
            content_type="text/plain",
            content=b"brief",
            principal=initial,
        )
    )
    await started.wait()
    if change == "key_revoked":
        db.auth.resolve_principal.side_effect = ApiKeyInvalidError()
    elif change == "inactive_user":
        db.auth.resolve_principal.side_effect = InactiveUserError(user_id=initial.user_id)
    else:
        db.auth.resolve_principal.return_value = replace(
            principal,
            scope=ApiTokenScope.READ,
            api_key=replace(principal.api_key, scopes=frozenset({"projects:read"})),
        )
    release.set()
    with pytest.raises((AuthServiceError, ApiKeyScopeError)):
        await running
    db.catalog.lock_request.assert_awaited_once()
    db.catalog.create.assert_not_awaited()
    db.catalog.save_submission.assert_not_awaited()
    db.unit_of_work.commit.assert_not_awaited()
    assert not list(tmp_path.rglob("*.txt"))
    assert db.auth.resolve_principal.await_args.kwargs["record_usage"] is False


async def test_auth_recheck_does_not_audit_or_commit_inside_domain_transaction(current_principal):
    # Сам реальный AuthService пропускает audit-write при повторной проверке.
    from types import SimpleNamespace

    from src.repositories.api_keys import ApiKeysRepository
    from src.repositories.api_tokens import ApiTokensRepository
    from src.repositories.users import UsersRepository
    from src.utils.api_keys import generate_api_key, hash_api_key

    raw, prefix = generate_api_key()
    keys, users, tokens = (
        AsyncMock(spec=ApiKeysRepository),
        AsyncMock(spec=UsersRepository),
        AsyncMock(spec=ApiTokensRepository),
    )
    keys.get_active_by_prefix.return_value = SimpleNamespace(
        id=uuid4(),
        key_hash=hash_api_key(raw),
        key_prefix=prefix,
        kind="personal",
        owner_user_id=current_principal.user_id,
        expires_at=None,
        scopes=["projects:write"],
        label="Synthetic",
    )
    users.get_by_id.return_value = SimpleNamespace(
        id=current_principal.user_id,
        username="tester",
        first_name="Тест",
        last_name="Тестов",
        middle_name=None,
        is_active=True,
    )
    auth = AuthService(
        users_repository=users,
        tokens_repository=tokens,
        api_keys_repository=keys,
        invite_code="synthetic",
    )
    result = await auth.resolve_principal(session_token=None, bearer_secret=raw, record_usage=False)
    assert result.allows("projects:write")
    keys.touch_last_used.assert_not_awaited()
    tokens.touch_last_used.assert_not_awaited()
