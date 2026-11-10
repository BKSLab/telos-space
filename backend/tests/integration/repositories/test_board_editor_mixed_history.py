"""Настройки и смешанная сцена сохраняются одной транзакцией и защищают locks."""

import pytest

from src.exceptions.board_shapes import BoardBatchConflictError, BoardShapeInvalidError
from src.exceptions.board_stickers import BoardStickerLockedError
from src.schemas.board_shapes import BoardBatchSchema, BoardShapeUpdateSchema
from src.schemas.board_stickers import (
    BoardStickerPositionUpdateSchema,
    BoardStickerUpdateSchema,
)
from tests.integration.repositories.test_board_mixed_batch import copy_ops
from tests.integration.repositories.test_boards_service import services


def metadata(groups=None, locked=None):
    return {"version": 1, "groups": groups or [], "locked": locked or [], "templates": []}


async def test_scene_and_group_history_are_atomic_with_recreated_ids(
    db_session, project, user, board
):
    svc = services(db_session)
    pid, bid, uid = project.id, board.id, user.id
    result = await svc.shapes.apply_batch(
        project_id=pid,
        board_id=bid,
        author_id=uid,
        data=BoardBatchSchema(
            ops=copy_ops(),
            editor_state={
                "expected_revision": 1,
                "metadata": metadata(
                    [
                        {
                            "id": "g",
                            "name": "Процесс",
                            "members": [
                                {"type": "shape", "client_id": "node"},
                                {"type": "sticker", "client_id": "note"},
                            ],
                        }
                    ]
                ),
            },
        ),
    )
    assert result.editor_state["revision"] == 2
    members = result.editor_state["metadata"]["groups"][0]["members"]
    assert members == [
        {"type": "shape", "id": result.id_map["node"]},
        {"type": "sticker", "id": result.id_map["note"]},
    ]
    old_ids = result.id_map.copy()
    deletion = [
        {"op": "delete_shape", "id": shape.id, "revision": shape.revision}
        for shape in result.shapes
    ] + [
        {
            "op": "delete_sticker",
            "id": result.stickers[0].id,
            "revision": result.stickers[0].revision,
        }
    ]
    deleted = await svc.shapes.apply_batch(
        project_id=pid,
        board_id=bid,
        author_id=uid,
        data=BoardBatchSchema(
            ops=deletion, editor_state={"expected_revision": 2, "metadata": metadata()}
        ),
    )
    assert deleted.editor_state["metadata"]["groups"] == []
    restored = await svc.shapes.apply_batch(
        project_id=pid,
        board_id=bid,
        author_id=uid,
        data=BoardBatchSchema(
            ops=copy_ops(),
            editor_state={
                "expected_revision": 3,
                "metadata": metadata(
                    [
                        {
                            "id": "g",
                            "name": "Процесс",
                            "members": [
                                {"type": "shape", "client_id": "node"},
                                {"type": "sticker", "client_id": "note"},
                            ],
                        }
                    ]
                ),
            },
        ),
    )
    assert restored.id_map["note"] != old_ids["note"]
    assert (
        restored.editor_state["metadata"]["groups"][0]["members"][1]["id"]
        == restored.id_map["note"]
    )


async def test_late_invalid_metadata_rolls_back_created_scene(db_session, project, user, board):
    svc = services(db_session)
    pid, bid, uid = project.id, board.id, user.id
    await db_session.commit()
    with pytest.raises(BoardShapeInvalidError):
        await svc.shapes.apply_batch(
            project_id=pid,
            board_id=bid,
            author_id=uid,
            data=BoardBatchSchema(
                ops=copy_ops(),
                editor_state={
                    "expected_revision": 1,
                    "metadata": metadata(locked=[{"type": "shape", "client_id": "note"}]),
                },
            ),
        )
    content = await svc.boards.get_content(project_id=pid, board_id=bid)
    assert not content.shapes and not content.stickers
    fresh = await svc.shapes.editor_repository.get_board(bid)
    assert fresh.revision == 1 and fresh.editor_metadata["locked"] == []


