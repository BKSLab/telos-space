"""Проверки аутентификации и разрешения сущностей в MCP-инструментах.

Контекст вызова — единственное место, где MCP решает, кто спрашивает и
что ему доступно. Он обязан отвечать теми же правилами, что и HTTP-слой,
поэтому проверяется именно обращение к общим сервисам, а не собственная
логика доступа.
"""

from types import SimpleNamespace

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from src.core.app_state import (
    RUNTIME_STATE_KEY,
    SESSION_FACTORY_STATE_KEY,
    SETTINGS_STATE_KEY,
)
from src.core.settings import get_settings
from src.db.models.api_tokens import ApiTokenScope
from src.exceptions.access import ResourceNotAvailableError
from src.exceptions.auth import NotAuthenticatedError
from src.exceptions.projects import ProjectNotFoundError
from src.exceptions.tasks import TaskNotFoundError
from src.mcp_server import context as ctx
from src.mcp_server.context import (
    NOT_AUTHENTICATED,
    PROJECT_NOT_AVAILABLE,
    READ_ONLY_TOKEN,
    RESOURCES_MISSING,
    TASK_NOT_AVAILABLE,
    ToolContext,
    _authorization_header,
    ensure_project_access,
    resolve_project,
    resolve_task,
    tool_context,
)
from src.services.project_query import ResolvedTask
from tests.unit.mcp_server.conftest import (
    PROJECT_ID,
    make_principal,
    make_runtime,
    make_services,
)


class HeadersContext:
    """Контекст вызова MCP с произвольным набором заголовков."""

    def __init__(self, headers: dict[str, str] | None):
        self.headers = headers


class TransportContext:
    """Контекст вызова с состоянием запущенного приложения.

    Смонтированный MCP-транспорт видит state основного приложения, и
    именно оттуда берутся клиенты, настройки и фабрика сессий.
    """

    def __init__(self, *, state: dict | None = None, headers: dict[str, str] | None = None):
        self.headers = {"Authorization": "Bearer telos_token_test"} if headers is None else headers
        request = SimpleNamespace(state=SimpleNamespace(**(state or {})))
        self.request_context = SimpleNamespace(request=request)


def app_state() -> dict:
    """Состояние приложения, которое в проде наполняет lifespan."""

    class Factory:
        """Фабрика сессий с областью на один вызов инструмента."""

        def __call__(self):
            return self

        async def __aenter__(self):
            return object()

        async def __aexit__(self, *exc_info) -> bool:
            return False

    return {
        RUNTIME_STATE_KEY: make_runtime(),
        SETTINGS_STATE_KEY: get_settings(),
        SESSION_FACTORY_STATE_KEY: Factory(),
    }


def make_tools(scope: ApiTokenScope = ApiTokenScope.READ) -> ToolContext:
    """Контекст инструмента с сервисами-дублёрами."""
    return ToolContext(
        principal=make_principal(scope),
        services=make_services(),
        runtime=make_runtime(),
        settings=get_settings(),
    )


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        ({"Authorization": "Bearer telos_token_x"}, "Bearer telos_token_x"),
        ({"authorization": "Bearer telos_token_x"}, "Bearer telos_token_x"),
        ({"AUTHORIZATION": "Bearer telos_token_x"}, "Bearer telos_token_x"),
        ({"X-Other": "нет"}, None),
        ({}, None),
        (None, None),
    ],
)
def test_authorization_header_is_case_insensitive(
    headers: dict[str, str] | None,
    expected: str | None,
) -> None:
    """Заголовок ищется без учёта регистра: транспорты нормализуют по-разному."""
    assert _authorization_header(HeadersContext(headers)) == expected


async def test_resolve_project_hides_foreign_and_missing_projects() -> None:
    """Доступный проект отдаёт идентификатор, чужой и отсутствующий — одно сообщение."""
    # Доступный проект отдаётся идентификатором, а не ORM-моделью.
    tools = make_tools()

    assert await resolve_project(tools, "PROJ") == PROJECT_ID
    tools.services.access.ensure_project_access.assert_awaited_once_with(
        project_id=PROJECT_ID,
        user_id=1,
    )
    # Чужой проект недоступен, хотя и существует.
    tools = make_tools()
    tools.services.access.ensure_project_access.side_effect = ResourceNotAvailableError(
        resource="Проект",
        resource_id=PROJECT_ID,
    )

    with pytest.raises(ToolError) as error:
        await resolve_project(tools, "PROJ")

    assert str(error.value) == PROJECT_NOT_AVAILABLE
    # Несуществующий проект отвечает тем же текстом, что и чужой. Разные ответы позволили бы перебором ключей узнавать, какие проекты существуют в системе.
    tools = make_tools()
    tools.services.query.resolve_project_id.side_effect = ProjectNotFoundError(project_id=0)

    with pytest.raises(ToolError) as error:
        await resolve_project(tools, "NOPE")

    assert str(error.value) == PROJECT_NOT_AVAILABLE


