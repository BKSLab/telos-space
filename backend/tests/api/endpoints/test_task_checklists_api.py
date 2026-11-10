"""HTTP-контракт чек-листа и генерации черновика."""

import json
from unittest.mock import AsyncMock

import pytest

from main import app
from src.dependencies.services import get_checklist_suggestion_service, get_tasks_service
from src.exceptions.ai import AiProviderError
from src.exceptions.auth import NotAuthenticatedError
from src.exceptions.tasks import TaskChecklistConflictError, TaskChecklistGenerationError
from src.schemas.task_checklists import ChecklistSuggestionSchema, TaskChecklistSchema
from src.services.task_checklist_suggestions import TaskChecklistSuggestionService
from src.services.tasks import TasksService


def service_override():
    service = AsyncMock(spec=TaskChecklistSuggestionService)
    service.max_file_size = 1000
    service.suggest.return_value = ChecklistSuggestionSchema(
        checklist=TaskChecklistSchema(items=[{"text": str(i)} for i in range(3)])
    )
    app.dependency_overrides[get_checklist_suggestion_service] = lambda: service
    return service


async def test_suggestion_forwards_draft_and_files_and_does_not_require_write_scope(api_client):
    service = service_override()
    response = await api_client.post(
        "/api/v1/projects/1/tasks/checklist-suggestion",
        data={
            "payload": json.dumps({"title": "Задача", "description_md": "Описание", "task_id": 12})
        },
        files={"files": ("draft.txt", b"requirements", "text/plain")},
        headers={"Authorization": "Bearer telos_token_read"},
    )
    assert response.status_code == 200
    assert len(response.json()["checklist"]["items"]) == 3
    call = service.suggest.await_args.kwargs
    assert call["bearer_secret"] == "telos_token_read"
    assert call["data"].task_id == 12
    assert call["files"][0].content == b"requirements"


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (NotAuthenticatedError(), 401),
        (AiProviderError("private"), 503),
        (TaskChecklistGenerationError("private"), 502),
    ],
)
async def test_suggestion_maps_errors_without_private_details(api_client, error, status):
    service = service_override()
    service.suggest.side_effect = error
    response = await api_client.post(
        "/api/v1/projects/1/tasks/checklist-suggestion", data={"payload": '{"title":"Task"}'}
    )
    assert response.status_code == status and "private" not in response.text


async def test_validation_and_file_size_are_checked_before_ai(api_client):
    service = service_override()
    invalid = await api_client.post(
        "/api/v1/projects/1/tasks/checklist-suggestion", data={"payload": '{"title":" "}'}
    )
    assert invalid.status_code == 422
    oversized = await api_client.post(
        "/api/v1/projects/1/tasks/checklist-suggestion",
        data={"payload": '{"title":"Task"}'},
        files={"files": ("large.txt", b"a" * 1001)},
    )
    assert oversized.status_code == 413
    service.suggest.assert_not_awaited()


async def test_checklist_patch_requires_version_and_maps_concurrent_change(api_client):
    service = AsyncMock(spec=TasksService)
    app.dependency_overrides[get_tasks_service] = lambda: service
    response = await api_client.patch("/api/v1/tasks/12", json={"checklist": None})
    assert response.status_code == 422
    service.update_task.assert_not_awaited()
    service.update_task.side_effect = TaskChecklistConflictError("version changed")
    response = await api_client.patch(
        "/api/v1/tasks/12", json={"checklist": None, "checklist_revision": 2}
    )
    assert response.status_code == 409
    assert service.update_task.await_args.kwargs["data"] == {
        "checklist": None,
        "checklist_revision": 2,
    }
