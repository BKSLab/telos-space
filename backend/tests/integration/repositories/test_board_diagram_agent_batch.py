"""Real PostgreSQL checks of the existing atomic batch used by the new actions."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from src.agent.tools import AgentToolContext
from src.db.models.board_shapes import BoardShape
from src.db.models.boards import Board
from src.exceptions.board_shapes import BoardBatchConflictError, BoardShapeInvalidError
from src.exceptions.boards import BoardNotFoundError
from src.repositories.board_shapes import BoardShapesRepository
from src.repositories.board_stickers import BoardStickersRepository
from src.repositories.boards import BoardsRepository
from src.repositories.unit_of_work import UnitOfWork
from src.schemas.board_diagrams import BoardDiagramInput, BoardSceneInput
from src.schemas.board_shapes import BoardBatchSchema
from src.services.agent_board_actions import draw_board_diagram, get_board_scene
from src.services.board_shapes import BoardShapesService
from src.services.boards import BoardsService
from tests.board_support import local_user


async def setup_board(session):
    user = local_user(username="diagram-" + uuid4().hex, display_name="Diagram test")
    session.add(user)
    await session.flush()
    board = Board(
        title="Agent test", project_id=None, created_by_user_id=user.id, position=0
    )
    session.add(board)
    await session.commit()
    boards = BoardsRepository(session)
    shapes = BoardShapesRepository(session)
    stickers = BoardStickersRepository(session)
    uow = UnitOfWork(session)
    db = SimpleNamespace(
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
        ),
    )
    return db, user.id, board.id


@pytest.mark.asyncio
async def test_agent_diagram_persists_editable_objects_author_and_refs_atomically(
    db_session,
):
    db, author_id, board_id = await setup_board(db_session)
    result = await draw_board_diagram(
        db,
        AgentToolContext(project_id=None, user_id=author_id, can_write=True),
        BoardDiagramInput(
            board_id=board_id,
            nodes=[
                {"key": "a", "label": "Start", "kind": "ellipse"},
                {"key": "b", "label": "Check?", "kind": "diamond"},
            ],
            edges=[
                {
                    "source": {"type": "node", "key": "a"},
                    "target": {"type": "node", "key": "b"},
                    "label": "Next",
                }
            ],
        ),
    )
    delta = result["board_change"]
    assert delta["operation_count"] == 3
    content = await db.boards.get_free_content(board_id=board_id, user_id=author_id)
    assert len(content.shapes) == 3
    assert {shape.created_by_user_id for shape in content.shapes} == {author_id}
    arrow = next(shape for shape in content.shapes if shape.kind == "arrow")
    assert arrow.start_ref.id == delta["batch"]["id_map"]["node_a"]
    assert arrow.end_ref.id == delta["batch"]["id_map"]["node_b"]
    assert {shape.kind for shape in content.shapes} == {"ellipse", "diamond", "arrow"}


@pytest.mark.asyncio
async def test_late_invalid_arrow_rolls_back_already_inserted_shape(db_session):
    db, author_id, board_id = await setup_board(db_session)
    batch = BoardBatchSchema.model_validate(
        {
            "ops": [
                {
                    "op": "create_shape",
                    "client_id": "a",
                    "data": {
                        "kind": "rect",
                        "x": 0,
                        "y": 0,
                        "w": 100,
                        "h": 100,
                        "label": "Must rollback",
                    },
                },
                {
                    "op": "create_shape",
                    "data": {
                        "kind": "arrow",
                        "points": [[0, 0], [100, 100]],
                        "start_ref": {"type": "shape", "client_id": "a"},
                        "end_ref": {"type": "shape", "id": 999999999},
                    },
                },
            ]
        }
    )
    with pytest.raises(BoardShapeInvalidError):
        await db.board_shapes.apply_batch(
            project_id=None, board_id=board_id, data=batch, author_id=author_id
        )
    count = await db_session.scalar(
        select(func.count()).select_from(BoardShape).where(BoardShape.board_id == board_id)
    )
    assert count == 0


@pytest.mark.asyncio
async def test_one_stale_revision_rejects_all_updates(db_session):
    db, author_id, board_id = await setup_board(db_session)
    saved = await draw_board_diagram(
        db,
        AgentToolContext(project_id=None, user_id=author_id, can_write=True),
        BoardDiagramInput(
            board_id=board_id,
            nodes=[
                {"key": "a", "label": "Original A"},
                {"key": "b", "label": "Original B"},
            ],
        ),
    )
    id_map = saved["board_change"]["batch"]["id_map"]
    batch = BoardBatchSchema.model_validate(
        {
            "ops": [
                {
                    "op": "update_shape",
                    "id": id_map["node_a"],
                    "revision": 1,
                    "data": {"label": "Changed A"},
                },
                {
                    "op": "update_shape",
                    "id": id_map["node_b"],
                    "revision": 99,
                    "data": {"label": "Changed B"},
                },
            ]
        }
    )
    with pytest.raises(BoardBatchConflictError):
        await db.board_shapes.apply_batch(
            project_id=None, board_id=board_id, data=batch, author_id=author_id
        )
    content = await db.boards.get_free_content(board_id=board_id, user_id=author_id)
    assert {shape.label for shape in content.shapes} == {"Original A", "Original B"}
    assert {shape.revision for shape in content.shapes} == {1}


@pytest.mark.asyncio
async def test_another_users_personal_board_is_hidden_for_reads_and_writes(db_session):
    db, author_id, board_id = await setup_board(db_session)
    other = local_user(username="other-" + uuid4().hex, display_name="Other user")
    db_session.add(other)
    await db_session.commit()
    ctx = AgentToolContext(project_id=None, user_id=other.id, can_write=True)
    with pytest.raises(BoardNotFoundError):
        await get_board_scene(db, ctx, BoardSceneInput(board_id=board_id))
    with pytest.raises(BoardNotFoundError):
        await draw_board_diagram(
            db,
            ctx,
            BoardDiagramInput(board_id=board_id, nodes=[{"key": "a", "label": "Forbidden"}]),
        )
    count = await db_session.scalar(
        select(func.count()).select_from(BoardShape).where(BoardShape.board_id == board_id)
    )
    assert count == 0 and other.id != author_id
