import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.project_stages import ProjectStage
from src.repositories.task_comments import TaskCommentsRepository
from src.repositories.tasks import TasksRepository


@pytest.mark.asyncio
async def test_search_on_real_postgres_finds_task_by_comment_prefix(
    db_session: AsyncSession,
    stage: ProjectStage,
) -> None:
    task = await TasksRepository(db_session).save(
        data={
            "project_id": stage.project_id,
            "stage_id": stage.id,
            "number": 1,
            "title": "Карточка",
            "priority": "MEDIUM",
            "position": 1.0,
        }
    )
    repository = TaskCommentsRepository(db_session)
    await repository.save(
        task_id=task.id,
        author_name="Борис",
        body_md="Нужно проверить пользовательский сценарий.",
    )

    task_ids = await repository.search_task_ids(search="пользова")
    highlights = await repository.get_search_highlights(
        task_ids=[task.id],
        search="пользова",
    )

    assert task_ids == {task.id}
    assert await repository.search_task_ids(search="пользова", project_id=stage.project_id) == {
        task.id
    }
    assert await repository.search_task_ids(search="пользова", project_id=-1) == set()
    assert highlights[task.id]["search_match_source"] == "comment"
    assert "__FTS_START__" in highlights[task.id]["search_excerpt"]


@pytest.mark.asyncio
async def test_context_counts_only_selected_tasks_and_bounds_latest_preview(
    db_session: AsyncSession,
    stage: ProjectStage,
) -> None:
    from datetime import UTC, datetime

    tasks = TasksRepository(db_session)
    selected, unrelated, empty = [
        await tasks.save(
            data={
                "project_id": stage.project_id,
                "stage_id": stage.id,
                "number": number,
                "title": f"Карточка {number}",
                "priority": "MEDIUM",
                "position": float(number),
            }
        )
        for number in (51, 52, 53)
    ]
    repository = TaskCommentsRepository(db_session)
    first = await repository.save(task_id=selected.id, author_name="Тест", body_md="старый")
    body = "Последний 🔎 " * 1000
    last = await repository.save(task_id=selected.id, author_name="Тест", body_md=body)
    await repository.save(task_id=unrelated.id, author_name="Тест", body_md="другая задача")
    first.created_at = last.created_at = datetime(2026, 1, 1, tzinfo=UTC)
    await db_session.flush()
    assert await repository.get_task_context(task_ids=[], preview_length=320) == {}
    assert await repository.get_task_context(
        task_ids=[selected.id, empty.id], preview_length=320
    ) == {selected.id: (2, body[:320])}
    assert await repository.get_task_context(task_ids=[selected.id]) == {selected.id: (2, body)}
