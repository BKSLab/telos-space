"""Короткие области работы с базой для долгоживущих сценариев.

Обычный запрос получает сессию через yield-зависимость FastAPI, и она
живёт до конца ответа. Для streaming-выдачи и для сценариев с медленным
внешним вызовом это неприемлемо: соединение с PostgreSQL оставалось бы
занятым всё время передачи файла или ожидания модели.

Здесь собирается SQLAlchemy-реализация коротких областей. Сервисы
получают только фабрику области и о SQLAlchemy не знают.
"""

from collections import Counter
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from time import monotonic
from typing import Annotated

from fastapi import Depends
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.core.settings import Settings
from src.db.session import async_session_factory
from src.dependencies.settings import SettingsDep
from src.dependencies.storage import TaskAttachmentStorageDep
from src.exceptions.project_chats import ChatRepositoryError
from src.repositories.agent_conversations import AgentConversationsRepository
from src.repositories.agent_files import AgentFilesRepository
from src.repositories.agent_messages import AgentMessagesRepository
from src.repositories.agent_tool_runs import AgentToolRunsRepository
from src.repositories.analytics_reports import AnalyticsReportsRepository
from src.repositories.api_key_usage import ApiKeyUsageRepository
from src.repositories.api_keys import ApiKeysRepository
from src.repositories.api_tokens import ApiTokensRepository
from src.repositories.board_editor import BoardEditorRepository
from src.repositories.board_shapes import BoardShapesRepository
from src.repositories.board_stickers import BoardStickersRepository
from src.repositories.boards import BoardsRepository
from src.repositories.chat_agent_turns import ChatAgentTurnsRepository
from src.repositories.chat_attachments import ChatAttachmentsRepository
from src.repositories.chat_entity_lookup import ChatEntityLookupRepository
from src.repositories.chat_events import ChatEventsRepository
from src.repositories.chat_message_entities import ChatMessageEntitiesRepository
from src.repositories.chat_message_mentions import ChatMessageMentionsRepository
from src.repositories.chat_messages import ChatMessagesRepository
from src.repositories.chat_participants import ChatParticipantsRepository
from src.repositories.chat_reactions import ChatReactionsRepository
from src.repositories.chat_read_states import ChatReadStatesRepository
from src.repositories.document_links import DocumentLinksRepository
from src.repositories.documents import DocumentsRepository
from src.repositories.knowledge_index_jobs import KnowledgeIndexJobsRepository
from src.repositories.knowledge_sources import KnowledgeSourcesRepository
from src.repositories.milestones import MilestonesRepository
from src.repositories.project_chats import ProjectChatsRepository
from src.repositories.project_deadline_changes import ProjectDeadlineChangesRepository
from src.repositories.project_files import ProjectFilesRepository
from src.repositories.project_members import ProjectMembersRepository
from src.repositories.project_risks import ProjectRiskRepository
from src.repositories.project_stages import ProjectStagesRepository
from src.repositories.projects import ProjectsRepository
from src.repositories.task_activity import TaskActivityRepository
from src.repositories.task_attachments import TaskAttachmentsRepository
from src.repositories.task_comments import TaskCommentsRepository
from src.repositories.task_dependencies import TaskDependenciesRepository
from src.repositories.task_participants import TaskParticipantsRepository
from src.repositories.task_reports import TaskReportsRepository
from src.repositories.tasks import TasksRepository
from src.repositories.unit_of_work import AgentToolUnitOfWork, UnitOfWork
from src.repositories.users import UsersRepository
from src.repositories.wbs_nodes import WbsNodesRepository
from src.services.access import AccessService
from src.services.agent_tool_scope import AgentProjectToolScope, AgentProjectToolScopeFactory
from src.services.analytics import AnalyticsReportReader
from src.services.api_key_usage import ApiKeyUsageService
from src.services.attachment_download import (
    AttachmentDownloadScope,
    AttachmentDownloadService,
)
from src.services.auth import AuthService
from src.services.board_shapes import BoardShapesService
from src.services.board_stickers import BoardStickersService
from src.services.boards import BoardsService
from src.services.calendar import CalendarService
from src.services.calendar_scenarios import CalendarScenarioService
from src.services.chat_scope import ChatScope, ChatScopeFactory
from src.services.db_scope import (
    AgentConversationScope,
    AgentConversationScopeFactory,
    AnalyticsDbScope,
    ArisChatScope,
    ArisChatScopeFactory,
    ProjectAgentScope,
    ProjectQueryScope,
    ProjectQueryScopeFactory,
    TaskDescriptionScope,
    TaskDocumentImportScope,
    WbsSuggestionScope,
)
from src.services.document_links import DocumentLinksService
from src.services.documents import DocumentsService
from src.services.knowledge_events import KnowledgeEvents
from src.services.milestones import MilestonesService
from src.services.project_files import (
    ProjectFilesScope,
    ProjectFilesScopeFactory,
    ProjectFilesService,
)
from src.services.project_members import ProjectMembersService
from src.services.project_risks import ProjectRiskService
from src.services.project_stages import ProjectStagesService
from src.services.projects import ProjectsService
from src.services.risk_suggestions import RiskSuggestionScope, RiskSuggestionScopeFactory
from src.services.task_attachments import TaskAttachmentsService
from src.services.task_checklist_suggestions import (
    ChecklistSuggestionScope,
    ChecklistSuggestionScopeFactory,
)
from src.services.task_comments import TaskCommentsService
from src.services.task_dependencies import TaskDependenciesService
from src.services.task_reports import TaskReportsService
from src.services.tasks import TasksService
from src.services.users import UsersService
from src.services.wbs_nodes import WbsNodesService
from src.storage.agent_tool_files import AgentToolFileStorage
from src.storage.avatars import AvatarStorage
from src.storage.task_attachments import TaskAttachmentStorage

