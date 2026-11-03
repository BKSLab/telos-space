"""Режим Ариса ограничивает инструменты сервером, а не выбором модели."""

from unittest.mock import AsyncMock

from pydantic import BaseModel

from src.agent.action_catalog import PERSONAL_ACTIONS, PROJECT_ACTIONS
from src.agent.action_scopes import tools_for_scopes
from src.agent.tim import TimMode
from src.agent.tools import AgentToolContext, AgentToolRequest
from src.dependencies.services import build_project_tool_executor
from src.services.agent_actions import AgentActionsService


def executor():
    actions = AgentActionsService(scope=AsyncMock(), actions=PROJECT_ACTIONS + PERSONAL_ACTIONS)
    actions.execute = AsyncMock(return_value={"ok": True})
    query = AsyncMock()
    query.read_knowledge.return_value = {"items": []}
    query.resolve_project_id.return_value = 7
    return build_project_tool_executor(query, actions), actions


def names(tools, mode):
    return {item["name"] for item in tools.describe(include_parameters=False, mode=mode)}


def test_chat_mode_has_no_personal_or_cross_project_tools_and_personal_mode_no_rag():
    tools, _ = executor()
    chat = names(tools, TimMode.PROJECT_CHAT)
    widget = names(tools, TimMode.WIDGET_PROJECT)
    personal = names(tools, TimMode.WIDGET_PERSONAL)
    personal_actions = {item.name for item in PERSONAL_ACTIONS}
    assert not chat & (personal_actions | {"run_project_action", "attach_uploaded_file"})
    assert {"list_tasks", "read_source", "get_tool_schemas"} <= chat
    assert personal_actions | {"run_project_action", "attach_uploaded_file", "list_tasks"} <= widget
    assert personal_actions | {"run_project_action", "get_tool_schemas"} <= personal
    assert not personal & {"list_tasks", "read_source", "search_sources", "attach_uploaded_file"}


async def test_executor_refuses_tool_outside_mode_even_if_model_asks():
    tools, actions = executor()
    chat = AgentToolContext(project_id=1, user_id=1, can_write=True, mode=TimMode.PROJECT_CHAT)
    refused = await tools.execute(chat, AgentToolRequest(name="list_my_tasks", arguments={}))
    assert "error" in refused
    actions.execute.assert_not_awaited()
    personal = AgentToolContext(
        project_id=None, user_id=1, can_write=True, mode=TimMode.WIDGET_PERSONAL
    )
    await tools.execute(
        personal,
        AgentToolRequest(
            name="run_project_action",
            arguments={"project_key": "prj", "name": "list_tasks", "arguments": {}},
        ),
    )
    context = actions.execute.await_args.kwargs["context"]
    assert context.project_id == 7 and context.user_id == 1
    # Схемы проектных действий нужны и вне проекта — для run_project_action.
    schemas = await tools.execute(
        personal,
        AgentToolRequest(
            name="get_tool_schemas", arguments={"names": ["create_task", "read_source"]}
        ),
    )
    assert [item["name"] for item in schemas["tools"]] == ["create_task"]


def test_personal_actions_never_leak_into_mcp_catalog():
    project_names = {item.name for item in PROJECT_ACTIONS}
    assert not project_names & {item.name for item in PERSONAL_ACTIONS}
    assert all(not item.needs_project for item in PERSONAL_ACTIONS)
    assert all(item.needs_project for item in PROJECT_ACTIONS)
    assert all(issubclass(item.parameters, BaseModel) for item in PERSONAL_ACTIONS)


def test_structure_permissions_are_separate_from_tasks_for_external_agent():
    writes = {"create_wbs_node", "update_wbs_node", "move_wbs_node", "delete_wbs_node", "place_task"}
    assert not writes & tools_for_scopes({"tasks:write", "tasks:delete"})
    assert "get_structure" not in tools_for_scopes({"projects:read"})
    assert writes <= tools_for_scopes({"structure:write"})
    assert "get_structure" in tools_for_scopes({"structure:read"})
