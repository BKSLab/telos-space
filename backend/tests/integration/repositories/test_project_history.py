from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from main import app
from src.db.models.documents import Document
from src.db.models.project_deadline_changes import ProjectDeadlineChange
from src.db.models.project_files import ProjectFile
from src.db.models.project_history_events import ProjectHistoryEvent
from src.db.models.project_members import ProjectMember
from src.db.models.project_risks import ProjectRisk
from src.db.models.projects import ProjectContextHistory
from src.db.models.task_activity import TaskActivity, TaskActivityEventType
from src.db.models.task_comments import TaskComment
from src.db.models.task_reports import TaskReport
from src.dependencies.access import require_project_access
from src.dependencies.auth import get_principal
from src.dependencies.db_session import get_db_session
from src.repositories.tasks import TasksRepository
from src.repositories.users import UsersRepository


async def test_member_reads_combined_project_history(
    db_session,
    project,
    stage,
    user,
    current_principal,
    monkeypatch,
):
    timestamp = datetime(2026, 11, 19, 23, 59, 59, 999999, tzinfo=UTC)
    next_day = datetime(2026, 11, 20, tzinfo=UTC)
    task, moved_task = [
        await TasksRepository(db_session).save(
            data={
                "project_id": project.id,
                "stage_id": stage.id,
                "number": number,
                "title": title,
                "priority": "MEDIUM",
                "position": 0.0,
                "created_at": timestamp - timedelta(days=2),
            }
        )
        for number, title in [(1, "Поставка"), (2, "Перенесённая задача")]
    ]
    db_session.add_all(
        [
            TaskActivity(
                task_id=task.id,
                event_type=TaskActivityEventType.STAGE_CHANGED,
                actor_name="Анна",
                actor_user_id=user.id,
                from_value="Ожидание",
                to_value="Готово",
                created_at=timestamp,
            ),
            TaskActivity(
                task_id=task.id,
                event_type=TaskActivityEventType.COMMENT_ADDED,
                created_at=timestamp,
            ),
            TaskComment(
                task_id=task.id,
                body_md="Готово на 100%",
                author_name="Анна",
                author_id=user.id,
                created_at=timestamp,
            ),
            TaskReport(
                task_id=task.id,
                revision=1,
                body_md="Готово на 1000",
                author_name="Анна",
                created_at=timestamp,
            ),
            TaskActivity(
                task_id=moved_task.id,
                event_type=TaskActivityEventType.TASK_MOVED,
                created_at=next_day,
            ),
            TaskComment(
                task_id=moved_task.id, body_md="Старый личный комментарий", created_at=timestamp
            ),
            ProjectDeadlineChange(
                project_id=project.id, changed_by_name="Анна", created_at=next_day
            ),
            ProjectContextHistory(
                project_id=project.id,
                revision=0,
                changed_fields=[],
                is_initial=True,
                created_at=timestamp,
            ),
            ProjectContextHistory(
                project_id=project.id,
                revision=1,
                changed_fields=["goal"],
                changed_by_name="Анна",
                is_initial=False,
                created_at=timestamp,
            ),
            ProjectRisk(
                project_id=project.id,
                title="Поставка",
                description="Задержка",
                probability="LOW",
                impact="LOW",
                risk_level="LOW",
                response_strategy="ACCEPT",
                owner_user_id=user.id,
                created_at=timestamp,
            ),
            Document(
                project_id=project.id,
                slug="delivery",
                title="Поставка",
                content_md="Текст",
                authored_by_user_id=user.id,
                created_at=timestamp,
            ),
            ProjectFile(
                project_id=project.id,
                original_name="deleted.txt",
                content_type="text/plain",
                size=3,
                storage_key="test",
                content_hash="a" * 64,
                deleted_at=next_day,
                created_at=timestamp,
            ),
        ]
    )
    await db_session.flush()

    member = await UsersRepository(db_session).save(
        data={
            "username": "member",
            "password_hash": "hash",
            "last_name": "Участников",
            "first_name": "Иван",
            "is_active": True,
        }
    )
    db_session.add(ProjectMember(project_id=project.id, user_id=member.id))
    await db_session.flush()
    # An installation's existing sources have no journal until the explicit backfill.
    await db_session.execute(delete(ProjectHistoryEvent))
    principal = replace(current_principal, user_id=member.id)
    monkeypatch.delitem(app.dependency_overrides, require_project_access)
    monkeypatch.setitem(app.dependency_overrides, get_db_session, lambda: db_session)
    monkeypatch.setitem(app.dependency_overrides, get_principal, lambda: principal)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        path = f"/api/v1/projects/{project.id}/history"
        all_items = []
        for offset in (0, 3, 6):
            response = await client.get(
                path,
                params={
                    "offset": offset,
                    "limit": 3,
                    "since": "2026-11-19",
                    "until": "2026-11-20",
                },
            )
            assert response.status_code == 200, response.text
            page = response.json()
            assert page["next_offset"] == (offset + 3 if offset < 6 else None)
            all_items.extend(page["items"])
        assert [item["kind"] for item in all_items] == [
            "project_deadline",
            "task_report",
            "task_comment",
            "task_activity",
            "risk_created",
            "project_context",
            "document_created",
        ]
        assert len({item["id"] for item in all_items}) == 7
        by_kind = {item["kind"]: item for item in all_items}
        assert by_kind["risk_created"]["actor_name"] is None
        assert by_kind["document_created"]["actor_name"] == "Владельцев Виктор"
        assert by_kind["document_created"]["document_ref"].startswith("d1_")
        assert by_kind["task_comment"]["task_id"] == task.id
        assert set(by_kind["task_comment"]) == {
            "id",
            "kind",
            "title",
            "detail",
            "created_at",
            "actor_name",
            "task_id",
            "document_ref",
            "actor_user_id",
            "event_type",
            "from_value",
            "to_value",
            "comment_id",
            "report_id",
            "report_revision",
            "source_available",
        }
        activity = by_kind["task_activity"]
        assert activity["actor_user_id"] == user.id
        assert (activity["event_type"], activity["from_value"], activity["to_value"]) == (
            "STAGE_CHANGED",
            "Ожидание",
            "Готово",
        )
        assert by_kind["task_comment"]["comment_id"] is not None
        assert by_kind["task_report"]["report_revision"] == 1
        assert by_kind["task_report"]["report_id"] is not None
        response = await client.get(path, params={"actor_user_id": user.id, "task_id": task.id})
        assert {item["kind"] for item in response.json()["items"]} == {
            "task_comment",
            "task_activity",
        }
        response = await client.get(path, params={"event_id": activity["id"]})
        assert response.json() == {
            "items": [activity], "next_offset": None, "previous_day": None, "revision": "0",
        }
        assert (await client.get(path, params={"task_id": moved_task.id})).json()["items"] == []
        response = await client.get(
            path,
            params={
                "q": "100%",
                "actor": "анна",
                "since": "2026-11-19",
                "until": "2026-11-19",
            },
        )
        assert response.status_code == 200, response.text
        assert response.json() == {
            "items": [by_kind["task_comment"]],
            "next_offset": None,
            "previous_day": None,
            "revision": "0",
        }
        response = await client.get(
            path, params={"kind": "project_deadline", "until": "2026-11-19"}
        )
        assert response.json() == {
            "items": [], "next_offset": None, "previous_day": None, "revision": "0",
        }
        for query in (
            "kind=git",
            "timezone=Unknown/Zone",
            "actor_user_id=0",
            "actor_user_id=99999999999999999",
            "task_id=-1",
            "task_id=99999999999999999",
            "event_id=bad",
            "since=0001-01-01&timezone=Europe/Samara",
            "until=9999-12-31&timezone=America/New_York",
            "limit=101",
            "offset=-1",
            "since=12345",
            "since=2026-11-20&until=2026-11-19",
        ):
            assert (await client.get(f"{path}?{query}")).status_code == 422
        principal = replace(principal, user_id=user.id + 1000)
        assert (await client.get(path)).status_code == 404


