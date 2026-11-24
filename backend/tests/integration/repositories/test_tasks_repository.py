from datetime import date, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.project_stages import ProjectStage
from src.db.models.tasks import TaskPriority
from src.exceptions.tasks import TaskNumberAlreadyExistsRepositoryError
from src.repositories.project_stages import ProjectStagesRepository
from src.repositories.tasks import TasksRepository
from src.repositories.wbs_nodes import WbsNodesRepository

TODAY = date.today()


def task_data(stage: ProjectStage, number: int, **overrides) -> dict:
    """Собирает минимальные поля задачи для репозитория."""
    return {
        "project_id": stage.project_id,
        "stage_id": stage.id,
        "number": number,
        "title": f"Задача {number}",
        "priority": "MEDIUM",
        "position": float(number) * 1000,
        **overrides,
    }


@pytest.mark.asyncio
async def test_duplicate_number_within_project_raises_domain_error(
    db_session: AsyncSession,
    stage: ProjectStage,
) -> None:
    repository = TasksRepository(db_session)
    await repository.save(data=task_data(stage, 1))

    with pytest.raises(TaskNumberAlreadyExistsRepositoryError) as exc_info:
        await repository.save(data=task_data(stage, 1))

    assert exc_info.value.number == 1


@pytest.mark.asyncio
async def test_get_next_number_follows_existing_tasks(
    db_session: AsyncSession,
    stage: ProjectStage,
) -> None:
    repository = TasksRepository(db_session)

    assert await repository.get_next_number(project_id=stage.project_id) == 1

    await repository.save(data=task_data(stage, 7))

    assert await repository.get_next_number(project_id=stage.project_id) == 8


@pytest.mark.asyncio
async def test_project_statistics_are_aggregated_without_loading_tasks(
    db_session: AsyncSession,
    stage: ProjectStage,
) -> None:
    repository = TasksRepository(db_session)
    await repository.save(
        data=task_data(
            stage,
            1,
            priority="HIGH",
            assignee="Анна",
            due_date=TODAY - timedelta(days=1),
        )
    )
    await repository.save(data=task_data(stage, 2, priority="LOW", assignee=None))

    statistics = await repository.get_project_statistics(
        project_id=stage.project_id,
        today=TODAY,
    )

    assert statistics.total == 2
    assert statistics.overdue == 1
    assert statistics.by_stage == {stage.id: 2}
    assert statistics.by_priority == {"HIGH": 1, "LOW": 1}
    assert statistics.by_assignee == {"Анна": 1, "не назначен": 1}


@pytest.mark.asyncio
async def test_calendar_range_is_inclusive_and_applies_all_filters(
    db_session: AsyncSession,
    stage: ProjectStage,
) -> None:
    repository = TasksRepository(db_session)
    node = await WbsNodesRepository(db_session).save(
        data={
            "project_id": stage.project_id,
            "parent_id": None,
            "title": "Backend",
            "position": 1000.0,
        }
    )
    first = await repository.save(
        data=task_data(
            stage,
            1,
            priority="HIGH",
            assignee="Анна",
            due_date=TODAY,
            wbs_node_id=node.id,
        )
    )
    second = await repository.save(
        data=task_data(
            stage,
            2,
            priority="HIGH",
            assignee="Анна",
            due_date=TODAY + timedelta(days=2),
            wbs_node_id=node.id,
        )
    )
    await repository.save(
        data=task_data(stage, 3, priority="LOW", due_date=TODAY + timedelta(days=1))
    )
    await repository.save(
        data=task_data(
            stage,
            4,
            priority="HIGH",
            assignee="Анна",
            due_date=TODAY - timedelta(days=1),
        )
    )

    tasks = await repository.get_calendar_range(
        project_id=stage.project_id,
        date_from=TODAY,
        date_to=TODAY + timedelta(days=2),
        stage_id=stage.id,
        priority=TaskPriority.HIGH,
        assignee="Анна",
        wbs_node_id=node.id,
    )

    assert [task.id for task in tasks] == [first.id, second.id]


@pytest.mark.asyncio
async def test_calendar_range_includes_intervals_that_cross_window(
    db_session: AsyncSession,
    stage: ProjectStage,
) -> None:
    repository = TasksRepository(db_session)
    crossing = await repository.save(
        data=task_data(
            stage,
            10,
            start_date=TODAY - timedelta(days=10),
            due_date=TODAY + timedelta(days=10),
        )
    )
    single_day = await repository.save(data=task_data(stage, 11, due_date=TODAY))
    await repository.save(
        data=task_data(
            stage,
            12,
            start_date=TODAY - timedelta(days=10),
            due_date=TODAY - timedelta(days=2),
        )
    )

    tasks = await repository.get_calendar_range(
        project_id=stage.project_id,
        date_from=TODAY - timedelta(days=1),
        date_to=TODAY + timedelta(days=1),
    )

    assert {task.id for task in tasks} == {crossing.id, single_day.id}


