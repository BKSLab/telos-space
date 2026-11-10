"""Metadata и promotion на PostgreSQL: reload, CAS, права и единый rollback."""

import asyncio
from dataclasses import replace
from datetime import date
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from main import app
from src.db.models.api_tokens import ApiTokenScope
from src.db.models.board_shapes import BoardShape, BoardShapeKind
from src.db.models.board_stickers import BoardSticker
from src.db.models.boards import Board
from src.db.models.knowledge_index_jobs import KnowledgeIndexJob
from src.db.models.project_members import ProjectMember, ProjectRole
from src.db.models.project_stages import ProjectStage
from src.db.models.projects import Project
from src.db.models.users import User
from src.dependencies.auth import get_principal
from src.dependencies.db_session import get_db_session
from src.dependencies.services import get_access_service, get_projects_service
from src.exceptions.access import ResourceNotAvailableError
from src.exceptions.board_editor import (
    BoardAlreadyPromotedError,
    BoardEditorValidationError,
    BoardObjectLockedError,
)
from src.exceptions.boards import (
    BoardNotFoundError,
    BoardRevisionConflictError,
)
from src.exceptions.projects import ProjectMemberUserNotFoundError
from src.repositories.board_editor import BoardEditorRepository, BoardPromotionUnitOfWork
from src.repositories.boards import BoardsRepository
from src.repositories.document_links import DocumentLinksRepository
from src.repositories.documents import DocumentsRepository
from src.repositories.knowledge_index_jobs import KnowledgeIndexJobsRepository
from src.repositories.project_deadline_changes import ProjectDeadlineChangesRepository
from src.repositories.project_members import ProjectMembersRepository
from src.repositories.project_stages import ProjectStagesRepository
from src.repositories.projects import ProjectsRepository
from src.repositories.task_comments import TaskCommentsRepository
from src.repositories.tasks import TasksRepository
from src.repositories.unit_of_work import UnitOfWork
from src.repositories.users import UsersRepository
from src.schemas.board_editor import BoardEditorMetadata, BoardEditorWrite, BoardPromotionWrite
from src.services.board_editor import (
    BoardEditorService,
    metadata_without_objects,
    require_objects_unlocked,
)
from src.services.knowledge_events import KnowledgeEvents
from src.services.projects import DEFAULT_STAGES
from tests.board_support import local_user, user_display_name


def service(session, *, repository=None):
    projects = get_projects_service(
        projects_repository=ProjectsRepository(session),
        members_repository=ProjectMembersRepository(session),
        stages_repository=ProjectStagesRepository(session),
        tasks_repository=TasksRepository(session),
        storage=None,
        knowledge_events=KnowledgeEvents(KnowledgeIndexJobsRepository(session)),
        unit_of_work=BoardPromotionUnitOfWork(session),
        users_repository=UsersRepository(session),
        deadline_changes_repository=ProjectDeadlineChangesRepository(session),
        boards_repository=BoardsRepository(session),
    )
    access = get_access_service(
        members_repository=ProjectMembersRepository(session),
        tasks_repository=TasksRepository(session),
        stages_repository=ProjectStagesRepository(session),
        documents_repository=DocumentsRepository(session),
        comments_repository=TaskCommentsRepository(session),
        links_repository=DocumentLinksRepository(session),
    )
    return BoardEditorService(
        repository=repository or BoardEditorRepository(session),
        access=access,
        projects=projects,
        unit_of_work=UnitOfWork(session),
    )