async def test_locks_protect_direct_and_indirect_mutations_but_allow_node_text(
    db_session, project, user, board
):
    svc = services(db_session)
    pid, bid, uid = project.id, board.id, user.id
    initial = await svc.shapes.apply_batch(
        project_id=pid, board_id=bid, author_id=uid, data=BoardBatchSchema(ops=copy_ops())
    )
    note, edge = (
        initial.stickers[0],
        next(shape for shape in initial.shapes if shape.kind.value == "arrow"),
    )
    await svc.shapes.apply_batch(
        project_id=pid,
        board_id=bid,
        author_id=uid,
        data=BoardBatchSchema(
            ops=[],
            editor_state={
                "expected_revision": initial.editor_state["revision"],
                "metadata": metadata(locked=[{"type": "shape", "id": edge.id}]),
            },
        ),
    )
    await svc.stickers.update_sticker(
        project_id=pid,
        board_id=bid,
        sticker_id=note.id,
        data=BoardStickerUpdateSchema(revision=1, body="Текст меняется без перемещения"),
    )
    with pytest.raises(BoardStickerLockedError):
        await svc.stickers.move_sticker(
            project_id=pid,
            board_id=bid,
            sticker_id=note.id,
            data=BoardStickerPositionUpdateSchema(canvas_x=300, canvas_y=40),
        )
    with pytest.raises(BoardShapeInvalidError):
        await svc.shapes.update_shape(
            project_id=pid,
            board_id=bid,
            shape_id=edge.id,
            data=BoardShapeUpdateSchema(label="Нельзя"),
        )
    fresh = await svc.shapes.editor_repository.get_board(bid)
    assert fresh.revision == initial.editor_state["revision"] + 2


async def test_stale_metadata_revision_rejects_scene_before_any_write(
    db_session, project, user, board
):
    svc = services(db_session)
    pid, bid, uid = project.id, board.id, user.id
    await db_session.commit()
    with pytest.raises(BoardBatchConflictError):
        await svc.shapes.apply_batch(
            project_id=pid,
            board_id=bid,
            author_id=uid,
            data=BoardBatchSchema(
                ops=copy_ops(), editor_state={"expected_revision": 99, "metadata": metadata()}
            ),
        )
    content = await svc.boards.get_content(project_id=pid, board_id=bid)
    assert not content.shapes and not content.stickers


async def test_individual_sticker_delete_honors_direct_and_indirect_locks(
    db_session, project, user, board
):
    svc = services(db_session)
    pid, bid, uid = project.id, board.id, user.id
    initial = await svc.shapes.apply_batch(
        project_id=pid, board_id=bid, author_id=uid, data=BoardBatchSchema(ops=copy_ops())
    )
    note = initial.stickers[0]
    edge = next(shape for shape in initial.shapes if shape.kind.value == "arrow")
    for ref in ({"type": "sticker", "id": note.id}, {"type": "shape", "id": edge.id}):
        current_board = await svc.shapes.editor_repository.get_board(bid)
        await svc.shapes.apply_batch(
            project_id=pid,
            board_id=bid,
            author_id=uid,
            data=BoardBatchSchema(
                ops=[],
                editor_state={
                    "expected_revision": current_board.revision,
                    "metadata": metadata(locked=[ref]),
                },
            ),
        )
        with pytest.raises(BoardStickerLockedError):
            await svc.stickers.delete_sticker(
                project_id=pid, board_id=bid, sticker_id=note.id, revision=1
            )
        content = await svc.boards.get_content(project_id=pid, board_id=bid)
        assert [sticker.id for sticker in content.stickers] == [note.id]
        assert (
            next(shape for shape in content.shapes if shape.id == edge.id).start_ref.id == note.id
        )


@pytest.mark.parametrize("collection", ["groups", "locked", "templates"])
def test_malformed_original_metadata_collections_are_rejected(collection):
    from pydantic import ValidationError

    value = metadata()
    value[collection] = {}
    with pytest.raises(ValidationError):
        BoardBatchSchema(ops=[], editor_state={"expected_revision": 1, "metadata": value})


async def test_agent_adds_diagram_to_board_with_real_sticker(db_session, project, user, board):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from src.agent.tools import AgentToolContext
    from src.schemas.board_diagrams import BoardDiagramInput
    from src.schemas.board_stickers import BoardStickerCreateSchema
    from src.services.agent_board_actions import draw_board_diagram

    svc = services(db_session)
    pid, bid, uid = project.id, board.id, user.id
    note = await svc.stickers.create_sticker(
        project_id=pid,
        board_id=bid,
        author_id=uid,
        author_username="owner",
        author_display_name="Owner",
        data=BoardStickerCreateSchema(body="Существующий стикер", canvas_x=80, canvas_y=120),
    )
    db = SimpleNamespace(
        boards=svc.boards,
        board_shapes=svc.shapes,
        access=SimpleNamespace(
            ensure_project_access=AsyncMock(return_value=SimpleNamespace(can_write=True))
        ),
    )
    result = await draw_board_diagram(
        db,
        AgentToolContext(project_id=pid, user_id=uid, can_write=True),
        BoardDiagramInput(board_id=bid, nodes=[{"key": "new", "label": "Новый блок"}]),
    )
    assert len(result["board_change"]["batch"]["shapes"]) == 1
    content = await svc.boards.get_content(project_id=pid, board_id=bid)
    assert content.stickers[0].id == note.id
    assert content.shapes[0].y >= note.canvas_y + note.height
