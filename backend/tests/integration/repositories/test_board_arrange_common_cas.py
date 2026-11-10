"""A scene CAS must observe ordinary editor writes, including legacy sticker moves."""

import pytest

from src.exceptions.agent_tools import AgentToolConflictError
from src.repositories.tasks import TasksRepository
from src.schemas.board_shapes import (
    BoardBatchSchema,
    BoardShapeCreateSchema,
    BoardShapeToStickerSchema,
    BoardShapeUpdateSchema,
)
from src.schemas.board_stickers import (
    BoardStickerCreateSchema,
    BoardStickerPositionUpdateSchema,
    BoardStickerUpdateSchema,
)
from src.services.agent_board_actions import arrange_board_objects
from src.services.board_stickers import BoardStickersService
from tests.integration.repositories.test_board_arrange_agent_batch import (
    arrangement,
    context,
    seed,
    services,
    snapshot,
)


@pytest.mark.parametrize(
    "operation",
    [
        "create_shape",
        "update_shape",
        "delete_shape",
        "convert_shape",
        "create_sticker",
        "move_sticker",
        "update_sticker",
        "delete_sticker",
    ],
)
async def test_single_editor_write_invalidates_board_cas(db_session, operation):
    env = await seed(db_session)
    db = services(db_session)
    args, _ = await arrangement(db, env)
    shapes = db.board_shapes
    stickers = BoardStickersService(
        boards_repository=shapes.boards_repository,
        shapes_repository=shapes.shapes_repository,
        stickers_repository=shapes.stickers_repository,
        tasks_repository=TasksRepository(db_session),
        editor_repository=shapes.editor_repository,
        unit_of_work=shapes.unit_of_work,
    )
    scope = dict(project_id=None, board_id=env.board_id)
    if operation == "create_shape":
        await shapes.create_shape(
            **scope,
            author_id=env.owner_id,
            data=BoardShapeCreateSchema(kind="text", x=5, y=5, w=80, h=80, label="New"),
        )
    elif operation == "update_shape":
        await shapes.update_shape(
            **scope,
            shape_id=env.shape_id,
            user_id=env.owner_id,
            data=BoardShapeUpdateSchema(revision=3, label="Manual change"),
        )
    elif operation == "delete_shape":
        await shapes.delete_shape(
            **scope,
            shape_id=env.shape_id,
            user_id=env.owner_id,
            revision=3,
        )
    elif operation == "convert_shape":
        await shapes.shape_to_sticker(
            **scope,
            shape_id=env.shape_id,
            author_id=env.owner_id,
            author_username="owner",
            author_display_name="Owner",
            data=BoardShapeToStickerSchema(revision=3),
        )
    elif operation == "create_sticker":
        await stickers.create_sticker(
            **scope,
            author_id=env.owner_id,
            author_username="owner",
            author_display_name="Owner",
            data=BoardStickerCreateSchema(body="New"),
        )
    elif operation == "move_sticker":
        await stickers.move_sticker(
            **scope,
            sticker_id=env.sticker_id,
            user_id=env.owner_id,
            data=BoardStickerPositionUpdateSchema(canvas_x=800, canvas_y=240),
        )
    elif operation == "update_sticker":
        await stickers.update_sticker(
            **scope,
            sticker_id=env.sticker_id,
            user_id=env.owner_id,
            data=BoardStickerUpdateSchema(revision=5, body="Manual change"),
        )
    else:
        await stickers.delete_sticker(
            **scope,
            sticker_id=env.sticker_id,
            user_id=env.owner_id,
            revision=5,
        )
    board = await shapes.editor_repository.get_board(env.board_id)
    assert board.revision == args.expected_revision + 1
    before = await snapshot(db_session, db, env)
    with pytest.raises(AgentToolConflictError):
        await arrange_board_objects(db, context(env), args)
    assert await snapshot(db_session, db, env) == before


@pytest.mark.parametrize("revisioned_move", [False, True])
async def test_mixed_batch_counts_only_real_sticker_revision_increments(
    db_session, revisioned_move
):
    env = await seed(db_session)
    db = services(db_session)
    move = {
        "op": "move_sticker",
        "id": env.sticker_id,
        "data": {"canvas_x": 800, "canvas_y": 240},
    }
    if revisioned_move:
        move["revision"] = 5
    batch = BoardBatchSchema.model_validate(
        {
            "expected_revision": 12,
            "ops": [
                move,
                {
                    "op": "update_sticker",
                    "id": env.sticker_id,
                    "revision": 6 if revisioned_move else 5,
                    "data": {"body": "Updated after moving", "canvas_x": 800, "canvas_y": 240},
                },
            ],
        }
    )
    result = await db.board_shapes.apply_batch(
        project_id=None,
        board_id=env.board_id,
        author_id=env.owner_id,
        data=batch,
    )
    assert result.stickers[0].revision == (7 if revisioned_move else 6)
    assert result.stickers[0].body == "Updated after moving"
    assert result.editor_state["revision"] == 13
