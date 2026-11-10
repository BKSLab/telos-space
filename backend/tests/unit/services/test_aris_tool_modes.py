"""Режим Ариса ограничивает инструменты сервером, а не выбором модели."""

from unittest.mock import AsyncMock

from pydantic import BaseModel

from src.agent.action_catalog import PERSONAL_ACTIONS, PROJECT_ACTIONS
from src.agent.action_scopes import tools_for_scopes
from src.agent.aris import ArisMode
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
    chat = names(tools, ArisMode.PROJECT_CHAT)
    widget = names(tools, ArisMode.WIDGET_PROJECT)
    personal = names(tools, ArisMode.WIDGET_PERSONAL)
    personal_actions = {item.name for item in PERSONAL_ACTIONS}
    assert not chat & (personal_actions | {"telos_run_project_action", "telos_attach_uploaded_file"})
    assert {"telos_list_tasks", "telos_read_source", "telos_get_tool_schemas"} <= chat
    assert personal_actions | {"telos_run_project_action", "telos_attach_uploaded_file", "telos_list_tasks"} <= widget
    assert personal_actions | {"telos_run_project_action", "telos_get_tool_schemas"} <= personal
    assert not personal & {"telos_list_tasks", "telos_read_source", "telos_search_sources", "telos_attach_uploaded_file"}


async def test_executor_refuses_tool_outside_mode_even_if_model_asks():
    tools, actions = executor()
    chat = AgentToolContext(project_id=1, user_id=1, can_write=True, mode=ArisMode.PROJECT_CHAT)
    refused = await tools.execute(chat, AgentToolRequest(name="telos_list_my_tasks", arguments={}))
    assert "error" in refused
    actions.execute.assert_not_awaited()
    personal = AgentToolContext(
        project_id=None, user_id=1, can_write=True, mode=ArisMode.WIDGET_PERSONAL
    )
    await tools.execute(
        personal,
        AgentToolRequest(
            name="telos_run_project_action",
            arguments={"project_key": "prj", "name": "telos_list_tasks", "arguments": {}},
        ),
    )
    context = actions.execute.await_args.kwargs["context"]
    assert context.project_id == 7 and context.user_id == 1
    # Схемы проектных действий нужны и вне проекта — для telos_run_project_action.
    schemas = await tools.execute(
        personal,
        AgentToolRequest(
            name="telos_get_tool_schemas", arguments={"names": ["telos_create_task", "telos_read_source"]}
        ),
    )
    assert [item["name"] for item in schemas["tools"]] == ["telos_create_task"]


def test_personal_actions_never_leak_into_mcp_catalog():
    project_names = {item.name for item in PROJECT_ACTIONS}
    assert not project_names & {item.name for item in PERSONAL_ACTIONS}
    assert all(not item.needs_project for item in PERSONAL_ACTIONS)
    assert all(item.needs_project for item in PROJECT_ACTIONS)
    assert all(issubclass(item.parameters, BaseModel) for item in PERSONAL_ACTIONS)


def test_structure_permissions_are_separate_from_tasks_for_external_agent():
    writes = {"telos_create_wbs_node", "telos_update_wbs_node", "telos_move_wbs_node", "telos_delete_wbs_node", "telos_place_task"}
    assert not writes & tools_for_scopes({"tasks:write", "tasks:delete"})
    assert "telos_get_structure" not in tools_for_scopes({"projects:read"})
    assert writes <= tools_for_scopes({"structure:write"})
    assert "telos_get_structure" in tools_for_scopes({"structure:read"})
