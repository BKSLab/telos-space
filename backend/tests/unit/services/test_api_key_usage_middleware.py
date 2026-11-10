"""Audit records local ownership and hides query secrets after a REST response."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from src.api.middleware.api_key_usage import ApiKeyUsageMiddleware
from src.services.api_key_usage import ApiKeyUsageService
from src.services.auth import ApiKeyGrant


@pytest.mark.parametrize("status", [200, 403])
async def test_audit_keeps_local_actor_and_status_without_query_secrets(status):
    key = ApiKeyGrant(
        key_id=uuid4(), label="Local integration", key_prefix="telos_key_local",
        scopes=frozenset(), owner_user_id=1,
    )

    async def handler(request):
        request.state.api_key = key
        request.state.api_key_actor_login = "local_owner"
        return JSONResponse({"ok": status == 200}, status_code=status)

    repository = SimpleNamespace(add=AsyncMock())
    app = ApiKeyUsageMiddleware(
        Starlette(routes=[Route("/api/v1/example", handler)]),
        recorder=ApiKeyUsageService(repository).record,
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/example", params={"token": "private", "q": "task"})
    assert response.status_code == status
    await asyncio.gather(*app._pending)
    record = repository.add.call_args.kwargs["data"]
    assert record["actor_login"] == "local_owner"
    assert record["status_code"] == status
    assert record["query"] == {"token": "***", "q": "task"}
    assert "private" not in repr(record)
