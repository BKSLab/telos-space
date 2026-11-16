"""Правило причины применяется к каждой уже назначенной дате и любому виду задачи."""

from datetime import date
from unittest.mock import AsyncMock

import pytest

from src.exceptions.tasks import TaskDateChangeReasonError
from src.repositories.task_activity import TaskActivityRepository
from src.repositories.tasks import TasksRepository
from tests.unit.services.test_calendar_scenarios_service import NOW
from tests.unit.services.test_calendar_scenarios_service import build_service as scenario_service
from tests.unit.services.test_calendar_scenarios_service import task as scenario_task
from tests.unit.services.test_tasks_service import build_service, make_task

START = date(2026, 11, 16)
DUE = date(2026, 11, 20)
NEXT = date(2026, 11, 21)


async def test_first_assignment_each_missing_date_and_noop_do_not_require_reason():
    """Наличие другой даты не превращает первое назначение в перенос."""
    cases = (
        (None, None, {"start_date": START}),
        (None, None, {"due_date": DUE}),
        (None, DUE, {"start_date": START}),
        (START, None, {"due_date": DUE}),
        (START, DUE, {"start_date": START, "due_date": DUE}),
        (None, None, {"start_date": None, "due_date": None}),
    )
    for project_id in (1, None):
        for start, due, patch in cases:
            tasks = AsyncMock(spec=TasksRepository)
            before = make_task(start_date=start, due_date=due)
            after = make_task(
                start_date=patch.get("start_date", start), due_date=patch.get("due_date", due)
            )
            before.project_id = after.project_id = project_id
            tasks.get_by_id.return_value = before
            tasks.update.return_value = after
            activity = AsyncMock(spec=TaskActivityRepository)
            result = await build_service(tasks, activity_repository=activity).update_task(10, patch)
            assert (result.start_date, result.due_date) == (after.start_date, after.due_date)
            changed = (start, due) != (after.start_date, after.due_date)
            assert activity.save.await_count == int(changed)
            if changed:
                assert activity.save.await_args.kwargs["reason"] is None


async def test_existing_date_change_or_removal_requires_nonempty_reason_before_any_write():
    """Добавление другой даты не маскирует перенос, запрет атомарен и для личной задачи."""
    patches = (
        {"start_date": DUE},
        {"start_date": None},
        {"due_date": NEXT},
        {"due_date": None},
        {"start_date": None, "due_date": NEXT},
    )
    for project_id in (1, None):
        for patch in patches:
            for reason in (None, "", " \t\n", "x" * 2001):
                tasks = AsyncMock(spec=TasksRepository)
                before = make_task(start_date=START, due_date=DUE)
                before.project_id = project_id
                tasks.get_by_id.return_value = before
                activity = AsyncMock(spec=TaskActivityRepository)
                service = build_service(tasks, activity_repository=activity)
                with pytest.raises(TaskDateChangeReasonError):
                    await service.update_task(
                        10, {**patch, "title": "Также не сохраняется", "date_change_reason": reason}
                    )
                tasks.update.assert_not_awaited()
                activity.save.assert_not_awaited()
                service.unit_of_work.commit.assert_not_awaited()
                service.unit_of_work.rollback.assert_awaited_once()

    tasks = AsyncMock(spec=TasksRepository)
    tasks.get_by_id.return_value = make_task(start_date=None, due_date=DUE)
    with pytest.raises(TaskDateChangeReasonError):
        await build_service(tasks).update_task(10, {"start_date": START, "due_date": NEXT})


async def test_both_dates_have_one_history_entry_and_reason_is_not_an_orm_field():
    tasks = AsyncMock(spec=TasksRepository)
    tasks.get_by_id.return_value = make_task(start_date=START, due_date=DUE)
    tasks.update.return_value = make_task(start_date=None, due_date=NEXT)
    activity = AsyncMock(spec=TaskActivityRepository)
    service = build_service(tasks, activity_repository=activity)
    await service.update_task(
        10, {"start_date": None, "due_date": NEXT, "date_change_reason": "  Изменение поставки  "}
    )
    assert activity.save.await_count == 1
    assert activity.save.await_args.kwargs == {
        "task_id": 10,
        "event_type": "DATES_CHANGED",
        "from_value": "начало: 2026-11-16; завершение: 2026-11-20",
        "to_value": "начало: —; завершение: 2026-11-21",
        "reason": "Изменение поставки",
    }
    assert "date_change_reason" not in tasks.update.await_args.kwargs["data"]
    service.unit_of_work.commit.assert_awaited_once()


async def test_scenario_checks_all_reasons_before_writing_and_allows_initial_assignment_and_noop():
    service, tasks, dependencies, _, activity, uow = scenario_service()
    tasks.get_by_project.return_value = [scenario_task(1, None, None), scenario_task(2, START, DUE)]
    dependencies.get_by_project.return_value = []
    changes = [
        {"task_id": 1, "start_date": START, "due_date": DUE, "expected_updated_at": NOW},
        {"task_id": 2, "start_date": START, "due_date": NEXT, "expected_updated_at": NOW},
    ]
    with pytest.raises(TaskDateChangeReasonError):
        await service.apply(1, changes, date_change_reason=" \n ")
    tasks.update.assert_not_awaited()
    activity.save.assert_not_awaited()
    uow.rollback.assert_awaited_once()
    uow.commit.assert_not_awaited()
    tasks.get_by_project.assert_awaited_once_with(1, for_update=True)

    changes[1]["due_date"] = DUE
    result = await service.apply(1, changes)
    assert result.task_ids == [1]
    assert activity.save.await_count == 1
    assert activity.save.await_args.kwargs["reason"] is None
