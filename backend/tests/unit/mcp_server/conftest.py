"""Общие дублёры для проверок MCP-инструментов.

После перевода MCP на сервисный слой инструмент получает готовые use
case и не собирает репозитории. Поэтому тесты подменяют сервисы, а не
модули с репозиториями: так проверяется контракт инструмента, а не
внутреннее устройство слоёв под ним.
"""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from src.clients.vision import DisabledVisionCapability
from src.core.settings import get_settings
from src.db.models.api_tokens import ApiTokenScope
from src.mcp_server import context as ctx
from src.mcp_server import server as srv
from src.mcp_server import write_tools as wt
from src.mcp_server.context import READ_ONLY_TOKEN, ToolContext
from src.mcp_server.registry import current_tool_call
from src.mcp_server.services import ToolServices
from src.services.access import AccessGrant, AccessService
from src.services.agent_actions import AgentActionsService
from src.services.auth import AuthService, Principal
from src.services.calendar import CalendarService
from src.services.milestones import MilestonesService
from src.services.project_members import ProjectMembersService
from src.services.project_query import ProjectQueryService
from src.services.project_risks import ProjectRiskService
from src.services.task_comments import TaskCommentsService
from src.services.tasks import TasksService

PROJECT_ID = 1
PROJECT_KEY = "PROJ"


class FakeContext:
    """Контекст вызова MCP с заголовком токена."""

    def __init__(self, headers: dict[str, str] | None = None) -> None:
        self.headers = {"Authorization": "Bearer telos_token_test"} if headers is None else headers


def make_principal(scope: ApiTokenScope = ApiTokenScope.READ) -> Principal:
    """Принципал владельца токена."""
    return Principal(
        user_id=1,
        username="tester",
        last_name="Тестов",
        first_name="Тест",
        middle_name=None,
        scope=scope,
        via_api_token=True,
    )


def make_services() -> ToolServices:
    """Собирает набор сервисов-дублёров с фиксированными спецификациями."""
    query = AsyncMock(spec=ProjectQueryService)
    query.resolve_project_id.return_value = PROJECT_ID

    access = AsyncMock(spec=AccessService)
    access.ensure_project_access.return_value = AccessGrant(
        project_id=PROJECT_ID,
        resource_id=PROJECT_ID,
        is_owner=True,
    )

    return ToolServices(
        auth=AsyncMock(spec=AuthService),
        access=access,
        query=query,
        tasks=AsyncMock(spec=TasksService),
        comments=AsyncMock(spec=TaskCommentsService),
        milestones=AsyncMock(spec=MilestonesService),
        calendar=AsyncMock(spec=CalendarService),
        members=AsyncMock(spec=ProjectMembersService),
        risks=AsyncMock(spec=ProjectRiskService),
        actions=AsyncMock(spec=AgentActionsService),
    )


def make_runtime() -> SimpleNamespace:
    """Контейнер клиентов, который в проде создаёт lifespan приложения."""
    return SimpleNamespace(
        embedding_client=AsyncMock(),
        qdrant_client=AsyncMock(),
        llm_client=AsyncMock(),
        vision=DisabledVisionCapability(),
    )


@pytest.fixture
def services() -> ToolServices:
    """Сервисы одного вызова инструмента."""
    return make_services()


@pytest.fixture
def tools(services: ToolServices, monkeypatch: pytest.MonkeyPatch):
    """Подменяет контекст вызова во всех модулях инструментов.

    Возвращает фабрику: тест выбирает права токена, а всё остальное
    остаётся тем же контекстом.
    """
    state: dict = {"scope": ApiTokenScope.WRITE}

    def install(scope: ApiTokenScope = ApiTokenScope.WRITE) -> ToolServices:
        state["scope"] = scope
        return services

    @asynccontextmanager
    async def fake_tool_context(context, *, require_write: bool | None = None):
        principal = make_principal(state["scope"])
        if require_write is None:
            call = current_tool_call()
            require_write = bool(call and call.spec.write)
        if require_write and not principal.can_write:
            raise ToolError(READ_ONLY_TOKEN)
        yield ToolContext(
            principal=principal,
            services=services,
            runtime=make_runtime(),
            settings=get_settings(),
        )

    from src.mcp_server import project_tools, risk_tools, task_tools

    for module in (srv, wt, ctx, risk_tools, project_tools, task_tools):
        monkeypatch.setattr(module, "tool_context", fake_tool_context, raising=False)
    return install
