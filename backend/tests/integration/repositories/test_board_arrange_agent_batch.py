"""Explicit arrange uses the same real PostgreSQL CAS, locks and transaction as batch."""

from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import update

from src.agent.tools import AgentToolContext
from src.db.models.board_shapes import BoardShape, BoardShapeKind
from src.db.models.board_stickers import BoardSticker, BoardStickerTaskLink
from src.db.models.boards import Board
from src.db.models.project_members import ProjectMember, ProjectRole
from src.db.models.tasks import Task
from src.exceptions.agent_tools import (
    AgentToolAccessError,
    AgentToolArgumentsError,
    AgentToolConflictError,
)
from src.exceptions.board_shapes import BoardBatchConflictError, BoardShapeInvalidError
from src.exceptions.boards import BoardNotFoundError
from src.repositories.board_editor import BoardEditorRepository
from src.repositories.board_shapes import BoardShapesRepository
from src.repositories.board_stickers import BoardStickersRepository
from src.repositories.boards import BoardsRepository
from src.repositories.project_members import ProjectMembersRepository
from src.repositories.unit_of_work import UnitOfWork
from src.schemas.board_diagrams import BoardArrangeInput, BoardSceneInput
from src.schemas.board_editor import BoardEditorMetadata
from src.schemas.board_shapes import BoardBatchSchema, default_style
from src.services.agent_board_actions import _objects, arrange_board_objects, get_board_scene
from src.services.board_arrange import compile_arrangement
from src.services.board_shapes import BoardShapesService
from src.services.boards import BoardsService
from tests.board_support import local_user, user_display_name


async def seed(session, *, owner=None, board=None):
    assert session.bind.dialect.name == "postgresql"
    if owner is None:
        owner = local_user(username="arrange-" + uuid4().hex, display_name="Arrange owner")
        session.add(owner)
        await session.flush()
    if board is None:
        board = Board(title="Arrange", created_by_user_id=owner.id, position=4)
        session.add(board)
        await session.flush()
    shape = BoardShape(
        board_id=board.id,
        kind=BoardShapeKind.RECT,
        x=300,
        y=50,
        w=120,
        h=90,
        label="Keep shape label",
        style={**default_style(BoardShapeKind.RECT), "fill": "yellow"},
        z_index=6,
        created_by_user_id=owner.id,
        revision=3,
    )
    sticker = BoardSticker(
        board_id=board.id,
        project_id=board.project_id,
        body="Keep sticker body",
        color="#c9edff",
        canvas_x=700,
        canvas_y=220,
        width=260,
        height=180,
        z_index=9,
        revision=5,
        created_by_user_id=owner.id,
        created_by_username_snapshot=owner.username,
        created_by_display_name_snapshot=user_display_name(owner),
    )
    outside = BoardShape(
        board_id=board.id,
        kind=BoardShapeKind.ELLIPSE,
        x=1000,
        y=500,
        w=100,
        h=80,
        label="Unselected",
        style=default_style(BoardShapeKind.ELLIPSE),
        revision=2,
    )
    session.add_all([shape, sticker, outside])
    await session.flush()
    internal = BoardShape(
        board_id=board.id,
        kind=BoardShapeKind.ARROW,
        x=420,
        y=95,
        w=280,
        h=215,
        points=[[420, 95], [500, 95], [500, 310], [700, 310]],
        start_ref={"type": "shape", "id": shape.id, "anchor": "right"},
        end_ref={"type": "sticker", "id": sticker.id, "anchor": "left"},
        label="Internal relation",
        revision=7,
        z_index=11,
        style={**default_style(BoardShapeKind.ARROW), "route": "orthogonal", "dash": "dashed"},
    )
    external = BoardShape(
        board_id=board.id,
        kind=BoardShapeKind.ARROW,
        x=960,
        y=310,
        w=40,
        h=230,
        points=[[960, 310], [980, 310], [980, 540], [1000, 540]],
        start_ref={"type": "sticker", "id": sticker.id, "anchor": "right"},
        end_ref={"type": "shape", "id": outside.id, "anchor": "left"},
        label="External relation",
        revision=11,
        style=default_style(BoardShapeKind.ARROW),
    )
    detached = BoardShape(
        board_id=board.id,
        kind=BoardShapeKind.ARROW,
        x=-100,
        y=-100,
        w=50,
        h=50,
        points=[[-100, -100], [-50, -50]],
        label="Detached",
        revision=13,
        style=default_style(BoardShapeKind.ARROW),
    )
    session.add_all([internal, external, detached])
    await session.flush()
    metadata = BoardEditorMetadata.model_validate(
        {
            "groups": [
                {
                    "id": "keep-group",
                    "name": "Review process",
                    "members": [
                        {"type": "shape", "id": shape.id},
                        {"type": "sticker", "id": sticker.id},
                    ],
                }
            ],
            "templates": [
                {
                    "id": "keep-template",
                    "name": "Saved selection",
                    "objects": [
                        {
                            "type": "sticker",
                            "object_id": "template-note",
                            "data": {"body": "Template content must survive"},
                        }
                    ],
                }
            ],
        }
    ).model_dump(mode="json")
    board.editor_metadata = metadata
    board.revision = 12
    env = SimpleNamespace(
        owner_id=owner.id,
        board_id=board.id,
        project_id=board.project_id,
        shape_id=shape.id,
        sticker_id=sticker.id,
        outside_id=outside.id,
        internal_id=internal.id,
        external_id=external.id,
        detached_id=detached.id,
    )
    await session.commit()
    return env


