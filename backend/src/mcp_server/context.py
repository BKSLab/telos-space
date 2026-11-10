"""Аутентификация и разрешение сущностей для MCP-инструментов.

MCP-слой намеренно не имеет собственной логики доступа: он вызывает те же
сервисы, что и HTTP-эндпоинты. Иначе две реализации прав рано или поздно
разъедутся, и разойдутся молча.

Наружу отдаётся контекст с готовыми сервисами, а не с сессией: обработчик
инструмента не должен решать, что и в какой транзакции читать.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError

from src.core.app_state import (
    RUNTIME_STATE_KEY,
    SESSION_FACTORY_STATE_KEY,
    SETTINGS_STATE_KEY,
)
from src.core.settings import Settings
from src.exceptions.access import AccessServiceError
from src.exceptions.auth import AuthServiceError
from src.exceptions.projects import ProjectNotFoundError, ProjectsServiceError
from src.exceptions.tasks import TaskNotFoundError
from src.knowledge.runtime import KnowledgeRuntime
from src.mcp_server.registry import current_tool_call
from src.mcp_server.services import ToolServices, build_tool_services
from src.services.access import AccessGrant
from src.services.auth import Principal
from src.services.project_query import ResolvedTask
from src.utils.api_tokens import extract_bearer_secret
from src.utils.request_actor import reset_request_actor, set_request_actor

logger = logging.getLogger(__name__)

NOT_AUTHENTICATED = "Требуется действующий токен доступа."
PROJECT_NOT_AVAILABLE = "Проект недоступен."
TASK_NOT_AVAILABLE = "Задача недоступна."
READ_ONLY_PROJECT = "Проект доступен только для чтения."
READ_ONLY_TOKEN = "Токен выдан только на чтение."
RESOURCES_MISSING = "Сервер запущен без ресурсов приложения."


@dataclass(slots=True)
class ToolContext:
    """Всё, что нужно инструменту: кто спрашивает и какими сервисами.

    Сессия сюда не попадает: работа с базой остаётся внутри сервисов, а
    обработчик инструмента получает готовые use case.
    """

    principal: Principal
    services: ToolServices
    runtime: KnowledgeRuntime
    settings: Settings
    writes: bool = False
    grant: AccessGrant | None = None


@asynccontextmanager
async def tool_context(
    context: Context, *, require_write: bool | None = None
) -> AsyncIterator[ToolContext]:
    """Определяет пользователя вызова и собирает сервисы инструмента.

    Args:
        context: Контекст вызова MCP с заголовками транспорта.
        require_write: Требуется ли право изменять данные.

    Yields:
        Принципал запроса и готовые сервисы.

    Raises:
        ToolError: Если токен не предъявлен, недействителен или недостаточен.
    """
    call = current_tool_call()
    writes = require_write if require_write is not None else bool(call and call.spec.write)
    authorization = _authorization_header(context)
    api_key = _header(context, "x-api-key")
    runtime, settings, session_factory = _app_resources(context)
    async with session_factory() as session:
        services = build_tool_services(
            session=session,
            session_factory=session_factory,
            settings=settings,
        )
        try:
            principal = await services.auth.resolve_principal(
                session_token=None,
                bearer_secret=api_key or extract_bearer_secret(authorization),
            )
        except AuthServiceError as error:
            # Клиенту не сообщается, чем именно плох токен: отозванный,
            # истёкший и выдуманный неотличимы.
            logger.info("ℹ️ MCP-вызов отклонён на аутентификации: %s.", error.status_code)
            raise ToolError(NOT_AUTHENTICATED) from error

        if call is not None:
            call.principal = principal
            call.session_factory = session_factory
            try:
                principal.require(call.spec.scope)
            except AuthServiceError as error:
                raise ToolError(error.detail) from error
            if call.actor and call.actor.casefold() != principal.username.casefold():
                raise ToolError("Действие можно выполнить только от имени владельца ключа.")
        elif getattr(principal, "api_key", None) is not None:
            raise ToolError("Инструмент не зарегистрирован в каталоге прав.")
        if writes and not principal.can_write:
            raise ToolError(READ_ONLY_TOKEN)
        actor_token = set_request_actor(principal.request_actor())
        try:
            yield ToolContext(principal=principal, services=services, runtime=runtime,
                              settings=settings, writes=writes)
        finally:
            reset_request_actor(actor_token)



async def resolve_project(tools: ToolContext, project_key: str) -> int:
    """Находит проект по ключу и проверяет доступ пользователя.

    Args:
        tools: Контекст вызова инструмента.
        project_key: Отображаемый ключ проекта, например ``PROJ``.

    Returns:
        Идентификатор доступного пользователю проекта.

    Raises:
        ToolError: Если проект не найден или недоступен.
    """
    try:
        project_id = await tools.services.query.resolve_project_id(project_key=project_key)
    except ProjectNotFoundError as error:
        raise ToolError(PROJECT_NOT_AVAILABLE) from error
    except ProjectsServiceError as error:
        raise ToolError("Не удалось получить проект.") from error

    await ensure_project_access(tools, project_id)
    return project_id


async def resolve_task(tools: ToolContext, task_key: str) -> ResolvedTask:
    """Находит задачу по отображаемому ключу и проверяет доступ.

    Args:
        tools: Контекст вызова инструмента.
        task_key: Ключ задачи вида ``PROJ-142``.

    Returns:
        Идентификаторы задачи и её проекта.

    Raises:
        ToolError: Если ключ некорректен, задача не найдена или недоступна.
    """
    project_key, _, number_text = (task_key or "").strip().rpartition("-")
    if not project_key or not number_text.isdigit():
        raise ToolError("Ключ задачи должен иметь вид PROJ-142.")

    project_id = await resolve_project(tools, project_key)
    try:
        return await tools.services.query.resolve_task(
            project_id=project_id,
            project_key=project_key.strip().upper(),
            number=int(number_text),
        )
    except TaskNotFoundError as error:
        raise ToolError(TASK_NOT_AVAILABLE) from error
    except ProjectsServiceError as error:
        raise ToolError("Не удалось получить задачи проекта.") from error


async def ensure_project_access(tools: ToolContext, project_id: int) -> None:
    """Проверяет участие пользователя в проекте тем же сервисом, что и HTTP-слой.

    Отсутствие доступа и отсутствие проекта неразличимы: иначе перебором
    выяснялось бы существование чужих проектов.
    """
    try:
        tools.grant = await tools.services.access.ensure_project_access(
            project_id=project_id,
            user_id=tools.principal.user_id,
        )
        if tools.writes and not tools.grant.can_write:
            raise ToolError(READ_ONLY_PROJECT)
    except AccessServiceError as error:
        raise ToolError(PROJECT_NOT_AVAILABLE) from error


def _app_resources(context: Context) -> tuple[KnowledgeRuntime, Settings, object]:
    """Достаёт ресурсы приложения из состояния запущенного приложения.

    Смонтированный ASGI-транспорт видит то же состояние, что и основное
    приложение, поэтому отдельный набор ресурсов для MCP не создаётся.

    Args:
        context: Контекст вызова MCP.

    Returns:
        Клиенты, настройки и фабрику сессий приложения.

    Raises:
        ToolError: Если приложение запущено без lifespan.
    """
    request = getattr(context.request_context, "request", None)
    state = getattr(request, "state", None)
    runtime = getattr(state, RUNTIME_STATE_KEY, None)
    settings = getattr(state, SETTINGS_STATE_KEY, None)
    session_factory = getattr(state, SESSION_FACTORY_STATE_KEY, None)
    if runtime is None or settings is None or session_factory is None:
        logger.error("❌ MCP-вызов без ресурсов приложения: lifespan не отработал.")
        raise ToolError(RESOURCES_MISSING)
    return runtime, settings, session_factory


def _authorization_header(context: Context) -> str | None:
    """Достаёт заголовок ``Authorization`` из транспорта MCP."""
    headers = context.headers
    if not headers:
        return None
    for name, value in headers.items():
        if name.lower() == "authorization":
            return value
    return None


def bearer_secret_of(context: Context) -> str | None:
    """Возвращает credentials транспорта для повторной проверки после ожидания lock."""
    return _header(context, "x-api-key") or extract_bearer_secret(_authorization_header(context))


def _header(context: Context, name: str) -> str | None:
    for key, value in (context.headers or {}).items():
        if key.lower() == name:
            return value
    return None
