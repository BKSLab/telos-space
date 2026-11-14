"""Настоящий HTTP/service/PostgreSQL/local storage: identity не равна текущему пользователю."""

from io import BytesIO
from uuid import uuid4

import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from PIL import Image
from sqlalchemy import update

from src.api.v1.endpoints.users import router
from src.db.models import ApiKey, ApiToken, ApiTokenScope, User
from src.dependencies.services import get_auth_service, get_users_service
from src.repositories.api_keys import ApiKeysRepository
from src.repositories.api_tokens import ApiTokensRepository
from src.repositories.users import UsersRepository
from src.services.auth import AuthService
from src.services.users import UsersService
from src.storage.avatars import AvatarStorage
from src.utils.api_keys import generate_api_key, hash_api_key
from src.utils.api_tokens import generate_token_secret, hash_token_secret
from src.utils.tokens import create_access_token


def png(color):
    stream = BytesIO()
    Image.new("RGB", (2, 2), color).save(stream, format="PNG")
    return stream.getvalue()


@pytest_asyncio.fixture
async def identity_api():
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield app, client


async def test_global_identity_avatar_and_working_user_search_with_real_auth(
    chat_env, tmp_path, identity_api
):
    app, api_client = identity_api
    storage = AvatarStorage(tmp_path / "identity-avatars")
    own_image, target_image = png("blue"), png("red")
    allowed_key, allowed_prefix = generate_api_key()
    denied_key, denied_prefix = generate_api_key()
    legacy = generate_token_secret()
    async with chat_env.factory() as db:
        users = UsersService(UsersRepository(db), storage)
        await users.set_avatar(user_id=1, content_type="image/png", content=own_image)
        await users.set_avatar(user_id=2, content_type="image/png", content=target_image)
        await db.execute(
            update(User).values(email="private@example.test", phone="private", telegram="private")
        )
        db.add_all(
            [
                ApiKey(
                    id=uuid4(),
                    label="Synthetic identity",
                    key_hash=hash_api_key(allowed_key),
                    key_prefix=allowed_prefix,
                    owner_user_id=3,
                    kind="personal",
                    scopes=["projects:read"],
                    is_active=True,
                ),
                ApiKey(
                    id=uuid4(),
                    label="Synthetic narrow",
                    key_hash=hash_api_key(denied_key),
                    key_prefix=denied_prefix,
                    owner_user_id=3,
                    kind="personal",
                    scopes=["tasks:read"],
                    is_active=True,
                ),
                ApiToken(
                    user_id=3,
                    name="Synthetic READ",
                    token_hash=hash_token_secret(legacy),
                    prefix=legacy[:8],
                    scope=ApiTokenScope.READ,
                ),
            ]
        )
        await db.commit()

    async def auth():
        async with chat_env.factory() as db:
            yield AuthService(
                UsersRepository(db),
                tokens_repository=ApiTokensRepository(db),
                api_keys_repository=ApiKeysRepository(db),
                invite_code="synthetic",
            )

    async def users_service():
        async with chat_env.factory() as db:
            yield UsersService(UsersRepository(db), storage)

    app.dependency_overrides.clear()
    app.dependency_overrides.update({get_auth_service: auth, get_users_service: users_service})
    assert (await api_client.get("/api/v1/users/2/avatar")).status_code == 401
    api_client.cookies.set("telos_session", create_access_token(1))
    own = await api_client.get("/api/v1/users/me/avatar")
    target = await api_client.get("/api/v1/users/2/avatar")
    assert own.status_code == target.status_code == 200
    assert (
        own.content == own_image
        and target.content == target_image
        and own.content != target.content
    )
    assert target.headers["cache-control"] == "private, no-cache, must-revalidate"
    assert target.headers["x-content-type-options"] == "nosniff"
    assert {value.strip().lower() for value in target.headers["vary"].split(",")} >= {
        "cookie",
        "authorization",
    }
    identities = await api_client.get("/api/v1/users", params={"query": "chatuser2", "limit": 1})
    assert identities.status_code == 200 and [item["id"] for item in identities.json()] == [2]
    assert set(identities.json()[0]) == {
        "id",
        "username",
        "last_name",
        "first_name",
        "middle_name",
        "has_avatar",
    }
    assert identities.json()[0]["has_avatar"]

    # Пользователь 3 не состоит в этих проектах; публичная identity доступна без членства.
    api_client.cookies.set("telos_session", create_access_token(3))
    assert (await api_client.get("/api/v1/users/2/avatar")).content == target_image
    for secret, expected in ((allowed_key, 200), (denied_key, 403), (legacy, 200)):
        headers = {"Authorization": f"Bearer {secret}"}
        avatar = await api_client.get("/api/v1/users/2/avatar", headers=headers)
        search = await api_client.get("/api/v1/users", headers=headers)
        assert avatar.status_code == search.status_code == expected
        if expected == 200:
            assert avatar.content == target_image
    assert (await api_client.get("/api/v1/users/3/avatar")).status_code == 404
    assert (await api_client.get("/api/v1/users/999999/avatar")).status_code == 404
    async with chat_env.factory() as db:
        await db.execute(update(User).where(User.id == 2).values(is_active=False))
        await db.commit()
    inactive = await api_client.get("/api/v1/users/2/avatar")
    assert inactive.status_code == 404
    assert (await api_client.get("/api/v1/users", params={"query": "chatuser2"})).json() == []
    async with chat_env.factory() as db:
        await db.execute(
            update(User).where(User.id == 2).values(is_active=True, avatar_key="2/missing.png")
        )
        await db.commit()
    assert (await api_client.get("/api/v1/users/2/avatar")).status_code == 404
    async with chat_env.factory() as db:
        await db.execute(update(User).where(User.id == 3).values(is_active=False))
        await db.commit()
    assert (await api_client.get("/api/v1/users/2/avatar")).status_code == 403