SessionFactory = async_sessionmaker[AsyncSession]


def build_chat_scope(
    *, session_factory: SessionFactory, invite_code: str, metrics: Counter | None = None
) -> ChatScopeFactory:
    """Создаёт сессию только на одну операцию чата или проверку handshake."""

    @asynccontextmanager
    async def scope() -> AsyncIterator[ChatScope]:
        async with session_factory() as session:
            started = monotonic()
            if metrics is not None:
                metrics["db_acquiring"] += 1
            try:
                await session.connection()
            except SQLAlchemyError as error:
                if metrics is not None:
                    metrics["db_acquire_failures_total"] += 1
                raise ChatRepositoryError(type(error).__name__) from None
            finally:
                if metrics is not None:
                    duration = (monotonic() - started) * 1000
                    metrics["db_acquiring"] -= 1
                    metrics["db_acquire_count"] += 1
                    metrics["db_acquire_ms_sum"] += duration
                    metrics["db_acquire_ms_max"] = max(metrics["db_acquire_ms_max"], duration)
            yield ChatScope(
                chats=ProjectChatsRepository(session),
                messages=ChatMessagesRepository(session),
                entities=ChatMessageEntitiesRepository(session),
                entity_lookup=ChatEntityLookupRepository(session),
                mentions=ChatMessageMentionsRepository(session),
                attachments=ChatAttachmentsRepository(session),
                reactions=ChatReactionsRepository(session),
                reads=ChatReadStatesRepository(session),
                events=ChatEventsRepository(session),
                participants=ChatParticipantsRepository(session),
                auth=AuthService(
                    UsersRepository(session),
                    tokens_repository=ApiTokensRepository(session),
                    api_keys_repository=ApiKeysRepository(session),
                    invite_code=invite_code,
                ),
                unit_of_work=UnitOfWork(session),
                turns=ChatAgentTurnsRepository(session),
                runs=AgentToolRunsRepository(session),
            )

    return scope


