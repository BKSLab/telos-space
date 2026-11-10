"""Новые ключи не открывают управление ключами и чужие scopes."""

from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from main import app
from src.db.models.api_tokens import ApiTokenScope
from src.dependencies.auth import get_principal
from src.dependencies.services import get_api_keys_service
from src.schemas.api_keys import ApiKeyCreatedSchema
from src.services.api_keys import ApiKeysService
from src.services.auth import ApiKeyGrant
from src.utils.request_actor import reset_request_actor, set_request_actor


async def test_key_creation_contract_uses_server_owner_and_rejects_removed_kind(api_client):
    """Владелец определяется сессией, прежний выбор вида ключа отклоняется."""
    service = AsyncMock(spec=ApiKeysService)
    now = datetime.now(UTC)
    key_id = uuid4()
    service.create_personal_key.return_value = ApiKeyCreatedSchema(
        id=key_id, label="Мой клиент", key_prefix="12345678", is_active=True,
        owner_user_id=1, scopes=["tasks:read"], created_at=now, updated_at=now,
        raw_key="telos_key_12345678_11111111111111111111111111111111",
    )
    app.dependency_overrides[get_api_keys_service] = lambda: service
    path = "/api/v1/users/me/api-keys"
    response = await api_client.post(path, json={"label": "Мой клиент", "scopes": ["tasks:read"]})
    assert response.status_code == 201 and "kind" not in response.json()
    assert response.json()["raw_key"].startswith("telos_key_")
    assert service.create_personal_key.await_args.kwargs["owner_user_id"] == 1
    for method, suffix in (("POST", ""), ("PATCH", f"/{key_id}")):
        for removed_field in ({"kind": "service"}, {"owner_user_id": 2}):
            response = await api_client.request(
                method, path + suffix, json={"label": "Мой клиент", **removed_field},
            )
            assert response.status_code == 422
    assert service.create_personal_key.await_count == 1
    service.update_personal_key.assert_not_awaited()


def key_principal(principal, scopes):
    return replace(principal, via_api_token=True, scope=ApiTokenScope.WRITE,
        api_key=ApiKeyGrant(key_id=uuid4(), label="Мой клиент", key_prefix="12345678",
            owner_user_id=principal.user_id, scopes=frozenset(scopes)))


@pytest.mark.parametrize("new_key", [False, True])
@pytest.mark.parametrize("method,suffix", [("get", ""), ("get", "/scopes"), ("post", ""), ("patch", "/00000000-0000-4000-8000-000000000001")])
async def test_both_token_formats_cannot_manage_keys(api_client, current_principal, method, suffix, new_key):
    service = AsyncMock(spec=ApiKeysService)
    principal = key_principal(current_principal, {"projects:write", "tasks:write"}) if new_key else replace(current_principal, via_api_token=True)
    app.dependency_overrides[get_principal] = lambda: principal
    app.dependency_overrides[get_api_keys_service] = lambda: service
    response = await getattr(api_client, method)("/api/v1/users/me/api-keys" + suffix,
        **({"json": {"label": "Ключ", "is_active": False}} if method in {"post", "patch"} else {}))
    assert response.status_code == 403
    service.list_personal_keys.assert_not_awaited()
    service.create_personal_key.assert_not_awaited()
    service.update_personal_key.assert_not_awaited()


@pytest.mark.parametrize("scopes,allowed", [({"boards:write"}, False), ({"projects:write"}, False), ({"boards:write", "projects:write"}, True)])
async def test_promoting_board_requires_both_board_and_project_permissions(current_principal, scopes, allowed):
    principal = key_principal(current_principal, scopes)
    auth = AsyncMock()
    auth.resolve_principal.return_value = principal
    path = "/api/v1/boards/{board_id}/create-project"
    request = Request({"type": "http", "method": "POST", "path": "/api/v1/boards/1/create-project",
        "headers": [], "route": SimpleNamespace(path=path)})
    token = set_request_actor(None)
    try:
        if allowed:
            assert await get_principal(auth, authorization="Bearer telos_key_12345678_11111111111111111111111111111111", request=request) is principal
        else:
            with pytest.raises(HTTPException) as error:
                await get_principal(auth, authorization="Bearer telos_key_12345678_11111111111111111111111111111111", request=request)
            assert error.value.status_code == 403
    finally:
        reset_request_actor(token)