def services(session):
    boards = BoardsRepository(session)
    shapes = BoardShapesRepository(session)
    stickers = BoardStickersRepository(session)
    uow = UnitOfWork(session)
    return SimpleNamespace(
        boards=BoardsService(
            boards_repository=boards,
            shapes_repository=shapes,
            stickers_repository=stickers,
            unit_of_work=uow,
        ),
        board_shapes=BoardShapesService(
            boards_repository=boards,
            shapes_repository=shapes,
            stickers_repository=stickers,
            unit_of_work=uow,
            editor_repository=BoardEditorRepository(session),
        ),
    )


def context(env, **overrides):
    return AgentToolContext(
        **{"project_id": env.project_id, "user_id": env.owner_id, "can_write": True, **overrides}
    )


async def content(db, env):
    if env.project_id is None:
        return await db.boards.get_free_content(board_id=env.board_id, user_id=env.owner_id)
    return await db.boards.get_content(project_id=env.project_id, board_id=env.board_id)


async def snapshot(session, db, env):
    session.expunge_all()
    scene = await content(db, env)
    board = await BoardEditorRepository(session).get_board(env.board_id)
    return {
        "scene": scene.model_dump(mode="json"),
        "metadata": deepcopy(board.editor_metadata),
        "revision": board.revision,
    }


async def arrangement(db, env, **overrides):
    scene = await content(db, env)
    objects = _objects(scene)
    selected = [
        item
        for item in objects
        if (item["type"], item["id"]) in {("shape", env.shape_id), ("sticker", env.sticker_id)}
    ]
    args = BoardArrangeInput(
        **{
            "board_id": env.board_id,
            "expected_revision": scene.board.revision,
            "objects": [
                {"type": item["type"], "id": item["id"], "revision": item["revision"]}
                for item in selected
            ],
            "mode": "align",
            "alignment": "left",
            **overrides,
        }
    )
    board = await db.board_shapes.editor_repository.get_board(env.board_id)
    batch = compile_arrangement(args, objects=objects, metadata=board.editor_metadata)
    return args, batch


async def apply(db, env, batch, *, author_id=None):
    return await db.board_shapes.apply_batch(
        project_id=env.project_id,
        board_id=env.board_id,
        author_id=author_id if author_id is not None else env.owner_id,
        data=batch,
    )


def without(item, fields):
    return {key: value for key, value in item.items() if key not in fields}


