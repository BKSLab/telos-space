"""Сборка сервисов приложения для графа зависимостей запроса.

Каждая фабрика объявлена вместе со своим `...Dep` alias, а вышестоящие
фабрики используют именно alias, а не повторяют `Annotated[..., Depends(...)]`:
иначе один и тот же сервис пришлось бы описывать в нескольких местах и
подменять в тестах тоже в нескольких местах.

Настройки читаются здесь, на уровне сборки графа. Сервис получает готовые
значения и клиентов через конструктор и о конфигурации приложения не знает.
"""

from dataclasses import replace
from datetime import timedelta
from typing import Annotated, Any

from fastapi import Depends
from pydantic import BaseModel

from src.agent.action_catalog import PERSONAL_ACTIONS, PROJECT_ACTIONS
from src.agent.tim import PROJECT_MODES, WIDGET_MODES
from src.agent.tools import AgentTool, AgentToolContext, AgentToolExecutor, AgentToolHandler
from src.agent.worker import AgentWorker
from src.clients.agent_model import build_agent_model
from src.core.settings import Settings
from src.dependencies.clients import (
    EmbeddingClientDep,
    LlmClientDep,
    QdrantClientDep,
    VisionCapabilityDep,
)
from src.dependencies.db_session import DbSessionDep
from src.dependencies.repositories import (
    ApiKeysRepositoryDep,
    ApiKeyUsageRepositoryDep,
    ApiTokensRepositoryDep,
    BoardShapesRepositoryDep,
    CalendarFeedTokensRepositoryDep,
    DocumentLinksRepositoryDep,
    DocumentsRepositoryDep,
    KnowledgeIndexJobsRepositoryDep,
    MilestonesRepositoryDep,
    ProjectBoardsRepositoryDep,
    ProjectDeadlineChangesRepositoryDep,
    ProjectMembersRepositoryDep,
    ProjectRiskRepositoryDep,
    ProjectsRepositoryDep,
    ProjectStagesRepositoryDep,
    ProjectStickersRepositoryDep,
    TaskActivityRepositoryDep,
    TaskAttachmentsRepositoryDep,
    TaskAttentionRepositoryDep,
    TaskCommentsRepositoryDep,
    TaskDependenciesRepositoryDep,
    TaskMovesRepositoryDep,
    TaskParticipantsRepositoryDep,
    TaskReportsRepositoryDep,
    TasksRepositoryDep,
    UnitOfWorkDep,
    UsersRepositoryDep,
    WbsNodesRepositoryDep,
)
from src.dependencies.scopes import (
    AgentConversationScopeDep,
    AgentProjectToolScopeDep,
    AnalyticsScopeDep,
    ChecklistSuggestionScopeDep,
    ProjectAgentScopeDep,
    ProjectKnowledgeQueryScopeDep,
    RiskSuggestionScopeDep,
    SessionFactory,
    TaskDescriptionScopeDep,
    TaskDocumentImportScopeDep,
    WbsSuggestionScopeDep,
    build_agent_conversation_scope,
    build_agent_project_tool_scope,
    build_project_agent_scope,
    build_project_knowledge_query_scope,
    build_tim_chat_scope,
)
from src.dependencies.settings import SettingsDep
from src.dependencies.storage import AvatarStorageDep, TaskAttachmentStorageDep
from src.exceptions.base import ServiceError
from src.knowledge.runtime import KnowledgeRuntime
from src.repositories.board_editor import BoardEditorRepository
from src.schemas.knowledge import KnowledgeReadParameters, KnowledgeReadRequest
from src.schemas.project_tool_inputs import ProjectActionInput, ToolSchemasInput
from src.services.access import AccessService
from src.services.agent_actions import AgentActionsService
from src.services.agent_conversations import AgentConversationConfig, AgentConversationsService
from src.services.agent_files import AgentFilesService
from src.services.agent_requests import AgentRequestsService, RequestLinks
from src.services.analytics import AnalyticsService
from src.services.api_key_usage import ApiKeyUsageService
from src.services.api_keys import ApiKeysService
from src.services.api_tokens import ApiTokensService
from src.services.auth import AuthService
from src.services.board_shapes import BoardShapesService
from src.services.calendar import CalendarService
from src.services.calendar_feed import CalendarFeedService
from src.services.calendar_scenarios import CalendarScenarioService
from src.services.calendar_workload import CalendarWorkloadService
from src.services.dashboard import DashboardService
from src.services.document_links import DocumentLinksService
from src.services.documents import DocumentsService
from src.services.knowledge_events import KnowledgeEvents
from src.services.milestones import MilestonesService
from src.services.personal_calendar import PersonalCalendarService
from src.services.project_agent import ProjectAgentConfig, ProjectAgentService
from src.services.project_boards import ProjectBoardsService
from src.services.project_icons import ProjectIconsService
from src.services.project_members import ProjectMembersService
from src.services.project_query import ProjectQueryService
from src.services.project_risks import ProjectRiskService
from src.services.project_stages import ProjectStagesService
from src.services.project_stickers import ProjectStickersService
from src.services.projects import ProjectsService
from src.services.risk_formulation import RiskFormulationService
from src.services.risk_suggestions import RiskSuggestionService
from src.services.task_activity import TaskActivityService
from src.services.task_attachments import TaskAttachmentsService
from src.services.task_attention import TaskAttentionService
from src.services.task_checklist_suggestions import TaskChecklistSuggestionService
from src.services.task_comments import TaskCommentsService
from src.services.task_dependencies import TaskDependenciesService
from src.services.task_descriptions import TaskDescriptionService
from src.services.task_documents import TaskDocumentImportService
from src.services.task_moves import TaskMovesService
from src.services.task_reports import TaskReportsService
from src.services.tasks import TasksService
from src.services.tim_chat import TimChatConfig, TimChatService
from src.services.tracker_agent import TrackerAgentService
from src.services.users import UsersService
from src.services.wbs_nodes import WbsNodesService
from src.services.wbs_suggestion import WbsSuggestionService


