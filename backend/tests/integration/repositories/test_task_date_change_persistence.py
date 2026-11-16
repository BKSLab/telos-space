"""История сроков, атомарность и конкуренция на настоящем PostgreSQL."""

import asyncio
from datetime import date
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from src.db.models.task_activity import TaskActivity, TaskActivityEventType
from src.db.models.tasks import Task
from src.exceptions.calendar import CalendarScenarioVersionConflictError, CalendarServiceError
from src.exceptions.knowledge import KnowledgeEventsServiceError
from src.exceptions.tasks import TaskDateChangeReasonError, TasksRepositoryError, TasksServiceError
from src.repositories.milestones import MilestonesRepository
from src.repositories.projects import ProjectsRepository
from src.repositories.task_activity import TaskActivityRepository
from src.repositories.task_dependencies import TaskDependenciesRepository
from src.repositories.tasks import TasksRepository
from src.repositories.unit_of_work import UnitOfWork
from src.schemas.task_activity import ActivitySchema
from src.services.calendar_scenarios import CalendarScenarioService
from src.services.knowledge_events import KnowledgeEvents
from src.utils.request_actor import RequestActor, reset_request_actor, set_request_actor
from tests.integration.repositories import test_task_revision
from tests.integration.repositories.test_personal_tasks_and_moves import services
from tests.integration.repositories.test_task_checklist_persistence import build_service

dates_project = test_task_revision.revision_project

START = date(2026, 11, 16)
DUE = date(2026, 11, 20)
LATER = date(2026, 11, 23)


def scenario_service(session):
    return CalendarScenarioService(
        ProjectsRepository(session),
        TasksRepository(session),
        TaskDependenciesRepository(session),
        MilestonesRepository(session),
        TaskActivityRepository(session),
        UnitOfWork(session),
    )


async def test_project_and_personal_date_history_keep_author_period_and_single_reason(
    db_session, project, stage, user, tmp_path
):
    service, _ = services(db_session, tmp_path)
    user_id = user.id
    project_task = await service.create_task(
        project.id, {"title": "Проектная задача", "stage_id": stage.id}
    )
    personal_task = await service.create_personal_task(
        data={"title": "Личная задача"}, created_by_user_id=user_id
    )
    actor_token = set_request_actor(RequestActor(user_id, "Автор переноса", "Тестовый ключ"))
    try:
        for task in (project_task, personal_task):
            await service.update_task(task.id, {"due_date": DUE})
            # Отсутствующую дату начала назначаем без причины, несмотря на уже заданный дедлайн.
            await service.update_task(task.id, {"start_date": START})
            await service.update_task(task.id, {"start_date": START, "due_date": DUE})
            with pytest.raises(TaskDateChangeReasonError):
                await service.update_task(task.id, {"due_date": None, "title": "Не сохранять"})
            await service.update_task(
                task.id,
                {
                    "start_date": None,
                    "due_date": LATER,
                    "date_change_reason": "  Задержка поставки  ",
                },
            )
            history = await TaskActivityRepository(db_session).get_for_task(task.id)
            assert len(history) == 3
            assert all(event.event_type is TaskActivityEventType.DATES_CHANGED for event in history)
            assert [event.reason for event in history] == [None, None, "Задержка поставки"]
            changed = history[-1]
            assert changed.from_value == "начало: 2026-11-16; завершение: 2026-11-20"
            assert changed.to_value == "начало: —; завершение: 2026-11-23"
            assert (changed.actor_user_id, changed.actor_name, changed.via_api_key_label) == (
                user_id,
                "Автор переноса",
                "Тестовый ключ",
            )
            assert changed.created_at is not None
            assert ActivitySchema.model_validate(changed).reason == "Задержка поставки"
            actual = await db_session.get(Task, task.id)
            assert actual.title == task.title
            assert (actual.start_date, actual.due_date) == (None, LATER)
    finally:
        reset_request_actor(actor_token)


async def test_date_and_reason_rollback_with_task_and_outbox_failure(dates_project):
    ctx = dates_project
    async with ctx.factory() as session:
        service = build_service(session, ctx.tmp_path)
        await service.update_task(ctx.task_id, {"start_date": START, "due_date": DUE})
        service.knowledge_events.upsert = AsyncMock(
            spec=KnowledgeEvents.upsert, side_effect=KnowledgeEventsServiceError("outbox failure")
        )
        with pytest.raises(TasksServiceError):
            await service.update_task(
                ctx.task_id,
                {
                    "start_date": None,
                    "due_date": LATER,
                    "title": "Не сохранять",
                    "date_change_reason": "Причина тоже откатывается",
                },
            )
    async with ctx.factory() as session:
        task = await session.get(Task, ctx.task_id)
        assert (task.start_date, task.due_date, task.title) == (START, DUE, "Первоначальный текст")
        history = await TaskActivityRepository(session).get_for_task(ctx.task_id)
        assert len(history) == 1 and history[0].reason is None


