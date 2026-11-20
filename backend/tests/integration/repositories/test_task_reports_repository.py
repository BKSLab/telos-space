import pytest
from sqlalchemy import delete

from src.db.models.task_comments import TaskComment
from src.db.models.tasks import Task
from src.db.models.users import User
from src.repositories.task_reports import TaskReportsRepository
from src.schemas.enums import TaskStatus


@pytest.mark.asyncio
async def test_versions_and_source_snapshot_survive_comment_deletion(db_session):
    user = User(username="report.test", password_hash="test", last_name="Автор", first_name="Тест")
    db_session.add(user)
    await db_session.flush()
    task = Task(number=1, title="Отчёт", status=TaskStatus.NEW)
    db_session.add(task)
    await db_session.flush()
    comment = TaskComment(task_id=task.id, body_md="готово", author_id=user.id, author_name="Автор")
    db_session.add(comment)
    await db_session.flush()
    source_id = comment.id
    repo = TaskReportsRepository(db_session)
    await repo.append(
        {
            "task_id": task.id,
            "revision": 1,
            "body_md": "готово",
            "author_id": user.id,
            "author_name": "Публикатор",
            "source_comment_id": source_id,
            "source_comment_original_id": source_id,
            "source_author_name": "Автор",
        }
    )
    await repo.append(
        {
            "task_id": task.id,
            "revision": 2,
            "body_md": "уточнение",
            "author_id": user.id,
            "author_name": "Публикатор",
        }
    )
    assert (await repo.latest(task.id)).revision == 2
    await db_session.execute(delete(TaskComment).where(TaskComment.id == source_id))
    await db_session.flush()
    task_id = task.id
    db_session.expire_all()
    versions = await repo.history(task_id)
    assert [r.revision for r in versions] == [2, 1]
    assert versions[1].body_md == "готово"
    assert versions[1].source_comment_id is None
    assert versions[1].source_comment_original_id == source_id
    assert versions[1].source_author_name == "Автор"


@pytest.mark.asyncio
async def test_exact_old_report_lookup_is_scoped_to_task(db_session):
    from src.db.models.task_reports import TaskReport

    tasks = [Task(number=n, title=f"Task {n}", status=TaskStatus.NEW) for n in (1, 2)]
    db_session.add_all(tasks)
    await db_session.flush()
    reports = [
        TaskReport(task_id=tasks[0].id, revision=n, body_md=f"Version {n}", author_name="Автор")
        for n in range(1, 56)
    ]
    other = TaskReport(task_id=tasks[1].id, revision=1, body_md="Private", author_name="Автор")
    db_session.add_all([*reports, other])
    await db_session.flush()
    repo = TaskReportsRepository(db_session)
    assert len(await repo.history(tasks[0].id)) == 50
    assert [r.revision for r in await repo.history(tasks[0].id, report_id=reports[0].id)] == [1]
    assert [r.id for r in await repo.history(tasks[0].id, revision=1)] == [reports[0].id]
    assert await repo.history(tasks[0].id, report_id=other.id) == []
    assert await repo.history(tasks[0].id, report_id=reports[0].id, revision=2) == []