def get_knowledge_events(
    jobs_repository: KnowledgeIndexJobsRepositoryDep,
    settings: SettingsDep,
) -> KnowledgeEvents:
    """Создаёт безопасный publisher изменений для фонового индексатора."""
    return KnowledgeEvents(
        repository=jobs_repository,
        enabled=settings.knowledge.knowledge_enabled,
    )


KnowledgeEventsDep = Annotated[KnowledgeEvents, Depends(get_knowledge_events)]


def get_project_query_service(scope: ProjectKnowledgeQueryScopeDep) -> ProjectQueryService:
    return ProjectQueryService(scope=scope)


ProjectQueryServiceDep = Annotated[ProjectQueryService, Depends(get_project_query_service)]


def get_auth_service(
    api_keys_repository: ApiKeysRepositoryDep,
    users_repository: UsersRepositoryDep,
    tokens_repository: ApiTokensRepositoryDep,
    settings: SettingsDep,
) -> AuthService:
    """Создаёт сервис регистрации, входа и разрешения принципала."""
    return AuthService(
        users_repository=users_repository,
        tokens_repository=tokens_repository,
        api_keys_repository=api_keys_repository,
        invite_code=settings.auth.registration_invite_code.get_secret_value(),
    )


AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]


def get_access_service(
    members_repository: ProjectMembersRepositoryDep,
    tasks_repository: TasksRepositoryDep,
    stages_repository: ProjectStagesRepositoryDep,
    documents_repository: DocumentsRepositoryDep,
    comments_repository: TaskCommentsRepositoryDep,
    links_repository: DocumentLinksRepositoryDep,
) -> AccessService:
    """Создаёт сервис разрешения доступа к объектам проекта."""
    return AccessService(
        members_repository=members_repository,
        tasks_repository=tasks_repository,
        stages_repository=stages_repository,
        documents_repository=documents_repository,
        comments_repository=comments_repository,
        links_repository=links_repository,
    )


AccessServiceDep = Annotated[AccessService, Depends(get_access_service)]


def get_project_risk_service(
    risks_repository: ProjectRiskRepositoryDep,
    tasks_repository: TasksRepositoryDep,
    members_repository: ProjectMembersRepositoryDep,
    access_service: AccessServiceDep,
    knowledge_events: KnowledgeEventsDep,
    unit_of_work: UnitOfWorkDep,
    projects_repository: ProjectsRepositoryDep,
) -> ProjectRiskService:
    """Создаёт сервис реестра с единой транзакцией риска и outbox."""
    return ProjectRiskService(
        risks_repository=risks_repository,
        tasks_repository=tasks_repository,
        members_repository=members_repository,
        access_service=access_service,
        projects_repository=projects_repository,
        knowledge_events=knowledge_events,
        unit_of_work=unit_of_work,
    )


ProjectRiskServiceDep = Annotated[ProjectRiskService, Depends(get_project_risk_service)]


def get_risk_suggestion_service(
    scope: RiskSuggestionScopeDep, llm_client: LlmClientDep
) -> RiskSuggestionService:
    """Создаёт AI-сценарий без request-scoped сессии базы."""
    return RiskSuggestionService(scope=scope, llm_client=llm_client)


RiskSuggestionServiceDep = Annotated[RiskSuggestionService, Depends(get_risk_suggestion_service)]


def get_risk_formulation_service(
    scope: RiskSuggestionScopeDep, llm_client: LlmClientDep
) -> RiskFormulationService:
    """Создаёт AI-помощь с формулировками полей риска."""
    return RiskFormulationService(scope=scope, llm_client=llm_client)


RiskFormulationServiceDep = Annotated[RiskFormulationService, Depends(get_risk_formulation_service)]


def get_api_tokens_service(
    tokens_repository: ApiTokensRepositoryDep,
    settings: SettingsDep,
) -> ApiTokensService:
    """Создаёт сервис токенов доступа в рамках сессии запроса."""
    return ApiTokensService(
        tokens_repository=tokens_repository,
        max_active_tokens=settings.auth.api_token_max_active,
    )


ApiTokensServiceDep = Annotated[ApiTokensService, Depends(get_api_tokens_service)]


def get_users_service(
    users_repository: UsersRepositoryDep,
    avatar_storage: AvatarStorageDep,
) -> UsersService:
    """Создаёт сервис профиля пользователя."""
    return UsersService(users_repository=users_repository, avatar_storage=avatar_storage)


UsersServiceDep = Annotated[UsersService, Depends(get_users_service)]


def get_projects_service(
    boards_repository: ProjectBoardsRepositoryDep,
    projects_repository: ProjectsRepositoryDep,
    members_repository: ProjectMembersRepositoryDep,
    stages_repository: ProjectStagesRepositoryDep,
    tasks_repository: TasksRepositoryDep,
    storage: TaskAttachmentStorageDep,
    knowledge_events: KnowledgeEventsDep,
    unit_of_work: UnitOfWorkDep,
    users_repository: UsersRepositoryDep,
    deadline_changes_repository: ProjectDeadlineChangesRepositoryDep,
) -> ProjectsService:
    """Создаёт сервис проектов."""
    return ProjectsService(
        projects_repository=projects_repository,
        boards_repository=boards_repository,
        members_repository=members_repository,
        stages_repository=stages_repository,
        tasks_repository=tasks_repository,
        attachment_storage=storage,
        knowledge_events=knowledge_events,
        unit_of_work=unit_of_work,
        users_repository=users_repository,
        deadline_changes_repository=deadline_changes_repository,
    )