async def test_local_day_bounds_follow_dst(db_session, project):
    from src.repositories.project_history import ProjectHistoryRepository
    from src.schemas.project_history import ProjectHistoryQuery

    repository = ProjectHistoryRepository(db_session)
    # New York spring day is 23h, autumn day is 25h. Adjacent UTC days differ.
    for local_day, start, end in [
        (
            date(2026, 3, 8),
            datetime(2026, 3, 8, 5, tzinfo=UTC),
            datetime(2026, 3, 9, 4, tzinfo=UTC),
        ),
        (
            date(2026, 11, 1),
            datetime(2026, 11, 1, 4, tzinfo=UTC),
            datetime(2026, 11, 2, 5, tzinfo=UTC),
        ),
    ]:
        records = [
            ProjectDeadlineChange(project_id=project.id, created_at=instant, changed_by_name="Тест")
            for instant in [
                start - timedelta(microseconds=1),
                start,
                end - timedelta(microseconds=1),
                end,
            ]
        ]
        db_session.add_all(records)
        await db_session.flush()
        items = await repository.read(
            project.id,
            ProjectHistoryQuery(since=local_day, until=local_day, timezone="America/New_York"),
        )
        assert {item.id for item in items} == {
            f"project_deadline:{records[1].id}",
            f"project_deadline:{records[2].id}",
        }


