"""Отдельный MCP, единый формат и проверки актуальных прав."""

from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from mcp.server.mcpserver.exceptions import ToolError

from src.core.settings import get_settings
from src.db.models.api_tokens import ApiTokenScope
from src.mcp_server import app as application
from src.mcp_server import board_tools, project_tools, registry
from src.mcp_server import context as ctx
from src.mcp_server.server import mcp_server
from src.schemas.agent_tools import AgentToolRunSchema
from src.schemas.board_diagrams import BoardArrangeInput
from src.services.auth import ApiKeyGrant
from tests.unit.mcp_server.conftest import FakeContext, make_principal
from tests.unit.mcp_server.test_tool_context import TransportContext, app_state


async def test_standalone_health_is_public_and_mcp_requires_local_credentials(monkeypatch):
    check = AsyncMock(return_value=False)
    monkeypatch.setattr(application, "build_credentials_check", lambda *_: check)
    app = application.create_app(settings=get_settings(), session_factory=AsyncMock())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        live = await client.get("/health/live")
        anonymous = await client.post("/mcp/", json={})
        invalid = await client.post("/mcp/", json={}, headers={"Authorization": "Bearer telos_key_invalid"})
        removed_header = await client.post("/mcp/", json={}, headers={"X-API-Key": "telos_key_invalid"})
    assert live.status_code == 200 and live.json()["status"] == "ok"
    assert anonymous.status_code == invalid.status_code == 401
    assert removed_header.status_code == 401
    check.assert_awaited_once_with("telos_key_invalid")


async def test_canonical_list_tasks_returns_one_paginated_shape(tools):
    from src.services.project_query import TaskPageDto, TaskSummaryDto

    services = tools()
    services.query.list_tasks.return_value = TaskPageDto(
        items=[TaskSummaryDto("PROJ-1", "Задача", "Новая", False, "MEDIUM", None, None)],
        total=1, offset=0, limit=50,
    )
    current = await mcp_server._tool_manager.get_tool("telos_list_tasks").fn(FakeContext(), project_key="PROJ")
    assert current["items"][0]["task_key"] == "PROJ-1"
    assert current["total"] == 1 and current["has_more"] is False
    assert current["next_offset"] is None


@pytest.mark.parametrize("scopes", [{"tasks:write"}, {"boards:read"}])
async def test_task_or_read_only_key_cannot_mutate_boards(services, monkeypatch, scopes):
    key = ApiKeyGrant(key_id=uuid4(), key_prefix="test", label="Личный", owner_user_id=1, scopes=frozenset(scopes))
    principal = replace(make_principal(ApiTokenScope.WRITE), api_key=key)
    services.auth.resolve_principal.return_value = principal
    monkeypatch.setattr(ctx, "build_tool_services", lambda **_: services)
    monkeypatch.setattr(registry, "_record_usage", AsyncMock())
    data = BoardArrangeInput.model_validate({"board_id": 4, "expected_revision": 1,
        "objects": [{"type": "shape", "id": 7, "revision": 1}], "mode": "align", "alignment": "left"})
    with pytest.raises(ToolError):
        await board_tools.arrange_board_objects(TransportContext(state=app_state()), data, uuid4())
    services.actions.execute.assert_not_awaited()


async def test_scope_removed_during_elicitation_prevents_destructive_action(services, monkeypatch):
    key = ApiKeyGrant(key_id=uuid4(), key_prefix="test", label="Мой клиент", owner_user_id=1,
        scopes=frozenset({"projects:read", "tasks:delete"}))
    before = replace(make_principal(ApiTokenScope.WRITE), api_key=key)
    after = replace(before, api_key=replace(key, scopes=frozenset({"projects:read", "tasks:write"})))
    services.auth.resolve_principal.side_effect = [before, after]
    monkeypatch.setattr(ctx, "build_tool_services", lambda **_: services)
    monkeypatch.setattr(registry, "_record_usage", AsyncMock())
    action = AgentToolRunSchema(id=uuid4(), tool_name="telos_delete_task", title="Удалить задачу",
        arguments={"task_id": 7, "expected_updated_at": "2026-09-21T10:00:00Z"},
        status="pending", result={}, created_at=datetime.now(UTC))
    services.actions.execute.return_value = {"action": action.model_dump(mode="json")}
    context = TransportContext(state=app_state())
    context.elicit = AsyncMock(return_value=SimpleNamespace(action="accept",
        data=project_tools.ActionApproval(approve=True)))
    with pytest.raises(ToolError, match="недоступно"):
        await project_tools.execute_project_tool(context, "PROJ", "telos_delete_task", action.arguments, uuid4())
    services.actions.execute.assert_awaited_once()
    services.actions.decide.assert_not_awaited()
