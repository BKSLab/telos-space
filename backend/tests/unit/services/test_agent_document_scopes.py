"""Документные действия сохраняют scopes владельца в live и receipt путях."""

from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.agent.action_scopes import tools_for_scopes
from src.agent.tools import AgentToolContext
from src.db.models.api_tokens import ApiTokenScope
from src.exceptions.api_keys import ApiKeyInvalidError, ApiKeyScopeError
from src.exceptions.auth import NotAuthenticatedError
from src.repositories.api_keys import ApiKeysRepository
from src.repositories.api_tokens import ApiTokensRepository
from src.repositories.users import UsersRepository
from src.schemas.project_tool_inputs import DocumentUpdateInput
from src.services.agent_actions import AgentActionsService
from src.services.agent_document_actions import update_document
from src.services.auth import ApiKeyGrant, AuthService
from src.services.document_catalog import DocumentCatalogService
from src.storage.task_attachments import TaskAttachmentStorage
from src.utils.document_refs import encode_document_ref


def scoped(principal, scopes):
    return replace(
        principal,
        via_api_token=True,
        scope=ApiTokenScope.WRITE,
        api_key=ApiKeyGrant(
            key_id=uuid4(),
            label="Synthetic",
            key_prefix="12345678",
            owner_user_id=principal.user_id,
            scopes=frozenset(scopes),
        ),
    )


async def test_background_principal_refresh_intersects_snapshot_and_rejects_changed_owner(
    current_principal,
):
    initial = scoped(current_principal, {"projects:read", "tasks:write"})
    users, keys = AsyncMock(spec=UsersRepository), AsyncMock(spec=ApiKeysRepository)
    users.get_by_id.return_value = SimpleNamespace(
        id=initial.user_id,
        username=initial.username,
        last_name=initial.last_name,
        first_name=initial.first_name,
        middle_name=initial.middle_name,
        is_active=True,
    )
    key = SimpleNamespace(
        id=initial.api_key.key_id,
        owner_user_id=initial.user_id,
        kind="personal",
        is_active=True,
        revoked_at=None,
        expires_at=None,
        scopes=["projects:read", "projects:write", "tasks:write"],
        label="Current",
        key_prefix="12345678",
    )
    keys.get_by_id.return_value = key
    auth = AuthService(
        users,
        tokens_repository=AsyncMock(spec=ApiTokensRepository),
        api_keys_repository=keys,
        invite_code="unused",
    )
    current = await auth.refresh_principal(initial)
    assert current.api_key.scopes == frozenset({"projects:read", "tasks:write"})
    assert not current.allows("projects:write")
    key.scopes = ["projects:read"]
    assert (await auth.refresh_principal(initial)).scope is ApiTokenScope.READ
    key.owner_user_id += 1
    with pytest.raises(ApiKeyInvalidError):
        await auth.refresh_principal(initial)
    key.owner_user_id = initial.user_id
    key.revoked_at = datetime.now(UTC)
    with pytest.raises(ApiKeyInvalidError):
        await auth.refresh_principal(initial)
    keys.touch_last_used.assert_not_awaited()


async def test_legacy_token_refresh_checks_identity_owner_and_current_scope_without_audit(
    current_principal,
):
    principal = replace(
        current_principal, via_api_token=True, api_token_id=12, scope=ApiTokenScope.WRITE
    )
    users, tokens = AsyncMock(spec=UsersRepository), AsyncMock(spec=ApiTokensRepository)
    users.get_by_id.return_value = SimpleNamespace(
        id=principal.user_id,
        username=principal.username,
        last_name=principal.last_name,
        first_name=principal.first_name,
        middle_name=principal.middle_name,
        is_active=True,
    )
    token = SimpleNamespace(id=12, user_id=principal.user_id, scope=ApiTokenScope.READ)
    tokens.get_active_by_id.return_value = token
    auth = AuthService(users, tokens_repository=tokens, invite_code="unused")
    current = await auth.refresh_principal(principal)
    assert current.scope is ApiTokenScope.READ and current.api_token_id == 12
    token.scope = ApiTokenScope.WRITE
    assert (
        await auth.refresh_principal(replace(principal, scope=ApiTokenScope.READ))
    ).scope is ApiTokenScope.READ
    token.user_id += 1
    with pytest.raises(NotAuthenticatedError):
        await auth.refresh_principal(principal)
    tokens.get_active_by_id.return_value = None
    with pytest.raises(NotAuthenticatedError):
        await auth.refresh_principal(principal)
    with pytest.raises(NotAuthenticatedError):
        await auth.refresh_principal(replace(principal, api_token_id=None))
    tokens.touch_last_used.assert_not_awaited()


@pytest.mark.parametrize(
    "owner_task_id,scopes,missing",
    [
        (7, {"projects:read", "projects:write"}, "tasks:write"),
        (None, {"projects:read", "tasks:write"}, "projects:write"),
    ],
)
async def test_handler_and_completed_receipt_apply_actual_owner_scopes(
    tmp_path, current_principal, owner_task_id, scopes, missing
):
    principal = scoped(current_principal, scopes)
    row = {
        "storage_kind": "document",
        "source_id": 9,
        "owner_task_id": owner_task_id,
        "project_id": 1,
    }
    catalog_db = SimpleNamespace(auth=AsyncMock(), catalog=AsyncMock())
    catalog_db.auth.refresh_principal.return_value = principal
    catalog_db.catalog.get.return_value = row

    @asynccontextmanager
    async def scope():
        yield catalog_db

    catalog = DocumentCatalogService(
        scope=scope,
        storage=TaskAttachmentStorage(tmp_path),
        knowledge_enabled=False,
        max_file_size=1024,
        embedding_signature="test",
        commit_writes=False,
    )
    db = SimpleNamespace(document_catalog=catalog)
    context = AgentToolContext(
        project_id=1, user_id=principal.user_id, can_write=True, principal=principal
    )
    ref = encode_document_ref("document", 9)
    parameters = DocumentUpdateInput(
        document_ref=ref, changes={"expected_revision": 1, "content_md": "Не должно сохраниться"}
    )
    assert "telos_update_document" in tools_for_scopes(
        list(scopes)
    )  # Только предварительный допуск.
    with pytest.raises(ApiKeyScopeError) as error:
        await update_document(db, context, parameters)
    assert missing in error.value.detail
    receipt = SimpleNamespace(status="completed", result={"document_ref": ref})
    with pytest.raises(ApiKeyScopeError):
        await AgentActionsService._require_document_receipt(
            db, context, SimpleNamespace(name="telos_update_document"), parameters, receipt
        )
    catalog_db.catalog.update.assert_not_awaited()
