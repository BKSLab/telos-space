from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.db.models.task_dependencies import TaskDependencyType
from src.exceptions.calendar import CalendarScenarioVersionConflictError
from src.repositories.milestones import MilestonesRepository
from src.repositories.projects import ProjectsRepository
from src.repositories.task_activity import TaskActivityRepository
from src.repositories.task_dependencies import TaskDependenciesRepository
from src.repositories.tasks import TasksRepository
from src.repositories.unit_of_work import UnitOfWork
from src.services.calendar_scenarios import CalendarScenarioService

NOW = datetime(2026, 9, 2, 10, tzinfo=UTC)


def task(task_id: int, start: date, due: date, *, updated_at: datetime = NOW):
    return SimpleNamespace(
        id=task_id,
        project_id=1,
        number=task_id,
        title=f"Задача {task_id}",
        wbs_node_id=None,
        start_date=start,
        due_date=due,
        updated_at=updated_at,
    )


def dependency(predecessor_id: int, successor_id: int, lag_days: int = 0):
    return SimpleNamespace(
        id=1,
        project_id=1,
        predecessor_task_id=predecessor_id,
        successor_task_id=successor_id,
        dependency_type=TaskDependencyType.FINISH_TO_START,
        lag_days=lag_days,
    )


def build_service():
    projects = AsyncMock(spec=ProjectsRepository)
    projects.get_by_id.return_value = SimpleNamespace(
        id=1,
        key="TEST",
        due_date=date(2026, 9, 30),
    )
    tasks = AsyncMock(spec=TasksRepository)
    dependencies = AsyncMock(spec=TaskDependenciesRepository)
    milestones = AsyncMock(spec=MilestonesRepository)
    milestones.get_by_project.return_value = []
    activity = AsyncMock(spec=TaskActivityRepository)
    uow = AsyncMock(spec=UnitOfWork)
    return (
        CalendarScenarioService(projects, tasks, dependencies, milestones, activity, uow),
        tasks,
        dependencies,
        milestones,
        activity,
        uow,
    )


@pytest.mark.asyncio
async def test_apply_rejects_stale_version_before_any_write() -> None:
    service, tasks, dependencies, _, activity, uow = build_service()
    item = task(1, date(2026, 9, 1), date(2026, 9, 5), updated_at=NOW + timedelta(seconds=1))
    tasks.get_by_project.return_value = [item]
    dependencies.get_by_project.return_value = []

    with pytest.raises(CalendarScenarioVersionConflictError):
        await service.apply(
            1,
            [
                {
                    "task_id": 1,
                    "start_date": date(2026, 9, 2),
                    "due_date": date(2026, 9, 6),
                    "expected_updated_at": NOW,
                }
            ],
        )

    tasks.update.assert_not_awaited()
    activity.save.assert_not_awaited()
    uow.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_apply_records_all_dates_and_commits_once() -> None:
    service, tasks, dependencies, _, activity, uow = build_service()
    first = task(1, date(2026, 9, 1), date(2026, 9, 5))
    second = task(2, date(2026, 9, 6), date(2026, 9, 8))
    tasks.get_by_project.return_value = [first, second]
    dependencies.get_by_project.return_value = [dependency(1, 2)]

    result = await service.apply(
        1,
        [
            {
                "task_id": 1,
                "start_date": date(2026, 9, 2),
                "due_date": date(2026, 9, 6),
                "expected_updated_at": NOW,
            },
            {
                "task_id": 2,
                "start_date": date(2026, 9, 6),
                "due_date": date(2026, 9, 9),
                "expected_updated_at": NOW,
            },
        ],
        date_change_reason="  Изменилось согласование  ",
    )

    assert result.applied_count == 2
    assert tasks.update.await_count == 2
    assert activity.save.await_count == 2
    assert {call.kwargs["reason"] for call in activity.save.await_args_list} == {
        "Изменилось согласование"
    }
    assert {call.kwargs["event_type"].value for call in activity.save.await_args_list} == {
        "DATES_CHANGED"
    }
    uow.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_preview_cascades_dates_without_writing() -> None:
    """Каскад по преемникам, цепочка из трёх и влияние на веху — всё без записи."""

    service, tasks, dependencies, _, activity, uow = build_service()
    predecessor = task(1, date(2026, 9, 1), date(2026, 9, 5))
    successor = task(2, date(2026, 9, 6), date(2026, 9, 8))
    tasks.get_by_project.return_value = [predecessor, successor]
    dependencies.get_by_project.return_value = [dependency(1, 2, lag_days=1)]

    result = await service.preview(
        1,
        [
            {
                "task_id": 1,
                "start_date": date(2026, 9, 4),
                "due_date": date(2026, 9, 10),
            }
        ],
    )

    assert [(item.task_id, item.source.value) for item in result.changes] == [
        (1, "DIRECT"),
        (2, "CASCADE"),
    ]
    assert result.changes[1].proposed.start_date == date(2026, 9, 11)
    assert result.changes[1].proposed.due_date == date(2026, 9, 13)
    assert result.consequences_count == 1
    tasks.update.assert_not_awaited()
    activity.save.assert_not_awaited()
    uow.commit.assert_not_awaited()

    service, tasks, dependencies, _, _, _ = build_service()
    tasks.get_by_project.return_value = [
        task(1, date(2026, 9, 1), date(2026, 9, 5)),
        task(2, date(2026, 9, 6), date(2026, 9, 8)),
        task(3, date(2026, 9, 9), date(2026, 9, 10)),
        task(4, date(2026, 9, 11), date(2026, 9, 12)),
    ]
    dependencies.get_by_project.return_value = [
        dependency(1, 2, lag_days=1),
        dependency(2, 3, lag_days=1),
        dependency(3, 4, lag_days=1),
    ]

    result = await service.preview(
        1,
        [
            {
                "task_id": 1,
                "start_date": date(2026, 9, 4),
                "due_date": date(2026, 9, 10),
            }
        ],
    )

    assert result.consequences_count == 3
    assert [
        (item.task_id, item.proposed.start_date, item.proposed.due_date) for item in result.changes
    ] == [
        (1, date(2026, 9, 4), date(2026, 9, 10)),
        (2, date(2026, 9, 11), date(2026, 9, 13)),
        (3, date(2026, 9, 14), date(2026, 9, 15)),
        (4, date(2026, 9, 16), date(2026, 9, 17)),
    ]

    service, tasks, dependencies, milestones, _, _ = build_service()
    item = task(1, date(2026, 9, 1), date(2026, 9, 5))
    tasks.get_by_project.return_value = [item]
    dependencies.get_by_project.return_value = []
    milestones.get_by_project.return_value = [
        SimpleNamespace(
            title="MVP",
            due_date=date(2026, 9, 12),
            wbs_node_id=None,
        )
    ]

    result = await service.preview(
        1,
        [
            {
                "task_id": 1,
                "start_date": date(2026, 9, 10),
                "due_date": date(2026, 10, 2),
            }
        ],
    )

    assert result.can_apply is True
    assert result.changes[0].reasons[-1].code == "MILESTONE_AT_RISK"
    assert result.changes[0].reasons[-1].milestone_title == "MVP"