def build_agent_project_tool_scope(
    *, session_factory: SessionFactory, settings: Settings
) -> AgentProjectToolScopeFactory:
    """Объединяет результат инструмента и доменные commit одной внешней транзакцией.

    Сервисы используют обычный UnitOfWork. Их commit завершает SAVEPOINT;
    внешнюю транзакцию фиксирует только AgentToolUnitOfWork после записи журнала.
    """

    @asynccontextmanager
    async def scope() -> AsyncIterator[AgentProjectToolScope]:
        files = AgentToolFileStorage(settings.app.uploads_path)
        upload_storage = AgentToolFileStorage(settings.app.uploads_path / "agent")
        async with session_factory() as outer:
            async with outer.begin():
                connection = await outer.connection()
                async with session_factory(
                    bind=connection, join_transaction_mode="create_savepoint"
                ) as session:
                    unit_of_work = AgentToolUnitOfWork(outer, session)
                    domain_uow = UnitOfWork(session)
                    projects = ProjectsRepository(session)
                    members = ProjectMembersRepository(session)
                    users = UsersRepository(session)
                    stages = ProjectStagesRepository(session)
                    tasks = TasksRepository(session)
                    participants = TaskParticipantsRepository(session)
                    comments = TaskCommentsRepository(session)
                    activity = TaskActivityRepository(session)
                    nodes = WbsNodesRepository(session)
                    documents = DocumentsRepository(session)
                    links = DocumentLinksRepository(session)
                    milestones = MilestonesRepository(session)
                    dependencies = TaskDependenciesRepository(session)
                    risks = ProjectRiskRepository(session)
                    access = AccessService(
                        members_repository=members,
                        tasks_repository=tasks,
                        stages_repository=stages,
                        documents_repository=documents,
                        comments_repository=comments,
                        links_repository=links,
                    )
                    events = KnowledgeEvents(
                        repository=KnowledgeIndexJobsRepository(session),
                        enabled=settings.knowledge.knowledge_enabled,
                    )

                    @asynccontextmanager
                    async def analytics_scope() -> AsyncIterator[AnalyticsDbScope]:
                        # Сессией и её транзакцией владеет внешняя область инструмента.
                        yield _build_analytics_db_scope(session=session)

                    try:
                        yield AgentProjectToolScope(
                            projects=ProjectsService(
                                projects_repository=projects,
                                members_repository=members,
                                stages_repository=stages,
                                tasks_repository=tasks,
                                unit_of_work=domain_uow,
                                attachment_storage=files,
                                knowledge_events=events,
                                users_repository=users,
                                deadline_changes_repository=ProjectDeadlineChangesRepository(
                                    session
                                ),
                                boards_repository=BoardsRepository(session),
                            ),
                            tasks=TasksService(
                                tasks_repository=tasks,
                                members_repository=members,
                                participants_repository=participants,
                                projects_repository=projects,
                                stages_repository=stages,
                                comments_repository=comments,
                                activity_repository=activity,
                                wbs_nodes_repository=nodes,
                                unit_of_work=domain_uow,
                                attachment_storage=files,
                                knowledge_events=events,
                                users_repository=UsersRepository(session),
                                reports_repository=TaskReportsRepository(session),
                            ),
                            comments=TaskCommentsService(
                                knowledge_enabled=settings.knowledge.knowledge_enabled,
                                comments_repository=comments,
                                tasks_repository=tasks,
                                activity_repository=activity,
                                unit_of_work=domain_uow,
                                knowledge_events=events,
                                users_repository=UsersRepository(session),
                            ),
                            members=ProjectMembersService(
                                members_repository=members,
                                users_repository=users,
                                participants_repository=participants,
                                tasks_repository=tasks,
                                unit_of_work=domain_uow,
                                risks_repository=risks,
                                knowledge_events=events,
                                users_service=UsersService(
                                    users_repository=users,
                                    avatar_storage=AvatarStorage(settings.auth.avatars_path),
                                ),
                            ),
                            stickers=BoardStickersService(
                                stickers_repository=BoardStickersRepository(session),
                                tasks_repository=tasks,
                                unit_of_work=domain_uow,
                                boards_repository=BoardsRepository(session),
                                shapes_repository=BoardShapesRepository(session),
                                editor_repository=BoardEditorRepository(session),
                            ),
                            stages=ProjectStagesService(
                                stages_repository=stages,
                                projects_repository=projects,
                                tasks_repository=tasks,
                                unit_of_work=domain_uow,
                            ),
                            documents=DocumentsService(
                                documents_repository=documents,
                                projects_repository=projects,
                                unit_of_work=domain_uow,
                                knowledge_events=events,
                            ),
                            links=DocumentLinksService(
                                document_links_repository=links,
                                documents_repository=documents,
                                tasks_repository=tasks,
                                projects_repository=projects,
                                members_repository=members,
                            ),
                            attachments=TaskAttachmentsService(
                                knowledge_enabled=settings.knowledge.knowledge_enabled,
                                attachments_repository=TaskAttachmentsRepository(session),
                                tasks_repository=tasks,
                                storage=files,
                                unit_of_work=domain_uow,
                                knowledge_events=events,
                            ),
                            wbs=WbsNodesService(
                                wbs_nodes_repository=nodes,
                                projects_repository=projects,
                                stages_repository=stages,
                                tasks_repository=tasks,
                                activity_repository=activity,
                                unit_of_work=domain_uow,
                                knowledge_events=events,
                            ),
                            milestones=MilestonesService(
                                milestones_repository=milestones,
                                projects_repository=projects,
                                wbs_nodes_repository=nodes,
                                unit_of_work=domain_uow,
                                knowledge_events=events,
                            ),
                            dependencies=TaskDependenciesService(
                                dependencies_repository=dependencies,
                                projects_repository=projects,
                                tasks_repository=tasks,
                                unit_of_work=domain_uow,
                            ),
                            calendar=CalendarService(
                                projects_repository=projects,
                                tasks_repository=tasks,
                                stages_repository=stages,
                                wbs_nodes_repository=nodes,
                                activity_repository=activity,
                                dependencies_repository=dependencies,
                                milestones_repository=milestones,
                            ),
                            scenarios=CalendarScenarioService(
                                projects_repository=projects,
                                tasks_repository=tasks,
                                dependencies_repository=dependencies,
                                milestones_repository=milestones,
                                activity_repository=activity,
                                unit_of_work=domain_uow,
                            ),
                            risks=ProjectRiskService(
                                risks_repository=risks,
                                tasks_repository=tasks,
                                members_repository=members,
                                access_service=access,
                                knowledge_events=events,
                                unit_of_work=domain_uow,
                                projects_repository=projects,
                            ),
                            analytics_reader=AnalyticsReportReader(scope=analytics_scope),
                            access=access,
                            users=users,
                            conversations=AgentConversationsRepository(session),
                            messages=AgentMessagesRepository(session),
                            runs=AgentToolRunsRepository(session),
                            unit_of_work=unit_of_work,
                            uploads=AgentFilesRepository(session),
                            upload_storage=upload_storage,
                            boards=BoardsService(
                                boards_repository=BoardsRepository(session),
                                stickers_repository=BoardStickersRepository(session),
                                shapes_repository=BoardShapesRepository(session),
                                unit_of_work=domain_uow,
                            ),
                            board_shapes=BoardShapesService(
                                boards_repository=BoardsRepository(session),
                                shapes_repository=BoardShapesRepository(session),
                                stickers_repository=BoardStickersRepository(session),
                                tasks_repository=tasks,
                                unit_of_work=domain_uow,
                                editor_repository=BoardEditorRepository(session),
                            ),
                            reports=TaskReportsService(
                                reports=TaskReportsRepository(session),
                                tasks=tasks,
                                comments=comments,
                                access=access,
                                unit_of_work=domain_uow,
                                projects=projects,
                            ),
                            turns=ChatAgentTurnsRepository(session),
                            project_rows=projects,
                            task_rows=tasks,
                            activity_rows=activity,
                            api_keys=ApiKeysRepository(session),
                            auth=AuthService(
                                users_repository=users,
                                tokens_repository=ApiTokensRepository(session),
                                api_keys_repository=ApiKeysRepository(session),
                                invite_code=settings.auth.registration_invite_code.get_secret_value(),
                            ),
                        )
                    finally:
                        if not unit_of_work.committed:
                            await outer.rollback()
                        await files.finish(committed=unit_of_work.committed)
                        await upload_storage.finish(committed=unit_of_work.committed)

    return scope


