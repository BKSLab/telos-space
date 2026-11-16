"""Composition Git-сервисов: отдельные HTTP credentials и короткие DB-области."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

import httpx
from fastapi import Depends, HTTPException, Request
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.clients.github import GitHubClient
from src.core.settings import Settings
from src.db.session import async_session_factory
from src.dependencies.context_auth import ContextAuthorizationHeaderDep, ContextSessionCookieDep
from src.dependencies.document_catalog import assemble_document_catalog_scope
from src.dependencies.settings import SettingsDep
from src.exceptions.auth import AuthServiceError
from src.repositories.git_sources import GitSourcesRepository
from src.repositories.projects import ProjectsRepository
from src.repositories.unit_of_work import UnitOfWork
from src.services.auth import Principal
from src.services.git_credentials import GitCredentialCodec
from src.services.git_sources import (
    GitSourceLimits,
    GitSourceScope,
    GitSourceScopeFactory,
    GitSourceService,
)
from src.utils.api_tokens import extract_bearer_secret
from src.utils.request_actor import set_request_actor


def build_git_source_scope(
    *, session_factory: async_sessionmaker[AsyncSession], settings: Settings
) -> GitSourceScopeFactory:
    @asynccontextmanager
    async def scope() -> AsyncIterator[GitSourceScope]:
        async with session_factory() as session:
            unit_of_work = UnitOfWork(session)
            common = assemble_document_catalog_scope(
                session=session, settings=settings, unit_of_work=unit_of_work
            )
            yield GitSourceScope(
                sources=GitSourcesRepository(session),
                access=common.access,
                auth=common.auth,
                projects=ProjectsRepository(session),
                tasks=common.tasks,
                knowledge_events=common.knowledge_events,
                unit_of_work=unit_of_work,
            )

    return scope


@asynccontextmanager
async def github_client_scope(credential: SecretStr | None) -> AsyncIterator[GitHubClient]:
    """Токен закреплён только за fixed GitHub API и не смешан с общим AI HTTP pool."""
    headers = {}
    if credential is not None:
        headers["Authorization"] = "Bearer " + credential.get_secret_value()
    async with httpx.AsyncClient(
        headers=headers,
        timeout=10.0,
        trust_env=False,
        follow_redirects=False,
        limits=httpx.Limits(max_connections=4, max_keepalive_connections=2),
    ) as http_client:
        yield GitHubClient(http_client=http_client, max_requests=4)


def build_git_source_service(
    *, session_factory: async_sessionmaker[AsyncSession], settings: Settings
) -> GitSourceService:
    return GitSourceService(
        scope=build_git_source_scope(session_factory=session_factory, settings=settings),
        client_factory=github_client_scope,
        credential_codec=GitCredentialCodec(settings.git.git_credentials_key),
        limits=GitSourceLimits(
            poll_interval_seconds=settings.git.git_poll_interval_seconds,
            batch_files=settings.git.git_sync_batch_files,
        ),
    )


def get_git_source_service(settings: SettingsDep) -> GitSourceService:
    return build_git_source_service(session_factory=async_session_factory, settings=settings)


GitSourceServiceDep = Annotated[GitSourceService, Depends(get_git_source_service)]


async def get_git_source_principal(
    service: GitSourceServiceDep,
    request: Request,
    session_cookie: ContextSessionCookieDep = None,
    authorization: ContextAuthorizationHeaderDep = None,
) -> Principal:
    """Auth закрывается до GitHub GET; scopes и owner проверяет доменный сервис."""
    try:
        principal = await service.resolve_principal(
            session_token=session_cookie, bearer_secret=extract_bearer_secret(authorization)
        )
        if principal.api_key is not None:
            request.state.api_key = principal.api_key
            request.state.api_key_actor_login = principal.username
        set_request_actor(principal.request_actor())
        return principal
    except AuthServiceError as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


GitSourcePrincipalDep = Annotated[Principal, Depends(get_git_source_principal)]