async def seed(session):
    suffix = uuid4().hex[:10]
    owner = local_user(username=f"editor.{suffix}", display_name="Владелец")
    other = local_user(username=f"other.{suffix}", display_name="Другой")
    viewer = local_user(
        username=f"viewer.{suffix}",
        display_name="Наблюдатель",
    )
    session.add_all([owner, other, viewer])
    await session.flush()
    board = Board(title="Идеи", created_by_user_id=owner.id, position=4)
    foreign = Board(title="Другая доска", created_by_user_id=owner.id)
    session.add_all([board, foreign])
    await session.flush()
    shape = BoardShape(
        board_id=board.id,
        kind=BoardShapeKind.RECT,
        x=10,
        y=20,
        w=100,
        h=80,
        style={},
        label="Прямоугольник",
    )
    foreign_shape = BoardShape(
        board_id=foreign.id,
        kind=BoardShapeKind.RECT,
        x=0,
        y=0,
        w=100,
        h=80,
        style={},
        label="Чужая",
    )
    sticker = BoardSticker(
        board_id=board.id,
        body="Заметка",
        created_by_user_id=owner.id,
        created_by_username_snapshot=owner.username,
        created_by_display_name_snapshot=user_display_name(owner),
    )
    session.add_all([shape, foreign_shape, sticker])
    await session.flush()
    arrow = BoardShape(
        board_id=board.id,
        kind=BoardShapeKind.ARROW,
        x=0,
        y=0,
        w=100,
        h=100,
        points=[[0, 0], [100, 100]],
        start_ref={"type": "shape", "id": shape.id, "anchor": "right"},
        end_ref={"type": "sticker", "id": sticker.id, "anchor": "left"},
        style={},
        label="Связь",
    )
    session.add(arrow)
    await session.commit()
    return SimpleNamespace(
        owner_id=owner.id,
        other_id=other.id,
        viewer_id=viewer.id,
        board_id=board.id,
        foreign_id=foreign.id,
        shape_id=shape.id,
        arrow_id=arrow.id,
        foreign_shape_id=foreign_shape.id,
        sticker_id=sticker.id,
        member_username=other.username,
    )


def metadata(env):
    return BoardEditorMetadata.model_validate(
        {
            "version": 1,
            "groups": [
                {
                    "id": "group-1",
                    "name": "Смешанная группа",
                    "members": [
                        {"type": "shape", "id": env.shape_id},
                        {"type": "sticker", "id": env.sticker_id},
                    ],
                }
            ],
            "locked": [{"type": "sticker", "id": env.sticker_id}],
            "templates": [
                {
                    "id": "template-1",
                    "name": "Схема с заметкой",
                    "objects": [
                        {
                            "type": "shape",
                            "object_id": "rectangle",
                            "data": {"kind": "rect", "x": 0, "y": 0, "w": 100, "h": 80},
                        },
                        {"type": "sticker", "object_id": "note", "data": {"body": "Шаблон"}},
                        {
                            "type": "shape",
                            "object_id": "arrow",
                            "data": {
                                "kind": "arrow",
                                "points": [[0, 0], [100, 100]],
                                "start_ref": {"object_id": "rectangle"},
                                "end_ref": {"object_id": "note"},
                            },
                        },
                    ],
                }
            ],
        }
    )


def promotion(*, revision=1, key="BOARD", **project):
    return BoardPromotionWrite(
        expected_revision=revision, project={"key": key, "name": "Из доски", **project}
    )


async def test_metadata_reload_cas_and_foreign_refs(db_session):
    env = await seed(db_session)
    expected = metadata(env)
    editor = service(db_session)
    result = await editor.put_metadata(
        board_id=env.board_id,
        project_id=None,
        user_id=env.owner_id,
        data=BoardEditorWrite(expected_revision=1, metadata=expected),
    )
    assert result.revision == 2
    db_session.expunge_all()
    reloaded = await editor.get_metadata(
        board_id=env.board_id, project_id=None, user_id=env.owner_id
    )
    assert reloaded.metadata == expected
    await db_session.commit()
    with pytest.raises(BoardRevisionConflictError):
        await editor.put_metadata(
            board_id=env.board_id,
            project_id=None,
            user_id=env.owner_id,
            data=BoardEditorWrite(expected_revision=1, metadata=BoardEditorMetadata()),
        )
    invalid = expected.model_dump()
    invalid["locked"] = [{"type": "shape", "id": env.foreign_shape_id}]
    with pytest.raises(BoardEditorValidationError):
        await editor.put_metadata(
            board_id=env.board_id,
            project_id=None,
            user_id=env.owner_id,
            data=BoardEditorWrite(expected_revision=2, metadata=invalid),
        )
    persisted = await editor.get_metadata(
        board_id=env.board_id, project_id=None, user_id=env.owner_id
    )
    assert persisted.revision == 2
    assert persisted.metadata == expected


