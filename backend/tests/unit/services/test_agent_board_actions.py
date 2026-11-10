from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.agent.tools import AgentToolContext
from src.db.models.board_shapes import BoardShapeKind
from src.exceptions.agent_tools import AgentToolAccessError, AgentToolConflictError
from src.exceptions.board_shapes import BoardBatchConflictError
from src.exceptions.boards import BoardNotFoundError
from src.schemas.board_diagrams import (
    BoardDiagramInput,
    BoardSceneInput,
    BoardShapeUpdatesInput,
)
from src.schemas.board_shapes import (
    BoardBatchResultSchema,
    BoardShapeSchema,
    complete_style,
)
from src.schemas.boards import BoardContentSchema, BoardSchema
from src.services.access import AccessService
from src.services.agent_board_actions import (
    draw_board_diagram,
    get_board_scene,
    update_board_shapes,
)
from src.services.board_shapes import BoardShapesService
from src.services.boards import BoardsService


def scene():
    now = datetime.now(UTC)
    shape = BoardShapeSchema(
        id=2,
        board_id=4,
        kind="rect",
        x=0,
        y=0,
        w=100,
        h=100,
        points=None,
        start_ref=None,
        end_ref=None,
        style=complete_style(BoardShapeKind.RECT, None),
        label="before",
        z_index=0,
        created_by_user_id=5,
        revision=3,
        created_at=now,
        updated_at=now,
    )
    board = BoardSchema(
        id=4,
        project_id=7,
        title="Board",
        position=0,
        revision=1,
        created_by_user_id=1,
        created_at=now,
        updated_at=now,
    )
    return BoardContentSchema(board=board, shapes=[shape], stickers=[])


@pytest.fixture
def db():
    boards = AsyncMock(spec=BoardsService)
    boards.get_content.return_value = scene()
    boards.get_free_content.return_value = scene()
    access = AsyncMock(spec=AccessService)
    access.ensure_project_access.return_value = SimpleNamespace(can_write=True)
    shapes = AsyncMock(spec=BoardShapesService)
    shapes.apply_batch.return_value = BoardBatchResultSchema(
        shapes=[],
        stickers=[],
        deleted_shape_ids=[],
        id_map={},
    )
    return SimpleNamespace(boards=boards, access=access, board_shapes=shapes)


def diagram():
    return BoardDiagramInput(board_id=4, nodes=[{"key": "n", "label": "New"}])


@pytest.mark.asyncio
async def test_viewer_reads_but_cannot_write(db):
    ctx = AgentToolContext(project_id=7, user_id=1, can_write=True)
    db.access.ensure_project_access.return_value.can_write = False
    result = await get_board_scene(db, ctx, BoardSceneInput(board_id=4))
    assert result["objects"][0]["revision"] == 3
    with pytest.raises(AgentToolAccessError):
        await draw_board_diagram(db, ctx, diagram())
    db.board_shapes.apply_batch.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("project_id", [7, None])
async def test_readonly_context_and_anonymous_user_never_write(db, project_id):
    for ctx in [
        AgentToolContext(project_id=project_id, user_id=1, can_write=False),
        AgentToolContext(project_id=project_id, user_id=None, can_write=True),
    ]:
        with pytest.raises(AgentToolAccessError):
            await draw_board_diagram(db, ctx, diagram())
    db.board_shapes.apply_batch.assert_not_awaited()


@pytest.mark.asyncio
async def test_personal_board_checks_owner_and_never_falls_back_to_project(db):
    ctx = AgentToolContext(project_id=None, user_id=1, can_write=True)
    db.boards.get_free_content.side_effect = BoardNotFoundError(4)
    with pytest.raises(BoardNotFoundError):
        await draw_board_diagram(db, ctx, diagram())
    db.boards.get_free_content.assert_awaited_once_with(board_id=4, user_id=1)
    db.boards.get_content.assert_not_awaited()
    db.access.ensure_project_access.assert_not_awaited()
    db.board_shapes.apply_batch.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("project_id", [7, None])
async def test_diagram_is_one_batch_with_current_author_and_refresh_delta(db, project_id):
    ctx = AgentToolContext(project_id=project_id, user_id=11, can_write=True)
    result = await draw_board_diagram(db, ctx, diagram())
    db.board_shapes.apply_batch.assert_awaited_once()
    args = db.board_shapes.apply_batch.await_args.kwargs
    assert args["author_id"] == 11 and args["project_id"] == project_id and args["board_id"] == 4
    assert args["data"].ops[0].data.kind == "rect"
    assert result["board_change"]["before_shapes"] == []
    assert result["board_change"]["operation_count"] == 1
    assert result["board_change"]["project_id"] == project_id


@pytest.mark.asyncio
async def test_updates_preserve_revision_and_before_state_as_one_history_entry(db):
    ctx = AgentToolContext(project_id=7, user_id=1, can_write=True)
    args = BoardShapeUpdatesInput(
        board_id=4,
        updates=[
            {"id": 2, "revision": 3, "data": {"label": "after"}},
        ],
    )
    result = await update_board_shapes(db, ctx, args)
    op = db.board_shapes.apply_batch.await_args.kwargs["data"].ops[0]
    assert op.revision == 3 and op.data.model_dump(exclude_unset=True) == {"label": "after"}
    assert result["board_change"]["before_shapes"][0]["label"] == "before"


@pytest.mark.asyncio
@pytest.mark.parametrize("id,revision", [(2, 2), (999, 3)])
async def test_stale_or_foreign_update_does_not_write_anything(db, id, revision):
    ctx = AgentToolContext(project_id=7, user_id=1, can_write=True)
    args = BoardShapeUpdatesInput(
        board_id=4,
        updates=[
            {"id": id, "revision": revision, "data": {"label": "after"}},
        ],
    )
    with pytest.raises((AgentToolConflictError, AgentToolAccessError)):
        await update_board_shapes(db, ctx, args)
    db.board_shapes.apply_batch.assert_not_awaited()


@pytest.mark.asyncio
async def test_concurrent_change_conflict_from_atomic_service_is_not_swallowed(db):
    db.board_shapes.apply_batch.side_effect = BoardBatchConflictError([{"kind": "shape", "id": 2}])
    ctx = AgentToolContext(project_id=7, user_id=1, can_write=True)
    args = BoardShapeUpdatesInput(
        board_id=4,
        updates=[
            {"id": 2, "revision": 3, "data": {"label": "after"}},
        ],
    )
    with pytest.raises(BoardBatchConflictError):
        await update_board_shapes(db, ctx, args)


@pytest.mark.asyncio
async def test_scene_pagination_returns_next_offset_and_full_object_data(db):
    ctx = AgentToolContext(project_id=7, user_id=1)
    content = scene()
    content.shapes.append(content.shapes[0].model_copy(update={"id": 3}))
    db.boards.get_content.return_value = content
    result = await get_board_scene(db, ctx, BoardSceneInput(board_id=4, limit=1))
    assert result["total"] == 2 and result["next_offset"] == 1
    assert result["objects"][0]["type"] == "shape"
    result = await get_board_scene(db, ctx, BoardSceneInput(board_id=4, limit=1, offset=1))
    assert result["objects"][0]["id"] == 3 and result["next_offset"] is None
