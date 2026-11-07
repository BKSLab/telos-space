"""Сборка коротких DB-областей для отправки и миниатюр локальных файлов."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.core.settings import Settings
from src.db.session import async_session_factory
from src.dependencies.settings import SettingsDep
from src.dependencies.storage import TaskAttachmentStorageDep
from src.repositories.api_keys import ApiKeysRepository
from src.repositories.api_tokens import ApiTokensRepository
from src.repositories.document_links import DocumentLinksRepository
from src.repositories.documents import DocumentsRepository
from src.repositories.file_comment_submissions import FileCommentSubmissionsRepository
from src.repositories.file_processing_jobs import FileProcessingJobsRepository
from src.repositories.knowledge_index_jobs import KnowledgeIndexJobsRepository
from src.repositories.project_files import ProjectFilesRepository
from src.repositories.project_members import ProjectMembersRepository
from src.repositories.project_stages import ProjectStagesRepository
from src.repositories.task_activity import TaskActivityRepository
from src.repositories.task_attachments import TaskAttachmentsRepository
from src.repositories.task_comment_attachments import TaskCommentAttachmentsRepository
from src.repositories.task_comments import TaskCommentsRepository
from src.repositories.tasks import TasksRepository
from src.repositories.unit_of_work import UnitOfWork
from src.repositories.users import UsersRepository
from src.services.access import AccessService
from src.services.auth import AuthService
from src.services.file_comments import (
    FileCommentsScope,
    FileCommentsScopeFactory,
    FileCommentsService,
)
from src.services.file_metadata import FilePreviewsService, TaskFileMutationsService
from src.services.knowledge_events import KnowledgeEvents
from src.services.task_attachments import TaskAttachmentsService
from src.storage.task_attachments import TaskAttachmentStorage


def build_file_comments_scope(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    storage: TaskAttachmentStorage,
) -> FileCommentsScopeFactory:
    """Собирает одну UoW для файлов, комментария, связей, истории и квитанции.

    Args:
        session_factory: Явная фабрика коротких сессий приложения или теста.
        settings: Настройки именно этого приложения.
        storage: Явное локальное хранилище originals/previews.
    Returns:
        Фабрику контекста, закрывающего сессию до дисковых операций.
    """

    @asynccontextmanager
    async def scope() -> AsyncIterator[FileCommentsScope]:
        async with session_factory() as session:
            tasks = TasksRepository(session)
            comments = TaskCommentsRepository(session)
            files = TaskAttachmentsRepository(session)
            unit_of_work = UnitOfWork(session)
            knowledge_events = KnowledgeEvents(
                KnowledgeIndexJobsRepository(session), enabled=settings.knowledge.knowledge_enabled
            )
            yield FileCommentsScope(
                auth=AuthService(
                    users_repository=UsersRepository(session),
                    tokens_repository=ApiTokensRepository(session),
                    api_keys_repository=ApiKeysRepository(session),
                    invite_code=settings.auth.registration_invite_code.get_secret_value(),
                ),
                access=AccessService(
                    members_repository=ProjectMembersRepository(session),
                    tasks_repository=tasks,
                    stages_repository=ProjectStagesRepository(session),
                    documents_repository=DocumentsRepository(session),
                    comments_repository=comments,
                    links_repository=DocumentLinksRepository(session),
                ),
                tasks=tasks,
                files=files,
                project_files=ProjectFilesRepository(session),
                processing_jobs=FileProcessingJobsRepository(session),
                attachments=TaskAttachmentsService(
                    attachments_repository=files,
                    tasks_repository=tasks,
                    storage=storage,
                    unit_of_work=unit_of_work,
                    knowledge_events=knowledge_events,
                    knowledge_enabled=settings.knowledge.knowledge_enabled,
                ),
                comments=comments,
                links=TaskCommentAttachmentsRepository(session),
                submissions=FileCommentSubmissionsRepository(session),
                activity=TaskActivityRepository(session),
                knowledge_events=knowledge_events,
                unit_of_work=unit_of_work,
            )

    return scope


def get_file_comments_service(
    settings: SettingsDep, storage: TaskAttachmentStorageDep
) -> FileCommentsService:
    """Создаёт сервис без request-scoped сессии во время multipart/IO."""
    return FileCommentsService(
        scope=build_file_comments_scope(
            session_factory=async_session_factory, settings=settings, storage=storage
        ),
        storage=storage,
        knowledge_enabled=settings.knowledge.knowledge_enabled,
    )


def get_task_file_mutations_service(
    settings: SettingsDep, storage: TaskAttachmentStorageDep
) -> TaskFileMutationsService:
    """Создаёт короткий сценарий загрузки и изменения подписи файла задачи."""
    return TaskFileMutationsService(
        scope=build_file_comments_scope(
            session_factory=async_session_factory, settings=settings, storage=storage
        ),
        storage=storage,
    )


def get_file_previews_service(
    settings: SettingsDep, storage: TaskAttachmentStorageDep
) -> FilePreviewsService:
    """Создаёт сервис, освобождающий БД до декодирования и передачи растра."""
    return FilePreviewsService(
        scope=build_file_comments_scope(
            session_factory=async_session_factory, settings=settings, storage=storage
        ),
        storage=storage,
    )


FileCommentsServiceDep = Annotated[FileCommentsService, Depends(get_file_comments_service)]
TaskFileMutationsServiceDep = Annotated[
    TaskFileMutationsService, Depends(get_task_file_mutations_service)
]
FilePreviewsServiceDep = Annotated[FilePreviewsService, Depends(get_file_previews_service)]