MALFORMED_TASK_KEYS = ("", "   ", "PROJ", "PROJ-", "-142", "PROJ-abc")


async def test_resolve_task_rejects_malformed_key() -> None:
    """Некорректный ключ отклоняется до любых обращений к данным."""
    tools = make_tools()
    accepted: list[str] = []

    for value in MALFORMED_TASK_KEYS:
        try:
            await resolve_task(tools, value)
        except ToolError as error:
            if "PROJ-142" not in str(error):
                accepted.append(f"{value!r}: подсказка формата потеряна")
        else:
            accepted.append(f"{value!r}: принят как ключ задачи")

    assert not accepted, "Некорректные ключи задачи: " + "; ".join(accepted)

async def test_resolve_task_checks_key_access_and_existence() -> None:
    """Ключ задачи: разбор, проверка доступа к проекту раньше номера, неизвестный номер."""
    # Задача отдаётся идентификаторами и своим отображаемым ключом.
    tools = make_tools()
    tools.services.query.resolve_task.return_value = ResolvedTask(
        task_id=7,
        project_id=PROJECT_ID,
        task_key="PROJ-142",
    )

    resolved = await resolve_task(tools, "proj-142")

    assert (resolved.task_id, resolved.task_key) == (7, "PROJ-142")
    tools.services.query.resolve_task.assert_awaited_once_with(
        project_id=PROJECT_ID,
        project_key="PROJ",
        number=142,
    )
    # Отсутствующий номер задачи в доступном проекте — отказ.
    tools = make_tools()
    tools.services.query.resolve_task.side_effect = TaskNotFoundError(task_id=0)

    with pytest.raises(ToolError) as error:
        await resolve_task(tools, "PROJ-999")

    assert str(error.value) == TASK_NOT_AVAILABLE
    # Доступ к проекту проверяется раньше поиска задачи. Иначе по разнице ответов можно было бы понять, есть ли в чужом проекте задача с таким номером.
    tools = make_tools()
    tools.services.access.ensure_project_access.side_effect = ResourceNotAvailableError(
        resource="Проект",
        resource_id=PROJECT_ID,
    )

    with pytest.raises(ToolError) as error:
        await resolve_task(tools, "OTHER-1")

    assert str(error.value) == PROJECT_NOT_AVAILABLE
    tools.services.query.resolve_task.assert_not_awaited()


async def test_ensure_project_access_uses_the_shared_access_service() -> None:
    """Проверку участия выполняет тот же сервис, что и HTTP-слой."""
    tools = make_tools()

    await ensure_project_access(tools, PROJECT_ID)

    tools.services.access.ensure_project_access.assert_awaited_once_with(
        project_id=PROJECT_ID,
        user_id=1,
    )


def test_tool_context_exposes_principal_without_orm_model() -> None:
    """Инструменту доступны только факты о пользователе, но не сессия БД.

    Без сессии в контексте обработчик физически не может обойти сервисы
    и сходить в базу напрямую.
    """
    tools = make_tools()

    assert tools.principal.short_name == "Тестов Тест"
    assert not hasattr(tools, "session")
    assert not hasattr(tools, "user")


async def test_tool_context_authenticates_and_guards_write_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    """Принципал приходит из auth-сервиса, причина плохого токена не раскрывается, READ-токен не попадает в write-инструмент, отсутствие ресурсов приложения падает громко."""
    # Владельца вызова определяет общий сервис аутентификации.
    services = make_services()
    services.auth.resolve_principal.return_value = make_principal()
    monkeypatch.setattr(ctx, "build_tool_services", lambda **_: services)

    async with tool_context(TransportContext(state=app_state())) as tools:
        assert tools.principal.username == "tester"
        assert tools.services is services

    services.auth.resolve_principal.assert_awaited_once_with(
        session_token=None,
        bearer_secret="telos_token_test",
    )
    # Отозванный, истёкший и выдуманный токен неотличимы для клиента.
    services = make_services()
    services.auth.resolve_principal.side_effect = NotAuthenticatedError()
    monkeypatch.setattr(ctx, "build_tool_services", lambda **_: services)

    with pytest.raises(ToolError) as error:
        async with tool_context(TransportContext(state=app_state())):
            pass

    assert str(error.value) == NOT_AUTHENTICATED
    # Право записи проверяется до выдачи контекста инструменту.
    services = make_services()
    services.auth.resolve_principal.return_value = make_principal(ApiTokenScope.READ)
    monkeypatch.setattr(ctx, "build_tool_services", lambda **_: services)

    with pytest.raises(ToolError) as error:
        async with tool_context(TransportContext(state=app_state()), require_write=True):
            pass

    assert str(error.value) == READ_ONLY_TOKEN
    # Запуск без lifespan — отказ, а не собственные клиенты у MCP. Иначе MCP тихо завёл бы второй набор соединений мимо приложения.
    with pytest.raises(ToolError) as error:
        async with tool_context(TransportContext(state={})):
            pass

    assert str(error.value) == RESOURCES_MISSING