async def test_promotion_preserves_ids_metadata_refs_and_project_defaults(db_session):
    env = await seed(db_session)
    editor = service(db_session)
    await editor.put_metadata(
        board_id=env.board_id,
        project_id=None,
        user_id=env.owner_id,
        data=BoardEditorWrite(expected_revision=1, metadata=metadata(env)),
    )
    result = await editor.promote(
        board_id=env.board_id,
        user_id=env.owner_id,
        data=promotion(
            revision=2, member_usernames=[env.member_username], due_date=date(2099, 1, 1)
        ),
    )
    project_id = result.project.id
    assert result.board.id == env.board_id
    assert result.board.project_id == project_id
    assert result.board.revision == 3
    assert result.board.title == "Идеи"
    assert result.project.owner_id == env.owner_id
    db_session.expunge_all()
    boards = list(
        (
            await db_session.execute(
                select(Board).where(Board.project_id == project_id)
            )
        ).scalars()
    )
    assert [board.id for board in boards] == [env.board_id]
    assert boards[0].position == 0
    assert boards[0].editor_metadata == metadata(env).model_dump(mode="json")
    shape = await db_session.get(BoardShape, env.shape_id)
    sticker = await db_session.get(BoardSticker, env.sticker_id)
    assert shape.board_id == env.board_id and shape.revision == 1
    assert sticker.board_id == env.board_id and sticker.project_id == project_id
    assert sticker.revision == 2
    arrow = await db_session.get(BoardShape, env.arrow_id)
    assert arrow.board_id == env.board_id and arrow.revision == 1
    assert arrow.start_ref == {"type": "shape", "id": env.shape_id, "anchor": "right"}
    assert arrow.end_ref == {"type": "sticker", "id": env.sticker_id, "anchor": "left"}
    roles = dict(
        (
            await db_session.execute(
                select(ProjectMember.user_id, ProjectMember.role).where(
                    ProjectMember.project_id == project_id
                )
            )
        ).all()
    )
    assert roles == {env.owner_id: ProjectRole.OWNER, env.other_id: ProjectRole.MEMBER}
    stages = list(
        (
            await db_session.execute(
                select(ProjectStage)
                .where(
                    ProjectStage.project_id == project_id,
                )
                .order_by(ProjectStage.order_index)
            )
        ).scalars()
    )
    assert [stage.name for stage in stages] == [stage["name"] for stage in DEFAULT_STAGES]
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(KnowledgeIndexJob)
            .where(
                KnowledgeIndexJob.project_id == project_id,
            )
        )
        > 0
    )
    await db_session.commit()
    with pytest.raises(BoardAlreadyPromotedError):
        await editor.promote(
            board_id=env.board_id, user_id=env.owner_id, data=promotion(key="TWICE")
        )
    assert (
        await db_session.scalar(
            select(func.count()).select_from(Project).where(Project.key == "TWICE")
        )
        == 0
    )