async def test_creation_and_previous_day_share_project_filters_and_local_dates(
    db_session, project, stage, user, current_principal, monkeypatch
):
    from src.db.models.task_participants import TaskParticipant, TaskParticipantRole

    repository = TasksRepository(db_session)
    created, older, moved = [
        await repository.save(
            data={
                "project_id": project.id,
                "stage_id": stage.id,
                "number": number,
                "title": title,
                "priority": "MEDIUM",
                "position": 0.0,
                "created_at": timestamp,
            }
        )
        for number, title, timestamp in [
            (1, "Новая задача", datetime(2026, 11, 23, 21, 30, tzinfo=UTC)),
            (2, "Ранняя задача", datetime(2026, 11, 22, 21, 30, tzinfo=UTC)),
            (3, "Перенесённая задача", datetime(2026, 11, 23, 22, tzinfo=UTC)),
        ]
    ]
    member = ProjectMember(project_id=project.id, user_id=user.id)
    db_session.add(member)
    await db_session.flush()
    db_session.add_all(
        [
            # A current reporter is not proof of the original creator's identity.
            TaskParticipant(
                task_id=created.id,
                project_member_id=member.id,
                role=TaskParticipantRole.REPORTER,
            ),
            TaskActivity(
                task_id=moved.id,
                event_type=TaskActivityEventType.TASK_MOVED,
                created_at=datetime(2026, 11, 23, 22, 10, tzinfo=UTC),
            ),
            TaskActivity(
                task_id=older.id,
                event_type=TaskActivityEventType.STAGE_CHANGED,
                actor_user_id=user.id,
                actor_name="Автор изменения",
                created_at=datetime(2026, 11, 23, 22, 15, tzinfo=UTC),
            ),
        ]
    )
    await db_session.flush()
    # Exercise the legacy-source fallback, separately from the durable-journal tests.
    await db_session.execute(delete(ProjectHistoryEvent))
    monkeypatch.delitem(app.dependency_overrides, require_project_access)
    monkeypatch.setitem(app.dependency_overrides, get_db_session, lambda: db_session)
    monkeypatch.setitem(
        app.dependency_overrides,
        get_principal,
        lambda: replace(current_principal, user_id=user.id),
    )
    params = {
        "since": "2026-11-24",
        "until": "2026-11-24",
        "timezone": "Europe/Samara",
        "include_previous": "true",
    }
    path = f"/api/v1/projects/{project.id}/history"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(path, params=params)
        assert response.status_code == 200, response.text
        page = response.json()
        assert len(page["items"]) == 2
        creation = next(item for item in page["items"] if item["event_type"] == "TASK_CREATED")
        assert creation["id"] == f"task_created:{created.id}"
        assert creation["kind"] == "task_activity"
        assert creation["task_id"] == created.id
        assert creation["actor_name"] is None and creation["actor_user_id"] is None
        assert page["previous_day"] == "2026-11-23"
        assert page["next_offset"] is None

        # The previous-day lookup retains kind/text/task/actor filters and timezone.
        for additional, count, previous in [
            ({"q": "Создана задача"}, 1, "2026-11-23"),
            ({"task_id": created.id}, 1, None),
            ({"actor_user_id": user.id}, 1, None),
            ({"kind": "task_comment"}, 0, None),
            ({"task_id": moved.id}, 0, None),
            ({"include_previous": "false"}, 2, None),
            ({"offset": 1}, 1, None),
        ]:
            filtered = (await client.get(path, params={**params, **additional})).json()
            assert len(filtered["items"]) == count, additional
            assert filtered["previous_day"] == previous, additional

        exact = await client.get(path, params={**params, "event_id": creation["id"]})
        assert exact.json() == {
            "items": [creation], "next_offset": None, "previous_day": None, "revision": "0",
        }
        # Year 1 has no previous calendar day; there must be no date underflow.
        edge = await client.get(
            path,
            params={"since": "0001-01-01", "until": "0001-01-01", "include_previous": True},
        )
        assert edge.status_code == 200 and edge.json()["previous_day"] is None
