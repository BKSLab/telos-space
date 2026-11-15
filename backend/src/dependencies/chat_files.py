"""Сборка чтения файлов чата: DB-сессия не живёт вместе с vision или parser."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.clients.vision import VisionCapability
from src.core.settings import Settings
from src.repositories.agent_chat_files import AgentChatFilesRepository
from src.repositories.api_keys import ApiKeysRepository
from src.repositories.api_tokens import ApiTokensRepository
from src.repositories.chat_file_texts import ChatFileTextsRepository
from src.repositories.document_links import DocumentLinksRepository
from src.repositories.documents import DocumentsRepository
from src.repositories.project_members import ProjectMembersRepository
from src.repositories.project_stages import ProjectStagesRepository
from src.repositories.task_comments import TaskCommentsRepository
from src.repositories.tasks import TasksRepository
from src.repositories.unit_of_work import UnitOfWork
from src.repositories.users import UsersRepository
from src.services.access import AccessService
from src.services.agent_chat_files import (
    AgentChatFilesScope,
    AgentChatFilesScopeFactory,
    AgentChatFilesService,
)
from src.services.auth import AuthService
from src.storage.task_attachments import TaskAttachmentStorage


def _build_chat_files_scope(*, session: AsyncSession, settings: Settings) -> AgentChatFilesScope:
    """Одна SQL-сессия для всех свежих проверок пользователя, контекста и оригинала."""
    users = UsersRepository(session)
    return AgentChatFilesScope(
        files=AgentChatFilesRepository(session),
        texts=ChatFileTextsRepository(session),
        users=users,
        auth=AuthService(
            users_repository=users,
            tokens_repository=ApiTokensRepository(session),
            api_keys_repository=ApiKeysRepository(session),
            invite_code=settings.auth.registration_invite_code.get_secret_value(),
        ),
        access=AccessService(
            members_repository=ProjectMembersRepository(session),
            tasks_repository=TasksRepository(session),
            stages_repository=ProjectStagesRepository(session),
            documents_repository=DocumentsRepository(session),
            comments_repository=TaskCommentsRepository(session),
            links_repository=DocumentLinksRepository(session),
        ),
        unit_of_work=UnitOfWork(session),
    )


def build_agent_chat_files_scope(
    *, session_factory: async_sessionmaker[AsyncSession], settings: Settings
) -> AgentChatFilesScopeFactory:
    """Каждый вызов открывает отдельную короткую область авторизации/кэша."""

    @asynccontextmanager
    async def scope() -> AsyncIterator[AgentChatFilesScope]:
        async with session_factory() as session:
            yield _build_chat_files_scope(session=session, settings=settings)

    return scope


def bind_agent_chat_files_service(
    source: AgentChatFilesService, *, session: AsyncSession, settings: Settings
) -> AgentChatFilesService:
    """Сохраняет весь reader-путь внутри UoW записи, не занимая второе соединение."""

    @asynccontextmanager
    async def scope() -> AsyncIterator[AgentChatFilesScope]:
        yield _build_chat_files_scope(session=session, settings=settings)

    return AgentChatFilesService(
        scope=scope,
        agent_storage=source.agent_storage,
        chat_storage=source.chat_storage,
        vision=source.vision,
        max_text_chars=source.max_text_chars,
        lease_seconds=source.lease_seconds,
        poll_seconds=source.poll_seconds,
        wait_seconds=source.wait_seconds,
    )


def build_agent_chat_files_service(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    vision: VisionCapability,
) -> AgentChatFilesService:
    """Runtime и инструменты получают один контракт, без request-scoped repositories."""
    return AgentChatFilesService(
        scope=build_agent_chat_files_scope(session_factory=session_factory, settings=settings),
        agent_storage=TaskAttachmentStorage(settings.app.uploads_path / "agent"),
        chat_storage=TaskAttachmentStorage(settings.app.uploads_path / "chat"),
        vision=vision,
        max_text_chars=settings.app.chat_file_text_max_chars,
        lease_seconds=settings.app.chat_file_text_lease_seconds,
        poll_seconds=settings.app.chat_file_text_poll_seconds,
        wait_seconds=settings.app.chat_file_text_wait_seconds,
    )