async def test_promotion_custom_stages_and_access_denials(db_session):
    env = await seed(db_session)
    editor = service(db_session)
    with pytest.raises(BoardNotFoundError):
        await editor.promote(board_id=env.board_id, user_id=env.other_id, data=promotion())
    with pytest.raises(BoardNotFoundError):
        await editor.get_metadata(board_id=env.board_id, project_id=None, user_id=env.other_id)
    await db_session.rollback()
    result = await editor.promote(
        board_id=env.board_id,
        user_id=env.owner_id,
        data=promotion(stages=[{"name": "Единственная", "color": "#123456"}]),
    )
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(ProjectStage)
            .where(
                ProjectStage.project_id == result.project.id,
            )
        )
        == 1
    )
    await db_session.commit()
    with pytest.raises(ResourceNotAvailableError):
        await editor.get_metadata(
            board_id=env.board_id, project_id=result.project.id, user_id=env.other_id
        )
    await db_session.rollback()
    # A local account outside the project receives no metadata or write access.
    with pytest.raises(ResourceNotAvailableError):
        await editor.put_metadata(
            board_id=env.board_id,
            project_id=result.project.id,
            user_id=env.viewer_id,
            data=BoardEditorWrite(expected_revision=2, metadata=BoardEditorMetadata()),
        )
    with pytest.raises(ResourceNotAvailableError):
        await editor.get_metadata(
            board_id=env.board_id,
            project_id=result.project.id,
            user_id=env.viewer_id,
        )
    await db_session.rollback()
    with pytest.raises(BoardNotFoundError):
        await editor.get_metadata(board_id=env.board_id, project_id=None, user_id=env.owner_id)


async def test_promotion_rolls_back_project_board_objects_and_outbox(db_session):
    env = await seed(db_session)

    class FailAfterAttach(BoardEditorRepository):
        async def attach_to_new_project(self, board, project_id):
            await super().attach_to_new_project(board, project_id)
            raise RuntimeError("injected failure after transfer")

    with pytest.raises(RuntimeError, match="injected failure"):
        await service(db_session, repository=FailAfterAttach(db_session)).promote(
            board_id=env.board_id,
            user_id=env.owner_id,
            data=promotion(key="ROLLBACK"),
        )
    db_session.expunge_all()
    board = await db_session.get(Board, env.board_id)
    sticker = await db_session.get(BoardSticker, env.sticker_id)
    assert board.project_id is None and board.revision == 1 and board.position == 4
    assert sticker.project_id is None and sticker.revision == 1
    assert (
        await db_session.scalar(
            select(func.count()).select_from(Project).where(Project.key == "ROLLBACK")
        )
        == 0
    )
    assert await db_session.scalar(select(func.count()).select_from(KnowledgeIndexJob)) == 0


async def test_promotion_invalid_member_and_revision_leave_board_free(db_session):
    env = await seed(db_session)
    editor = service(db_session)
    with pytest.raises(BoardRevisionConflictError):
        await editor.promote(
            board_id=env.board_id, user_id=env.owner_id, data=promotion(revision=9)
        )
    with pytest.raises(ProjectMemberUserNotFoundError):
        await editor.promote(
            board_id=env.board_id,
            user_id=env.owner_id,
            data=promotion(member_usernames=["missing.person"]),
        )
    board = await db_session.get(Board, env.board_id)
    assert board.project_id is None and board.revision == 1


async def test_locks_and_removal_helpers(db_session):
    env = await seed(db_session)
    board = await BoardEditorRepository(db_session).get_board(env.board_id, lock=True)
    board.editor_metadata = metadata(env).model_dump(mode="json")
    with pytest.raises(BoardObjectLockedError):
        require_objects_unlocked(board, {("sticker", env.sticker_id)})
    with pytest.raises(BoardObjectLockedError):
        require_objects_unlocked(board, {("shape", env.shape_id)})
    require_objects_unlocked(board, {("shape", env.foreign_shape_id)})
    cleaned = metadata_without_objects(
        metadata(env), {("shape", env.shape_id), ("sticker", env.sticker_id)}
    )
    assert cleaned.groups == [] and cleaned.locked == []
    assert cleaned.templates == metadata(env).templates