def assert_preserved(before, after, env, *, mode):
    old = before["scene"]
    new = after["scene"]
    assert after["metadata"] == before["metadata"]
    assert after["revision"] == before["revision"] + 1
    assert without(new["board"], {"revision", "updated_at"}) == without(
        old["board"], {"revision", "updated_at"}
    )
    old_shapes = {item["id"]: item for item in old["shapes"]}
    new_shapes = {item["id"]: item for item in new["shapes"]}
    assert old_shapes.keys() == new_shapes.keys()
    for shape_id, original in old_shapes.items():
        changed = new_shapes[shape_id]
        if shape_id in {env.outside_id, env.detached_id}:
            assert changed == original
            continue
        mutable = {"x", "y", "revision", "updated_at"}
        if original["kind"] == "arrow":
            mutable |= {"w", "h", "points"}
            if mode == "layout":
                mutable |= {"start_ref", "end_ref"}
            for name in ("start_ref", "end_ref"):
                assert without(changed[name], {"anchor"}) == without(original[name], {"anchor"})
        assert without(changed, mutable) == without(original, mutable)
        assert changed["revision"] == original["revision"] + 1
    assert len(old["stickers"]) == len(new["stickers"]) == 1
    original, changed = old["stickers"][0], new["stickers"][0]
    assert without(changed, {"canvas_x", "canvas_y", "revision", "updated_at"}) == without(
        original, {"canvas_x", "canvas_y", "revision", "updated_at"}
    )
    assert changed["revision"] == original["revision"] + 1
    external = new_shapes[env.external_id]
    assert external["points"][1:] == old_shapes[env.external_id]["points"][1:]
    assert external["points"][0] != old_shapes[env.external_id]["points"][0]
    if mode == "align":
        assert (
            new_shapes[env.internal_id]["points"][1:-1]
            == old_shapes[env.internal_id]["points"][1:-1]
        )


@pytest.mark.parametrize(
    ("mode", "direction"), [("align", "TB"), ("layout", "TB"), ("layout", "LR")]
)
async def test_agent_arrange_preserves_fields_refs_metadata_and_increments_board_once(
    db_session, mode, direction
):
    env = await seed(db_session)
    db = services(db_session)
    before = await snapshot(db_session, db, env)
    args, batch = await arrangement(db, env, mode=mode, direction=direction)
    assert batch.expected_revision == before["revision"]
    assert batch.editor_state is None and batch.editor_insert is None
    assert all(op.revision is not None for op in batch.ops)

    result = await arrange_board_objects(db, context(env), args)
    after = await snapshot(db_session, db, env)

    assert_preserved(before, after, env, mode=mode)
    delta = result["board_change"]
    assert delta["operation_count"] == len(batch.ops)
    assert delta["batch"]["id_map"] == {}
    assert delta["batch"]["deleted_shape_ids"] == []
    assert delta["batch"]["deleted_sticker_ids"] == []
    assert delta["batch"]["editor_state"]["revision"] == before["revision"] + 1
    assert {item["id"] for item in delta["before_stickers"]} == {env.sticker_id}
    assert {item["id"] for item in delta["before_shapes"]} == {
        env.shape_id,
        env.internal_id,
        env.external_id,
    }


async def test_scene_exposes_groups_locks_and_board_revision_without_template_contents(db_session):
    env = await seed(db_session)
    db = services(db_session)
    board = await db.board_shapes.editor_repository.get_board(env.board_id)
    metadata = deepcopy(board.editor_metadata)
    metadata["locked"] = [{"type": "shape", "id": env.outside_id}]
    board.editor_metadata = metadata
    await db_session.commit()

    result = await get_board_scene(db, context(env), BoardSceneInput(board_id=env.board_id))

    assert result["revision"] == result["board"]["revision"] == 12
    assert result["editor_metadata"] == {"groups": metadata["groups"], "locked": metadata["locked"]}
    assert "templates" not in result["editor_metadata"]
    assert {(item["type"], item["id"]) for item in result["objects"]} == {
        ("shape", env.shape_id),
        ("shape", env.outside_id),
        ("shape", env.internal_id),
        ("shape", env.external_id),
        ("shape", env.detached_id),
        ("sticker", env.sticker_id),
    }
    assert all(item["revision"] >= 1 for item in result["objects"])


async def test_late_invalid_arrow_rolls_back_positions_object_revisions_and_metadata(db_session):
    env = await seed(db_session)
    db = services(db_session)
    before = await snapshot(db_session, db, env)
    _, batch = await arrangement(db, env)
    raw = batch.model_dump(mode="json", exclude_unset=True)
    raw["ops"].append(
        {
            "op": "update_shape",
            "id": env.detached_id,
            "revision": 13,
            "data": {
                "points": [[0, 0], [100, 100]],
                "end_ref": {"type": "shape", "id": 2_000_000_000},
            },
        }
    )
    assert any(op["op"] == "move_sticker" for op in raw["ops"][:-1])

    with pytest.raises(BoardShapeInvalidError):
        await apply(db, env, BoardBatchSchema.model_validate(raw))

    assert await snapshot(db_session, db, env) == before