def get_agent_project_tool_scope(settings: SettingsDep) -> AgentProjectToolScopeFactory:
    """Внедряет фабрику коротких атомарных действий проекта."""
    return build_agent_project_tool_scope(session_factory=async_session_factory, settings=settings)


AgentProjectToolScopeDep = Annotated[
    AgentProjectToolScopeFactory, Depends(get_agent_project_tool_scope)
]


def build_agent_conversation_scope(
    *, session_factory: SessionFactory
) -> AgentConversationScopeFactory:
    """Собирает короткие области переписки и повторной проверки доступа worker-а."""

    @asynccontextmanager
    async def scope() -> AsyncIterator[AgentConversationScope]:
        async with session_factory() as session:
            yield AgentConversationScope(
                conversations=AgentConversationsRepository(session),
                messages=AgentMessagesRepository(session),
                runs=AgentToolRunsRepository(session),
                uploads=AgentFilesRepository(session),
                users=UsersRepository(session),
                access=AccessService(
                    members_repository=ProjectMembersRepository(session),
                    tasks_repository=TasksRepository(session),
                    stages_repository=ProjectStagesRepository(session),
                    documents_repository=DocumentsRepository(session),
                    comments_repository=TaskCommentsRepository(session),
                    links_repository=DocumentLinksRepository(session),
                ),
                unit_of_work=UnitOfWork(session),
                projects=ProjectsRepository(session),
                api_keys=ApiKeysRepository(session),
                boards=BoardsRepository(session),
                board_shapes=BoardShapesRepository(session),
                board_stickers=BoardStickersRepository(session),
            )

    return scope