ProjectsServiceDep = Annotated[ProjectsService, Depends(get_projects_service)]


def get_project_members_service(
    members_repository: ProjectMembersRepositoryDep,
    users_repository: UsersRepositoryDep,
    participants_repository: TaskParticipantsRepositoryDep,
    tasks_repository: TasksRepositoryDep,
    unit_of_work: UnitOfWorkDep,
    users_service: UsersServiceDep,
    risks_repository: ProjectRiskRepositoryDep,
    knowledge_events: KnowledgeEventsDep,
) -> ProjectMembersService:
    """Создаёт сервис управления проектной командой."""
    return ProjectMembersService(
        members_repository=members_repository,
        users_repository=users_repository,
        participants_repository=participants_repository,
        tasks_repository=tasks_repository,
        unit_of_work=unit_of_work,
        users_service=users_service,
        risks_repository=risks_repository,
        knowledge_events=knowledge_events,
    )


ProjectMembersServiceDep = Annotated[
    ProjectMembersService,
    Depends(get_project_members_service),
]


def get_project_stickers_service(
    stickers_repository: ProjectStickersRepositoryDep,
    tasks_repository: TasksRepositoryDep,
    boards_repository: ProjectBoardsRepositoryDep,
    shapes_repository: BoardShapesRepositoryDep,
    unit_of_work: UnitOfWorkDep,
    session: DbSessionDep,
) -> ProjectStickersService:
    return ProjectStickersService(
        stickers_repository=stickers_repository,
        tasks_repository=tasks_repository,
        boards_repository=boards_repository,
        shapes_repository=shapes_repository,
        editor_repository=BoardEditorRepository(session),
        unit_of_work=unit_of_work,
    )


ProjectStickersServiceDep = Annotated[
    ProjectStickersService,
    Depends(get_project_stickers_service),
]


def get_project_stages_service(
    stages_repository: ProjectStagesRepositoryDep,
    projects_repository: ProjectsRepositoryDep,
    tasks_repository: TasksRepositoryDep,
    unit_of_work: UnitOfWorkDep,
) -> ProjectStagesService:
    """Создаёт сервис стадий проекта."""
    return ProjectStagesService(
        stages_repository=stages_repository,
        projects_repository=projects_repository,
        tasks_repository=tasks_repository,
        unit_of_work=unit_of_work,
    )


ProjectStagesServiceDep = Annotated[
    ProjectStagesService,
    Depends(get_project_stages_service),
]


def get_tasks_service(
    users_repository: UsersRepositoryDep,
    reports_repository: TaskReportsRepositoryDep,
    tasks_repository: TasksRepositoryDep,
    members_repository: ProjectMembersRepositoryDep,
    participants_repository: TaskParticipantsRepositoryDep,
    projects_repository: ProjectsRepositoryDep,
    stages_repository: ProjectStagesRepositoryDep,
    comments_repository: TaskCommentsRepositoryDep,
    activity_repository: TaskActivityRepositoryDep,
    wbs_nodes_repository: WbsNodesRepositoryDep,
    storage: TaskAttachmentStorageDep,
    knowledge_events: KnowledgeEventsDep,
    unit_of_work: UnitOfWorkDep,
) -> TasksService:
    """Создаёт сервис задач со всеми доменными зависимостями."""
    return TasksService(
        tasks_repository=tasks_repository,
        users_repository=users_repository,
        reports_repository=reports_repository,
        members_repository=members_repository,
        participants_repository=participants_repository,
        projects_repository=projects_repository,
        stages_repository=stages_repository,
        comments_repository=comments_repository,
        activity_repository=activity_repository,
        wbs_nodes_repository=wbs_nodes_repository,
        attachment_storage=storage,
        knowledge_events=knowledge_events,
        unit_of_work=unit_of_work,
    )


TasksServiceDep = Annotated[TasksService, Depends(get_tasks_service)]


def get_wbs_nodes_service(
    wbs_nodes_repository: WbsNodesRepositoryDep,
    projects_repository: ProjectsRepositoryDep,
    stages_repository: ProjectStagesRepositoryDep,
    tasks_repository: TasksRepositoryDep,
    activity_repository: TaskActivityRepositoryDep,
    knowledge_events: KnowledgeEventsDep,
    unit_of_work: UnitOfWorkDep,
) -> WbsNodesService:
    """Создаёт сервис структуры ИСР."""
    return WbsNodesService(
        wbs_nodes_repository=wbs_nodes_repository,
        projects_repository=projects_repository,
        stages_repository=stages_repository,
        tasks_repository=tasks_repository,
        activity_repository=activity_repository,
        knowledge_events=knowledge_events,
        unit_of_work=unit_of_work,
    )


WbsNodesServiceDep = Annotated[WbsNodesService, Depends(get_wbs_nodes_service)]


def get_wbs_suggestion_service(
    scope: WbsSuggestionScopeDep,
    llm_client: LlmClientDep,
) -> WbsSuggestionService:
    """Создаёт сервис предложения структуры ИСР.

    Сервис получает фабрику короткой области, а не сессию запроса: между
    чтением проекта и ответом модели соединение с базой удерживаться не
    должно.
    """
    return WbsSuggestionService(scope=scope, llm_client=llm_client)


