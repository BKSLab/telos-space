"""Версии паспорта, история и гонки проверяются настоящим PostgreSQL."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import AsyncSession

from main import app
from src.db.models.project_members import ProjectRole
from src.db.models.projects import Project
from src.db.models.users import User
from src.dependencies.access import require_project_access, require_project_ownership
from src.dependencies.auth import get_principal
from src.dependencies.services import get_access_service, get_projects_service
from src.exceptions.knowledge import KnowledgeEventsServiceError
from src.exceptions.projects import (
    ProjectContextConflictError,
    ProjectOwnerRequiredError,
    ProjectsServiceError,
)
from src.repositories.document_links import DocumentLinksRepository
from src.repositories.documents import DocumentsRepository
from src.repositories.project_members import ProjectMembersRepository
from src.repositories.project_stages import ProjectStagesRepository
from src.repositories.projects import ProjectsRepository
from src.repositories.task_comments import TaskCommentsRepository
from src.repositories.tasks import TasksRepository
from src.repositories.users import UsersRepository
from src.schemas.projects import ProjectDescriptionSchema
from src.services.access import AccessService
from src.services.knowledge_events import KnowledgeEvents
from src.utils.project_description import compose_project_description
from tests.integration.repositories.test_project_passport import project_service

FIELDS = {
    "problem",
    "goal",
    "expected_result",
    "additional",
    "audience",
    "capabilities",
    "boundaries",
    "constraints",
    "glossary",
    "decisions",
    "open_questions",
}


async def persisted_state(session, project_id):
    """Состояние бизнес-строки, снимков и очереди для атомарных сравнений."""
    project = (
        await session.execute(
            text(
                "SELECT description_md,description_sections,context_revision,updated_at "
                "FROM projects WHERE id=:id"
            ),
            {"id": project_id},
        )
    ).one()
    history = (
        await session.execute(
            text(
                "SELECT revision,description_md,description_sections,changed_by_user_id,"
                "changed_by_name,created_at,is_initial,changed_fields "
                "FROM project_context_history WHERE project_id=:id ORDER BY revision"
            ),
            {"id": project_id},
        )
    ).all()
    jobs = (
        await session.execute(
            text(
                "SELECT id,status,entity_type,entity_id,operation FROM knowledge_index_jobs "
                "WHERE project_id=:id ORDER BY id"
            ),
            {"id": project_id},
        )
    ).all()
    return project, history, jobs


async def test_create_context_preserves_full_unicode_and_history_is_metadata_only(db_session, user):
    service = project_service(db_session)
    goal = "Проверяемый результат🙂 ё и е\u0308. " * 80
    sections = {"goal": goal, "audience": "Команда", "open_questions": "Кто принимает итог?"}
    project = await service.create_project(
        {
            "key": "CONTEXT",
            "name": "Контекст",
            "color": "#334455",
            "description_sections": sections,
        },
        user.id,
    )
    data = project.model_dump()
    assert project.context_revision == 1
    assert set(data["description_sections"]) == FIELDS
    assert all(isinstance(value, str) for value in data["description_sections"].values())
    assert data["description_sections"]["goal"] == goal.strip()
    assert goal.strip() in project.description_md
    assert data["context"]["revision"] == 1
    assert data["context"]["fields"] == {
        key: value.strip()[:160] for key, value in sections.items()
    }
    assert set(data["context"]["unknown_fields"]) == FIELDS - sections.keys()
    assert data["context"]["truncated_fields"] == ["goal"]

    first_snapshot = (await persisted_state(db_session, project.id))[1]
    assert first_snapshot[0].revision == 1 and first_snapshot[0].is_initial
    assert first_snapshot[0].changed_by_user_id == user.id
    next_sections = {**data["description_sections"], "audience": "Вся команда"}
    await service.update_project(
        project.id, {"description_sections": next_sections, "expected_context_revision": 1}, user.id
    )
    await db_session.execute(
        text("UPDATE users SET last_name='Новое имя' WHERE id=:id"), {"id": user.id}
    )
    history = await service.get_context_history(project.id, limit=1, offset=0)
    page = history.model_dump()
    assert (
        page["total"],
        page["offset"],
        page["limit"],
        page["has_more"],
        page["next_offset"],
    ) == (2, 0, 1, True, 1)
    assert page["items"][0]["revision"] == 2
    assert page["items"][0]["changed_fields"] == ["audience"]
    assert page["items"][0]["changed_by_user_id"] == user.id
    assert page["items"][0]["changed_by_name"] == "Владельцев Виктор"
    assert (
        "description_md" not in page["items"][0] and "description_sections" not in page["items"][0]
    )
    assert (await persisted_state(db_session, project.id))[1][0] == first_snapshot[0]
    empty = await service.get_context_history(project.id, limit=1, offset=2)
    assert empty.items == [] and not empty.has_more and empty.next_offset is None
    assert (await service.get_project(project.id)).description_sections.goal == goal.strip()


async def test_first_legacy_edit_records_unknown_baseline_without_parsing_or_rewriting_raw(
    db_session, user
):
    raw = (
        "## Цель\n\nЭто произвольный Markdown, а не заполненный паспорт.\n\n"
        + "Исходный текст🙂. " * 40
    )
    legacy = await ProjectsRepository(db_session).save(
        {
            "owner_id": user.id,
            "key": "LEGACYCTX",
            "name": "Прежний проект",
            "color": "#334455",
            "status": "ACTIVE",
            "order_index": 0,
            "description_md": raw,
        }
    )
    await db_session.commit()
    project_id, owner_id = legacy.id, user.id
    service = project_service(db_session)
    current = await service.get_project(project_id)
    context = current.model_dump()["context"]
    assert current.context_revision == 0 and current.description_md == raw
    assert context["fields"] == {"additional": raw[:160]}
    assert "goal" in context["unknown_fields"] and context["truncated_fields"] == ["additional"]
    await service.update_project(
        project_id, {"description_md": raw, "expected_context_revision": 0}, owner_id
    )
    assert (await service.get_context_history(project_id)).items == []

    recorded_after = await db_session.scalar(text("SELECT transaction_timestamp()"))
    changed = await service.update_project(
        project_id,
        {
            "description_sections": {"goal": "Явно заполненная цель"},
            "expected_context_revision": 0,
        },
        owner_id,
    )
    assert changed.context_revision == 1
    snapshots = (await persisted_state(db_session, project_id))[1]
    baseline, revision = snapshots
    assert baseline.revision == 0 and baseline.is_initial
    assert baseline.changed_by_user_id is None and baseline.changed_by_name is None
    assert recorded_after <= baseline.created_at <= datetime.now(UTC)
    assert baseline.description_md == raw and baseline.description_sections is None
    assert revision.revision == 1 and revision.changed_by_user_id == owner_id
    assert revision.description_sections["goal"] == "Явно заполненная цель"
    assert ProjectDescriptionSchema().model_dump() == dict.fromkeys(FIELDS, "")


async def test_noop_and_stale_patch_do_not_change_revision_history_or_outbox(db_session, user):
    service = project_service(db_session)
    project = await service.create_project(
        {
            "key": "CTXNOOP",
            "name": "Без повторной версии",
            "color": "#334455",
            "description_sections": {"goal": "Прежняя цель"},
        },
        user.id,
    )
    project_id, owner_id = project.id, user.id
    before = await persisted_state(db_session, project_id)
    result = await service.update_project(
        project_id,
        {
            "description_sections": project.description_sections.model_dump(),
            "expected_context_revision": 1,
        },
        owner_id,
    )
    assert result.context_revision == 1
    assert await persisted_state(db_session, project_id) == before
    for sections in (project.description_sections.model_dump(), {"goal": "Потерянное изменение"}):
        with pytest.raises(ProjectContextConflictError):
            await service.update_project(
                project_id,
                {
                    "description_sections": sections,
                    "expected_context_revision": 0,
                },
                owner_id,
            )
        assert await persisted_state(db_session, project_id) == before
    renamed = await service.update_project(
        project_id, {"name": "Новое имя", "expected_context_revision": 1}, owner_id
    )
    assert renamed.context_revision == 1
    assert (await persisted_state(db_session, project_id))[1] == before[1]


async def test_context_outbox_failure_rolls_back_text_revision_and_history(db_session, user):
    service = project_service(db_session)
    project = await service.create_project(
        {
            "key": "CTXROLL",
            "name": "Атомарность",
            "color": "#334455",
            "description_sections": {"goal": "Зафиксированная цель"},
        },
        user.id,
    )
    project_id, owner_id = project.id, user.id
    before = await persisted_state(db_session, project_id)
    service.knowledge_events = AsyncMock(spec=KnowledgeEvents)
    service.knowledge_events.upsert.side_effect = KnowledgeEventsServiceError(
        "Synthetic outbox failure"
    )
    with pytest.raises(ProjectsServiceError):
        await service.update_project(
            project_id,
            {
                "description_sections": {"goal": "Незафиксированная цель"},
                "expected_context_revision": 1,
            },
            owner_id,
        )
    assert await persisted_state(db_session, project_id) == before


async def test_project_context_http_rechecks_membership_owner_and_authenticated_actor(
    db_session, user, api_client, current_principal
):
    service = project_service(db_session)
    member = await UsersRepository(db_session).save(
        {
            "username": "context-member",
            "password_hash": "!",
            "is_active": True,
            "last_name": "Участников",
            "first_name": "Иван",
        }
    )
    outsider = await UsersRepository(db_session).save(
        {
            "username": "context-outsider",
            "password_hash": "!",
            "is_active": True,
            "last_name": "Посторонний",
            "first_name": "Пётр",
        }
    )
    project = await service.create_project(
        {
            "key": "CTXAUTH",
            "name": "Права паспорта",
            "color": "#334455",
            "member_usernames": [member.username],
            "description_sections": {"goal": "Цель"},
        },
        user.id,
    )
    owner_id, member_id, outsider_id, project_id = user.id, member.id, outsider.id, project.id
    access = AccessService(
        members_repository=ProjectMembersRepository(db_session),
        tasks_repository=TasksRepository(db_session),
        stages_repository=ProjectStagesRepository(db_session),
        documents_repository=DocumentsRepository(db_session),
        comments_repository=TaskCommentsRepository(db_session),
        links_repository=DocumentLinksRepository(db_session),
    )
    app.dependency_overrides.pop(require_project_access)
    app.dependency_overrides.pop(require_project_ownership)
    app.dependency_overrides.update(
        {get_projects_service: lambda: service, get_access_service: lambda: access}
    )
    for actor_id, expected in ((owner_id, 200), (member_id, 200), (outsider_id, 404)):
        actor = replace(current_principal, user_id=actor_id)
        app.dependency_overrides[get_principal] = lambda actor=actor: actor
        assert (await api_client.get(f"/api/v1/projects/{project_id}")).status_code == expected
        assert (
            await api_client.get(f"/api/v1/projects/{project_id}/context-history")
        ).status_code == expected
        if actor_id != owner_id:
            response = await api_client.patch(
                f"/api/v1/projects/{project_id}",
                json={
                    "description_sections": {"goal": "Чужая правка"},
                    "expected_context_revision": 1,
                    "updated_by_user_id": owner_id,
                },
            )
            assert response.status_code == (403 if actor_id == member_id else 404)
    app.dependency_overrides[get_principal] = lambda: replace(current_principal, user_id=owner_id)
    response = await api_client.patch(
        f"/api/v1/projects/{project_id}",
        json={
            "description_sections": {"goal": "Правка владельца"},
            "expected_context_revision": 1,
            "updated_by_user_id": member_id,
        },
    )
    assert response.status_code == 200
    history = await service.get_context_history(project_id)
    assert history.items[0].changed_by_user_id == owner_id
    assert (await service.get_project(project_id)).description_sections.goal == "Правка владельца"


async def test_context_http_partial_sections_preserve_unsubmitted_text_and_explicit_clear(
    db_session, user, api_client, current_principal
):
    """HTTP PATCH различает отсутствующий раздел и явную пустую строку."""
    owner_id = user.id
    service = project_service(db_session)
    sections = dict.fromkeys(FIELDS, "")
    sections.update(
        goal=" \n" + "Полная цель🙂 е\u0308. " * 400 + "\n ",
        expected_result="\t" + "Проверяемый результат. " * 300 + "\n",
        constraints=" \n" + "Сохранить локальные данные и права. " * 250 + "\t ",
    )
    original = dict(sections)
    # Старое сохранённое JSON-содержимое не нормализуется повторно из-за чужого поля.
    structured = await ProjectsRepository(db_session).save(
        {
            "owner_id": owner_id,
            "key": "CTXPART",
            "name": "Частичный паспорт",
            "status": "ACTIVE",
            "color": "#334455",
            "order_index": 0,
            "description_sections": sections,
            "description_md": compose_project_description(
                ProjectDescriptionSchema.model_construct(**sections)
            ),
        }
    )
    await ProjectMembersRepository(db_session).save(
        data={"project_id": structured.id, "user_id": owner_id, "role": ProjectRole.OWNER}
    )
    raw = (
        " \n## Цель\n\nНе извлекать утверждённую цель из этого заголовка.\n"
        + "Исходный текст🙂 е\u0308. " * 80
        + "\n\t "
    )
    legacy = await ProjectsRepository(db_session).save(
        {
            "owner_id": owner_id,
            "key": "CTXPRAW",
            "name": "Прежний Markdown",
            "status": "ACTIVE",
            "color": "#334455",
            "order_index": 1,
            "description_md": raw,
        }
    )
    await ProjectMembersRepository(db_session).save(
        data={"project_id": legacy.id, "user_id": owner_id, "role": ProjectRole.OWNER}
    )
    await db_session.commit()
    structured_id, legacy_id = structured.id, legacy.id
    access = AccessService(
        members_repository=ProjectMembersRepository(db_session),
        tasks_repository=TasksRepository(db_session),
        stages_repository=ProjectStagesRepository(db_session),
        documents_repository=DocumentsRepository(db_session),
        comments_repository=TaskCommentsRepository(db_session),
        links_repository=DocumentLinksRepository(db_session),
    )
    app.dependency_overrides.pop(require_project_access)
    app.dependency_overrides.pop(require_project_ownership)
    app.dependency_overrides.update(
        {
            get_projects_service: lambda: service,
            get_access_service: lambda: access,
            get_principal: lambda: replace(current_principal, user_id=owner_id),
        }
    )

    response = await api_client.patch(
        f"/api/v1/projects/{structured_id}",
        json={"description_sections": {"audience": "Команда"}, "expected_context_revision": 0},
    )
    assert response.status_code == 200, response.json()
    assert response.json()["context_revision"] == 1
    saved, snapshots, _ = await persisted_state(db_session, structured_id)
    assert saved.description_sections == {**original, "audience": "Команда"}
    assert all(
        original[name] in saved.description_md
        for name in ("goal", "expected_result", "constraints")
    )
    assert snapshots[-1].changed_fields == ["audience"]
    assert snapshots[0].description_sections == original

    response = await api_client.patch(
        f"/api/v1/projects/{structured_id}",
        json={"description_sections": {"goal": ""}, "expected_context_revision": 1},
    )
    assert response.status_code == 200, response.json()
    saved, snapshots, _ = await persisted_state(db_session, structured_id)
    assert saved.context_revision == 2
    assert saved.description_sections == {**original, "audience": "Команда", "goal": ""}
    assert snapshots[-1].changed_fields == ["goal"]
    assert "goal" in response.json()["context"]["unknown_fields"]

    before_empty_patch = await persisted_state(db_session, legacy_id)
    response = await api_client.patch(
        f"/api/v1/projects/{legacy_id}",
        json={"description_sections": {}, "expected_context_revision": 0},
    )
    assert response.status_code == 200, response.json()
    assert await persisted_state(db_session, legacy_id) == before_empty_patch
    response = await api_client.patch(
        f"/api/v1/projects/{legacy_id}",
        json={"description_sections": {"audience": "Команда"}, "expected_context_revision": 0},
    )
    assert response.status_code == 200, response.json()
    saved, snapshots, _ = await persisted_state(db_session, legacy_id)
    assert saved.context_revision == 1
    assert saved.description_sections["additional"] == raw
    assert saved.description_sections.get("goal", "") == ""
    assert raw in saved.description_md
    assert snapshots[0].description_md == raw and snapshots[0].description_sections is None
    assert snapshots[-1].changed_fields == ["audience"]
    assert response.json()["context"]["fields"]["additional"] == raw.strip()[:160]
    assert "goal" in response.json()["context"]["unknown_fields"]


@pytest_asyncio.fixture
async def committed_context_project(engine):
    """Две конкурентные сессии видят committed seed собственного теста."""
    marker = uuid4().hex[:8]
    async with AsyncSession(engine, expire_on_commit=False) as session:
        users = UsersRepository(session)
        owner = await users.save(
            {
                "username": f"ctx-owner-{marker}",
                "password_hash": "!",
                "is_active": True,
                "last_name": "Владелец",
                "first_name": "Контекст",
            }
        )
        successor = await users.save(
            {
                "username": f"ctx-next-{marker}",
                "password_hash": "!",
                "is_active": True,
                "last_name": "Преемник",
                "first_name": "Контекст",
            }
        )
        project = await project_service(session).create_project(
            {
                "key": f"C{marker}",
                "name": "Concurrent context",
                "color": "#334455",
                "description_sections": {"goal": "Исходная цель"},
                "member_usernames": [successor.username],
            },
            owner.id,
        )
        ids = project.id, owner.id, successor.id
    try:
        yield ids
    finally:
        async with engine.begin() as connection:
            await connection.execute(delete(Project).where(Project.id == ids[0]))
            await connection.execute(delete(User).where(User.id.in_(ids[1:])))


async def wait_for_postgres_lock(session, backend_pid):
    """Ждёт реальную блокировку второй PG-сессии, без подмены repository."""
    for _ in range(100):
        if await session.scalar(
            text("SELECT EXISTS(SELECT 1 FROM pg_locks WHERE pid=:pid AND NOT granted)"),
            {"pid": backend_pid},
        ):
            return
        await asyncio.sleep(0.01)
    pytest.fail("Second PostgreSQL session did not wait for the project row lock")


async def test_concurrent_context_edits_commit_one_revision_and_reject_the_stale_writer(
    engine, committed_context_project
):
    project_id, owner_id, _ = committed_context_project
    async with (
        AsyncSession(engine, expire_on_commit=False) as first,
        AsyncSession(engine, expire_on_commit=False) as second,
    ):
        await ProjectsRepository(first).get_by_id(project_id, for_update=True)
        pid = await second.scalar(text("SELECT pg_backend_pid()"))
        blocked = asyncio.create_task(
            project_service(second).update_project(
                project_id,
                {
                    "description_sections": {"goal": "Проигравшая правка"},
                    "expected_context_revision": 1,
                },
                owner_id,
            )
        )
        try:
            await wait_for_postgres_lock(first, pid)
            winner = await project_service(first).update_project(
                project_id,
                {
                    "description_sections": {"goal": "Выигравшая правка"},
                    "expected_context_revision": 1,
                },
                owner_id,
            )
            assert winner.context_revision == 2
            with pytest.raises(ProjectContextConflictError):
                await asyncio.wait_for(blocked, timeout=10)
        finally:
            if not blocked.done():
                blocked.cancel()
                await asyncio.gather(blocked, return_exceptions=True)
        saved = await project_service(first).get_project(project_id)
        history = await project_service(first).get_context_history(project_id)
        assert (
            saved.context_revision == 2 and saved.description_sections.goal == "Выигравшая правка"
        )
        assert [row.revision for row in history.items] == [2, 1]


async def test_owner_change_while_waiting_for_row_lock_invalidates_cached_authority(
    engine, committed_context_project
):
    project_id, owner_id, successor_id = committed_context_project
    async with (
        AsyncSession(engine, expire_on_commit=False) as transfer,
        AsyncSession(engine, expire_on_commit=False) as editor,
    ):
        cached = await ProjectsRepository(editor).get_by_id(project_id)
        assert cached.owner_id == owner_id
        await ProjectsRepository(transfer).get_by_id(project_id, for_update=True)
        pid = await editor.scalar(text("SELECT pg_backend_pid()"))
        blocked = asyncio.create_task(
            project_service(editor).update_project(
                project_id,
                {
                    "description_sections": {"goal": "Старая сессия владельца"},
                    "expected_context_revision": 1,
                },
                owner_id,
            )
        )
        try:
            await wait_for_postgres_lock(transfer, pid)
            await project_service(transfer).transfer_ownership(project_id, successor_id, owner_id)
            with pytest.raises(ProjectOwnerRequiredError):
                await asyncio.wait_for(blocked, timeout=10)
        finally:
            if not blocked.done():
                blocked.cancel()
                await asyncio.gather(blocked, return_exceptions=True)
        saved = await project_service(transfer).get_project(project_id)
        history = await project_service(transfer).get_context_history(project_id)
        assert saved.owner_id == successor_id and saved.context_revision == 1
        assert saved.description_sections.goal == "Исходная цель"
        assert [row.revision for row in history.items] == [1]