def get_agent_conversation_scope() -> AgentConversationScopeFactory:
    """Возвращает фабрику коротких транзакций проектного диалога."""
    return build_agent_conversation_scope(session_factory=async_session_factory)


AgentConversationScopeDep = Annotated[
    AgentConversationScopeFactory, Depends(get_agent_conversation_scope)
]


def build_project_knowledge_query_scope(
    *, session_factory: SessionFactory
) -> ProjectQueryScopeFactory:
    """Собирает короткую область существующего сервиса чтения знаний."""

    @asynccontextmanager
    async def scope() -> AsyncIterator[ProjectQueryScope]:
        async with session_factory() as session:
            yield ProjectQueryScope(
                sources=KnowledgeSourcesRepository(session),
                projects=ProjectsRepository(session),
                members=ProjectMembersRepository(session),
                stages=ProjectStagesRepository(session),
                tasks=TasksRepository(session),
                comments=TaskCommentsRepository(session),
                wbs_nodes=WbsNodesRepository(session),
                milestones=MilestonesRepository(session),
            )

    return scope


def get_project_knowledge_query_scope() -> ProjectQueryScopeFactory:
    """Возвращает фабрику области инструментов чтения."""
    return build_project_knowledge_query_scope(session_factory=async_session_factory)


ProjectKnowledgeQueryScopeDep = Annotated[
    ProjectQueryScopeFactory, Depends(get_project_knowledge_query_scope)
]


def build_checklist_suggestion_scope(
    *, session_factory: SessionFactory, settings: Settings
) -> ChecklistSuggestionScopeFactory:
    """Собирает авторизацию и чтение контекста задачи до внешнего вызова."""

    @asynccontextmanager
    async def scope() -> AsyncIterator[ChecklistSuggestionScope]:
        async with session_factory() as session:
            yield ChecklistSuggestionScope(
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
                projects=ProjectsRepository(session),
                tasks=TasksRepository(session),
                documents=DocumentsRepository(session),
                links=DocumentLinksRepository(session),
                attachments=TaskAttachmentsRepository(session),
            )

    return scope


def get_checklist_suggestion_scope(settings: SettingsDep) -> ChecklistSuggestionScopeFactory:
    """Возвращает короткую область генерации чек-листа."""
    return build_checklist_suggestion_scope(
        session_factory=async_session_factory, settings=settings
    )


ChecklistSuggestionScopeDep = Annotated[
    ChecklistSuggestionScopeFactory, Depends(get_checklist_suggestion_scope)
]


def build_risk_suggestion_scope(
    *, session_factory: SessionFactory, settings: Settings
) -> RiskSuggestionScopeFactory:
    """Собирает чтение и авторизацию AI-предложений в одной короткой области."""

    @asynccontextmanager
    async def scope() -> AsyncIterator[RiskSuggestionScope]:
        async with session_factory() as session:
            yield RiskSuggestionScope(
                activity=TaskActivityRepository(session),
                milestones=MilestonesRepository(session),
                dependencies=TaskDependenciesRepository(session),
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
                projects=ProjectsRepository(session),
                tasks=TasksRepository(session),
                stages=ProjectStagesRepository(session),
                comments=TaskCommentsRepository(session),
                documents=DocumentsRepository(session),
                nodes=WbsNodesRepository(session),
                risks=ProjectRiskRepository(session),
            )

    return scope


def get_risk_suggestion_scope(settings: SettingsDep) -> RiskSuggestionScopeFactory:
    """Возвращает фабрику короткой области AI-предложений рисков."""
    return build_risk_suggestion_scope(session_factory=async_session_factory, settings=settings)


RiskSuggestionScopeDep = Annotated[RiskSuggestionScopeFactory, Depends(get_risk_suggestion_scope)]