async def test_concurrent_cas_and_double_promotion(engine):
    async with AsyncSession(engine, expire_on_commit=False) as session:
        env = await seed(session)

    async def write(name):
        async with AsyncSession(engine, expire_on_commit=False) as session:
            try:
                return await service(session).put_metadata(
                    board_id=env.board_id,
                    project_id=None,
                    user_id=env.owner_id,
                    data=BoardEditorWrite(expected_revision=1, metadata=BoardEditorMetadata()),
                )
            except BoardRevisionConflictError as error:
                return error

    keys = ["B" + uuid4().hex[:8].upper() for _ in range(2)]

    async def promote(key):
        async with AsyncSession(engine, expire_on_commit=False) as session:
            try:
                return await service(session).promote(
                    board_id=env.board_id,
                    user_id=env.owner_id,
                    data=promotion(revision=2, key=key),
                )
            except BoardAlreadyPromotedError as error:
                return error

    try:
        writes = await asyncio.gather(write("one"), write("two"))
        assert sum(isinstance(result, BoardRevisionConflictError) for result in writes) == 1
        promotions = await asyncio.gather(*(promote(key) for key in keys))
        assert sum(isinstance(result, BoardAlreadyPromotedError) for result in promotions) == 1
        async with AsyncSession(engine) as session:
            projects = list(
                (await session.execute(select(Project).where(Project.key.in_(keys)))).scalars()
            )
            assert len(projects) == 1
            board = await session.get(Board, env.board_id)
            sticker = await session.get(BoardSticker, env.sticker_id)
            assert board.project_id == projects[0].id and board.revision == 3
            assert sticker.project_id == projects[0].id
    finally:
        async with AsyncSession(engine) as session:
            project_ids = list(
                (await session.execute(select(Project.id).where(Project.key.in_(keys)))).scalars()
            )
            await session.execute(delete(Project).where(Project.key.in_(keys)))
            await session.execute(
                delete(Board).where(Board.id.in_([env.board_id, env.foreign_id]))
            )
            await session.execute(
                delete(User).where(User.id.in_([env.owner_id, env.other_id, env.viewer_id]))
            )
            # Outbox rows have no project FK; also remove jobs emitted by deletion.
            await session.execute(
                delete(KnowledgeIndexJob).where(KnowledgeIndexJob.project_id.in_(project_ids))
            )
            await session.commit()


@pytest.mark.parametrize(
    "invalid",
    [
        {"groups": [{"id": "g", "name": "g", "members": [{"type": "shape", "id": "n1"}]}]},
        {"locked": [{"type": "shape", "id": True}]},
        {"locked": [{"type": "shape", "id": 2_147_483_648}]},
        {"locked": [{"type": "shape", "id": 1}, {"type": "shape", "id": 1}]},
        {
            "templates": [
                {
                    "id": "t",
                    "name": "t",
                    "objects": [
                        {
                            "type": "sticker",
                            "object_id": "o",
                            "data": {"body": "a", "task_ids": [1]},
                        }
                    ],
                }
            ]
        },
        {
            "templates": [
                {
                    "id": "t",
                    "name": "t",
                    "objects": [
                        {
                            "type": "shape",
                            "object_id": "o",
                            "data": {
                                "kind": "arrow",
                                "points": [[0, 0], [1, 1]],
                                "end_ref": {"object_id": "missing"},
                            },
                        }
                    ],
                }
            ]
        },
        {"groups": [{"id": "g", "name": "a\x00", "members": [{"type": "shape", "id": 1}]}]},
    ],
)
def test_metadata_rejects_scene_keys_duplicates_and_invalid_templates(invalid):
    with pytest.raises(ValidationError):
        BoardEditorMetadata.model_validate(invalid)


