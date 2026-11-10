"""Полный каталог действий через общие сценарии проектного чата."""

import json
from datetime import date
from typing import Annotated, Any
from uuid import UUID

from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from mcp.shared.exceptions import MCPError
from pydantic import BaseModel, Field, ValidationError

from src.agent.action_catalog import PROJECT_ACTIONS
from src.agent.action_scopes import ACTION_SCOPES
from src.agent.tools import AgentToolContext, describe_validation_error
from src.exceptions.agent_tools import AgentToolsServiceError
from src.exceptions.base import ApplicationError
from src.mcp_server.context import bearer_secret_of, resolve_project, tool_context
from src.mcp_server.links import build_links, put_url
from src.mcp_server.registry import telos_tool
from src.schemas.project_stages import StageCreateSchema
from src.schemas.projects import ProjectCreateSchema, ProjectDescriptionSchema, ProjectStatus
from src.utils.api_scopes import SCOPE_PROJECTS_READ, SCOPE_PROJECTS_WRITE

ACTION_CLOSED_FOR_KEYS = (
    "Это действие недоступно по сервисному API-ключу: его выполняют люди из интерфейса."
)


@telos_tool(
    name="telos_create_project",
    scope=SCOPE_PROJECTS_WRITE,
    title="Создать проект",
    strict_arguments=True,
    description=(
        "Создаёт проект вне контекста другого проекта: паспорт, команду, стадии и "
        "начальную свободную доску одной транзакцией. Владелец — текущий пользователь. "
        "description_sections имеет приоритет перед description_md, как в REST. "
        "Для безопасного повтора после потери ответа сохраните UUID request_id и все "
        "аргументы: повтор возвращает исходный результат; другой payload с тем же UUID "
        "даёт конфликт. Ключ проекта должен быть свободен, участники — существующими "
        "активными пользователями. Права ключа и доступ проверяются заново при повторе. "
        "После создания используйте project_key для telos_get_project и telos_create_task."
    ),
)
async def create_project(
    context: Context,
    key: Annotated[str, ProjectCreateSchema.model_fields["key"]],
    name: Annotated[str, ProjectCreateSchema.model_fields["name"]],
    request_id: Annotated[
        UUID, Field(description="Стабильный UUID создания; повторяйте его и исходные аргументы при сетевом сбое.")
    ],
    description_sections: Annotated[
        ProjectDescriptionSchema | None,
        Field(description="Блоки problem, goal, expected_result, additional. Имеют приоритет перед description_md."),
    ] = None,
    description_md: Annotated[str | None, ProjectCreateSchema.model_fields["description_md"]] = None,
    member_usernames: Annotated[
        list[str] | None,
        Field(max_length=100, description="Точные логины участников; владелец включается автоматически."),
    ] = None,
    stages: Annotated[list[StageCreateSchema] | None, ProjectCreateSchema.model_fields["stages"]] = None,
    status: Annotated[ProjectStatus, ProjectCreateSchema.model_fields["status"]] = ProjectStatus.PLANNING,
    color: Annotated[str, ProjectCreateSchema.model_fields["color"]] = "#58a6ff",
    icon: Annotated[str | None, ProjectCreateSchema.model_fields["icon"]] = None,
    start_date: Annotated[date | None, ProjectCreateSchema.model_fields["start_date"]] = None,
    due_date: Annotated[date | None, ProjectCreateSchema.model_fields["due_date"]] = None,
    actor: Annotated[
        str | None, Field(description="Только логин владельца ключа; владелец проекта определяется сервером.")
    ] = None,
) -> dict:
    """Применяет общий с REST сценарий создания и возвращает durable-результат."""
    try:
        data = ProjectCreateSchema.model_validate({
            "key": key, "name": name,
            "description_sections": description_sections, "description_md": description_md,
            "member_usernames": member_usernames or [], "stages": stages, "status": status,
            "color": color, "icon": icon, "start_date": start_date, "due_date": due_date,
        })
    except ValidationError as error:
        raise ToolError(describe_validation_error(error, ProjectCreateSchema)) from error
    async with tool_context(context, require_write=True) as tools:
        creation = tools.services.project_creation
        user_id = tools.principal.user_id
        links = build_links(tools.settings.app.app_public_url)
    try:
        result = await creation.create(
            actor_user_id=user_id, bearer_secret=bearer_secret_of(context),
            data=data, request_id=request_id,
        )
    except ApplicationError as error:
        raise ToolError(error.detail) from error
    return put_url(
        {field: value for field, value in result.items() if field != "project_id"},
        links.project(result["project_key"]),
    )


def action_allowed(principal, name: str) -> bool:
    """Открыто ли действие каталога предъявленным учётным данным."""
    scope = ACTION_SCOPES.get(name)
    if principal.api_key is None:
        return True
    return scope is not None and principal.allows(scope)


class ActionApproval(BaseModel):
    """Отдельный ответ участника в интерфейсе MCP-клиента."""

    approve: bool = Field(description="Выполнить указанное действие с показанными параметрами?")