def build_attachment_download_scope(
    *,
    session_factory: SessionFactory,
    settings: Settings,
    storage: TaskAttachmentStorage,
) -> Callable[[], AbstractAsyncContextManager[AttachmentDownloadScope]]:
    """Собирает фабрику короткой области подготовки выдачи файла.

    Область открывается на время проверки доступа и чтения метаданных и
    закрывается до начала передачи файла.

    Args:
        session_factory: Фабрика сессий PostgreSQL.
        settings: Настройки приложения.
        storage: Локальное хранилище файлов задач.

    Returns:
        Фабрику асинхронного контекста с узким набором операций.
    """

    @asynccontextmanager
    async def scope() -> AsyncIterator[AttachmentDownloadScope]:
        async with session_factory() as session:
            yield AttachmentDownloadScope(
                auth_service=AuthService(
                    users_repository=UsersRepository(session),
                    tokens_repository=ApiTokensRepository(session),
                    api_keys_repository=ApiKeysRepository(session),
                    invite_code=settings.auth.registration_invite_code.get_secret_value(),
                ),
                access_service=AccessService(
                    members_repository=ProjectMembersRepository(session),
                    tasks_repository=TasksRepository(session),
                    stages_repository=ProjectStagesRepository(session),
                    documents_repository=DocumentsRepository(session),
                    comments_repository=TaskCommentsRepository(session),
                    links_repository=DocumentLinksRepository(session),
                ),
                attachments_service=TaskAttachmentsService(
                    knowledge_enabled=settings.knowledge.knowledge_enabled,
                    attachments_repository=TaskAttachmentsRepository(session),
                    tasks_repository=TasksRepository(session),
                    storage=storage,
                    knowledge_events=KnowledgeEvents(
                        repository=KnowledgeIndexJobsRepository(session),
                        enabled=settings.knowledge.knowledge_enabled,
                    ),
                    unit_of_work=UnitOfWork(session),
                ),
            )

    return scope


def get_attachment_download_service(
    settings: SettingsDep,
    storage: TaskAttachmentStorageDep,
) -> AttachmentDownloadService:
    """Создаёт сервис подготовки выдачи файла задачи.

    Зависимость намеренно не является yield-зависимостью и не получает
    `DbSessionDep`: сессия не должна пережить подготовку ответа.
    """
    return AttachmentDownloadService(
        scope=build_attachment_download_scope(
            session_factory=async_session_factory,
            settings=settings,
            storage=storage,
        )
    )


AttachmentDownloadServiceDep = Annotated[
    AttachmentDownloadService,
    Depends(get_attachment_download_service),
]


