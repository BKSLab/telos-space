"""Сборка приватной выдачи файлов в короткой области PostgreSQL."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.core.settings import Settings
from src.db.session import async_session_factory
from src.dependencies.settings import SettingsDep
from src.repositories.agent_conversations import AgentConversationsRepository
from src.repositories.agent_files import AgentFilesRepository
from src.repositories.api_keys import ApiKeysRepository
from src.repositories.api_tokens import ApiTokensRepository
from src.repositories.document_links import DocumentLinksRepository
from src.repositories.documents import DocumentsRepository
from src.repositories.project_members import ProjectMembersRepository
from src.repositories.project_stages import ProjectStagesRepository
from src.repositories.task_comments import TaskCommentsRepository
from src.repositories.tasks import TasksRepository
from src.repositories.users import UsersRepository
from src.services.access import AccessService
from src.services.agent_file_content import AgentFileContentService, AgentFileReadScope
from src.services.auth import AuthService
from src.storage.task_attachments import TaskAttachmentStorage


def build_agent_file_content_service(
    *, session_factory: async_sessionmaker, settings: Settings
) -> AgentFileContentService:
    """Отдельная фабрика позволяет проверять выдачу на пуле из одного соединения."""

    @asynccontextmanager
    async def scope() -> AsyncIterator[AgentFileReadScope]:
        async with session_factory() as session:
            yield AgentFileReadScope(
                auth=AuthService(
                    users_repository=UsersRepository(session),
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
                conversations=AgentConversationsRepository(session),
                files=AgentFilesRepository(session),
            )

    return AgentFileContentService(
        scope=scope, storage=TaskAttachmentStorage(settings.app.uploads_path / "agent")
    )


def get_agent_file_content_service(settings: SettingsDep) -> AgentFileContentService:
    """Не открывает request-scoped сессию."""
    return build_agent_file_content_service(
        session_factory=async_session_factory, settings=settings
    )


AgentFileContentServiceDep = Annotated[
    AgentFileContentService, Depends(get_agent_file_content_service)
]