@telos_tool(
    name="telos_get_latest_project_summary",
    scope=SCOPE_PROJECTS_READ,
    title="Последний сохранённый обзор проекта",
    description=(
        "Читает последний сохранённый обзор Ариса со вкладки «Обзор»: дату, автора, "
        "оценку, находки, результаты и рекомендации целиком. found=false означает, "
        "что обзор ещё не создавался. Это исторический анализ на created_at; "
        "новый разбор и вызов модели не выполняются."
    ),
)
async def get_latest_project_summary(
    context: Context,
    project_key: Annotated[str, Field(description="Ключ проекта, например PROJ.")],
) -> dict:
    """Возвращает ту же сохранённую сводку, что читает HTTP-интерфейс.

    Args:
        context: Аутентифицированный вызов MCP.
        project_key: Ключ доступного проекта.
    Returns:
        Последний полный обзор либо явный результат его отсутствия.
    Raises:
        ToolError: При отказе аутентификации, scope или текущего доступа.
    """
    async with tool_context(context, require_write=False) as tools:
        project_id = await resolve_project(tools, project_key)
        actions = tools.services.actions
        actor = AgentToolContext(
            project_id=project_id, user_id=tools.principal.user_id, can_write=False,
        )
    try:
        return await actions.execute(
            context=actor, name="telos_get_latest_project_summary", arguments={},
        )
    except AgentToolsServiceError as error:
        raise ToolError(error.detail) from error


@telos_tool(
    name="telos_list_project_tools",
    scope=SCOPE_PROJECTS_READ,
    title="Каталог действий проекта",
    description="Все операции проектного агента. Без names возвращает краткий каталог; names раскрывает до 8 точных схем. Числовые ID берутся из ответов этих инструментов. Сервисному ключу видны только действия, на которые у него есть права.",
)
async def list_project_tools(
    context: Context,
    project_key: Annotated[str, Field(description="Ключ текущего проекта.")],
    names: Annotated[
        list[str] | None, Field(max_length=8, description="Имена для получения схем аргументов.")
    ] = None,
) -> dict:
    """Возвращает общий реестр с учётом прав токена."""
    async with tool_context(context) as tools:
        await resolve_project(tools, project_key)
        # Запись закрыта и токеном «только чтение», и ролью VIEWER в проекте.
        writable = tools.principal.can_write and tools.grant is not None and tools.grant.can_write
        definitions = {item.name: item for item in PROJECT_ACTIONS}
        if names and any(name not in definitions for name in names):
            raise ToolError("Неизвестное имя инструмента.")
        return {
            "tools": [
                {
                    "name": item.name,
                    "title": item.title,
                    "description": item.description,
                    "mutating": item.mutating,
                    "requires_confirmation": item.requires_confirmation,
                    "owner_only": item.owner_only,
                    **({"parameters": item.parameters.model_json_schema()} if names else {}),
                }
                for item in PROJECT_ACTIONS
                if (not names or item.name in names)
                and (writable or not item.mutating)
                and action_allowed(tools.principal, item.name)
            ]
        }


@telos_tool(
    name="telos_execute_project_tool",
    scope=SCOPE_PROJECTS_READ,
    write=False,
    title="Выполнить действие проекта",
    description="Исполняет операцию из telos_list_project_tools с общей проверкой проекта и прав. Для записи передавайте стабильный UUID request_id и повторяйте его при сетевом сбое. Удаления и массовые действия запрашивают решение участника через MCP elicitation; параметра самоподтверждения нет.",
)
async def execute_project_tool(
    context: Context,
    project_key: Annotated[str, Field(description="Ключ текущего проекта.")],
    name: Annotated[str, Field(max_length=64, description="Имя из каталога проектных действий.")],
    arguments: Annotated[
        dict[str, Any], Field(description="Параметры по точной схеме инструмента.")
    ],
    request_id: Annotated[
        UUID | None,
        Field(description="Стабильный ключ повторной записи; обязателен для изменений."),
    ] = None,
    actor: Annotated[
        str | None,
        Field(
            description=(
                "Логин текущего пользователя; действие выполняется с правами владельца ключа."
            )
        ),
    ] = None,
) -> dict:
    """Применяет то же атомарное действие, что проектный чат."""
    definition = next((item for item in PROJECT_ACTIONS if item.name == name), None)
    if definition is None:
        raise ToolError("Неизвестное имя инструмента.")
    # Закрываем область аутентификации до ожидания решения человека.
    async with tool_context(context, require_write=definition.mutating) as tools:
        if not action_allowed(tools.principal, name):
            raise ToolError(ACTION_CLOSED_FOR_KEYS)
        project_id = await resolve_project(tools, project_key)
        actor = AgentToolContext(
            project_id=project_id,
            user_id=tools.principal.user_id,
            can_write=tools.principal.can_write,
            request_id=request_id,
        )
        actions = tools.services.actions
    try:
        response = await actions.execute(context=actor, name=name, arguments=arguments)
        action = response.get("action")
        if action is not None and action["status"] == "pending":
            review = {
                "project_key": project_key,
                "title": action["title"],
                "arguments": action["arguments"],
                "preview": action["result"].get("preview"),
            }
            try:
                answer = await context.elicit(
                    message=json.dumps(review, ensure_ascii=False), schema=ActionApproval
                )
            except MCPError:
                return {
                    **response,
                    "message": "Действие не выполнено. Для решения участника нужен MCP-клиент с поддержкой form elicitation. Повторите тот же request_id в таком клиенте.",
                }
            if answer.action == "cancel":
                return response
            decision = "approve" if answer.action == "accept" and answer.data.approve else "reject"
            # Повторно проверяем токен после ожидания, включая отзыв и срок действия.
            async with tool_context(context, require_write=True) as current:
                await resolve_project(current, project_key)
                if current.principal.user_id != actor.user_id:
                    raise ToolError("Пользователь вызова изменился.")
                if not action_allowed(current.principal, name):
                    raise ToolError(ACTION_CLOSED_FOR_KEYS)
            decided = await actions.decide(
                context=actor, action_id=UUID(action["id"]), decision=decision
            )
            return {"action": decided.model_dump(mode="json")}
        return response
    except AgentToolsServiceError as error:
        raise ToolError(error.detail) from error