WbsSuggestionServiceDep = Annotated[WbsSuggestionService, Depends(get_wbs_suggestion_service)]


def get_analytics_service(
    scope: AnalyticsScopeDep,
    llm_client: LlmClientDep,
) -> AnalyticsService:
    """Создаёт сервис аналитического свода дашборда.

    Сервис получает фабрику короткой области: между сбором данных и
    записью результата стоит вызов модели, и соединение с базой на это
    время удерживаться не должно.
    """
    return AnalyticsService(scope=scope, llm_client=llm_client)


AnalyticsServiceDep = Annotated[AnalyticsService, Depends(get_analytics_service)]


def get_dashboard_service(
    projects_repository: ProjectsRepositoryDep,
    members_repository: ProjectMembersRepositoryDep,
    stages_repository: ProjectStagesRepositoryDep,
    tasks_repository: TasksRepositoryDep,
    risks_repository: ProjectRiskRepositoryDep,
) -> DashboardService:
    """Создаёт сервис общей сводки по проектам."""
    return DashboardService(
        projects_repository=projects_repository,
        members_repository=members_repository,
        stages_repository=stages_repository,
        tasks_repository=tasks_repository,
        risks_repository=risks_repository,
    )


DashboardServiceDep = Annotated[DashboardService, Depends(get_dashboard_service)]


def get_calendar_service(
    projects_repository: ProjectsRepositoryDep,
    tasks_repository: TasksRepositoryDep,
    stages_repository: ProjectStagesRepositoryDep,
    wbs_nodes_repository: WbsNodesRepositoryDep,
    activity_repository: TaskActivityRepositoryDep,
    milestones_repository: MilestonesRepositoryDep,
    dependencies_repository: TaskDependenciesRepositoryDep,
) -> CalendarService:
    """Создаёт сервис календаря проекта."""
    return CalendarService(
        projects_repository=projects_repository,
        tasks_repository=tasks_repository,
        stages_repository=stages_repository,
        wbs_nodes_repository=wbs_nodes_repository,
        activity_repository=activity_repository,
        milestones_repository=milestones_repository,
        dependencies_repository=dependencies_repository,
    )


CalendarServiceDep = Annotated[CalendarService, Depends(get_calendar_service)]


def get_calendar_scenario_service(
    projects_repository: ProjectsRepositoryDep,
    tasks_repository: TasksRepositoryDep,
    dependencies_repository: TaskDependenciesRepositoryDep,
    milestones_repository: MilestonesRepositoryDep,
    activity_repository: TaskActivityRepositoryDep,
    unit_of_work: UnitOfWorkDep,
) -> CalendarScenarioService:
    """Создаёт сервис preview и применения календарных сценариев."""
    return CalendarScenarioService(
        projects_repository=projects_repository,
        tasks_repository=tasks_repository,
        dependencies_repository=dependencies_repository,
        milestones_repository=milestones_repository,
        activity_repository=activity_repository,
        unit_of_work=unit_of_work,
    )


CalendarScenarioServiceDep = Annotated[
    CalendarScenarioService,
    Depends(get_calendar_scenario_service),
]


def get_milestones_service(
    milestones_repository: MilestonesRepositoryDep,
    projects_repository: ProjectsRepositoryDep,
    wbs_nodes_repository: WbsNodesRepositoryDep,
    unit_of_work: UnitOfWorkDep,
    knowledge_events: KnowledgeEventsDep,
) -> MilestonesService:
    """Создаёт сервис проектных вех."""
    return MilestonesService(
        milestones_repository=milestones_repository,
        projects_repository=projects_repository,
        wbs_nodes_repository=wbs_nodes_repository,
        unit_of_work=unit_of_work,
        knowledge_events=knowledge_events,
    )


MilestonesServiceDep = Annotated[MilestonesService, Depends(get_milestones_service)]


def get_task_dependencies_service(
    dependencies_repository: TaskDependenciesRepositoryDep,
    projects_repository: ProjectsRepositoryDep,
    tasks_repository: TasksRepositoryDep,
    unit_of_work: UnitOfWorkDep,
) -> TaskDependenciesService:
    """Создаёт сервис графа зависимостей проекта."""
    return TaskDependenciesService(
        dependencies_repository=dependencies_repository,
        projects_repository=projects_repository,
        tasks_repository=tasks_repository,
        unit_of_work=unit_of_work,
    )


TaskDependenciesServiceDep = Annotated[
    TaskDependenciesService,
    Depends(get_task_dependencies_service),
]


def get_documents_service(
    documents_repository: DocumentsRepositoryDep,
    projects_repository: ProjectsRepositoryDep,
    knowledge_events: KnowledgeEventsDep,
    unit_of_work: UnitOfWorkDep,
) -> DocumentsService:
    """Создаёт сервис документов проекта."""
    return DocumentsService(
        documents_repository=documents_repository,
        projects_repository=projects_repository,
        knowledge_events=knowledge_events,
        unit_of_work=unit_of_work,
    )


DocumentsServiceDep = Annotated[DocumentsService, Depends(get_documents_service)]


