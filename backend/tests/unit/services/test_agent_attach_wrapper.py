"""Маршрутизация copy-file сохраняет trusted source anchor и права target action."""

from dataclasses import replace
from unittest.mock import AsyncMock
from uuid import uuid4

from src.agent.action_catalog import PERSONAL_ACTIONS, PROJECT_ACTIONS
from src.agent.aris import ArisMode
from src.agent.tools import AgentToolContext, AgentToolRequest
from src.dependencies.services import build_project_tool_executor
from src.services.agent_actions import AgentActionsService


async def test_global_project_wrapper_preserves_source_anchor_and_refuses_readonly_copy():
    actions = AgentActionsService(scope=AsyncMock(), actions=PROJECT_ACTIONS + PERSONAL_ACTIONS)
    actions.execute = AsyncMock(return_value={"action": {"status": "completed"}})
    query = AsyncMock()
    query.resolve_project_id.return_value = 7
    tools = build_project_tool_executor(query, actions)
    context = AgentToolContext(
        project_id=None,
        user_id=2,
        can_write=True,
        mode=ArisMode.WIDGET_PERSONAL,
        message_id=11,
        run_id=uuid4(),
    )
    arguments = {"task_id": 19, "file_id": str(uuid4())}
    request = AgentToolRequest(
        name="telos_run_project_action",
        arguments={
            "project_key": "target",
            "name": "telos_attach_uploaded_file",
            "arguments": arguments,
        },
    )
    response = await tools.execute(context, request)
    assert response["action"]["status"] == "completed"
    routed = actions.execute.await_args.kwargs
    assert routed["context"].project_id == 7
    assert routed["context"].message_id == context.message_id
    assert routed["context"].run_id == context.run_id and routed["context"].user_id == 2
    assert routed["name"] == "telos_attach_uploaded_file" and routed["arguments"] == arguments
    actions.execute.reset_mock()
    assert "error" in await tools.execute(replace(context, can_write=False), request)
    actions.execute.assert_not_awaited()
