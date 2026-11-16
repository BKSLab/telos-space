from datetime import date, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.project_stages import ProjectStage
from src.db.models.task_activity import TaskActivityEventType
from src.db.models.task_dependencies import TaskDependencyType
from src.exceptions.calendar import CalendarScenarioVersionConflictError
from src.repositories.milestones import MilestonesRepository
from src.repositories.projects import ProjectsRepository
from src.repositories.task_activity import TaskActivityRepository
from src.repositories.task_dependencies import TaskDependenciesRepository
from src.repositories.tasks import TasksRepository
from src.repositories.unit_of_work import UnitOfWork
from src.services.calendar_scenarios import CalendarScenarioService


async def build_scenario(
    db_session: AsyncSession,
    stage: ProjectStage,
):
    tasks = TasksRepository(db_session)
    predecessor = await tasks.save(
        {
            "project_id": stage.project_id,
            "stage_id": stage.id,
            "number": 1,
            "title": "Основа",
            "start_date": date(2026, 9, 1),
            "due_date": date(2026, 9, 5),
            "position": 1000.0,
        }
    )
    successor = await tasks.save(
        {
            "project_id": stage.project_id,
            "stage_id": stage.id,
            "number": 2,
            "title": "Продолжение",
            "start_date": date(2026, 9, 6),
            "due_date": date(2026, 9, 8),
            "position": 2000.0,
        }
    )
    dependencies = TaskDependenciesRepository(db_session)
    await dependencies.save(
        {
            "project_id": stage.project_id,
            "predecessor_task_id": predecessor.id,
            "successor_task_id": successor.id,
            "dependency_type": TaskDependencyType.FINISH_TO_START,
            "lag_days": 1,
        }
    )
    await db_session.commit()
    service = CalendarScenarioService(
        ProjectsRepository(db_session),
        tasks,
        dependencies,
        MilestonesRepository(db_session),
        TaskActivityRepository(db_session),
        UnitOfWork(db_session),
    )
    return service, tasks, predecessor, successor


@pytest.mark.asyncio
async def test_preview_is_read_only_and_apply_commits_cascade_atomically(
    db_session: AsyncSession,
    stage: ProjectStage,
) -> None:
    service, tasks, predecessor, successor = await build_scenario(db_session, stage)

    preview = await service.preview(
        stage.project_id,
        [
            {
                "task_id": predecessor.id,
                "start_date": date(2026, 9, 4),
                "due_date": date(2026, 9, 10),
            }
        ],
    )

    assert predecessor.due_date == date(2026, 9, 5)
    assert successor.start_date == date(2026, 9, 6)
    assert preview.consequences_count == 1

    applied = await service.apply(
        stage.project_id,
        [
            {
                "task_id": change.task_id,
                "start_date": change.proposed.start_date,
                "due_date": change.proposed.due_date,
                "expected_updated_at": change.expected_updated_at,
            }
            for change in preview.changes
        ],
        date_change_reason="Перенос поставки",
    )

    assert applied.applied_count == 2
    assert (await tasks.get_by_id(predecessor.id)).due_date == date(2026, 9, 10)
    assert (await tasks.get_by_id(successor.id)).start_date == date(2026, 9, 11)
    activity = await TaskActivityRepository(db_session).get_for_task(successor.id)
    assert {item.event_type for item in activity} == {
        TaskActivityEventType.DATES_CHANGED,
    }
    assert len(activity) == 1 and activity[0].reason == "Перенос поставки"


@pytest.mark.asyncio
async def test_stale_scenario_rejects_whole_batch_before_first_update(
    db_session: AsyncSession,
    stage: ProjectStage,
) -> None:
    service, tasks, predecessor, successor = await build_scenario(db_session, stage)
    preview = await service.preview(
        stage.project_id,
        [
            {
                "task_id": predecessor.id,
                "start_date": date(2026, 9, 4),
                "due_date": date(2026, 9, 10),
            }
        ],
    )
    successor_version = next(
        change.expected_updated_at for change in preview.changes if change.task_id == successor.id
    )
    await tasks.update(
        successor,
        {
            "title": "Изменено параллельно",
            "updated_at": successor_version + timedelta(seconds=1),
        },
    )
    await db_session.commit()

    predecessor_id, successor_id = predecessor.id, successor.id
    with pytest.raises(CalendarScenarioVersionConflictError):
        await service.apply(
            stage.project_id,
            [
                {
                    "task_id": change.task_id,
                    "start_date": change.proposed.start_date,
                    "due_date": change.proposed.due_date,
                    "expected_updated_at": change.expected_updated_at,
                }
                for change in preview.changes
            ],
        )

    assert (await tasks.get_by_id(predecessor_id)).due_date == date(2026, 9, 5)
    persisted_successor = await tasks.get_by_id(successor_id)
    assert persisted_successor.start_date == date(2026, 9, 6)
    assert persisted_successor.title == "Изменено параллельно"
    assert await TaskActivityRepository(db_session).get_for_task(predecessor_id) == []