def build_project_agent_config(settings: Settings) -> ProjectAgentConfig:
    """Собирает неизменяемую конфигурацию Project Agent из настроек."""
    return ProjectAgentConfig(
        knowledge_enabled=settings.knowledge.knowledge_enabled,
        semantic_limit=settings.knowledge.knowledge_agent_semantic_limit,
        score_threshold=settings.knowledge.qdrant_score_threshold,
        chunk_target_chars=settings.knowledge.knowledge_chunk_target_chars,
        chunk_overlap_chars=settings.knowledge.knowledge_chunk_overlap_chars,
        tool_rounds=settings.agent.agent_tool_rounds,
        context_tokens=settings.agent.agent_context_tokens,
        retrieval_limit=settings.agent.agent_retrieval_limit,
        history_tokens=settings.agent.agent_history_tokens,
        app_public_url=settings.app.app_public_url,
    )


TIM_ACTIONS = PROJECT_ACTIONS + PERSONAL_ACTIONS


def build_knowledge_tool_executor(query: ProjectQueryService) -> AgentToolExecutor:
    """Регистрирует первый набор инструментов; новые возможности добавляются здесь."""
    descriptions = {
        "list_sources": "Перечень текущих источников проекта с постраничным продолжением.",
        "read_source": "Полный текст известного источника по страницам символов.",
        "related_sources": "Связи источника с другими объектами текущего проекта.",
        "search_sources": "Полнотекстовый поиск по всем типам источников проекта.",
    }

    def bind(name: str) -> AgentToolHandler:
        async def read(context: AgentToolContext, parameters: BaseModel) -> dict[str, Any]:
            request = KnowledgeReadRequest(name=name, **parameters.model_dump())
            return await query.read_knowledge(project_id=context.project_id, request=request)

        return read

    return AgentToolExecutor(
        [
            AgentTool(name, description, KnowledgeReadParameters, bind(name), modes=PROJECT_MODES)
            for name, description in descriptions.items()
        ]
    )


def get_agent_actions_service(
    scope: AgentProjectToolScopeDep, settings: SettingsDep
) -> AgentActionsService:
    """Собирает общий сценарий проектных инструментов для чата и MCP."""
    return AgentActionsService(
        scope=scope,
        actions=TIM_ACTIONS,
        confirm_ttl=timedelta(hours=settings.agent.agent_action_confirm_ttl_hours),
    )


AgentActionsServiceDep = Annotated[AgentActionsService, Depends(get_agent_actions_service)]


def get_agent_files_service(scope: AgentProjectToolScopeDep) -> AgentFilesService:
    """Собирает сценарии приватных загрузок в чат."""
    return AgentFilesService(scope=scope)


AgentFilesServiceDep = Annotated[AgentFilesService, Depends(get_agent_files_service)]


def build_project_tool_executor(
    query: ProjectQueryService, actions: AgentActionsService
) -> AgentToolExecutor:
    """Регистрирует общий полный набор без дублирования доменных обработчиков."""
    registered = list(build_knowledge_tool_executor(query).tools.values())

    def bind(name: str) -> AgentToolHandler:
        async def execute(context: AgentToolContext, parameters: BaseModel) -> dict[str, Any]:
            return await actions.execute(
                context=context,
                name=name,
                arguments=parameters.model_dump(mode="json", exclude_unset=True),
            )

        return execute

    registered.extend(
        AgentTool(
            definition.name,
            definition.description,
            definition.parameters,
            bind(definition.name),
            definition.mutating,
            definition.requires_confirmation,
            definition.modes,
        )
        for definition in actions.actions.values()
    )

    async def project_action(
        context: AgentToolContext, parameters: ProjectActionInput
    ) -> dict[str, Any]:
        """Проектное действие в проекте по ключу — из личного виджета Ариса."""
        definition = actions.actions.get(parameters.name)
        if definition is None or not definition.needs_project:
            return {
                "error": "Такого проектного действия нет. Имена и схемы — через get_tool_schemas."
            }
        # Обёртка не изменяющая, а действие внутри может менять проект: право
        # записи вопроса и права ключа проверяются здесь, как у прямого вызова.
        if definition.mutating and not (context.can_write and context.allows(definition.name)):
            return {"error": "Текущий запрос не разрешает изменения проекта."}
        if definition.name == "attach_uploaded_file":
            return {"error": "Файл прикрепляется только в проекте страницы."}
        try:
            project_id = await query.resolve_project_id(project_key=parameters.project_key)
        except ServiceError:
            return {"error": "Проект с таким ключом недоступен."}
        # Права на проект проверит сам сценарий действия от имени пользователя.
        return await actions.execute(
            context=replace(context, project_id=project_id),
            name=definition.name,
            arguments=parameters.arguments,
        )

    registered.append(
        AgentTool(
            "run_project_action",
            "Выполнить проектное действие (list_tasks, create_task, get_project и др.) в проекте "
            "по его ключу; схему действия получи через get_tool_schemas.",
            ProjectActionInput,
            project_action,
            modes=WIDGET_MODES,
        )
    )

    async def schemas(context: AgentToolContext, parameters: ToolSchemasInput) -> dict[str, Any]:
        """Отдаёт точные схемы выбранных инструментов без раздувания каждого шага."""
        wanted = set(parameters.names)
        return {
            "tools": [
                item
                for item in executor.describe()
                if item["name"] in wanted
                and (not item["mutating"] or (context.can_write and context.allows(item["name"])))
                # Схема проектного действия нужна и для run_project_action вне проекта.
                and (
                    context.mode in executor.tools[item["name"]].modes
                    or (
                        context.mode in WIDGET_MODES
                        and item["name"] in actions.actions
                        and actions.actions[item["name"]].needs_project
                    )
                )
            ]
        }

    registered.append(
        AgentTool(
            "get_tool_schemas",
            "Получить точные схемы аргументов перед вызовом выбранных инструментов.",
            ToolSchemasInput,
            schemas,
        )
    )
    executor = AgentToolExecutor(registered)
    return executor


