"""Параллельные изменения задач и все пути повышения версии в PostgreSQL."""

import asyncio
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.db.models.knowledge_index_jobs import KnowledgeIndexJob
from src.db.models.project_members import ProjectMember
from src.db.models.project_stages import ProjectStage
from src.db.models.projects import Project
from src.db.models.task_activity import TaskActivity
from src.db.models.task_participants import TaskParticipantRole
from src.db.models.tasks import Task
from src.db.models.users import User
from src.exceptions.tasks import TaskRevisionConflictError
from src.repositories.project_members import ProjectMembersRepository
from src.repositories.project_risks import ProjectRiskRepository
from src.repositories.task_attention import TaskAttentionRepository
from src.repositories.task_participants import TaskParticipantsRepository
from src.repositories.task_reports import TaskReportsRepository
from src.repositories.tasks import TasksRepository
from src.repositories.unit_of_work import UnitOfWork
from src.repositories.users import UsersRepository
from src.repositories.wbs_nodes import WbsNodesRepository
from src.schemas.enums import TaskStatus
from src.schemas.task_attention import TaskViewSchema
from src.schemas.task_transfers import TaskTransferTargetSchema
from src.services.knowledge_events import KnowledgeEvents
from src.services.project_members import ProjectMembersService
from src.services.task_attention import TaskAttentionService
from src.services.users import UsersService
from tests.integration.repositories.test_personal_tasks_and_moves import services
from tests.integration.repositories.test_task_checklist_persistence import build_service
from tests.unit.services.test_task_checklists import checklist


@pytest_asyncio.fixture
async def revision_project(engine, tmp_path):
    """Коммитит только синтетические данные для нескольких независимых соединений."""
    factory = async_sessionmaker(engine, expire_on_commit=False)
    marker = uuid4().hex[:8]
    async with factory() as session:
        user = User(
            username=f"revision_{marker}", password_hash="!", first_name="Тест", last_name="Версий"
        )
        session.add(user)
        await session.flush()
        project = Project(
            owner_id=user.id,
            key=f"R{marker.upper()}",
            name="Версии задач",
            color="#334455",
            order_index=0,
        )
        session.add(project)
        await session.flush()
        stage = ProjectStage(project_id=project.id, name="Работа", color="#334455", order_index=0)
        session.add_all([stage, ProjectMember(project_id=project.id, user_id=user.id, role="OWNER")])
        await session.flush()
        await session.commit()
        task = await build_service(session, tmp_path).create_task(
            project_id=project.id,
            data={"title": "Первоначальный текст", "stage_id": stage.id},
            created_by_user_id=user.id,
        )
        context = SimpleNamespace(
            factory=factory, task_id=task.id, project_id=project.id, stage_id=stage.id,
            user_id=user.id, tmp_path=tmp_path,
        )
    try:
        yield context
    finally:
        async with factory() as session:
            await session.execute(delete(Project).where(Project.id == context.project_id))
            await session.execute(delete(User).where(User.id == context.user_id))
            await session.commit()


async def test_stale_identity_map_and_competing_updates_cannot_overwrite_newer_text(revision_project):
    ctx = revision_project
    async with ctx.factory() as reader:
        cached = await reader.get(Task, ctx.task_id)
        assert cached.revision == 1
        async with ctx.factory() as writer:
            updated = await build_service(writer, ctx.tmp_path).update_task(
                ctx.task_id, {"description_md": "Сохранённый текст", "expected_revision": 1}
            )
        assert updated.revision == 2 and cached.revision == 1
        jobs_before = (await reader.execute(select(KnowledgeIndexJob.__table__))).mappings().all()
        activities_before = (await reader.execute(select(func.count(TaskActivity.id)))).scalar_one()
        with pytest.raises(TaskRevisionConflictError):
            await build_service(reader, ctx.tmp_path).update_task(
                ctx.task_id,
                {"description_md": "Устаревший текст", "observer_ids": [], "expected_revision": 1},
            )
        actual = await reader.get(Task, ctx.task_id)
        assert actual.description_md == "Сохранённый текст" and actual.revision == 2
        assert (await reader.execute(select(func.count(TaskActivity.id)))).scalar_one() == activities_before
        assert (await reader.execute(select(KnowledgeIndexJob.__table__))).mappings().all() == jobs_before

        # Старые клиенты продолжают писать и инвалидируют ранее прочитанные версии.
        legacy = await build_service(reader, ctx.tmp_path).update_task(
            ctx.task_id, {"title": "Из старого клиента"}
        )
        assert legacy.revision == 3 and actual.revision == 3

    async def change(description):
        async with ctx.factory() as session:
            return await build_service(session, ctx.tmp_path).update_task(
                ctx.task_id, {"description_md": description, "expected_revision": 3}
            )

    results = await asyncio.gather(change("Первый"), change("Второй"), return_exceptions=True)
    assert sum(isinstance(result, TaskRevisionConflictError) for result in results) == 1
    assert sum(not isinstance(result, BaseException) for result in results) == 1
    async with ctx.factory() as session:
        actual = await session.get(Task, ctx.task_id)
        assert actual.revision == 4 and actual.description_md in {"Первый", "Второй"}


