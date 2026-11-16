from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.db.models.project_milestones import ProjectMilestoneStatus
from src.db.models.task_dependencies import TaskDependencyType
from src.db.models.tasks import TaskPriority
from src.exceptions.calendar import CalendarFilterError, CalendarRangeError, CalendarServiceError
from src.exceptions.tasks import TasksRepositoryError
from src.repositories.milestones import MilestonesRepository
from src.repositories.project_stages import ProjectStagesRepository
from src.repositories.projects import ProjectsRepository
from src.repositories.task_activity import TaskActivityRepository
from src.repositories.task_dependencies import TaskDependenciesRepository
from src.repositories.task_participants import TaskParticipantsRepository
from src.repositories.tasks import CalendarTaskCounts, TasksRepository
from src.repositories.wbs_nodes import WbsNodesRepository
from src.services.calendar import MAX_CALENDAR_RANGE_DAYS, CalendarService

TODAY = date(2026, 9, 2)


def make_task(*, task_id: int = 1, due_date: date | None = TODAY) -> SimpleNamespace:
    """Создаёт полную задачу-дублёр календаря."""
    return SimpleNamespace(
        id=task_id,
        revision=4,
        project_id=1,
        stage_id=10,
        wbs_node_id=20,
        number=task_id,
        title=f"Задача {task_id}",
        priority=TaskPriority.HIGH,
        assignee="Анна",
        start_date=None,
        due_date=due_date,
        baseline_start_date=None,
        baseline_due_date=None,
        position=1000.0,
        updated_at=datetime.now(UTC),
    )


def build_service() -> tuple[
    CalendarService,
    AsyncMock,
    AsyncMock,
    AsyncMock,
    AsyncMock,
    AsyncMock,
]:
    """Собирает календарный сервис с типизированными дублёрами."""
    projects = AsyncMock(spec=ProjectsRepository)
    projects.get_by_id.return_value = SimpleNamespace(
        id=1,
        key="TEST",
        start_date=date(2026, 8, 1),
        due_date=date(2026, 12, 31),
    )
    tasks = AsyncMock(spec=TasksRepository)
    tasks.get_calendar_range.return_value = [make_task()]
    tasks.get_calendar_counts.return_value = CalendarTaskCounts(
        overdue=0,
        due_soon=1,
        unscheduled=2,
        drifted=0,
    )
    tasks.get_calendar_assignees.return_value = ["Анна"]
    tasks.get_latest_open_end_date.return_value = None
    tasks.get_by_ids.return_value = []
    stages = AsyncMock(spec=ProjectStagesRepository)
    stages.get_by_project.return_value = [
        SimpleNamespace(
            id=10,
            name="В работе",
            color="#58a6ff",
            order_index=1,
            is_done_stage=False,
        )
    ]
    nodes = AsyncMock(spec=WbsNodesRepository)
    nodes.get_by_project.return_value = [
        SimpleNamespace(id=20, parent_id=None, title="Backend", position=1000.0)
    ]
    activity = AsyncMock(spec=TaskActivityRepository)
    activity.get_recent_due_date_changes.return_value = []
    milestones = AsyncMock(spec=MilestonesRepository)
    milestones.get_range.return_value = []
    dependencies = AsyncMock(spec=TaskDependenciesRepository)
    dependencies.get_by_project.return_value = []
    participants = AsyncMock(spec=TaskParticipantsRepository)
    participants.get_by_task_ids.return_value = {}
    return (
        CalendarService(
            projects, tasks, stages, nodes, activity, milestones, dependencies, participants
        ),
        projects,
        tasks,
        stages,
        nodes,
        activity,
    )


