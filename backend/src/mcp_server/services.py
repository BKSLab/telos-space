"""Ручная сборка сервисов для MCP-инструментов.

Это единственное место MCP, где разрешено создавать репозитории. У MCP
нет `Depends`, поэтому граф собирается вручную — но собирается он здесь,
а не в обработчиках инструментов.

Инструменты записи не работают с репозиториями: они вызывают те же
сервисы, что и HTTP-эндпоинты, поэтому история задачи, нумерация и
постановка в очередь индексации происходят одинаково независимо от канала.
"""

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.agent.action_catalog import PERSONAL_ACTIONS, PROJECT_ACTIONS
from src.core.settings import Settings
from src.dependencies.scopes import build_agent_conversation_scope, build_agent_project_tool_scope
from src.repositories.api_keys import ApiKeysRepository
from src.repositories.api_tokens import ApiTokensRepository
from src.repositories.document_links import DocumentLinksRepository
from src.repositories.documents import DocumentsRepository
from src.repositories.knowledge_index_jobs import KnowledgeIndexJobsRepository
from src.repositories.knowledge_sources import KnowledgeSourcesRepository
from src.repositories.milestones import MilestonesRepository
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
from src.repositories.task_transfers import TaskTransfersRepository
from src.repositories.tasks import TasksRepository
from src.repositories.unit_of_work import UnitOfWork
from src.repositories.users import UsersRepository
from src.repositories.wbs_nodes import WbsNodesRepository
from src.services.access import AccessService
from src.services.agent_actions import AgentActionsService
from src.services.agent_requests import AgentRequestsService, RequestLinks
from src.services.auth import AuthService
from src.services.calendar import CalendarService
from src.services.db_scope import ProjectQueryScope
from src.services.knowledge_events import KnowledgeEvents
from src.services.knowledge_search import KnowledgeSearchService
from src.services.milestones import MilestonesService
from src.services.project_creation import ProjectCreationService
from src.services.project_members import ProjectMembersService
from src.services.project_query import ProjectQueryService
from src.services.project_risks import ProjectRiskService
from src.services.project_stages import ProjectStagesService
from src.services.task_activity import TaskActivityService
from src.services.task_attachments import TaskAttachmentsService
from src.services.task_comments import TaskCommentsService
from src.services.task_dependencies import TaskDependenciesService
from src.services.task_transfers import TaskTransfersService
from src.services.tasks import TasksService
from src.services.users import UsersService
from src.storage.task_attachments import TaskAttachmentStorage

SessionFactory = async_sessionmaker[AsyncSession]


@dataclass(frozen=True, slots=True)
class ToolServices:
    """Сервисы, доступные инструментам MCP.

    Инструмент получает готовые use case и не собирает их сам: сессия
    остаётся внутри сервиса и не поднимается в обработчик.
    """

    auth: AuthService
    access: AccessService
    query: ProjectQueryService
    tasks: TasksService
    comments: TaskCommentsService
    milestones: MilestonesService
    calendar: CalendarService
    members: ProjectMembersService
    risks: ProjectRiskService
    actions: AgentActionsService
    stages: ProjectStagesService | None = None
    activity: TaskActivityService | None = None
    attachments: TaskAttachmentsService | None = None
    dependencies: TaskDependenciesService | None = None
    moves: TaskTransfersService | None = None
    requests: AgentRequestsService | None = None
    project_creation: ProjectCreationService | None = None


def build_project_query_scope(
    session_factory: SessionFactory,
) -> Callable[[], AbstractAsyncContextManager[ProjectQueryScope]]:
    """Собирает фабрику короткой области read-сценариев проекта."""

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


def build_knowledge_events(session: AsyncSession, settings: Settings) -> KnowledgeEvents:
    """Создаёт публикатор событий индексации знаний."""
    return KnowledgeEvents(
        repository=KnowledgeIndexJobsRepository(session),
        enabled=settings.knowledge.knowledge_enabled,
    )


def build_auth_service(session: AsyncSession, settings: Settings) -> AuthService:
    """Создаёт сервис аутентификации тем же контрактом, что и HTTP-слой.

    MCP не имеет собственной логики доступа: он задаёт тот же вопрос тому
    же сервису, поэтому правила прав не расходятся между транспортами.
    """
    return AuthService(
        users_repository=UsersRepository(session),
        tokens_repository=ApiTokensRepository(session),
        api_keys_repository=ApiKeysRepository(session),
        invite_code=settings.auth.registration_invite_code.get_secret_value(),
    )


