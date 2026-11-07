"""Контракт повторных прямых MCP-записей и доступ к сохранённому результату."""

from uuid import uuid4

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from src.exceptions.agent_tools import AgentToolRequestConflictError
from src.mcp_server import write_tools as wt
from src.mcp_server.server import mcp_server
from src.services.access import AccessGrant
from src.services.project_query import ResolvedTask
from tests.unit.mcp_server.conftest import FakeContext


async def test_task_receipt_is_shared_by_aliases_and_rechecks_current_target(tools):
    """Прежнее имя повторяет ту же операцию; перенос не открывает доступ через кеш."""
    services = tools()
    services.actions.execute.return_value = {
        "action": {"result": {"task": {"id": 7, "key": "PROJ-7", "title": "Проверить"}}},
    }
    services.access.ensure_task_access.return_value = AccessGrant(
        project_id=1, resource_id=7, is_owner=True,
    )
    request_id = uuid4()
    arguments = {"project_key": "PROJ", "title": "Проверить", "request_id": request_id}
    result = await wt.create_task(FakeContext(), **arguments)
    legacy = await mcp_server._tool_manager.get_tool("create_task").fn(FakeContext(), **arguments)
    assert result == {"task_key": "PROJ-7", "title": "Проверить", "created": True, "revision": 1}
    assert legacy == {key: value for key, value in result.items() if key != "revision"}
    contexts = [call.kwargs["context"] for call in services.actions.execute.await_args_list]
    assert all(item.request_id == request_id and item.user_id == 1 for item in contexts)

    services.access.ensure_task_access.return_value = AccessGrant(
        project_id=2, resource_id=7, is_owner=True,
    )
    with pytest.raises(ToolError):
        await wt.create_task(FakeContext(), **arguments)


async def test_comment_receipt_keeps_server_author_and_legacy_response(tools):
    """Повторный комментарий остаётся серверным, а прежний ответ не получает новый ID."""
    services = tools()
    services.query.resolve_task.return_value = ResolvedTask(
        task_id=7, project_id=1, task_key="PROJ-7",
    )
    services.actions.execute.return_value = {
        "action": {"result": {"comment": {
            "id": 3, "author_name": "Серверный автор", "created_at": "2026-11-07T10:00:00Z",
        }}},
    }
    services.access.ensure_task_access.return_value = AccessGrant(
        project_id=1, resource_id=7, is_owner=True,
    )
    arguments = {
        "task_key": "PROJ-7", "body": "Готово", "author": "Чужая подпись", "request_id": uuid4(),
    }
    result = await wt.add_comment(FakeContext(), **arguments)
    legacy = await mcp_server._tool_manager.get_tool("add_comment").fn(FakeContext(), **arguments)
    assert result["comment_id"] == 3 and result["author"] == "Серверный автор"
    assert legacy == {key: value for key, value in result.items() if key != "comment_id"}


async def test_request_conflict_is_an_explicit_tool_error(tools):
    """Клиент понимает, что ключ нельзя использовать с изменёнными аргументами."""
    services = tools()
    services.actions.execute.side_effect = AgentToolRequestConflictError("conflict")
    with pytest.raises(ToolError, match="Ключ повтора"):
        await wt.create_task(FakeContext(), project_key="PROJ", title="Другой текст", request_id=uuid4())


async def test_receipt_lookup_precedes_mutable_stage_and_member_resolution(tools):
    """Повтор не зависит от существования первоначальных имён стадии и участников."""
    services = tools()
    services.actions.execute.return_value = {
        "action": {"result": {"task": {"id": 7, "key": "PROJ-7", "title": "Проверить"}}},
    }
    services.access.ensure_task_access.return_value = AccessGrant(
        project_id=1, resource_id=7, is_owner=True,
    )
    services.query.resolve_stage.side_effect = AssertionError("Имена не разрешаются до квитанции")
    services.members.resolve_member_user_id.side_effect = AssertionError("Участник уже удалён")
    result = await wt.create_task(
        FakeContext(), project_key="PROJ", title="Проверить", stage="Прежнее имя",
        assignee="former-user", observers=["former-user"], request_id=uuid4(),
    )
    assert result["task_key"] == "PROJ-7"
