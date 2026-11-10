"""Authenticated board tools use the same logged actions as the tracker agent."""

from typing import Annotated
from uuid import UUID

from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from src.agent.aris import ArisMode
from src.agent.tools import AgentToolContext
from src.exceptions.agent_tools import AgentToolsServiceError
from src.mcp_server.context import resolve_project, tool_context
from src.mcp_server.registry import telos_tool
from src.schemas.board_diagrams import (
    BoardArrangeInput,
    BoardDiagramInput,
    BoardSceneInput,
    BoardShapeUpdatesInput,
    PersonalBoardsInput,
)
from src.utils.api_scopes import SCOPE_BOARDS_READ, SCOPE_BOARDS_WRITE


async def _execute(
    context: Context,
    *,
    project_key: str | None,
    name: str,
    personal_name: str,
    arguments: dict,
    write: bool = False,
    request_id: UUID | None = None,
) -> dict:
    async with tool_context(context, require_write=write) as tools:
        project_id = await resolve_project(tools, project_key) if project_key else None
        selected = name if project_id is not None else personal_name
        actor = AgentToolContext(
            project_id=project_id,
            user_id=tools.principal.user_id,
            can_write=tools.principal.can_write,
            request_id=request_id,
            mode=ArisMode.WIDGET_PROJECT if project_id is not None else ArisMode.WIDGET_PERSONAL,
            allowed_tools=frozenset({selected}),
        )
        actions = tools.services.actions
    try:
        return await actions.execute(context=actor, name=selected, arguments=arguments)
    except AgentToolsServiceError as error:
        raise ToolError(error.detail) from error


@telos_tool(
    name="telos_list_personal_boards",
    title="Мои личные доски",
    description="Личные доски текущего пользователя; чужие и проектные доски сюда не входят.",
    scope=SCOPE_BOARDS_READ,
)
async def list_personal_boards(
    context: Context,
    args: PersonalBoardsInput,
    actor: Annotated[str | None, Field(description="Логин текущего пользователя, без смены прав.")] = None,
) -> dict:
    return await _execute(
        context,
        project_key=None,
        name="telos_list_personal_boards",
        personal_name="telos_list_personal_boards",
        arguments=args.model_dump(mode="json", exclude_unset=True),
    )


@telos_tool(
    name="telos_get_board_scene",
    title="Прочитать схему доски",
    description="Фигуры, стрелки, стикеры и ревизии. project_key — проект; без него только личная доска владельца. next_offset означает продолжение.",
    scope=SCOPE_BOARDS_READ,
)
async def get_board_scene(
    context: Context,
    args: BoardSceneInput,
    actor: Annotated[str | None, Field(description="Логин текущего пользователя, без смены прав.")] = None,
    project_key: Annotated[str | None, Field(min_length=1)] = None,
) -> dict:
    return await _execute(
        context,
        project_key=project_key,
        name="telos_get_board_scene",
        personal_name="telos_get_personal_board_scene",
        arguments=args.model_dump(mode="json", exclude_unset=True),
    )


@telos_tool(
    name="telos_draw_board_diagram",
    title="Нарисовать схему",
    description="Детерминированная раскладка nodes/edges в редактируемые фигуры одним атомарным batch. Привязки к существующим блокам берите из сцены; project_key опущен — личная доска. Повтор после сетевого сбоя — с тем же request_id.",
    scope=SCOPE_BOARDS_WRITE,
)
async def draw_board_diagram(
    context: Context,
    args: BoardDiagramInput,
    request_id: UUID,
    project_key: Annotated[str | None, Field(min_length=1)] = None,
    actor: Annotated[str | None, Field(description="Логин текущего пользователя, без смены прав.")] = None,
) -> dict:
    return await _execute(
        context,
        project_key=project_key,
        name="telos_draw_board_diagram",
        personal_name="telos_draw_personal_board_diagram",
        arguments=args.model_dump(mode="json", exclude_unset=True),
        write=True,
        request_id=request_id,
    )


@telos_tool(
    name="telos_update_board_shapes",
    title="Дополнить фигуры схемы",
    description="Правит прочитанные фигуры одним batch с обязательной revision каждой фигуры. Не удаляет объекты. Автор и права — текущего принципала; повторы с тем же request_id.",
    scope=SCOPE_BOARDS_WRITE,
)
async def update_board_shapes(
    context: Context,
    args: BoardShapeUpdatesInput,
    request_id: UUID,
    project_key: Annotated[str | None, Field(min_length=1)] = None,
    actor: Annotated[str | None, Field(description="Логин текущего пользователя, без смены прав.")] = None,
) -> dict:
    return await _execute(
        context,
        project_key=project_key,
        name="telos_update_board_shapes",
        personal_name="telos_update_personal_board_shapes",
        arguments=args.model_dump(mode="json", exclude_unset=True),
        write=True,
        request_id=request_id,
    )


@telos_tool(
    name="telos_arrange_board_objects",
    title="Упорядочить объекты доски",
    description="Атомарно раскладывает (layout TB/LR) или выравнивает (align) выбранные фигуры и стикеры с учётом groups/locks. expected_revision доски и revision объектов берите из свежей сцены; при конфликте перечитайте её. project_key опущен — личная доска владельца. После сетевого сбоя повторяйте тот же request_id.",
    scope=SCOPE_BOARDS_WRITE,
)
async def arrange_board_objects(
    context: Context,
    args: BoardArrangeInput,
    request_id: UUID,
    project_key: Annotated[str | None, Field(min_length=1)] = None,
    actor: Annotated[str | None, Field(description="Логин текущего пользователя, без смены прав.")] = None,
) -> dict:
    return await _execute(
        context,
        project_key=project_key,
        name="telos_arrange_board_objects",
        personal_name="telos_arrange_personal_board_objects",
        arguments=args.model_dump(mode="json", exclude_unset=True),
        write=True,
        request_id=request_id,
    )