async def test_failure_after_board_revision_write_rolls_back_entire_arrangement(
    db_session, monkeypatch
):
    env = await seed(db_session)
    db = services(db_session)
    _, batch = await arrangement(db, env)
    before = await snapshot(db_session, db, env)
    repository = db.board_shapes.editor_repository
    save = repository.save_metadata
    writes = []

    async def fail_after_save(board, metadata):
        await save(board, metadata)
        writes.append(board.revision)
        raise BoardShapeInvalidError("Forced failure after CAS write")

    monkeypatch.setattr(repository, "save_metadata", fail_after_save)

    with pytest.raises(BoardShapeInvalidError, match="Forced failure"):
        await apply(db, env, batch)

    assert writes == [before["revision"] + 1]
    assert await snapshot(db_session, db, env) == before


async def test_batch_cas_rechecks_compiled_board_revision_and_does_not_overwrite_new_state(
    db_session,
):
    env = await seed(db_session)
    db = services(db_session)
    _, batch = await arrangement(db, env)
    first = await apply(db, env, batch)
    before_retry = await snapshot(db_session, db, env)
    assert first.editor_state["revision"] == 13

    with pytest.raises(BoardBatchConflictError) as error:
        await apply(db, env, batch)

    assert error.value.status_code == 409
    assert error.value.conflicts == [{"kind": "board", "id": env.board_id}]
    assert await snapshot(db_session, db, env) == before_retry


@pytest.mark.parametrize("kind", ["shape", "sticker", "arrow"])
async def test_batch_rechecks_object_revision_after_compilation_without_partial_changes(
    db_session, kind
):
    env = await seed(db_session)
    db = services(db_session)
    _, batch = await arrangement(db, env)
    model = BoardSticker if kind == "sticker" else BoardShape
    object_id = {"shape": env.shape_id, "sticker": env.sticker_id, "arrow": env.internal_id}[kind]
    await db_session.execute(
        update(model).where(model.id == object_id).values(revision=model.revision + 1)
    )
    await db_session.commit()
    before = await snapshot(db_session, db, env)

    with pytest.raises(BoardBatchConflictError) as error:
        await apply(db, env, batch)

    assert error.value.conflicts == [
        {"kind": "sticker" if kind == "sticker" else "shape", "id": object_id}
    ]
    assert await snapshot(db_session, db, env) == before


@pytest.mark.parametrize("stale", ["board", "shape", "sticker"])
async def test_agent_rejects_stale_explicit_selection_before_writes(db_session, stale):
    env = await seed(db_session)
    db = services(db_session)
    args, _ = await arrangement(db, env)
    raw = args.model_dump(mode="json")
    if stale == "board":
        raw["expected_revision"] -= 1
    else:
        next(item for item in raw["objects"] if item["type"] == stale)["revision"] -= 1
    before = await snapshot(db_session, db, env)

    with pytest.raises(AgentToolConflictError) as error:
        await arrange_board_objects(db, context(env), BoardArrangeInput.model_validate(raw))

    assert error.value.status_code == 409
    assert await snapshot(db_session, db, env) == before


async def test_partial_group_alignment_returns_readable_error_and_preserves_everything(db_session):
    env = await seed(db_session)
    db = services(db_session)
    args, _ = await arrangement(db, env)
    raw = args.model_dump(mode="json")
    raw["objects"] = [
        {"type": "shape", "id": env.shape_id, "revision": 3},
        {"type": "shape", "id": env.outside_id, "revision": 2},
    ]
    before = await snapshot(db_session, db, env)

    with pytest.raises(AgentToolArgumentsError) as error:
        await arrange_board_objects(db, context(env), BoardArrangeInput.model_validate(raw))

    assert error.value.status_code == 422
    assert "Review process" in str(error.value.detail)
    assert await snapshot(db_session, db, env) == before


