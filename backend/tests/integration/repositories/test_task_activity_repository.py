import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.project_stages import ProjectStage
from src.db.models.task_activity import TaskActivityEventType
from src.repositories.task_activity import TaskActivityRepository
from src.repositories.tasks import TasksRepository


@pytest.mark.asyncio
async def test_save_and_get_for_task_on_real_postgres(
    db_session: AsyncSession,
    stage: ProjectStage,
) -> None:
    task = await TasksRepository(db_session).save(
        data={
            "project_id": stage.project_id,
            "stage_id": stage.id,
            "number": 1,
            "title": "Работа",
            "priority": "MEDIUM",
            "position": 0.0,
        }
    )
    activity_repository = TaskActivityRepository(db_session)

    event = await activity_repository.save(
        task_id=task.id,
        event_type=TaskActivityEventType.STAGE_CHANGED,
        from_value="Бэклог",
        to_value="В работе",
    )
    result = await activity_repository.get_for_task(task_id=task.id)

    assert [item.id for item in result] == [event.id]
    assert result[0].event_type == TaskActivityEventType.STAGE_CHANGED
    assert await activity_repository.get_count_by_project(project_id=stage.project_id) == 1
    assert await activity_repository.get_count_by_project(project_id=stage.project_id + 1000) == 0


@pytest.mark.asyncio
async def test_get_recent_due_date_changes_filters_event_type_on_real_postgres(
    db_session: AsyncSession,
    stage: ProjectStage,
) -> None:
    task = await TasksRepository(db_session).save(
        data={
            "project_id": stage.project_id,
            "stage_id": stage.id,
            "number": 2,
            "title": "Переносимая работа",
            "priority": "MEDIUM",
            "position": 1000.0,
        }
    )
    repository = TaskActivityRepository(db_session)
    await repository.save(
        task_id=task.id,
        event_type=TaskActivityEventType.STAGE_CHANGED,
        from_value="Бэклог",
        to_value="В работе",
    )
    due_change = await repository.save(
        task_id=task.id,
        event_type=TaskActivityEventType.DUE_DATE_CHANGED,
        from_value="2026-09-02",
        to_value="2026-09-08",
    )
    combined_change = await repository.save(
        task_id=task.id,
        event_type=TaskActivityEventType.DATES_CHANGED,
        from_value="начало: 2026-09-01; завершение: 2026-09-08",
        to_value="начало: 2026-09-02; завершение: 2026-09-09",
        reason="Поставка задержалась",
    )
    await repository.save(
        task_id=task.id,
        event_type=TaskActivityEventType.DATES_CHANGED,
        from_value="начало: 2026-09-02; завершение: 2026-09-09",
        to_value="начало: 2026-09-03; завершение: 2026-09-09",
        reason="Старт позже",
    )

    result = await repository.get_recent_due_date_changes(
        project_id=stage.project_id,
        limit=10,
    )

    assert [item.id for item in result] == [combined_change.id, due_change.id]
