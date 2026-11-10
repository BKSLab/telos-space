"""HTTP-контракт, настоящая авторизация и приватная выдача файлов чата."""

import asyncio
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from PIL import Image
from sqlalchemy import delete

from src.api.v1.endpoints.project_chats import download_router, router
from src.core.settings import get_settings
from src.db.models import ApiToken, ProjectMember
from src.db.models.api_tokens import ApiTokenScope
from src.dependencies.chat import get_chat_runtime
from src.services.chat_commands import ChatCommandsService
from src.utils.api_tokens import build_display_prefix, generate_token_secret, hash_token_secret
from src.utils.tokens import create_access_token


def credentials(user_id):
    return {"cookie": f"{get_settings().auth.session_cookie_name}={create_access_token(user_id)}"}


@pytest_asyncio.fixture
async def chat_http(chat_env):
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.include_router(download_router, prefix="/api/v1")
    runtime = SimpleNamespace(
        chat=chat_env.service,
        files=chat_env.files,
        commands=ChatCommandsService(
            chat_env.service,
            SimpleNamespace(rate_limit=AsyncMock()),
            send_limit=30,
            action_limit=120,
        ),
    )
    app.dependency_overrides[get_chat_runtime] = lambda: runtime

    async def transport(scope, receive, send):
        async def checked_send(event):
            if scope["path"].endswith(("/content", "/preview")) and event["type"] == "http.response.body":
                assert chat_env.engine.sync_engine.pool.checkedout() == 0
            await send(event)

        await app(scope, receive, checked_send)

    async with AsyncClient(
        transport=ASGITransport(app=transport), base_url="http://testserver"
    ) as client:
        yield client


async def test_http_auth_permissions_validation_and_concurrent_requests(chat_http, chat_env):
    path = "/api/v1/projects/1/chat"
    assert (await chat_http.get(path)).status_code == 401
    assert (await chat_http.get(path, headers=credentials(3))).status_code == 404
    secret = generate_token_secret()
    async with chat_env.factory() as db:
        db.add(
            ApiToken(
                user_id=1,
                name="Чтение",
                token_hash=hash_token_secret(secret),
                prefix=build_display_prefix(secret),
                scope=ApiTokenScope.READ,
            )
        )
        await db.commit()
    reader = {"Authorization": f"Bearer {secret}"}
    assert (await chat_http.get(path, headers=reader)).status_code == 200
    assert (
        await chat_http.post(f"{path}/entities/resolve", json=[], headers=reader)
    ).status_code == 200
    body = {"content": "Первое сообщение", "client_message_id": str(uuid4())}
    assert (await chat_http.post(f"{path}/messages", json=body, headers=reader)).status_code == 403
    assert (
        await chat_http.post(
            f"{path}/messages", json={**body, "content": ""}, headers=credentials(1)
        )
    ).status_code == 422
    # 12 конкурентных HTTP-сценариев на пуле из двух соединений: guard не
    # удерживает первое соединение в ожидании второго для ChatService.
    responses = await asyncio.wait_for(
        asyncio.gather(
            *(
                chat_http.post(
                    f"{path}/messages",
                    json={**body, "client_message_id": str(uuid4())},
                    headers=credentials(1),
                )
                for _ in range(12)
            )
        ),
        15,
    )
    assert all(response.status_code == 201 for response in responses)
    assert chat_env.engine.sync_engine.pool.checkedout() == 0


async def test_http_files_are_private_and_download_releases_database(chat_http, chat_env):
    path = "/api/v1/projects/1/chat"
    uploaded = await chat_http.post(
        f"{path}/attachments",
        files={"file": ("../решение.txt", b"approved", "image/png")},
        headers=credentials(1),
    )
    assert uploaded.status_code == 201
    metadata = uploaded.json()
    assert metadata["original_name"] == "решение.txt" and metadata["content_type"] == "text/plain"
    assert "storage_key" not in metadata
    download = f"{path}/attachments/{metadata['id']}/content"
    assert (await chat_http.get(download)).status_code == 401
    assert (await chat_http.get(download, headers=credentials(2))).status_code == 404
    assert (
        await chat_http.get(
            download.replace("/projects/1/", "/projects/2/"), headers=credentials(1)
        )
    ).status_code == 404
    sent = await chat_http.post(
        f"{path}/messages",
        json={"client_message_id": str(uuid4()), "attachment_ids": [metadata["id"]]},
        headers=credentials(1),
    )
    assert sent.status_code == 201
    response = await chat_http.get(f"{download}?inline=true", headers=credentials(2))
    assert response.content == b"approved"
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-disposition"].startswith("attachment;")
    async with chat_env.factory() as db:
        await db.execute(
            delete(ProjectMember).where(ProjectMember.project_id == 1, ProjectMember.user_id == 2)
        )
        await db.commit()
    assert (await chat_http.get(download, headers=credentials(2))).status_code == 404
    result = await chat_http.request(
        "DELETE",
        f"{path}/messages/{sent.json()['id']}",
        json={"expected_revision": 1},
        headers=credentials(1),
    )
    assert result.status_code == 200
    assert (await chat_http.get(download, headers=credentials(1))).status_code == 404
    invalid = await chat_http.post(
        f"{path}/attachments",
        files={"file": ("fake.png", b"<script>bad</script>", "image/png")},
        headers=credentials(1),
    )
    assert invalid.status_code == 422


async def test_http_image_previews_keep_draft_and_message_permissions(chat_http, chat_env, monkeypatch):
    """Локальный preview использует права оригинала и освобождает пул до decode."""
    content = BytesIO()
    Image.new("RGB", (1200, 800), "navy").save(content, format="PNG")
    prepare = chat_env.files.storage.prepare_preview

    async def checked_preview(key):
        assert chat_env.engine.pool.checkedout() == 0
        return await prepare(key)

    monkeypatch.setattr(chat_env.files.storage, "prepare_preview", checked_preview)
    path = "/api/v1/projects/1/chat"
    uploaded = await chat_http.post(
        f"{path}/attachments",
        files={"file": ("Фото.png", content.getvalue(), "image/png")},
        headers=credentials(1),
    )
    assert uploaded.status_code == 201
    file_id = uploaded.json()["id"]
    preview = f"{path}/attachments/{file_id}/preview"
    assert (await chat_http.get(preview)).status_code == 401
    assert (await chat_http.get(preview, headers=credentials(2))).status_code == 404
    shown = await chat_http.get(preview, headers=credentials(1))
    assert shown.status_code == 200
    assert shown.headers["content-type"] == "image/jpeg"
    with Image.open(BytesIO(shown.content)) as decoded:
        assert max(decoded.size) <= 640
    sent = await chat_http.post(
        f"{path}/messages", headers=credentials(1),
        json={"client_message_id": str(uuid4()), "attachment_ids": [file_id]},
    )
    assert sent.status_code == 201
    assert (await chat_http.get(preview, headers=credentials(2))).status_code == 200
    deleted = await chat_http.request(
        "DELETE", f"{path}/messages/{sent.json()['id']}",
        json={"expected_revision": 1}, headers=credentials(1),
    )
    assert deleted.status_code == 200
    assert (await chat_http.get(preview, headers=credentials(1))).status_code == 404