async def test_participant_only_patch_invalidates_stale_update_and_keeps_checklist_contract(revision_project):
    ctx = revision_project
    async with ctx.factory() as session:
        service = build_service(session, ctx.tmp_path)
        changed = await service.update_task(
            ctx.task_id, {"observer_ids": [ctx.user_id], "expected_revision": 1}
        )
        assert changed.revision == 2
        assert {participant.role for participant in changed.participants} == {
            TaskParticipantRole.REPORTER, TaskParticipantRole.OBSERVER,
        }
        with pytest.raises(TaskRevisionConflictError):
            await service.update_task(ctx.task_id, {"title": "Старый состав", "expected_revision": 1})

        # Старый контракт чек-листа не требует общей expected_revision.
        changed = await service.update_task(
            ctx.task_id, {"checklist": checklist(), "checklist_revision": 0}
        )
        assert changed.revision == 3 and changed.checklist_revision == 1
        changed = await service.update_task(
            ctx.task_id, {"description_md": "Описание", "expected_revision": 3}
        )
        assert changed.revision == 4 and changed.checklist_revision == 1
        assert changed.checklist is not None
        report = await TaskReportsRepository(session).append(
            {"task_id": ctx.task_id, "revision": 1, "body_md": "Отчёт", "author_name": "Тест"}
        )
        await session.commit()
        assert report.revision == 1
        changed = await service.update_task(ctx.task_id, {"title": "С отчётом", "expected_revision": 4})
        assert changed.revision == 5 and changed.checklist_revision == 1
        assert (await TaskReportsRepository(session).latest(ctx.task_id)).revision == 1


async def test_repository_legacy_update_uses_database_revision_with_a_stale_orm_snapshot(revision_project):
    ctx = revision_project
    async with ctx.factory() as reader:
        cached = await reader.get(Task, ctx.task_id)
        async with ctx.factory() as writer:
            changed = await build_service(writer, ctx.tmp_path).update_task(
                ctx.task_id, {"description_md": "Текст новой версии"}
            )
        assert cached.revision == 1 and changed.revision == 2
        # Календарь и ИСР могут передать в общий repository ранее прочитанный ORM-объект.
        actual = await TasksRepository(reader).update(cached, {"assignee": "Исполнитель"})
        await reader.commit()
        assert actual is cached
        assert actual.revision == 3 and actual.description_md == "Текст новой версии"


async def test_reordering_a_viewed_task_raises_common_revision_without_an_unread_signal(revision_project):
    ctx = revision_project
    async with ctx.factory() as session:
        tasks = build_service(session, ctx.tmp_path)
        attention = TaskAttentionService(TaskAttentionRepository(session), UnitOfWork(session))
        original = await tasks.get_task(ctx.task_id)
        token = await attention.token(ctx.user_id, ctx.task_id)
        await attention.mark_viewed(
            ctx.user_id, ctx.task_id, TaskViewSchema.model_validate(token.model_dump())
        )
        moved = await tasks.move_task(ctx.task_id, ctx.stage_id, position=2400)
        assert moved.revision == original.revision + 1
        reordered = await attention.token(ctx.user_id, ctx.task_id)
        assert reordered.revision == token.revision
        assert not reordered.is_new and not reordered.is_updated
        assert (await attention.snapshot(ctx.user_id)).total == 0

        for changes in ({"title": "Новый заголовок"}, {"description_md": "Новое описание"}):
            await attention.mark_viewed(
                ctx.user_id, ctx.task_id, TaskViewSchema.model_validate(reordered.model_dump())
            )
            moved = await tasks.update_task(
                ctx.task_id, {**changes, "expected_revision": moved.revision}
            )
            changed = await attention.token(ctx.user_id, ctx.task_id)
            assert changed.revision > reordered.revision and changed.is_updated
            assert (await attention.snapshot(ctx.user_id)).updated_tasks == 1
            reordered = changed