def build_access_service(session: AsyncSession) -> AccessService:
    """Создаёт сервис разрешения доступа к объектам проекта."""
    return AccessService(
        members_repository=ProjectMembersRepository(session),
        tasks_repository=TasksRepository(session),
        stages_repository=ProjectStagesRepository(session),
        documents_repository=DocumentsRepository(session),
        comments_repository=TaskCommentsRepository(session),
        links_repository=DocumentLinksRepository(session),
    )


def build_members_service(session: AsyncSession, settings: Settings) -> ProjectMembersService:
    """Создаёт сервис команды проекта для разрешения исполнителя."""
    return ProjectMembersService(
        members_repository=ProjectMembersRepository(session),
        users_repository=UsersRepository(session),
        participants_repository=TaskParticipantsRepository(session),
        tasks_repository=TasksRepository(session),
        unit_of_work=UnitOfWork(session),
        risks_repository=ProjectRiskRepository(session),
        knowledge_events=build_knowledge_events(session, settings),
        users_service=UsersService(
            users_repository=UsersRepository(session),
            avatar_storage=None,
        ),
    )


def build_tasks_service(session: AsyncSession, settings: Settings) -> TasksService:
    """Создаёт сервис задач со всеми доменными зависимостями."""
    return TasksService(
        tasks_repository=TasksRepository(session),
        users_repository=UsersRepository(session),
        reports_repository=TaskReportsRepository(session),
        members_repository=ProjectMembersRepository(session),
        participants_repository=TaskParticipantsRepository(session),
        projects_repository=ProjectsRepository(session),
        stages_repository=ProjectStagesRepository(session),
        comments_repository=TaskCommentsRepository(session),
        activity_repository=TaskActivityRepository(session),
        wbs_nodes_repository=WbsNodesRepository(session),
        unit_of_work=UnitOfWork(session),
        attachment_storage=TaskAttachmentStorage(settings.app.uploads_path),
        knowledge_events=build_knowledge_events(session, settings),
    )


def build_comments_service(session: AsyncSession, settings: Settings) -> TaskCommentsService:
    """Создаёт сервис комментариев задач."""
    return TaskCommentsService(
        knowledge_enabled=settings.knowledge.knowledge_enabled,
        users_repository=UsersRepository(session),
        comments_repository=TaskCommentsRepository(session),
        tasks_repository=TasksRepository(session),
        activity_repository=TaskActivityRepository(session),
        unit_of_work=UnitOfWork(session),
        knowledge_events=build_knowledge_events(session, settings),
    )


def build_calendar_service(session: AsyncSession) -> CalendarService:
    """Создаёт read-only сервис временной карты для MCP."""
    return CalendarService(
        projects_repository=ProjectsRepository(session),
        tasks_repository=TasksRepository(session),
        stages_repository=ProjectStagesRepository(session),
        wbs_nodes_repository=WbsNodesRepository(session),
        activity_repository=TaskActivityRepository(session),
        milestones_repository=MilestonesRepository(session),
        dependencies_repository=TaskDependenciesRepository(session),
    )


def build_milestones_service(session: AsyncSession, settings: Settings) -> MilestonesService:
    """Создаёт сервис проектных вех с transactional outbox."""
    return MilestonesService(
        milestones_repository=MilestonesRepository(session),
        projects_repository=ProjectsRepository(session),
        wbs_nodes_repository=WbsNodesRepository(session),
        unit_of_work=UnitOfWork(session),
        knowledge_events=build_knowledge_events(session, settings),
    )


def build_risks_service(session: AsyncSession, settings: Settings) -> ProjectRiskService:
    """Собирает тот же сценарий рисков и outbox, что используется HTTP."""
    return ProjectRiskService(
        risks_repository=ProjectRiskRepository(session),
        tasks_repository=TasksRepository(session),
        members_repository=ProjectMembersRepository(session),
        projects_repository=ProjectsRepository(session),
        access_service=build_access_service(session),
        knowledge_events=build_knowledge_events(session, settings),
        unit_of_work=UnitOfWork(session),
    )


def build_stages_service(session: AsyncSession) -> ProjectStagesService:
    """Создаёт сервис стадий проекта (чтение списка стадий)."""
    return ProjectStagesService(
        stages_repository=ProjectStagesRepository(session),
        projects_repository=ProjectsRepository(session),
        tasks_repository=TasksRepository(session),
        unit_of_work=UnitOfWork(session),
    )


