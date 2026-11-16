"""HTTP передаёт причину, сохраняет явный null и возвращает доменный отказ 422."""

from unittest.mock import AsyncMock

from main import app
from src.dependencies.services import get_calendar_scenario_service, get_tasks_service
from src.exceptions.tasks import TaskDateChangeReasonError
from src.services.calendar_scenarios import CalendarScenarioService
from src.services.tasks import TasksService


async def test_task_and_scenario_forward_reasons_and_expose_validation_error(api_client):
    tasks = AsyncMock(spec=TasksService)
    tasks.update_task.side_effect = TaskDateChangeReasonError()
    app.dependency_overrides[get_tasks_service] = lambda: tasks
    patch = {"due_date": None, "date_change_reason": "Отмена внешнего дедлайна"}
    response = await api_client.patch("/api/v1/tasks/12", json=patch)
    assert response.status_code == 422
    assert response.json() == {"detail": TaskDateChangeReasonError.detail}
    assert tasks.update_task.await_args.kwargs["data"] == patch
    tasks.update_task.reset_mock()
    response = await api_client.patch(
        "/api/v1/tasks/12", json={"due_date": None, "date_change_reason": "x" * 2001}
    )
    assert response.status_code == 422
    tasks.update_task.assert_not_awaited()

    scenarios = AsyncMock(spec=CalendarScenarioService)
    scenarios.apply.side_effect = TaskDateChangeReasonError()
    app.dependency_overrides[get_calendar_scenario_service] = lambda: scenarios
    response = await api_client.post(
        "/api/v1/projects/1/calendar/scenarios/apply",
        json={
            "changes": [
                {
                    "task_id": 12,
                    "start_date": None,
                    "due_date": "2026-11-23",
                    "expected_updated_at": "2026-11-16T09:00:00Z",
                }
            ],
            "date_change_reason": "Перенос согласования",
        },
    )
    assert response.status_code == 422
    assert response.json() == {"detail": TaskDateChangeReasonError.detail}
    assert scenarios.apply.await_args.kwargs == {"date_change_reason": "Перенос согласования"}