async def test_stage_status_transfer_and_bulk_writers_raise_the_revision(db_session, project, stage, user, tmp_path):
    await ProjectMembersRepository(db_session).save(
        data={"project_id": project.id, "user_id": user.id, "role": "OWNER"}
    )
    service, moves = services(db_session, tmp_path)
    created = await service.create_task(
        project_id=project.id,
        data={"title": "Все пути записи", "start_date": date(2026, 11, 1), "due_date": date(2026, 11, 7)},
        created_by_user_id=user.id,
    )
    repository = TasksRepository(db_session)
    task_id = created.id
    other = await service.create_task(project_id=project.id, data={"title": "Не изменяется"})
    done = ProjectStage(project_id=project.id, name="Готово", color="#334455", order_index=2, is_done_stage=True)
    db_session.add(done)
    await db_session.flush()
    moved = await service.move_task(task_id, done.id)
    assert moved.revision == created.revision + 1
    fixed = await service.fix_baseline(task_id)
    assert fixed.revision == moved.revision + 1
    transferred = await moves.move(task_id=task_id, target=TaskTransferTargetSchema(personal=True), user_id=user.id)
    assert transferred.task.revision == fixed.revision + 1
    changed_status = await service.change_status(task_id=task_id, status=TaskStatus.NEW)
    assert changed_status.revision == transferred.task.revision + 1
    returned = await moves.move(task_id=task_id, target=TaskTransferTargetSchema(project_id=project.id), user_id=user.id)
    assert returned.task.revision == changed_status.revision + 1

    node = await WbsNodesRepository(db_session).save(data={"project_id": project.id, "title": "Раздел", "position": 1000})
    row = await repository.get_by_id(task_id)
    row = await repository.update(row, {"wbs_node_id": node.id, "wbs_position": 1000, "assignee": "Тест"})
    revision = row.revision
    await repository.update_wbs_positions({task_id: 2000})
    assert row.revision == revision + 1 and row.wbs_position == 2000
    await repository.clear_wbs_node({node.id})
    assert row.revision == revision + 2 and row.wbs_node_id is None
    await repository.clear_assignees([task_id])
    assert row.revision == revision + 3 and row.assignee is None
    await repository.bump_revisions(task_ids={task_id})
    assert row.revision == revision + 4
    await db_session.commit()
    assert (await repository.get_by_id(other.id)).revision == other.revision


async def test_member_cascade_invalidates_tasks_in_all_participant_roles(db_session, project, stage, user, tmp_path):
    await ProjectMembersRepository(db_session).save(
        data={"project_id": project.id, "user_id": user.id, "role": "OWNER"}
    )
    member_user = User(username="revision_member", password_hash="!", first_name="Участник", last_name="Тест")
    db_session.add(member_user)
    await db_session.flush()
    await ProjectMembersRepository(db_session).save(
        data={"project_id": project.id, "user_id": member_user.id, "role": "MEMBER"}
    )
    task_service = build_service(db_session, tmp_path)
    tasks = [
        await task_service.create_task(
            project_id=project.id,
            data={"title": role, field: value},
            created_by_user_id=user.id,
        )
        for role, field, value in (
            ("Исполнитель", "executor_id", member_user.id),
            ("Наблюдатель", "observer_ids", [member_user.id]),
            ("Постановщик", "reporter_id", member_user.id),
        )
    ]
    members = ProjectMembersService(
        members_repository=ProjectMembersRepository(db_session),
        users_repository=UsersRepository(db_session),
        participants_repository=TaskParticipantsRepository(db_session),
        tasks_repository=TasksRepository(db_session),
        unit_of_work=UnitOfWork(db_session),
        users_service=AsyncMock(spec=UsersService),
        risks_repository=ProjectRiskRepository(db_session),
        knowledge_events=AsyncMock(spec=KnowledgeEvents),
    )
    await members.remove_member(project_id=project.id, user_id=member_user.id)
    for task in tasks:
        actual = await task_service.get_task(task.id)
        assert actual.revision == task.revision + 1
        assert all(person.user.id != member_user.id for person in actual.participants)
