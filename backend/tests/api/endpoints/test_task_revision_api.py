"""HTTP передаёт ожидаемую версию и отдаёт безопасный конфликт 409."""

from unittest.mock import AsyncMock

from main import app
from src.dependencies.services import get_tasks_service
from src.exceptions.tasks import TaskRevisionConflictError
from src.services.tasks import TasksService


async def test_update_validates_optional_revision_and_maps_stale_conflict(api_client):
    service = AsyncMock(spec=TasksService)
    app.dependency_overrides[get_tasks_service] = lambda: service
    for invalid in (0, -1, True, 1.0, "1"):
        response = await api_client.patch(
            "/api/v1/tasks/12", json={"description_md": "Правка", "expected_revision": invalid}
        )
        assert response.status_code == 422
    service.update_task.assert_not_awaited()
    service.update_task.side_effect = TaskRevisionConflictError("private internal details")
    response = await api_client.patch(
        "/api/v1/tasks/12", json={"description_md": "Правка", "expected_revision": 3}
    )
    assert response.status_code == 409
    assert response.json() == {"detail": TaskRevisionConflictError.detail}
    assert "private" not in response.text
    assert service.update_task.await_args.kwargs["data"] == {
        "description_md": "Правка",
        "expected_revision": 3,
    }
