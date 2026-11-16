"""Арис передаёт пользовательскую причину в те же сервисы обновления дат."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from src.agent.tools import AgentToolContext
from src.schemas.calendar_scenarios import ScenarioApplyResponseSchema
from src.schemas.project_tool_inputs import ScenarioApplyInput, TaskUpdateInput
from src.services.access import AccessService
from src.services.agent_structure_actions import apply_schedule
from src.services.agent_task_actions import update_task
from src.services.calendar_scenarios import CalendarScenarioService
from src.services.tasks import TasksService, to_task_schema
from tests.unit.services.test_tasks_service import make_task


async def test_agent_task_and_scenario_accept_and_forward_reason_without_changing_access():
    tasks = AsyncMock(spec=TasksService)
    tasks.get_task.return_value = tasks.update_task.return_value = to_task_schema(
        make_task(), "PROJ"
    )
    access = AsyncMock(spec=AccessService)
    access.ensure_task_access.return_value = SimpleNamespace(project_id=1)
    scenarios = AsyncMock(spec=CalendarScenarioService)
    scenarios.apply.return_value = ScenarioApplyResponseSchema(applied_count=1, task_ids=[10])
    scope = SimpleNamespace(tasks=tasks, access=access, scenarios=scenarios)
    ctx = AgentToolContext(project_id=1, user_id=7, can_write=True)

    args = TaskUpdateInput.model_validate_json(
        '{"task_id":10,"changes":{"due_date":null,"date_change_reason":"Срок отменён"}}'
    )
    await update_task(scope, ctx, args)
    access.ensure_task_access.assert_awaited_once_with(task_id=10, user_id=7)
    assert tasks.update_task.await_args.kwargs == {"updated_by_user_id": 7}
    assert tasks.update_task.await_args.args == (
        10, {"due_date": None, "date_change_reason": "Срок отменён"},
    )

    args = ScenarioApplyInput.model_validate_json(
        '{"changes":[{"task_id":10,"start_date":null,"due_date":"2026-11-23",'
        '"expected_updated_at":"2026-11-16T09:00:00Z"}],"date_change_reason":"Поставка позже"}'
    )
    result = await apply_schedule(scope, ctx, args)
    assert result == {"applied_count": 1, "task_ids": [10]}
    assert scenarios.apply.await_args.kwargs == {"date_change_reason": "Поставка позже"}