@pytest.mark.asyncio
async def test_calendar_range_includes_start_only_but_unscheduled_does_not_duplicate_it(
    db_session: AsyncSession,
    stage: ProjectStage,
) -> None:
    repository = TasksRepository(db_session)
    start_only = await repository.save(data=task_data(stage, 20, start_date=TODAY, due_date=None))
    without_dates = await repository.save(data=task_data(stage, 21, start_date=None, due_date=None))

    ranged = await repository.get_calendar_range(
        project_id=stage.project_id,
        date_from=TODAY,
        date_to=TODAY + timedelta(days=1),
    )
    unscheduled = await repository.get_unscheduled_page(
        project_id=stage.project_id,
        cursor=None,
        limit=10,
    )

    assert [task.id for task in ranged] == [start_only.id]
    assert [task.id for task in unscheduled] == [without_dates.id]


@pytest.mark.asyncio
async def test_calendar_counts_ignore_done_deadlines_and_count_unscheduled(
    db_session: AsyncSession,
    stage: ProjectStage,
) -> None:
    done_stage = await ProjectStagesRepository(db_session).save(
        data={
            "project_id": stage.project_id,
            "name": "Готово",
            "order_index": 2,
            "color": "#3fb950",
            "is_done_stage": True,
        }
    )
    repository = TasksRepository(db_session)
    await repository.save(data=task_data(stage, 1, due_date=TODAY - timedelta(days=1)))
    await repository.save(data=task_data(stage, 2, due_date=TODAY + timedelta(days=3)))
    await repository.save(data=task_data(stage, 3, due_date=None))
    await repository.save(data=task_data(stage, 6, start_date=TODAY, due_date=None))
    await repository.save(
        data=task_data(stage, 4, stage_id=done_stage.id, due_date=TODAY - timedelta(days=3))
    )
    await repository.save(
        data=task_data(
            stage,
            5,
            due_date=TODAY + timedelta(days=10),
            baseline_due_date=TODAY + timedelta(days=8),
        )
    )

    counts = await repository.get_calendar_counts(
        project_id=stage.project_id,
        today=TODAY,
        soon_until=TODAY + timedelta(days=7),
    )

    assert counts.overdue == 1
    assert counts.due_soon == 1
    assert counts.unscheduled == 1
    assert counts.drifted == 1


@pytest.mark.asyncio
async def test_unscheduled_tasks_use_stable_cursor_pagination(
    db_session: AsyncSession,
    stage: ProjectStage,
) -> None:
    repository = TasksRepository(db_session)
    created = [
        await repository.save(data=task_data(stage, number, due_date=None))
        for number in range(1, 5)
    ]

    first_page = await repository.get_unscheduled_page(
        project_id=stage.project_id,
        cursor=None,
        limit=2,
    )
    second_page = await repository.get_unscheduled_page(
        project_id=stage.project_id,
        cursor=first_page[1].id,
        limit=2,
    )

    assert [task.id for task in first_page] == [task.id for task in created[:3]]
    assert [task.id for task in second_page] == [task.id for task in created[2:]]


@pytest.mark.asyncio
async def test_calendar_due_date_index_exists(engine) -> None:
    async with engine.connect() as connection:
        result = await connection.exec_driver_sql(
            "SELECT indexname FROM pg_indexes WHERE tablename = 'tasks'"
        )

    assert "ix_tasks_project_due_date" in set(result.scalars().all())


@pytest.mark.asyncio
async def test_portfolio_counters_split_done_and_overdue(
    db_session: AsyncSession,
    stage: ProjectStage,
) -> None:
    done_stage = await ProjectStagesRepository(db_session).save(
        data={
            "project_id": stage.project_id,
            "name": "Готово",
            "order_index": 2,
            "color": "#3fb950",
            "is_done_stage": True,
        }
    )
    repository = TasksRepository(db_session)
    await repository.save(data=task_data(stage, 1, due_date=TODAY - timedelta(days=2)))
    await repository.save(data=task_data(stage, 2, due_date=TODAY + timedelta(days=3)))
    await repository.save(
        data=task_data(stage, 3, stage_id=done_stage.id, due_date=TODAY - timedelta(days=9))
    )

    rows = await repository.get_portfolio_counters(
        today=TODAY,
        soon_until=TODAY + timedelta(days=7),
    )

    row = next(item for item in rows if item.project_id == stage.project_id)
    assert row.total == 3
    assert row.done == 1
    assert row.overdue == 1
    assert row.due_soon == 1
    assert row.next_due_date == TODAY + timedelta(days=3)


@pytest.mark.asyncio
async def test_clear_wbs_node_releases_tasks_without_deleting(
    db_session: AsyncSession,
    stage: ProjectStage,
) -> None:
    node = await WbsNodesRepository(db_session).save(
        data={
            "project_id": stage.project_id,
            "parent_id": None,
            "title": "Backend",
            "position": 1000.0,
        }
    )
    repository = TasksRepository(db_session)
    task = await repository.save(data=task_data(stage, 1, wbs_node_id=node.id))

    released = await repository.clear_wbs_node(node_ids={node.id})
    tasks = await repository.get_by_project(project_id=stage.project_id)

    assert released == 1
    assert [item.id for item in tasks] == [task.id]
    assert tasks[0].wbs_node_id is None