def get_agent_tool_executor(
    scope: ProjectKnowledgeQueryScopeDep, actions: AgentActionsServiceDep
) -> AgentToolExecutor:
    """Внедряет чтение знаний через общий исполнитель инструментов."""
    return build_project_tool_executor(ProjectQueryService(scope=scope), actions)


AgentToolExecutorDep = Annotated[AgentToolExecutor, Depends(get_agent_tool_executor)]


def get_project_agent_service(
    scope: ProjectAgentScopeDep,
    llm_client: LlmClientDep,
    embedding_client: EmbeddingClientDep,
    qdrant_client: QdrantClientDep,
    settings: SettingsDep,
    tools: AgentToolExecutorDep,
) -> ProjectAgentService:
    """Создаёт Project Agent.

    Сервис получает фабрику короткой области: между сбором данных и
    ответом модели соединение с базой удерживаться не должно.
    """
    return ProjectAgentService(
        scope=scope,
        llm_client=llm_client,
        embedding_client=embedding_client,
        qdrant_client=qdrant_client,
        config=build_project_agent_config(settings),
        tools=tools,
        agent_model=build_agent_model(settings.llm, llm_client.http_client),
    )


ProjectAgentServiceDep = Annotated[ProjectAgentService, Depends(get_project_agent_service)]


def build_agent_conversation_config(settings: Settings) -> AgentConversationConfig:
    """Передаёт диалогам только используемые бюджеты исполнения и памяти."""
    return AgentConversationConfig(
        turn_timeout_seconds=settings.agent.agent_turn_timeout_seconds,
        history_messages=settings.agent.agent_history_messages,
        summary_batch_size=settings.agent.agent_summary_batch_size,
    )


def get_tracker_agent_service(
    scope: AgentProjectToolScopeDep,
    agent: ProjectAgentServiceDep,
    llm_client: LlmClientDep,
    settings: SettingsDep,
) -> TrackerAgentService:
    return TrackerAgentService(
        scope=scope,
        agent=agent,
        llm_client=llm_client,
        context_tokens=settings.agent.agent_context_tokens,
        history_tokens=settings.agent.agent_history_tokens,
    )


TrackerAgentServiceDep = Annotated[TrackerAgentService, Depends(get_tracker_agent_service)]


def get_agent_conversations_service(
    tracker: TrackerAgentServiceDep,
    scope: AgentConversationScopeDep,
    agent: ProjectAgentServiceDep,
    llm_client: LlmClientDep,
    settings: SettingsDep,
) -> AgentConversationsService:
    """Собирает сценарии постоянных диалогов из явных зависимостей."""
    return AgentConversationsService(
        scope=scope,
        tracker=tracker,
        agent=agent,
        llm_client=llm_client,
        config=build_agent_conversation_config(settings),
    )


AgentConversationsServiceDep = Annotated[
    AgentConversationsService, Depends(get_agent_conversations_service)
]


def build_agent_worker(
    *, session_factory: SessionFactory, settings: Settings, runtime: KnowledgeRuntime
) -> AgentWorker:
    """Собирает фоновое исполнение с теми же сервисами и клиентами, что HTTP."""
    tools = build_project_tool_executor(
        ProjectQueryService(
            scope=build_project_knowledge_query_scope(session_factory=session_factory)
        ),
        AgentActionsService(
            scope=build_agent_project_tool_scope(
                session_factory=session_factory, settings=settings
            ),
            actions=TIM_ACTIONS,
            confirm_ttl=timedelta(hours=settings.agent.agent_action_confirm_ttl_hours),
        ),
    )
    agent = ProjectAgentService(
        scope=build_project_agent_scope(session_factory=session_factory, settings=settings),
        llm_client=runtime.llm_client,
        embedding_client=runtime.embedding_client,
        qdrant_client=runtime.qdrant_client,
        config=build_project_agent_config(settings),
        tools=tools,
        agent_model=build_agent_model(settings.llm, runtime.http_client),
    )
    service = AgentConversationsService(
        scope=build_agent_conversation_scope(session_factory=session_factory),
        agent=agent,
        llm_client=runtime.llm_client,
        config=build_agent_conversation_config(settings),
    )
    tracker = TrackerAgentService(
        scope=build_agent_project_tool_scope(session_factory=session_factory, settings=settings),
        agent=agent,
        llm_client=runtime.llm_client,
        context_tokens=settings.agent.agent_context_tokens,
        history_tokens=settings.agent.agent_history_tokens,
    )
    service.tracker = tracker
    chat = TimChatService(
        scope=build_tim_chat_scope(session_factory=session_factory),
        agent=agent,
        config=TimChatConfig(turn_timeout_seconds=settings.agent.agent_turn_timeout_seconds),
    )
    return AgentWorker(
        services=[service, chat],
        poll_seconds=settings.agent.agent_poll_seconds,
        concurrency=settings.agent.agent_concurrency,
    )


def get_document_links_service(
    document_links_repository: DocumentLinksRepositoryDep,
    documents_repository: DocumentsRepositoryDep,
    tasks_repository: TasksRepositoryDep,
    projects_repository: ProjectsRepositoryDep,
    members_repository: ProjectMembersRepositoryDep,
) -> DocumentLinksService:
    """Создаёт сервис связей документов."""
    return DocumentLinksService(
        document_links_repository=document_links_repository,
        documents_repository=documents_repository,
        tasks_repository=tasks_repository,
        projects_repository=projects_repository,
        members_repository=members_repository,
    )


