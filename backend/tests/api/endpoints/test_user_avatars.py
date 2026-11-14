"""Публичная локальная identity защищена входом и тем же scope, что каталог users."""

from dataclasses import replace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.api.v1.endpoints.users import router
from src.db.models.api_tokens import ApiTokenScope
from src.dependencies.auth import get_principal
from src.dependencies.services import get_auth_service, get_users_service
from src.exceptions.auth import InactiveUserError, NotAuthenticatedError
from src.services.auth import ApiKeyGrant, AuthService
from src.services.users import UsersService


@pytest.fixture
def identity_app(current_principal):
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_principal] = lambda: current_principal
    return app


@pytest_asyncio.fixture
async def identity_client(identity_app):
    async with AsyncClient(
        transport=ASGITransport(app=identity_app), base_url="http://test"
    ) as client:
        yield client


async def test_user_avatar_returns_requested_identity_and_private_revalidating_image(
    identity_client, identity_app
):
    service = AsyncMock(spec=UsersService)
    service.get_identity_avatar.return_value = (b"photo-user-2", "image/png")
    identity_app.dependency_overrides[get_users_service] = lambda: service
    response = await identity_client.get("/api/v1/users/2/avatar")
    assert response.status_code == 200 and response.content == b"photo-user-2"
    assert response.headers["content-type"] == "image/png"
    assert response.headers["cache-control"] == "private, no-cache, must-revalidate"
    assert {part.strip().lower() for part in response.headers["vary"].split(",")} >= {
        "cookie",
        "authorization",
    }
    assert response.headers["x-content-type-options"] == "nosniff"
    service.get_identity_avatar.assert_awaited_once_with(user_id=2)
    assert (await identity_client.get("/api/v1/users/0/avatar")).status_code == 422


async def test_avatar_auth_and_api_key_read_scope_match_user_catalog(
    identity_client, identity_app, current_principal
):
    service = AsyncMock(spec=UsersService)
    service.get_identity_avatar.return_value = (b"photo", "image/webp")
    service.search_users.return_value = []
    auth = AsyncMock(spec=AuthService)
    identity_app.dependency_overrides.pop(get_principal)
    identity_app.dependency_overrides.update(
        {get_auth_service: lambda: auth, get_users_service: lambda: service}
    )
    for error, expected in ((NotAuthenticatedError(), 401), (InactiveUserError(1), 403)):
        auth.resolve_principal.side_effect = error
        assert (await identity_client.get("/api/v1/users/2/avatar")).status_code == expected
    service.get_identity_avatar.assert_not_awaited()
    auth.resolve_principal.side_effect = None
    key = ApiKeyGrant(
        key_id=uuid4(),
        label="Synthetic",
        key_prefix="12345678",
        scopes=frozenset({"projects:read"}),
        owner_user_id=1,
    )
    for grant, expected in (
        (key, 200),
        (replace(key, scopes=frozenset({"tasks:read"})), 403),
        (None, 200),
    ):
        auth.resolve_principal.return_value = replace(
            current_principal, scope=ApiTokenScope.READ, via_api_token=True, api_key=grant
        )
        assert (await identity_client.get("/api/v1/users/2/avatar")).status_code == expected
        assert (await identity_client.get("/api/v1/users")).status_code == expected
    assert service.get_identity_avatar.await_count == 2