@pytest.mark.parametrize("lock", ["direct", "group", "indirect_arrow"])
async def test_shared_batch_checks_direct_group_and_indirect_arrow_locks(db_session, lock):
    env = await seed(db_session)
    db = services(db_session)
    board = await db.board_shapes.editor_repository.get_board(env.board_id)
    metadata = deepcopy(board.editor_metadata)
    kind, object_id = {
        "direct": ("shape", env.shape_id),
        "group": ("sticker", env.sticker_id),
        "indirect_arrow": ("shape", env.internal_id),
    }[lock]
    metadata["locked"] = [{"type": kind, "id": object_id}]
    board.editor_metadata = metadata
    await db_session.commit()
    before = await snapshot(db_session, db, env)
    batch = BoardBatchSchema(
        expected_revision=12,
        ops=[
            {
                "op": "update_shape",
                "id": env.shape_id,
                "revision": 3,
                "data": {"x": 0, "y": 0},
            }
        ],
    )

    with pytest.raises(BoardShapeInvalidError) as error:
        await apply(db, env, batch)

    assert "разблок" in str(error.value.detail).lower()
    assert await snapshot(db_session, db, env) == before


async def test_agent_arrange_respects_locked_external_arrow(db_session):
    env = await seed(db_session)
    db = services(db_session)
    board = await db.board_shapes.editor_repository.get_board(env.board_id)
    metadata = deepcopy(board.editor_metadata)
    metadata["locked"] = [{"type": "shape", "id": env.external_id}]
    board.editor_metadata = metadata
    await db_session.commit()
    args, _ = await arrangement(db, env)
    before = await snapshot(db_session, db, env)

    with pytest.raises(BoardShapeInvalidError):
        await arrange_board_objects(db, context(env), args)

    assert await snapshot(db_session, db, env) == before


async def test_board_cas_requires_editor_repository_and_never_falls_back_to_last_write_wins(
    db_session,
):
    env = await seed(db_session)
    db = services(db_session)
    _, batch = await arrangement(db, env)
    before = await snapshot(db_session, db, env)
    db.board_shapes.editor_repository = None

    with pytest.raises(BoardShapeInvalidError) as error:
        await apply(db, env, batch)

    assert "CAS" in str(error.value.detail)
    assert await snapshot(db_session, db, env) == before


async def test_another_owner_cannot_read_arrange_or_directly_apply_to_personal_board(db_session):
    env = await seed(db_session)
    db = services(db_session)
    args, batch = await arrangement(db, env)
    other = local_user(username="arrange-other-" + uuid4().hex, display_name="Other owner")
    db_session.add(other)
    await db_session.flush()
    other_id = other.id
    await db_session.commit()
    before = await snapshot(db_session, db, env)

    with pytest.raises(BoardNotFoundError):
        await get_board_scene(
            db, context(env, user_id=other_id), BoardSceneInput(board_id=env.board_id)
        )
    with pytest.raises(BoardNotFoundError):
        await arrange_board_objects(db, context(env, user_id=other_id), args)
    with pytest.raises(BoardNotFoundError):
        await apply(db, env, batch, author_id=other_id)

    assert await snapshot(db_session, db, env) == before


async def test_read_only_agent_cannot_arrange_even_own_board(db_session):
    env = await seed(db_session)
    db = services(db_session)
    args, _ = await arrangement(db, env)
    before = await snapshot(db_session, db, env)

    with pytest.raises(AgentToolAccessError):
        await arrange_board_objects(db, context(env, can_write=False), args)

    assert await snapshot(db_session, db, env) == before


async def test_arrange_preserves_existing_project_sticker_task_links(
    db_session, project, user, board, stage
):
    from tests.integration.repositories.test_board_editor import service as editor_service

    env = await seed(db_session, owner=user, board=board)
    if (
        await ProjectMembersRepository(db_session).get(
            project_id=env.project_id, user_id=env.owner_id
        )
        is None
    ):
        db_session.add(
            ProjectMember(project_id=env.project_id, user_id=env.owner_id, role=ProjectRole.OWNER)
        )
    task = Task(project_id=env.project_id, stage_id=stage.id, number=1, title="Linked task")
    db_session.add(task)
    await db_session.flush()
    task_id = task.id
    db_session.add(BoardStickerTaskLink(sticker_id=env.sticker_id, task_id=task_id))
    await db_session.commit()
    db = services(db_session)
    db.access = editor_service(db_session).access
    before = await snapshot(db_session, db, env)
    args, _ = await arrangement(db, env)

    await arrange_board_objects(db, context(env), args)
    after = await snapshot(db_session, db, env)

    assert_preserved(before, after, env, mode="align")
    assert before["scene"]["stickers"][0]["task_ids"] == [task_id]
    assert after["scene"]["stickers"][0]["task_ids"] == [task_id]