DocumentLinksServiceDep = Annotated[
    DocumentLinksService,
    Depends(get_document_links_service),
]


def get_task_comments_service(
    users_repository: UsersRepositoryDep,
    comments_repository: TaskCommentsRepositoryDep,
    tasks_repository: TasksRepositoryDep,
    activity_repository: TaskActivityRepositoryDep,
    knowledge_events: KnowledgeEventsDep,
    unit_of_work: UnitOfWorkDep,
) -> TaskCommentsService:
    """Создаёт сервис комментариев задач."""
    return TaskCommentsService(
        users_repository=users_repository,
        comments_repository=comments_repository,
        tasks_repository=tasks_repository,
        activity_repository=activity_repository,
        knowledge_events=knowledge_events,
        unit_of_work=unit_of_work,
    )


TaskCommentsServiceDep = Annotated[
    TaskCommentsService,
    Depends(get_task_comments_service),
]


def get_task_activity_service(
    activity_repository: TaskActivityRepositoryDep,
    tasks_repository: TasksRepositoryDep,
) -> TaskActivityService:
    """Создаёт сервис истории задач."""
    return TaskActivityService(
        activity_repository=activity_repository,
        tasks_repository=tasks_repository,
    )


TaskActivityServiceDep = Annotated[
    TaskActivityService,
    Depends(get_task_activity_service),
]


def get_task_attachments_service(
    attachments_repository: TaskAttachmentsRepositoryDep,
    tasks_repository: TasksRepositoryDep,
    storage: TaskAttachmentStorageDep,
    knowledge_events: KnowledgeEventsDep,
    unit_of_work: UnitOfWorkDep,
) -> TaskAttachmentsService:
    """Создаёт сервис файлов задач."""
    return TaskAttachmentsService(
        attachments_repository=attachments_repository,
        tasks_repository=tasks_repository,
        storage=storage,
        knowledge_events=knowledge_events,
        unit_of_work=unit_of_work,
    )


TaskAttachmentsServiceDep = Annotated[
    TaskAttachmentsService,
    Depends(get_task_attachments_service),
]


def get_checklist_suggestion_service(
    scope: ChecklistSuggestionScopeDep,
    llm_client: LlmClientDep,
    vision: VisionCapabilityDep,
    storage: TaskAttachmentStorageDep,
    settings: SettingsDep,
) -> TaskChecklistSuggestionService:
    """Создаёт генерацию чек-листа с адаптерами файлов и модели."""
    return TaskChecklistSuggestionService(
        scope=scope,
        llm_client=llm_client,
        vision=vision,
        storage=storage,
        file_context_limit=settings.llm.task_rephrase_file_max_chars,
        max_file_size=TaskAttachmentsService.MAX_FILE_SIZE,
    )


ChecklistSuggestionServiceDep = Annotated[
    TaskChecklistSuggestionService, Depends(get_checklist_suggestion_service)
]


def get_task_description_service(
    scope: TaskDescriptionScopeDep,
    llm_client: LlmClientDep,
    vision: VisionCapabilityDep,
    settings: SettingsDep,
) -> TaskDescriptionService:
    """Создаёт сервис переформулирования черновика задачи.

    Сервис получает фабрику короткой области: соединение с базой не
    должно удерживаться во время вызова модели.
    """
    return TaskDescriptionService(
        scope=scope,
        llm_client=llm_client,
        vision=vision,
        file_context_limit=settings.llm.task_rephrase_file_max_chars,
        max_file_size=TaskAttachmentsService.MAX_FILE_SIZE,
    )


TaskDescriptionServiceDep = Annotated[
    TaskDescriptionService,
    Depends(get_task_description_service),
]


def get_task_document_import_service(
    scope: TaskDocumentImportScopeDep,
    storage: TaskAttachmentStorageDep,
    vision: VisionCapabilityDep,
    settings: SettingsDep,
) -> TaskDocumentImportService:
    """Создаёт составной импорт документа из формы задачи.

    Сервис получает фабрику короткой области: между чтением задачи и
    записью результата стоит распознавание файла.
    """
    return TaskDocumentImportService(
        scope=scope,
        attachment_storage=storage,
        vision=vision,
        max_file_size=TaskAttachmentsService.MAX_FILE_SIZE,
    )


TaskDocumentImportServiceDep = Annotated[
    TaskDocumentImportService,
    Depends(get_task_document_import_service),
]


def get_project_boards_service(
    boards_repository: ProjectBoardsRepositoryDep,
    stickers_repository: ProjectStickersRepositoryDep,
    shapes_repository: BoardShapesRepositoryDep,
    unit_of_work: UnitOfWorkDep,
) -> ProjectBoardsService:
    return ProjectBoardsService(
        boards_repository=boards_repository,
        stickers_repository=stickers_repository,
        shapes_repository=shapes_repository,
        unit_of_work=unit_of_work,
    )


ProjectBoardsServiceDep = Annotated[ProjectBoardsService, Depends(get_project_boards_service)]


def get_board_shapes_service(
    boards_repository: ProjectBoardsRepositoryDep,
    shapes_repository: BoardShapesRepositoryDep,
    stickers_repository: ProjectStickersRepositoryDep,
    tasks_repository: TasksRepositoryDep,
    unit_of_work: UnitOfWorkDep,
    session: DbSessionDep,
) -> BoardShapesService:
    return BoardShapesService(
        boards_repository=boards_repository,
        shapes_repository=shapes_repository,
        stickers_repository=stickers_repository,
        tasks_repository=tasks_repository,
        unit_of_work=unit_of_work,
        editor_repository=BoardEditorRepository(session),
    )


