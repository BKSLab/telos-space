"""Проверка локального ключа или токена до открытия MCP-сессии."""

import logging
from collections.abc import Awaitable, Callable

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from src.utils.api_tokens import extract_bearer_secret

logger = logging.getLogger(__name__)
PUBLIC_PATHS = frozenset({"/health", "/health/live", "/health/ready"})
CredentialsCheck = Callable[[str | None, str | None], Awaitable[bool]]


class CredentialsMiddleware:
    def __init__(self, app: ASGIApp, *, check: CredentialsCheck) -> None:
        self.app, self.check = app, check

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path") in PUBLIC_PATHS:
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        bearer = extract_bearer_secret(request.headers.get("authorization"))
        key = (request.headers.get("x-api-key") or "").strip() or None
        try:
            allowed = bool(bearer or key) and await self.check(bearer, key)
        except Exception:
            logger.exception("MCP: проверка доступа временно недоступна.")
            await JSONResponse({"detail": "Сбой проверки доступа."}, status_code=503)(scope, receive, send)
            return
        if not allowed:
            await JSONResponse(
                {"detail": "Требуется действующий API-ключ или токен трекера."},
                status_code=401,
                headers={"WWW-Authenticate": 'Bearer realm="telos-space-mcp"'},
            )(scope, receive, send)
            return
        await self.app(scope, receive, send)
