"""Общий адаптер MCP: права, идемпотентность и отдельное решение участника."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from src.db.models.api_tokens import ApiTokenScope
from src.mcp_server import project_tools as pt
from src.schemas.agent_tools import AgentToolRunSchema
from tests.unit.mcp_server.conftest import FakeContext


async def test_catalog_and_execution_share_registry_and_enforce_read_token(tools):
    services = tools(ApiTokenScope.READ)
    catalog = await pt.list_project_tools(FakeContext(), "PROJ")
    assert "telos_get_task" in {item["name"] for item in catalog["tools"]}
    assert not any(item["mutating"] for item in catalog["tools"])
    schema = await pt.list_project_tools(FakeContext(), "PROJ", names=["telos_get_task"])
    assert "task_id" in schema["tools"][0]["parameters"]["properties"]
    with pytest.raises(ToolError):
        await pt.execute_project_tool(
            FakeContext(), "PROJ", "telos_create_task", {"title": "Нельзя"}, uuid4()
        )
    services.actions.execute.assert_not_awaited()
    services.actions.execute.return_value = {"task": {"id": 7}}
    assert await pt.execute_project_tool(FakeContext(), "PROJ", "telos_get_task", {"task_id": 7}) == {
        "task": {"id": 7}
    }
    assert services.actions.execute.await_args.kwargs["context"].project_id == 1
    assert not services.actions.execute.await_args.kwargs["context"].can_write


@pytest.mark.parametrize("name", ["get_task", "tt_get_task", "projects_ask", "tt_projects_ask"])
async def test_dispatcher_rejects_removed_action_names_before_execution(tools, name):
    """Диспетчер не переводит прежние публичные имена в канонические."""
    services = tools()
    with pytest.raises(ToolError, match="Неизвестное имя"):
        await pt.execute_project_tool(FakeContext(), "PROJ", name, {})
    with pytest.raises(ToolError, match="Неизвестное имя"):
        await pt.list_project_tools(FakeContext(), "PROJ", names=[name])
    services.actions.execute.assert_not_awaited()


@pytest.mark.parametrize("accepted", [True, False])
async def test_destructive_mcp_action_uses_elicitation_and_stored_arguments(tools, accepted):
    services = tools()
    request_id = uuid4()
    run = AgentToolRunSchema(
        id=uuid4(),
        tool_name="telos_delete_task",
        title="Удалить задачу",
        arguments={"task_id": 7, "expected_updated_at": "2026-09-21T10:00:00Z"},
        status="pending",
        result={"preview": {"task": {"title": "Проверка"}}},
        created_at=datetime.now(UTC),
    )
    services.actions.execute.return_value = {"action": run.model_dump(mode="json")}
    services.actions.decide.return_value = run.model_copy(
        update={"status": "completed" if accepted else "rejected"}
    )
    context = FakeContext()
    context.elicit = AsyncMock(
        return_value=SimpleNamespace(action="accept", data=pt.ActionApproval(approve=accepted))
    )
    result = await pt.execute_project_tool(
        context, "PROJ", "telos_delete_task", run.arguments, request_id
    )
    assert result["action"]["status"] == ("completed" if accepted else "rejected")
    assert services.actions.execute.await_args.kwargs["context"].request_id == request_id
    call = services.actions.decide.await_args.kwargs
    assert call["action_id"] == run.id and "arguments" not in call
    assert call["decision"] == ("approve" if accepted else "reject")
    assert "Проверка" in context.elicit.await_args.kwargs["message"]
