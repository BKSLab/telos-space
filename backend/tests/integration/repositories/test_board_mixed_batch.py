"""Смешанный пакет: копии стикеров и стрелок, конфликты и полный откат."""

import pytest

from src.exceptions.board_shapes import BoardBatchConflictError, BoardShapeInvalidError
from src.schemas.board_shapes import BoardBatchSchema
from tests.integration.repositories.test_boards_service import services


def copy_ops():
    return [
        {"op": "create_sticker", "client_id": "note", "data": {"body": "Копия", "canvas_x": 10}},
        {
            "op": "create_shape",
            "client_id": "node",
            "data": {"kind": "rect", "x": 400, "y": 0, "w": 100, "h": 100},
        },
        {
            "op": "create_shape",
            "client_id": "edge",
            "data": {
                "kind": "arrow",
                "points": [[240, 100], [400, 100]],
                "start_ref": {"type": "sticker", "client_id": "note"},
                "end_ref": {"type": "shape", "client_id": "node"},
            },
        },
    ]


async def test_mixed_copy_creates_refs_and_supports_delete(db_session, project, user, board):
    svc = services(db_session)
    result = await svc.shapes.apply_batch(
        project_id=project.id,
        board_id=board.id,
        author_id=user.id,
        author_username="tester",
        author_display_name="Тест",
        data=BoardBatchSchema(ops=copy_ops()),
    )
    assert len(result.shapes) == 2 and len(result.stickers) == 1
    note = result.stickers[0]
    assert note.created_by_username_snapshot == "tester"
    edge = next(item for item in result.shapes if item.kind.value == "arrow")
    assert edge.start_ref.id == note.id
    assert edge.end_ref.id == result.id_map["node"]
    updated = await svc.shapes.apply_batch(
        project_id=project.id,
        board_id=board.id,
        author_id=user.id,
        data=BoardBatchSchema(
            ops=[
                {
                    "op": "update_sticker",
                    "id": note.id,
                    "revision": 1,
                    "data": {"body": "Изменено"},
                }
            ]
        ),
    )
    assert updated.stickers[0].body == "Изменено"
    assert updated.stickers[0].revision == 2
    deleted = await svc.shapes.apply_batch(
        project_id=project.id,
        board_id=board.id,
        author_id=user.id,
        data=BoardBatchSchema(ops=[{"op": "delete_sticker", "id": note.id, "revision": 2}]),
    )
    assert deleted.deleted_sticker_ids == [note.id]
    assert next(item for item in deleted.shapes if item.id == edge.id).start_ref is None


async def test_late_failure_rolls_back_created_sticker(db_session, project, user, board):
    svc = services(db_session)
    project_id, board_id = project.id, board.id
    await db_session.commit()
    ops = copy_ops()
    ops[-1]["data"]["end_ref"] = {"type": "shape", "id": 999999}
    with pytest.raises(BoardShapeInvalidError):
        await svc.shapes.apply_batch(
            project_id=project.id,
            board_id=board.id,
            author_id=user.id,
            data=BoardBatchSchema(ops=ops),
        )
    content = await svc.boards.get_content(project_id=project_id, board_id=board_id)
    assert content.stickers == [] and content.shapes == []


async def test_stale_sticker_revision_rolls_back_whole_batch(db_session, project, user, board):
    svc = services(db_session)
    initial = await svc.shapes.apply_batch(
        project_id=project.id,
        board_id=board.id,
        author_id=user.id,
        data=BoardBatchSchema(ops=copy_ops()),
    )
    note = initial.stickers[0]
    project_id, board_id = project.id, board.id
    with pytest.raises(BoardBatchConflictError):
        await svc.shapes.apply_batch(
            project_id=project.id,
            board_id=board.id,
            author_id=user.id,
            data=BoardBatchSchema(
                ops=[
                    {"op": "create_sticker", "client_id": "another", "data": {"body": "Лишний"}},
                    {"op": "delete_sticker", "id": note.id, "revision": 9},
                ]
            ),
        )
    content = await svc.boards.get_content(project_id=project_id, board_id=board_id)
    assert [item.id for item in content.stickers] == [note.id]