def build_activity_service(session: AsyncSession) -> TaskActivityService:
    """Создаёт сервис истории задачи."""
    return TaskActivityService(
        activity_repository=TaskActivityRepository(session),
        tasks_repository=TasksRepository(session),
    )


def build_dependencies_service(session: AsyncSession) -> TaskDependenciesService:
    """Создаёт сервис графа зависимостей проекта."""
    return TaskDependenciesService(
        dependencies_repository=TaskDependenciesRepository(session),
        projects_repository=ProjectsRepository(session),
        tasks_repository=TasksRepository(session),
        unit_of_work=UnitOfWork(session),
    )


def build_moves_service(session: AsyncSession, settings: Settings) -> TaskTransfersService:
    """Создаёт сервис переноса задачи в другой проект — тот же, что у REST."""
    return TaskTransfersService(
        tasks_repository=TasksRepository(session),
        task_transfers_repository=TaskTransfersRepository(session),
        participants_repository=TaskParticipantsRepository(session),
        members_repository=ProjectMembersRepository(session),
        projects_repository=ProjectsRepository(session),
        stages_repository=ProjectStagesRepository(session),
        users_repository=UsersRepository(session),
        activity_repository=TaskActivityRepository(session),
        knowledge_events=build_knowledge_events(session, settings),
        access_service=build_access_service(session),
        unit_of_work=UnitOfWork(session),
    )


def build_knowledge_search(
    query: ProjectQueryService, runtime, settings: Settings
) -> KnowledgeSearchService:
    """Собирает смысловой поиск по знаниям — тот же сервис, что у REST."""
    config = settings.knowledge
    return KnowledgeSearchService(
        query=query,
        embedding_client=runtime.embedding_client,
        qdrant_client=runtime.qdrant_client,
        enabled=config.knowledge_enabled,
        score_threshold=config.qdrant_score_threshold,
        target_chars=config.knowledge_chunk_target_chars,
        overlap_chars=config.knowledge_chunk_overlap_chars,
    )


def build_attachments_service(session: AsyncSession, settings: Settings) -> TaskAttachmentsService:
    """Общая локальная реализация вложений задач."""
    return TaskAttachmentsService(
        knowledge_enabled=settings.knowledge.knowledge_enabled,
        attachments_repository=TaskAttachmentsRepository(session),
        tasks_repository=TasksRepository(session),
        unit_of_work=UnitOfWork(session),
        storage=TaskAttachmentStorage(settings.app.uploads_path),
        knowledge_events=build_knowledge_events(session, settings),
    )


def build_tool_services(
    *,
    session: AsyncSession,
    session_factory: SessionFactory,
    settings: Settings,
) -> ToolServices:
    """Собирает полный набор сервисов одного вызова инструмента.

    Инструменты записи работают в одной сессии вызова — она же владеет их
    транзакцией. Read-сценарии получают фабрику короткой области: их
    сессия не должна жить дольше самого чтения.

    Args:
        session: Сессия вызова инструмента для сценариев записи.
        session_factory: Фабрика коротких областей для сценариев чтения.
        settings: Настройки приложения.

    Returns:
        Готовые сервисы, доступные обработчику инструмента.
    """
    return ToolServices(
        auth=build_auth_service(session, settings),
        access=build_access_service(session),
        query=ProjectQueryService(scope=build_project_query_scope(session_factory)),
        tasks=build_tasks_service(session, settings),
        comments=build_comments_service(session, settings),
        milestones=build_milestones_service(session, settings),
        calendar=build_calendar_service(session),
        members=build_members_service(session, settings),
        risks=build_risks_service(session, settings),
        stages=build_stages_service(session),
        activity=build_activity_service(session),
        attachments=build_attachments_service(session, settings),
        dependencies=build_dependencies_service(session),
        moves=build_moves_service(session, settings),
        requests=AgentRequestsService(scope=build_agent_conversation_scope(session_factory=session_factory), links=RequestLinks(settings.app.app_public_url)),
        project_creation=ProjectCreationService(
            scope=build_agent_project_tool_scope(
                session_factory=session_factory, settings=settings
            ),
        ),
        actions=AgentActionsService(
            scope=build_agent_project_tool_scope(
                session_factory=session_factory, settings=settings
            ),
            actions=PROJECT_ACTIONS + PERSONAL_ACTIONS,
            confirm_ttl=timedelta(hours=settings.agent.agent_action_confirm_ttl_hours),
        ),
    )