def build_project_files_scope(
    *, session_factory: SessionFactory, settings: Settings
) -> ProjectFilesScopeFactory:
    """Собирает короткую область файлов, включая актуальные auth/access."""

    @asynccontextmanager
    async def scope() -> AsyncIterator[ProjectFilesScope]:
        async with session_factory() as session:
            yield ProjectFilesScope(
                files=ProjectFilesRepository(session),
                sources=KnowledgeSourcesRepository(session),
                access=AccessService(
                    members_repository=ProjectMembersRepository(session),
                    tasks_repository=TasksRepository(session),
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
                unit_of_work=UnitOfWork(session),
            )

    return scope


def get_project_files_service(
    settings: SettingsDep, storage: TaskAttachmentStorageDep
) -> ProjectFilesService:
    """Создаёт сервис без request-scoped соединения PostgreSQL."""
    return ProjectFilesService(
        scope=build_project_files_scope(session_factory=async_session_factory, settings=settings),
        storage=storage,
        max_file_size=settings.app.project_file_max_size,
        knowledge_enabled=settings.knowledge.knowledge_enabled,
    )


ProjectFilesServiceDep = Annotated[ProjectFilesService, Depends(get_project_files_service)]


def build_wbs_suggestion_scope(
    *,
    session_factory: SessionFactory,
    settings: Settings,
) -> Callable[[], AbstractAsyncContextManager[WbsSuggestionScope]]:
    """Собирает фабрику короткой области сценария предложения ИСР.

    Args:
        session_factory: Фабрика сессий PostgreSQL.
        settings: Настройки приложения.

    Returns:
        Фабрику асинхронного контекста с репозиториями сценария.
    """

    @asynccontextmanager
    async def scope() -> AsyncIterator[WbsSuggestionScope]:
        async with session_factory() as session:
            yield WbsSuggestionScope(
                projects=ProjectsRepository(session),
                wbs_nodes=WbsNodesRepository(session),
                tasks=TasksRepository(session),
                stages=ProjectStagesRepository(session),
                activity=TaskActivityRepository(session),
                knowledge_events=KnowledgeEvents(
                    repository=KnowledgeIndexJobsRepository(session),
                    enabled=settings.knowledge.knowledge_enabled,
                ),
                unit_of_work=UnitOfWork(session),
            )

    return scope


def get_wbs_suggestion_scope(
    settings: SettingsDep,
) -> Callable[[], AbstractAsyncContextManager[WbsSuggestionScope]]:
    """Возвращает фабрику области для сценария предложения ИСР."""
    return build_wbs_suggestion_scope(
        session_factory=async_session_factory,
        settings=settings,
    )


WbsSuggestionScopeDep = Annotated[
    Callable[[], AbstractAsyncContextManager[WbsSuggestionScope]],
    Depends(get_wbs_suggestion_scope),
]


def build_task_description_scope(
    *,
    session_factory: SessionFactory,
) -> Callable[[], AbstractAsyncContextManager[TaskDescriptionScope]]:
    """Собирает фабрику короткой области переформулирования черновика."""

    @asynccontextmanager
    async def scope() -> AsyncIterator[TaskDescriptionScope]:
        async with session_factory() as session:
            yield TaskDescriptionScope(
                projects=ProjectsRepository(session),
                tasks=TasksRepository(session),
                documents=DocumentsRepository(session),
            )

    return scope


def get_task_description_scope() -> Callable[[], AbstractAsyncContextManager[TaskDescriptionScope]]:
    """Возвращает фабрику области для переформулирования черновика."""
    return build_task_description_scope(session_factory=async_session_factory)


TaskDescriptionScopeDep = Annotated[
    Callable[[], AbstractAsyncContextManager[TaskDescriptionScope]],
    Depends(get_task_description_scope),
]


def _build_analytics_db_scope(*, session: AsyncSession) -> AnalyticsDbScope:
    """Собирает зависимости аналитики на сессии, принадлежащей вызывающему коду."""
    return AnalyticsDbScope(
        attachments=TaskAttachmentsRepository(session),
        participants=TaskParticipantsRepository(session),
        risks=ProjectRiskRepository(session),
        reports=AnalyticsReportsRepository(session),
        projects=ProjectsRepository(session),
        members=ProjectMembersRepository(session),
        stages=ProjectStagesRepository(session),
        tasks=TasksRepository(session),
        comments=TaskCommentsRepository(session),
        activity=TaskActivityRepository(session),
        dependencies=TaskDependenciesRepository(session),
        wbs_nodes=WbsNodesRepository(session),
        milestones=MilestonesRepository(session),
        stickers=BoardStickersRepository(session),
        documents=DocumentsRepository(session),
        document_links=DocumentLinksRepository(session),
        unit_of_work=UnitOfWork(session),
    )


def build_analytics_scope(
    *,
    session_factory: SessionFactory,
) -> Callable[[], AbstractAsyncContextManager[AnalyticsDbScope]]:
    """Собирает фабрику короткой области сценария аналитического свода."""

    @asynccontextmanager
    async def scope() -> AsyncIterator[AnalyticsDbScope]:
        async with session_factory() as session:
            yield _build_analytics_db_scope(session=session)

    return scope


def get_analytics_scope() -> Callable[[], AbstractAsyncContextManager[AnalyticsDbScope]]:
    """Возвращает фабрику области для аналитического свода."""
    return build_analytics_scope(session_factory=async_session_factory)


AnalyticsScopeDep = Annotated[
    Callable[[], AbstractAsyncContextManager[AnalyticsDbScope]],
    Depends(get_analytics_scope),
]


def build_project_agent_scope(
    *,
    session_factory: SessionFactory,
    settings: Settings,
) -> Callable[[], AbstractAsyncContextManager[ProjectAgentScope]]:
    """Собирает фабрику короткой области сценариев Арис."""

    @asynccontextmanager
    async def scope() -> AsyncIterator[ProjectAgentScope]:
        async with session_factory() as session:
            yield ProjectAgentScope(
                sources=KnowledgeSourcesRepository(session),
                risks=ProjectRiskRepository(session),
                projects=ProjectsRepository(session),
                stages=ProjectStagesRepository(session),
                tasks=TasksRepository(session),
                wbs_nodes=WbsNodesRepository(session),
                documents=DocumentsRepository(session),
                activity=TaskActivityRepository(session),
                milestones=MilestonesRepository(session),
                dependencies=TaskDependenciesRepository(session),
                jobs=KnowledgeIndexJobsRepository(session),
                knowledge_events=KnowledgeEvents(
                    repository=KnowledgeIndexJobsRepository(session),
                    enabled=settings.knowledge.knowledge_enabled,
                ),
                unit_of_work=UnitOfWork(session),
                calendar=CalendarService(
                    projects_repository=ProjectsRepository(session),
                    tasks_repository=TasksRepository(session),
                    stages_repository=ProjectStagesRepository(session),
                    wbs_nodes_repository=WbsNodesRepository(session),
                    activity_repository=TaskActivityRepository(session),
                    milestones_repository=MilestonesRepository(session),
                    dependencies_repository=TaskDependenciesRepository(session),
                ),
                scenario=CalendarScenarioService(
                    projects_repository=ProjectsRepository(session),
                    tasks_repository=TasksRepository(session),
                    dependencies_repository=TaskDependenciesRepository(session),
                    milestones_repository=MilestonesRepository(session),
                    activity_repository=TaskActivityRepository(session),
                    unit_of_work=UnitOfWork(session),
                ),
            )

    return scope


def get_project_agent_scope(
    settings: SettingsDep,
) -> Callable[[], AbstractAsyncContextManager[ProjectAgentScope]]:
    """Возвращает фабрику области для сценариев Арис."""
    return build_project_agent_scope(
        session_factory=async_session_factory,
        settings=settings,
    )


ProjectAgentScopeDep = Annotated[
    Callable[[], AbstractAsyncContextManager[ProjectAgentScope]],
    Depends(get_project_agent_scope),
]


def build_task_document_import_scope(
    *,
    session_factory: SessionFactory,
    settings: Settings,
    storage: TaskAttachmentStorage,
) -> Callable[[], AbstractAsyncContextManager[TaskDocumentImportScope]]:
    """Собирает фабрику короткой области импорта документа в задачу.

    Все вложенные сервисы получают одну сессию: три записи импорта — это
    один бизнес-факт и одна транзакция.
    """

    @asynccontextmanager
    async def scope() -> AsyncIterator[TaskDocumentImportScope]:
        async with session_factory() as session:
            unit_of_work = UnitOfWork(session)
            events = KnowledgeEvents(
                repository=KnowledgeIndexJobsRepository(session),
                enabled=settings.knowledge.knowledge_enabled,
            )
            yield TaskDocumentImportScope(
                knowledge_sources=KnowledgeSourcesRepository(session),
                tasks=TasksRepository(session),
                attachments=TaskAttachmentsService(
                    knowledge_enabled=settings.knowledge.knowledge_enabled,
                    attachments_repository=TaskAttachmentsRepository(session),
                    tasks_repository=TasksRepository(session),
                    storage=storage,
                    knowledge_events=events,
                    unit_of_work=unit_of_work,
                ),
                documents=DocumentsService(
                    documents_repository=DocumentsRepository(session),
                    projects_repository=ProjectsRepository(session),
                    knowledge_events=events,
                    unit_of_work=unit_of_work,
                ),
                links=DocumentLinksService(
                    document_links_repository=DocumentLinksRepository(session),
                    documents_repository=DocumentsRepository(session),
                    tasks_repository=TasksRepository(session),
                    projects_repository=ProjectsRepository(session),
                    members_repository=ProjectMembersRepository(session),
                ),
                unit_of_work=unit_of_work,
            )

    return scope


def get_task_document_import_scope(
    settings: SettingsDep,
    storage: TaskAttachmentStorageDep,
) -> Callable[[], AbstractAsyncContextManager[TaskDocumentImportScope]]:
    """Возвращает фабрику области для импорта документа в задачу."""
    return build_task_document_import_scope(
        session_factory=async_session_factory,
        settings=settings,
        storage=storage,
    )


TaskDocumentImportScopeDep = Annotated[
    Callable[[], AbstractAsyncContextManager[TaskDocumentImportScope]],
    Depends(get_task_document_import_scope),
]


def build_aris_chat_scope(*, session_factory: SessionFactory) -> ArisChatScopeFactory:
    @asynccontextmanager
    async def scope() -> AsyncIterator[ArisChatScope]:
        async with session_factory() as session:
            yield ArisChatScope(
                chats=ProjectChatsRepository(session),
                messages=ChatMessagesRepository(session),
                events=ChatEventsRepository(session),
                participants=ChatParticipantsRepository(session),
                turns=ChatAgentTurnsRepository(session),
                runs=AgentToolRunsRepository(session),
                users=UsersRepository(session),
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

    return scope


def build_api_key_usage_recorder(session_factory: SessionFactory):
    async def record(**fields):
        try:
            async with session_factory() as session:
                await ApiKeyUsageService(ApiKeyUsageRepository(session)).record(**fields)
        except Exception:
            import logging

            logging.getLogger(__name__).exception("Не удалось записать обращение по API-ключу.")

    return record