@pytest.mark.asyncio
async def test_search_finds_tasks_by_number_and_text_within_limit(
    db_session: AsyncSession, stage: ProjectStage
) -> None:
    """Поиск находит задачу по номеру и тексту и отдаёт не больше тридцати результатов."""

    repository = TasksRepository(db_session)
    task = await repository.save(
        data=task_data(
            stage,
            142,
            title="Пользовательская фильтрация",
            description_md="Фильтры по статусу.",
        )
    )

    by_key = await repository.search_ids(project_id=stage.project_id, search="PROJ-142")
    by_number = await repository.search_ids(project_id=stage.project_id, search="142")
    by_prefix = await repository.search_ids(project_id=stage.project_id, search="пользова")

    assert by_key == {task.id}
    assert by_number == {task.id}
    assert by_prefix == {task.id}

    repository = TasksRepository(db_session)
    for number in range(1, 36):
        await repository.save(data=task_data(stage, number, title=f"Поисковая задача {number}"))

    tasks = await repository.search_ranked(
        project_id=stage.project_id,
        search="поисковая",
        limit=30,
    )

    assert len(tasks) == 30
    assert all("Поисковая" in task.title for task in tasks)


@pytest.mark.asyncio
async def test_list_projection_is_bounded_and_does_not_truncate_detail(
    db_session: AsyncSession,
    stage: ProjectStage,
) -> None:
    repository = TasksRepository(db_session)
    description = "Описание 🔎 " * 2000
    saved = await repository.save(data=task_data(stage, 71, description_md=description))
    rows = await repository.get_list_by_project(project_id=stage.project_id)
    assert len(rows) == 1
    row = rows[0]
    assert row.description_preview == description[:320]
    assert not {"description_md", "checklist", "search_vector"} & set(row._mapping)
    # The projection must not replace the full ORM value in the same session.
    detail = await repository.get_by_id(saved.id)
    assert detail.description_md == description
    assert await repository.get_list_by_project(stage.project_id, task_ids=set()) == []
    assert await repository.get_list_by_project(stage.project_id, stage_id=stage.id + 1000) == []
    assert [
        item.id
        for item in await repository.get_list_by_project(
            stage.project_id, stage_id=stage.id, task_ids={saved.id}
        )
    ] == [saved.id]


@pytest.mark.asyncio
async def test_search_matches_literal_substrings_in_full_text_and_is_project_scoped(
    db_session: AsyncSession,
    stage: ProjectStage,
    project,
) -> None:
    from src.db.models.projects import Project
    from src.repositories.task_comments import TaskCommentsRepository

    other = Project(
        owner_id=project.owner_id, key="OTHER", name="Другой", color="#58a6ff", order_index=1
    )
    db_session.add(other)
    await db_session.flush()
    other_stage = ProjectStage(project_id=other.id, name="Работа", color="#58a6ff", order_index=0)
    db_session.add(other_stage)
    await db_session.flush()
    tasks = TasksRepository(db_session)
    comments = TaskCommentsRepository(db_session)
    description = "Начало " * 100 + " abcQxdef abcXYZdef Ёж ＡＢ 100%_literal"
    selected = await tasks.save(
        data=task_data(stage, 91, title="abcJydef", assignee="abcVzdef", description_md=description)
    )
    foreign = await tasks.save(
        data=task_data(
            other_stage, 91, title="abcJydef", assignee="abcVzdef", description_md=description
        )
    )
    comment_body = "Начало " * 100 + " abcKzdef abcRSTdef Ёж ＡＢ 100%_literal"
    for task in (selected, foreign):
        await comments.save(task_id=task.id, author_name="Тест", body_md=comment_body)
    unrelated = await tasks.save(
        data=task_data(stage, 92, title="Nothing", description_md="No symbols")
    )
    await comments.save(task_id=unrelated.id, author_name="Тест", body_md="No symbols")
    for query in (
        "qx",
        "jY",
        "vZ",
        "%",
        "_",
        "%_",
        "xyz",
        "jY xyz",
        "xyz kZ",
        "PROJ-91 xyz",
        "еЖ",
        "ёЖ",
        "ab",
    ):
        assert await tasks.search_ids(project_id=project.id, search=query) == {selected.id}
        if "PROJ" not in query:
            assert await tasks.search_ids(project_id=other.id, search=query) == {foreign.id}
    for query in ("kZ", "%", "_", "%_", "rst", "kZ rst", "еЖ", "ёЖ", "ab"):
        assert await comments.search_task_ids(search=query, project_id=project.id) == {selected.id}
        assert await comments.search_task_ids(search=query, project_id=other.id) == {foreign.id}
    assert await tasks.search_ids(project_id=project.id, search="  ") == set()
    assert await comments.search_task_ids(search="  ", project_id=project.id) == set()
    # Matching text does not force its full value into the list response.
    preview = (await tasks.get_list_by_project(project.id, task_ids={selected.id}))[0]
    assert "Qx" not in preview.description_preview
    assert len(preview.description_preview) == 320
