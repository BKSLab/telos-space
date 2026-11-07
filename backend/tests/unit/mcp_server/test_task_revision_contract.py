"""Версия MCP-карточки относится к прочитанному тексту и передаётся в запись."""

from datetime import date
from types import SimpleNamespace

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from src.exceptions.tasks import TaskRevisionConflictError
from src.mcp_server import server as srv
from src.mcp_server import write_tools as wt
from src.mcp_server.server import mcp_server
from src.services.project_query import ResolvedTask, TaskDetailsDto, TaskSummaryDto
from tests.unit.mcp_server.conftest import FakeContext


async def test_task_revision_is_from_same_snapshot_as_description(tools):
    """Второй запрос не может подменить версию ранее прочитанного текста свежей."""
    services = tools()
    services.query.resolve_task.return_value = ResolvedTask(task_id=7, project_id=1, task_key="PROJ-7")
    details = TaskDetailsDto(
        summary=TaskSummaryDto(
            task_key="PROJ-7", title="Задача", stage=None, is_done=False,
            priority="MEDIUM", assignee=None, due_date=None,
        ),
        role=None, wbs_path=None, description="Первоначальный текст", comment_count=0,
        revision=3, start_date=date(2026, 11, 1),
    )
    services.query.get_task_details.return_value = details
    services.tasks.get_task.return_value = SimpleNamespace(
        revision=4, start_date=date(2026, 11, 2), checklist_revision=0, report=None, participants=[],
    )
    result = await srv.get_task(FakeContext(), task_key="PROJ-7", full_text=True)
    assert result["description"] == "Первоначальный текст"
    assert result["revision"] == 3
    assert result["start_date"] == "2026-11-01"
    legacy = await mcp_server._tool_manager.get_tool("get_task").fn(FakeContext(), task_key="PROJ-7")
    assert "revision" not in legacy and "description_meta" not in legacy


async def test_stale_revision_has_visible_conflict_and_new_writes_return_revision(tools):
    """Ожидаемая версия не становится изменяемым полем; конфликт не выглядит успехом."""
    services = tools()
    services.query.resolve_task.return_value = ResolvedTask(task_id=7, project_id=1, task_key="PROJ-7")
    services.tasks.update_task.side_effect = TaskRevisionConflictError("conflict")
    with pytest.raises(ToolError, match="Перечитайте"):
        await wt.update_task(FakeContext(), task_key="PROJ-7", title="Черновик", expected_revision=3)
    services.tasks.update_task.side_effect = None
    services.tasks.update_task.return_value = SimpleNamespace(id=7, key="PROJ-7", revision=5)
    result = await wt.update_task(
        FakeContext(), task_key="PROJ-7", title="Согласованная правка", expected_revision=4,
    )
    assert result == {"task_key": "PROJ-7", "updated_fields": ["title"], "revision": 5}
    assert services.tasks.update_task.await_args.kwargs["data"]["expected_revision"] == 4
    legacy = await mcp_server._tool_manager.get_tool("update_task").fn(
        FakeContext(), task_key="PROJ-7", title="Прежний клиент",
    )
    assert legacy == {"task_key": "PROJ-7", "updated_fields": ["title"]}
