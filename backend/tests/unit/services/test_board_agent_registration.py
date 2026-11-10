"""Registration, key scopes and executor gates for the board action surface."""

import ast
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from src.agent.action_scopes import ACTION_SCOPES, tools_for_scopes
from src.agent.tim import TimMode
from src.agent.tools import (
    AgentTool,
    AgentToolContext,
    AgentToolExecutor,
    AgentToolRequest,
)
from src.schemas.board_diagrams import BoardDiagramInput
from src.utils.api_scopes import (
    API_SCOPES,
    READ_SCOPES,
    SCOPE_BOARDS_READ,
    SCOPE_BOARDS_WRITE,
    WRITE_SCOPES,
)

SRC = Path(__file__).resolve().parents[3] / "src"


def test_board_scopes_are_explicit_and_not_implied_by_task_write():
    assert SCOPE_BOARDS_READ in API_SCOPES and SCOPE_BOARDS_READ in READ_SCOPES
    assert SCOPE_BOARDS_WRITE in WRITE_SCOPES
    assert "telos_draw_board_diagram" not in tools_for_scopes(["tasks:write"])
    assert "telos_draw_personal_board_diagram" in tools_for_scopes([SCOPE_BOARDS_WRITE])
    assert "telos_get_board_scene" in tools_for_scopes([SCOPE_BOARDS_READ])
    assert ACTION_SCOPES["telos_update_personal_board_shapes"] == SCOPE_BOARDS_WRITE


def test_catalog_registers_project_and_widget_only_personal_variants():
    tree = ast.parse((SRC / "agent/action_catalog.py").read_text(encoding="utf-8"))
    entries = {}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "ProjectAction"
        ):
            entries[node.args[0].value] = node
    for name in (
        "telos_get_board_scene",
        "telos_draw_board_diagram",
        "telos_update_board_shapes",
        "telos_arrange_board_objects",
        "telos_list_personal_boards",
        "telos_get_personal_board_scene",
        "telos_draw_personal_board_diagram",
        "telos_update_personal_board_shapes",
        "telos_arrange_personal_board_objects",
    ):
        assert name in entries
        node = entries[name]
        kwargs = {item.arg: item.value for item in node.keywords}
        if "personal" in name:
            assert kwargs["needs_project"].value is False
            assert kwargs["modes"].id == "WIDGET_MODES"
    # Destructive existing actions still enter the confirmation path.
    for name in ("telos_delete_task", "telos_delete_sticker", "telos_delete_document"):
        assert next(
            k.value.value for k in entries[name].keywords if k.arg == "requires_confirmation"
        )


def test_mcp_registration_has_read_write_scopes_actor_and_required_retry_id():
    tree = ast.parse((SRC / "mcp_server/board_tools.py").read_text(encoding="utf-8"))
    tools = [
        node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.decorator_list
    ]
    assert len(tools) == 5
    for tool in tools:
        declaration = tool.decorator_list[0]
        kwargs = {item.arg: item.value for item in declaration.keywords}
        is_write = kwargs["scope"].id == "SCOPE_BOARDS_WRITE"
        assert kwargs["name"].value.startswith("telos_")
        if is_write:
            params = [a.arg for a in tool.args.args]
            assert "actor" in params and "request_id" in params
            assert params.index("request_id") < len(params) - len(tool.args.defaults)
    server = (SRC / "mcp_server/server.py").read_text(encoding="utf-8")
    assert "from src.mcp_server import board_tools" in server
    services = (SRC / "mcp_server/services.py").read_text(encoding="utf-8")
    assert "actions=PROJECT_ACTIONS + PERSONAL_ACTIONS" in services


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "can_write,allowed,mode",
    [
        (False, None, TimMode.WIDGET_PERSONAL),
        (True, frozenset({"telos_create_task"}), TimMode.WIDGET_PERSONAL),
        (True, None, TimMode.PROJECT_CHAT),
    ],
)
async def test_personal_drawing_executor_refuses_readonly_missing_scope_and_project_chat(
    can_write, allowed, mode
):
    handler = AsyncMock()
    tool = AgentTool(
        name="telos_draw_personal_board_diagram",
        description="diagram",
        parameters=BoardDiagramInput,
        handler=handler,
        mutating=True,
        modes=frozenset({TimMode.WIDGET_PERSONAL, TimMode.WIDGET_PROJECT}),
    )
    executor = AgentToolExecutor([tool])
    result = await executor.execute(
        AgentToolContext(
            project_id=None,
            user_id=1,
            can_write=can_write,
            allowed_tools=allowed,
            mode=mode,
        ),
        AgentToolRequest(
            name=tool.name,
            arguments={"board_id": 4, "nodes": [{"key": "a", "label": "a"}]},
        ),
    )
    assert "error" in result
    handler.assert_not_awaited()


def test_personal_prompt_includes_board_tools_and_layout_instructions():
    tree = ast.parse((SRC / "prompts/project_agent.py").read_text(encoding="utf-8"))
    mapping = next(
        statement.value
        for statement in tree.body
        if isinstance(statement, ast.AnnAssign) and statement.target.id == "TIM_SYSTEM_PROMPTS"
    )
    personal = next(
        value
        for key, value in zip(mapping.keys, mapping.values, strict=True)
        if key.attr == "WIDGET_PERSONAL"
    )
    assert any(
        isinstance(node, ast.Name) and node.id == "BOARD_DIAGRAM_INSTRUCTIONS"
        for node in ast.walk(personal.args[1])
    )
