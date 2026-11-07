"""Контракт версий общих полей задачи и отсутствие эффектов при конфликте."""

import pytest
from pydantic import ValidationError

from src.exceptions.tasks import TaskRevisionConflictError, TaskRevisionValidationError
from src.schemas.project_tool_inputs import TaskChangesInput
from src.schemas.tasks import TaskUpdateSchema
from tests.unit.services.test_task_checklists import setup_service


def test_optional_revision_is_a_strict_positive_integer_in_http_and_agent_schemas():
    for schema in (TaskUpdateSchema, TaskChangesInput):
        assert schema(title="Правка").model_dump(exclude_unset=True) == {"title": "Правка"}
        assert schema(title="Правка", expected_revision=2).expected_revision == 2
        assert schema(title="Правка", expected_revision=None).expected_revision is None
        for invalid in (0, -1, True, 2.0, "2"):
            with pytest.raises(ValidationError):
                schema(title="Правка", expected_revision=invalid)


async def test_stale_task_revision_rejects_before_history_participants_or_outbox():
    service, task = setup_service()
    task.revision = 3
    with pytest.raises(TaskRevisionConflictError) as error:
        await service.update_task(
            task.id,
            {"title": "Устаревший текст", "observer_ids": [], "expected_revision": 2},
        )
    assert error.value.status_code == 409
    assert error.value.detail == "Задача изменилась. Перечитайте её и повторите изменения."
    assert task.title != "Устаревший текст"
    service.tasks_repository.get_for_update.assert_awaited_once_with(task_id=task.id)
    service.tasks_repository.update.assert_not_awaited()
    service.participants_repository.delete_for_task.assert_not_awaited()
    service.participants_repository.save_many.assert_not_awaited()
    service.activity_repository.save.assert_not_awaited()
    service.knowledge_events.upsert.assert_not_awaited()
    service.unit_of_work.commit.assert_not_awaited()
    service.unit_of_work.rollback.assert_awaited_once()


async def test_service_removes_revision_precondition_and_preserves_independent_checklist():
    service, task = setup_service()
    task.revision = 3
    result = await service.update_task(
        task.id, {"description_md": "Новый текст", "expected_revision": 3}
    )
    assert result.description_md == "Новый текст"
    assert result.checklist_revision == 2
    assert service.tasks_repository.update.await_args.kwargs["data"] == {
        "description_md": "Новый текст"
    }
    service.tasks_repository.get_by_id.assert_not_awaited()


async def test_service_also_validates_revision_for_internal_callers():
    service, task = setup_service()
    for invalid in (0, -1, True, 2.0, "2"):
        with pytest.raises(TaskRevisionValidationError) as error:
            await service.update_task(task.id, {"title": "Правка", "expected_revision": invalid})
        assert error.value.status_code == 422
    service.tasks_repository.get_for_update.assert_not_awaited()
    service.tasks_repository.update.assert_not_awaited()
