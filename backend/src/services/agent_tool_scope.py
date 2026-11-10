"""Явные зависимости одного короткого сценария инструмента проекта."""

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass

from src.repositories.agent_conversations import AgentConversationsRepository
from src.repositories.agent_files import AgentFilesRepository
from src.repositories.agent_messages import AgentMessagesRepository
from src.repositories.agent_tool_runs import AgentToolRunsRepository
from src.repositories.api_keys import ApiKeysRepository
from src.repositories.chat_agent_turns import ChatAgentTurnsRepository
from src.repositories.projects import ProjectsRepository
from src.repositories.task_activity import TaskActivityRepository
from src.repositories.tasks import TasksRepository
from src.repositories.unit_of_work import AgentToolUnitOfWork
from src.repositories.users import UsersRepository
from src.services.access import AccessService
from src.services.analytics import AnalyticsReportReader
from src.services.auth import AuthService
from src.services.board_shapes import BoardShapesService
from src.services.board_stickers import BoardStickersService
from src.services.boards import BoardsService
from src.services.calendar import CalendarService
from src.services.calendar_scenarios import CalendarScenarioService
from src.services.document_links import DocumentLinksService
from src.services.documents import DocumentsService
from src.services.milestones import MilestonesService
from src.services.project_members import ProjectMembersService
from src.services.project_risks import ProjectRiskService
from src.services.project_stages import ProjectStagesService
from src.services.projects import ProjectsService
from src.services.task_attachments import TaskAttachmentsService
from src.services.task_comments import TaskCommentsService
from src.services.task_dependencies import TaskDependenciesService
from src.services.task_reports import TaskReportsService
from src.services.tasks import TasksService
from src.services.wbs_nodes import WbsNodesService
from src.storage.agent_tool_files import AgentToolFileStorage


@dataclass(frozen=True, slots=True)
class AgentProjectToolScope:
    """Доменные сервисы и журнал одной атомарной операции."""

    projects: ProjectsService
    tasks: TasksService
    comments: TaskCommentsService
    members: ProjectMembersService
    stickers: BoardStickersService
    stages: ProjectStagesService
    documents: DocumentsService
    links: DocumentLinksService
    attachments: TaskAttachmentsService
    wbs: WbsNodesService
    milestones: MilestonesService
    dependencies: TaskDependenciesService
    calendar: CalendarService
    scenarios: CalendarScenarioService
    risks: ProjectRiskService
    analytics_reader: AnalyticsReportReader
    access: AccessService
    users: UsersRepository
    conversations: AgentConversationsRepository
    messages: AgentMessagesRepository
    runs: AgentToolRunsRepository
    uploads: AgentFilesRepository
    upload_storage: AgentToolFileStorage
    unit_of_work: AgentToolUnitOfWork
    boards: BoardsService | None = None
    board_shapes: BoardShapesService | None = None
    turns: ChatAgentTurnsRepository | None = None
    project_rows: ProjectsRepository | None = None
    task_rows: TasksRepository | None = None
    activity_rows: TaskActivityRepository | None = None
    reports: TaskReportsService | None = None
    api_keys: ApiKeysRepository | None = None
    auth: AuthService | None = None


AgentProjectToolScopeFactory = Callable[[], AbstractAsyncContextManager[AgentProjectToolScope]]