BoardShapesServiceDep = Annotated[BoardShapesService, Depends(get_board_shapes_service)]


def get_task_reports_service(
    reports: TaskReportsRepositoryDep,
    tasks: TasksRepositoryDep,
    comments: TaskCommentsRepositoryDep,
    access: AccessServiceDep,
    unit_of_work: UnitOfWorkDep,
    projects: ProjectsRepositoryDep,
) -> TaskReportsService:
    return TaskReportsService(
        reports=reports,
        tasks=tasks,
        comments=comments,
        access=access,
        unit_of_work=unit_of_work,
        projects=projects,
    )


TaskReportsServiceDep = Annotated[TaskReportsService, Depends(get_task_reports_service)]


def get_task_attention_service(
    repository: TaskAttentionRepositoryDep, unit_of_work: UnitOfWorkDep
) -> TaskAttentionService:
    return TaskAttentionService(repository, unit_of_work)


TaskAttentionServiceDep = Annotated[TaskAttentionService, Depends(get_task_attention_service)]


def get_task_moves_service(
    tasks_repository: TasksRepositoryDep,
    task_moves_repository: TaskMovesRepositoryDep,
    participants_repository: TaskParticipantsRepositoryDep,
    members_repository: ProjectMembersRepositoryDep,
    projects_repository: ProjectsRepositoryDep,
    stages_repository: ProjectStagesRepositoryDep,
    users_repository: UsersRepositoryDep,
    activity_repository: TaskActivityRepositoryDep,
    knowledge_events: KnowledgeEventsDep,
    access_service: AccessServiceDep,
    unit_of_work: UnitOfWorkDep,
) -> TaskMovesService:
    return TaskMovesService(
        tasks_repository=tasks_repository,
        task_moves_repository=task_moves_repository,
        participants_repository=participants_repository,
        members_repository=members_repository,
        projects_repository=projects_repository,
        stages_repository=stages_repository,
        users_repository=users_repository,
        activity_repository=activity_repository,
        knowledge_events=knowledge_events,
        access_service=access_service,
        unit_of_work=unit_of_work,
    )


TaskMovesServiceDep = Annotated[TaskMovesService, Depends(get_task_moves_service)]


def get_project_icons_service(
    projects_repository: ProjectsRepositoryDep,
    unit_of_work: UnitOfWorkDep,
    storage: TaskAttachmentStorageDep,
) -> ProjectIconsService:
    return ProjectIconsService(
        projects_repository=projects_repository, unit_of_work=unit_of_work, storage=storage
    )


ProjectIconsServiceDep = Annotated[ProjectIconsService, Depends(get_project_icons_service)]


def get_api_keys_service(
    keys_repository: ApiKeysRepositoryDep, users_repository: UsersRepositoryDep
) -> ApiKeysService:
    return ApiKeysService(keys_repository, users_repository)


ApiKeysServiceDep = Annotated[ApiKeysService, Depends(get_api_keys_service)]


def get_api_key_usage_service(usage_repository: ApiKeyUsageRepositoryDep) -> ApiKeyUsageService:
    return ApiKeyUsageService(usage_repository)


ApiKeyUsageServiceDep = Annotated[ApiKeyUsageService, Depends(get_api_key_usage_service)]


def get_personal_calendar_service(
    users_repository: UsersRepositoryDep,
    members_repository: ProjectMembersRepositoryDep,
    projects_repository: ProjectsRepositoryDep,
    stages_repository: ProjectStagesRepositoryDep,
    tasks_repository: TasksRepositoryDep,
    milestones_repository: MilestonesRepositoryDep,
) -> PersonalCalendarService:
    return PersonalCalendarService(
        users_repository=users_repository,
        members_repository=members_repository,
        projects_repository=projects_repository,
        stages_repository=stages_repository,
        tasks_repository=tasks_repository,
        milestones_repository=milestones_repository,
    )


PersonalCalendarServiceDep = Annotated[
    PersonalCalendarService, Depends(get_personal_calendar_service)
]


def get_calendar_workload_service(
    projects_repository: ProjectsRepositoryDep,
    members_repository: ProjectMembersRepositoryDep,
    users_repository: UsersRepositoryDep,
    tasks_repository: TasksRepositoryDep,
) -> CalendarWorkloadService:
    return CalendarWorkloadService(
        projects_repository=projects_repository,
        members_repository=members_repository,
        users_repository=users_repository,
        tasks_repository=tasks_repository,
    )


CalendarWorkloadServiceDep = Annotated[
    CalendarWorkloadService, Depends(get_calendar_workload_service)
]


def get_calendar_feed_service(
    tokens_repository: CalendarFeedTokensRepositoryDep,
    users_repository: UsersRepositoryDep,
    calendar_service: PersonalCalendarServiceDep,
) -> CalendarFeedService:
    return CalendarFeedService(
        tokens_repository=tokens_repository,
        users_repository=users_repository,
        calendar_service=calendar_service,
    )


CalendarFeedServiceDep = Annotated[CalendarFeedService, Depends(get_calendar_feed_service)]


TimWidgetServiceDep = AgentConversationsServiceDep


def get_agent_requests_service(
    scope: AgentConversationScopeDep, settings: SettingsDep
) -> AgentRequestsService:
    return AgentRequestsService(scope=scope, links=RequestLinks(settings.app.app_public_url))


AgentRequestsServiceDep = Annotated[AgentRequestsService, Depends(get_agent_requests_service)]