@pytest.mark.asyncio
async def test_get_range_builds_compact_calendar_with_client_today() -> None:
    service, _, tasks, _, _, _ = build_service()

    result = await service.get_range(
        project_id=1,
        date_from=date(2026, 9, 1),
        date_to=date(2026, 9, 30),
        today=TODAY,
        stage_id=10,
        priority=TaskPriority.HIGH,
        assignee="Анна",
        wbs_node_id=20,
    )

    assert result.range.today == TODAY
    assert result.tasks[0].key == "TEST-1"
    assert result.tasks[0].revision == 4
    assert result.tasks[0].is_due_soon is True
    assert result.summary.unscheduled == 2
    assert result.wbs_nodes[0].title == "Backend"
    tasks.get_calendar_range.assert_awaited_once_with(
        project_id=1,
        date_from=date(2026, 9, 1),
        date_to=date(2026, 9, 30),
        stage_id=10,
        priority=TaskPriority.HIGH,
        assignee="Анна",
        wbs_node_id=20,
    )


@pytest.mark.asyncio
async def test_get_range_validates_its_window_and_filters() -> None:
    """Границы окна: обратный интервал, превышение предела, ровно предел и чужие фильтры."""

    service, projects, _, _, _, _ = build_service()

    with pytest.raises(CalendarRangeError):
        await service.get_range(
            project_id=1,
            date_from=TODAY,
            date_to=TODAY - timedelta(days=1),
            today=TODAY,
        )
    with pytest.raises(CalendarRangeError):
        await service.get_range(
            project_id=1,
            date_from=TODAY,
            date_to=TODAY + timedelta(days=MAX_CALENDAR_RANGE_DAYS + 1),
            today=TODAY,
        )

    projects.get_by_id.assert_not_awaited()

    service, projects, _, _, _, _ = build_service()

    result = await service.get_range(
        project_id=1,
        date_from=TODAY,
        date_to=TODAY + timedelta(days=MAX_CALENDAR_RANGE_DAYS),
        today=TODAY,
    )

    assert result.range.date_to == TODAY + timedelta(days=MAX_CALENDAR_RANGE_DAYS)
    with pytest.raises(CalendarRangeError):
        await service.get_range(
            project_id=1,
            date_from=TODAY,
            date_to=TODAY + timedelta(days=MAX_CALENDAR_RANGE_DAYS + 1),
            today=TODAY,
        )
    projects.get_by_id.assert_awaited_once()

    service, _, tasks, _, _, _ = build_service()

    with pytest.raises(CalendarFilterError) as exc_info:
        await service.get_range(
            project_id=1,
            date_from=TODAY,
            date_to=TODAY,
            today=TODAY,
            stage_id=999,
        )

    assert exc_info.value.status_code == 422
    tasks.get_calendar_range.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_range_explains_dates_milestones_and_dependencies() -> None:
    """Календарь объясняет риски: история сроков, вехи с отклонением плана, частично заданный базовый план и связи задач."""

    service, _, tasks, _, _, activity = build_service()
    overdue = make_task(due_date=TODAY - timedelta(days=3))
    overdue.assignee = None
    tasks.get_calendar_range.return_value = [overdue]
    tasks.get_by_ids.return_value = [overdue]
    activity.get_recent_due_date_changes.return_value = [
        SimpleNamespace(
            id=7,
            task_id=overdue.id,
            from_value="2026-08-28",
            to_value="2026-08-30",
            created_at=datetime.now(UTC),
        )
    ]

    result = await service.get_range(
        project_id=1,
        date_from=TODAY - timedelta(days=7),
        date_to=TODAY,
        today=TODAY,
    )

    assert result.tasks[0].risk_level == "high"
    assert [reason.code for reason in result.tasks[0].risk_reasons] == [
        "OVERDUE",
        "NO_ASSIGNEE",
    ]
    assert result.recent_changes[0].task_key == "TEST-1"
    assert result.recent_changes[0].from_date == date(2026, 8, 28)
    assert result.recent_changes[0].to_date == date(2026, 8, 30)
    activity.get_recent_due_date_changes.return_value.append(SimpleNamespace(
        id=8, task_id=overdue.id,
        from_value="начало: 2026-08-20; завершение: 2026-08-30",
        to_value="начало: —; завершение: 2026-09-03",
        created_at=datetime.now(UTC),
    ))
    result = await service.get_range(
        project_id=1, date_from=TODAY - timedelta(days=7), date_to=TODAY, today=TODAY,
    )
    assert result.recent_changes[1].from_date == date(2026, 8, 30)
    assert result.recent_changes[1].to_date == date(2026, 9, 3)

    service, projects, tasks, _, _, _ = build_service()
    projects.get_by_id.return_value.due_date = date(2026, 9, 30)
    task = make_task(due_date=date(2026, 9, 12))
    task.baseline_start_date = date(2026, 9, 1)
    task.baseline_due_date = date(2026, 9, 8)
    tasks.get_calendar_range.return_value = [task]
    service.milestones_repository.get_range.return_value = [
        SimpleNamespace(
            id=9,
            title="MVP",
            due_date=date(2026, 9, 20),
            status=ProjectMilestoneStatus.PLANNED,
            wbs_node_id=20,
            description_md=None,
        )
    ]

    result = await service.get_range(
        project_id=1,
        date_from=date(2026, 9, 1),
        date_to=date(2026, 9, 30),
        today=TODAY,
    )

    assert result.tasks[0].drift_days == 4
    assert [(item.title, item.is_system) for item in result.milestones] == [
        ("MVP", False),
        ("Дедлайн проекта", True),
    ]

    service, _, tasks, _, _, _ = build_service()
    only_start = make_task(task_id=1, due_date=date(2026, 9, 12))
    only_start.baseline_start_date = date(2026, 9, 1)
    only_due = make_task(task_id=2, due_date=date(2026, 9, 12))
    only_due.baseline_due_date = date(2026, 9, 8)
    tasks.get_calendar_range.return_value = [only_start, only_due]

    result = await service.get_range(
        project_id=1,
        date_from=date(2026, 9, 1),
        date_to=date(2026, 9, 30),
        today=TODAY,
    )

    assert result.tasks[0].drift_days is None
    assert result.tasks[1].drift_days == 4

    service, _, tasks, _, _, _ = build_service()
    predecessor = make_task(task_id=1, due_date=date(2026, 9, 10))
    successor = make_task(task_id=2, due_date=date(2026, 9, 15))
    successor.start_date = date(2026, 9, 9)
    tasks.get_calendar_range.return_value = [predecessor, successor]
    tasks.get_by_ids.return_value = [predecessor, successor]
    service.dependencies_repository.get_by_project.return_value = [
        SimpleNamespace(
            id=5,
            project_id=1,
            predecessor_task_id=1,
            successor_task_id=2,
            dependency_type=TaskDependencyType.FINISH_TO_START,
            lag_days=2,
            created_at=datetime.now(UTC),
        )
    ]

    result = await service.get_range(
        project_id=1,
        date_from=date(2026, 9, 1),
        date_to=date(2026, 9, 30),
        today=TODAY,
    )

    risks = {task.id: task.risk_reasons for task in result.tasks}
    assert risks[1][-1].code == "BLOCKED_SUCCESSOR"
    assert risks[1][-1].task_key == "TEST-2"
    assert risks[2][-1].code == "NEGATIVE_SLACK"
    assert risks[2][-1].days == 3
    assert result.summary.dependency_risks == 2
    assert result.dependencies[0].predecessor_task_id == 1


@pytest.mark.asyncio
async def test_calendar_reads_report_failures_and_paginate_by_cursor() -> None:
    """Сбой репозитория становится ошибкой сервиса, несогласованные задачи листаются стабильным курсором."""

    service, _, tasks, _, _, _ = build_service()
    tasks.get_calendar_range.side_effect = TasksRepositoryError("БД недоступна")

    with pytest.raises(CalendarServiceError) as exc_info:
        await service.get_range(
            project_id=1,
            date_from=TODAY,
            date_to=TODAY,
            today=TODAY,
        )

    assert exc_info.value.status_code == 500

    service, _, tasks, _, _, _ = build_service()
    tasks.get_unscheduled_page.return_value = [
        make_task(task_id=1, due_date=None),
        make_task(task_id=2, due_date=None),
        make_task(task_id=3, due_date=None),
    ]

    result = await service.get_unscheduled(
        project_id=1,
        today=TODAY,
        cursor=None,
        limit=2,
    )

    assert [item.id for item in result.items] == [1, 2]
    assert all(item.due_date is None for item in result.items)
    assert all(item.revision == 4 for item in result.items)
    assert result.next_cursor == 2
