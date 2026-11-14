"""Композиция общей библиотеки документов и короткой аутентификации."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Cookie, Depends, Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.core.settings import Settings, get_settings
from src.db.session import async_session_factory
from src.dependencies.settings import SettingsDep
from src.dependencies.storage import TaskAttachmentStorageDep
from src.exceptions.api_tokens import InsufficientTokenScopeError
from src.exceptions.auth import AuthServiceError
from src.knowledge.catalog import digest
from src.repositories.api_keys import ApiKeysRepository
from src.repositories.api_tokens import ApiTokensRepository
from src.repositories.document_catalog import DocumentCatalogRepository
from src.repositories.document_links import DocumentLinksRepository
from src.repositories.documents import DocumentsRepository
from src.repositories.file_processing_jobs import FileProcessingJobsRepository
from src.repositories.knowledge_index_jobs import KnowledgeIndexJobsRepository
from src.repositories.project_members import ProjectMembersRepository
from src.repositories.project_stages import ProjectStagesRepository
from src.repositories.task_comments import TaskCommentsRepository
from src.repositories.tasks import TasksRepository
from src.repositories.unit_of_work import UnitOfWork
from src.repositories.users import UsersRepository
from src.services.access import AccessService
from src.services.auth import AuthService, Principal
from src.services.document_catalog import (
    DocumentCatalogScope,
    DocumentCatalogScopeFactory,
    DocumentCatalogService,
)
from src.services.knowledge_events import KnowledgeEvents
from src.storage.task_attachments import TaskAttachmentStorage
from src.utils.api_tokens import extract_bearer_secret
from src.utils.request_actor import set_request_actor

CatalogSessionCookieDep = Annotated[
    str | None, Cookie(alias=get_settings().auth.session_cookie_name)
]
CatalogAuthorizationHeaderDep = Annotated[str | None, Header(alias="Authorization")]


def assemble_document_catalog_scope(
    *, session: AsyncSession, settings: Settings, unit_of_work: UnitOfWork
) -> DocumentCatalogScope:
    """Собирает доменные зависимости одной общей транзакции."""
    tasks = TasksRepository(session)
    return DocumentCatalogScope(
        catalog=DocumentCatalogRepository(session),
        access=AccessService(
            members_repository=ProjectMembersRepository(session),
            tasks_repository=tasks,
            stages_repository=ProjectStagesRepository(session),
            documents_repository=DocumentsRepository(session),
            comments_repository=TaskCommentsRepository(session),
            links_repository=DocumentLinksRepository(session),
        ),
        auth=AuthService(
            users_repository=UsersRepository(session),
            tokens_repository=ApiTokensRepository(session),
            api_keys_repository=ApiKeysRepository(session),
            invite_code=settings.auth.registration_invite_code.get_secret_value(),
        ),
        tasks=tasks,
        processing_jobs=FileProcessingJobsRepository(session),
        knowledge_events=KnowledgeEvents(
            KnowledgeIndexJobsRepository(session), enabled=settings.knowledge.knowledge_enabled
        ),
        unit_of_work=unit_of_work,
    )


def build_document_catalog_scope(
    *, session_factory: async_sessionmaker[AsyncSession], settings: Settings
) -> DocumentCatalogScopeFactory:
    """Закрывает сессию до файлового ввода-вывода и передачи ответа."""

    @asynccontextmanager
    async def scope() -> AsyncIterator[DocumentCatalogScope]:
        async with session_factory() as session:
            yield assemble_document_catalog_scope(
                session=session, settings=settings, unit_of_work=UnitOfWork(session)
            )

    return scope


def build_bound_document_catalog_service(
    *,
    session: AsyncSession,
    settings: Settings,
    storage: TaskAttachmentStorage,
    unit_of_work: UnitOfWork,
) -> DocumentCatalogService:
    """Присоединяет сценарий к транзакции WriteGate без внутреннего commit."""

    @asynccontextmanager
    async def scope() -> AsyncIterator[DocumentCatalogScope]:
        yield assemble_document_catalog_scope(
            session=session, settings=settings, unit_of_work=unit_of_work
        )

    return DocumentCatalogService(
        scope=scope,
        storage=storage,
        knowledge_enabled=settings.knowledge.knowledge_enabled,
        max_file_size=settings.app.project_file_max_size,
        embedding_signature=digest(
            [settings.embedding.embedding_model, settings.embedding.embedding_dim]
        ),
        commit_writes=False,
    )


def get_document_catalog_service(
    settings: SettingsDep, storage: TaskAttachmentStorageDep
) -> DocumentCatalogService:
    """Возвращает сервис, не удерживающий request-scoped соединение."""
    return DocumentCatalogService(
        scope=build_document_catalog_scope(
            session_factory=async_session_factory, settings=settings
        ),
        storage=storage,
        knowledge_enabled=settings.knowledge.knowledge_enabled,
        max_file_size=settings.app.project_file_max_size,
        embedding_signature=digest(
            [settings.embedding.embedding_model, settings.embedding.embedding_dim]
        ),
    )


DocumentCatalogServiceDep = Annotated[DocumentCatalogService, Depends(get_document_catalog_service)]


async def get_document_catalog_principal(
    service: DocumentCatalogServiceDep,
    request: Request,
    session_cookie: CatalogSessionCookieDep = None,
    authorization: CatalogAuthorizationHeaderDep = None,
) -> Principal:
    """Аутентифицирует в короткой области; предметный scope проверяет сервис."""
    try:
        principal = await service.resolve_principal(
            session_token=session_cookie, bearer_secret=extract_bearer_secret(authorization)
        )
        if request.method not in {"GET", "HEAD", "OPTIONS"} and principal.api_key is None and not principal.can_write:
            raise InsufficientTokenScopeError()
        if principal.api_key is not None:
            request.state.api_key = principal.api_key
            request.state.api_key_actor_login = principal.username
        set_request_actor(principal.request_actor())
        return principal
    except (AuthServiceError, InsufficientTokenScopeError) as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


DocumentCatalogPrincipalDep = Annotated[Principal, Depends(get_document_catalog_principal)]
