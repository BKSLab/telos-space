"""Самостоятельное ASGI-приложение MCP с локальными проверками состояния."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from src.core.app_state import RUNTIME_STATE_KEY, SESSION_FACTORY_STATE_KEY, SETTINGS_STATE_KEY
from src.core.settings import Settings
from src.exceptions.auth import AuthServiceError
from src.knowledge.runtime import (
    build_knowledge_runtime,
    close_knowledge_runtime,
    create_http_client,
    create_qdrant_client,
)
from src.mcp_server.auth import CredentialsMiddleware
from src.mcp_server.registry import TOOL_REGISTRY
from src.mcp_server.server import build_mcp_app, mcp_server
from src.mcp_server.services import build_auth_service

logger = logging.getLogger(__name__)


def build_credentials_check(session_factory, settings: Settings):
    async def check(bearer: str) -> bool:
        async with session_factory() as session:
            try:
                await build_auth_service(session, settings).resolve_principal(
                    session_token=None, bearer_secret=bearer,
                )
            except AuthServiceError as error:
                if error.status_code >= 500:
                    raise
                return False
        return True
    return check


def create_app(*, settings: Settings, session_factory, engine=None) -> Starlette:
    transport = build_mcp_app(settings=settings)

    @asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncIterator[dict]:
        runtime = build_knowledge_runtime(
            settings=settings,
            http_client=create_http_client(settings),
            qdrant_client=create_qdrant_client(settings),
        )
        try:
            async with mcp_server.session_manager.run():
                yield {
                    RUNTIME_STATE_KEY: runtime,
                    SETTINGS_STATE_KEY: settings,
                    SESSION_FACTORY_STATE_KEY: session_factory,
                }
        finally:
            await close_knowledge_runtime(runtime)
            if engine is not None:
                await engine.dispose()

    async def health(_request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "server": "telos-space-mcp", "tools": len(TOOL_REGISTRY)})

    async def ready(_request: Request) -> JSONResponse:
        try:
            async with session_factory() as session:
                await session.execute(text("SELECT 1"))
        except Exception:
            logger.warning("MCP: база данных недоступна.", exc_info=True)
            return JSONResponse({"status": "unavailable"}, status_code=503)
        return JSONResponse({"status": "ready"})

    app = Starlette(routes=[
        Route("/health", health), Route("/health/live", health), Route("/health/ready", ready),
        Mount(settings.app.mcp_path, app=transport),
    ], lifespan=lifespan)
    app.add_middleware(CredentialsMiddleware, check=build_credentials_check(session_factory, settings))
    return app