async def test_http_readonly_tokens_denied_and_real_di(api_client, db_session, current_principal):
    env = await seed(db_session)
    principal = replace(current_principal, user_id=env.owner_id)
    app.dependency_overrides[get_principal] = lambda: principal

    async def session_override():
        yield db_session

    app.dependency_overrides[get_db_session] = session_override
    state = await api_client.get(f"/api/v1/boards/{env.board_id}/editor-metadata")
    assert state.status_code == 200, state.text
    saved = await api_client.put(
        f"/api/v1/boards/{env.board_id}/editor-metadata",
        json={
            "expected_revision": 1,
            "metadata": metadata(env).model_dump(mode="json"),
        },
    )
    assert saved.status_code == 200, saved.text
    principal = replace(principal, scope=ApiTokenScope.READ)
    denied = await api_client.put(
        f"/api/v1/boards/{env.board_id}/editor-metadata",
        json={
            "expected_revision": 2,
            "metadata": {},
        },
    )
    assert denied.status_code == 403
    denied = await api_client.post(
        f"/api/v1/boards/{env.board_id}/create-project",
        json={
            "expected_revision": 2,
            "project": {"key": "HTTP", "name": "Запрет"},
        },
    )
    assert denied.status_code == 403
    principal = replace(principal, scope=ApiTokenScope.WRITE)
    promoted = await api_client.post(
        f"/api/v1/boards/{env.board_id}/create-project",
        json={
            "expected_revision": 2,
            "project": {"key": "HTTP", "name": "По HTTP"},
        },
    )
    assert promoted.status_code == 201, promoted.text
    project_id = promoted.json()["project"]["id"]
    state = await api_client.get(
        f"/api/v1/projects/{project_id}/boards/{env.board_id}/editor-metadata"
    )
    assert state.status_code == 200, state.text
    assert state.json()["revision"] == 3


async def test_promotion_rechecks_owner_after_waiting_for_row_lock(engine):
    async with AsyncSession(engine, expire_on_commit=False) as session:
        env = await seed(session)

    attempted = asyncio.Event()

    class SignalingRepository(BoardEditorRepository):
        async def get_board(self, board_id, *, lock=False):
            attempted.set()
            return await super().get_board(board_id, lock=lock)

    async def promote():
        async with AsyncSession(engine, expire_on_commit=False) as session:
            # До блокировки в identity map лежит прежний owner; его нельзя использовать.
            await session.get(Board, env.board_id)
            return await service(session, repository=SignalingRepository(session)).promote(
                board_id=env.board_id,
                user_id=env.owner_id,
                data=promotion(key="OWNERWAIT"),
            )

    try:
        async with AsyncSession(engine, expire_on_commit=False) as owner_session:
            board = await BoardEditorRepository(owner_session).get_board(env.board_id, lock=True)
            board.created_by_user_id = env.other_id
            await owner_session.flush()
            task = asyncio.create_task(promote())
            await asyncio.wait_for(attempted.wait(), timeout=5)
            await owner_session.commit()
            with pytest.raises(BoardNotFoundError):
                await asyncio.wait_for(task, timeout=5)
        async with AsyncSession(engine) as session:
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(Project)
                    .where(Project.owner_id == env.owner_id)
                )
                == 0
            )
            board = await session.get(Board, env.board_id)
            assert board.project_id is None and board.created_by_user_id == env.other_id
    finally:
        async with AsyncSession(engine) as session:
            await session.execute(delete(Project).where(Project.owner_id == env.owner_id))
            await session.execute(
                delete(Board).where(
                    Board.id.in_([env.board_id, env.foreign_id]),
                )
            )
            await session.execute(
                delete(User).where(
                    User.id.in_([env.owner_id, env.other_id, env.viewer_id]),
                )
            )
            await session.commit()


def test_metadata_size_limits_are_enforced():
    with pytest.raises(ValidationError):
        BoardEditorMetadata(
            templates=[
                {
                    "id": f"t{template_id}",
                    "name": "Объёмный шаблон",
                    "objects": [
                        {
                            "type": "sticker",
                            "object_id": f"o{object_id}",
                            "data": {"body": "x" * 2000},
                        }
                        for object_id in range(200)
                    ],
                }
                for template_id in range(2)
            ]
        )