async def test_concurrent_first_assignments_refresh_cached_state_and_require_reason(dates_project):
    ctx = dates_project
    barrier = asyncio.Barrier(2)

    async def assign(due):
        async with ctx.factory() as session:
            cached = await session.get(Task, ctx.task_id)
            assert cached.due_date is None
            await barrier.wait()
            return await build_service(session, ctx.tmp_path).update_task(
                ctx.task_id, {"due_date": due}
            )

    outcomes = await asyncio.gather(assign(DUE), assign(LATER), return_exceptions=True)
    assert sum(isinstance(item, TaskDateChangeReasonError) for item in outcomes) == 1
    assert sum(not isinstance(item, BaseException) for item in outcomes) == 1
    async with ctx.factory() as session:
        actual = await session.get(Task, ctx.task_id)
        assert actual.due_date in {DUE, LATER}
        history = await TaskActivityRepository(session).get_for_task(ctx.task_id)
        assert len(history) == 1 and history[0].reason is None


async def test_concurrent_scenarios_recheck_version_after_row_lock(dates_project):
    ctx = dates_project
    async with ctx.factory() as session:
        original = await session.get(Task, ctx.task_id)
        expected = original.updated_at
    barrier = asyncio.Barrier(2)

    async def apply(due):
        async with ctx.factory() as session:
            # В identity map намеренно остаётся прежнее состояние до конкурентного commit.
            cached = await session.get(Task, ctx.task_id)
            assert cached.due_date is None
            await barrier.wait()
            return await scenario_service(session).apply(
                ctx.project_id,
                [
                    {
                        "task_id": ctx.task_id,
                        "start_date": START,
                        "due_date": due,
                        "expected_updated_at": expected,
                    }
                ],
            )

    outcomes = await asyncio.gather(apply(DUE), apply(LATER), return_exceptions=True)
    assert sum(isinstance(item, CalendarScenarioVersionConflictError) for item in outcomes) == 1
    assert sum(not isinstance(item, BaseException) for item in outcomes) == 1
    async with ctx.factory() as session:
        history = await TaskActivityRepository(session).get_for_task(ctx.task_id)
        assert len(history) == 1 and history[0].reason is None


async def test_scenario_failure_rolls_back_all_tasks_and_history(dates_project):
    ctx = dates_project
    async with ctx.factory() as session:
        tasks = build_service(session, ctx.tmp_path)
        second = await tasks.create_task(
            ctx.project_id, {"title": "Вторая задача", "stage_id": ctx.stage_id}
        )
        second = await tasks.update_task(second.id, {"start_date": START, "due_date": DUE})
        first = await tasks.get_task(ctx.task_id)
        second_id = second.id
        changes = [
            {
                "task_id": first.id,
                "start_date": START,
                "due_date": DUE,
                "expected_updated_at": first.updated_at,
            },
            {
                "task_id": second.id,
                "start_date": START,
                "due_date": LATER,
                "expected_updated_at": second.updated_at,
            },
        ]
        scenario = scenario_service(session)
        with pytest.raises(TaskDateChangeReasonError):
            await scenario.apply(ctx.project_id, changes)
        original_update = scenario.tasks_repository.update

        async def fail_second(task, data):
            if task.id == second_id:
                raise TasksRepositoryError("second task update failure")
            return await original_update(task, data)

        scenario.tasks_repository.update = fail_second
        with pytest.raises(CalendarServiceError):
            await scenario.apply(ctx.project_id, changes, date_change_reason="Общий перенос")
    async with ctx.factory() as session:
        first = await session.get(Task, ctx.task_id)
        second = await session.get(Task, second_id)
        assert (first.start_date, first.due_date) == (None, None)
        assert (second.start_date, second.due_date) == (START, DUE)
        events = (
            (
                await session.execute(
                    select(TaskActivity).where(TaskActivity.task_id.in_([ctx.task_id, second_id]))
                )
            )
            .scalars()
            .all()
        )
        assert len(events) == 1 and events[0].reason is None
        result = await scenario_service(session).apply(
            ctx.project_id, changes, date_change_reason="Общий перенос"
        )
        assert result.task_ids == [ctx.task_id, second_id]
        events = (
            (
                await session.execute(
                    select(TaskActivity).where(
                        TaskActivity.task_id.in_([ctx.task_id, second_id]),
                        TaskActivity.reason.is_not(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(events) == 2 and {event.reason for event in events} == {"Общий перенос"}
