"""Shared frontend wire fixture survives PostgreSQL and an atomic batch rollback."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from src.db.models.board_shapes import BoardShape, BoardShapeKind
from src.db.models.boards import Board
from src.repositories.board_editor import BoardEditorRepository
from src.repositories.unit_of_work import UnitOfWork
from src.schemas.board_editor import BoardEditorMetadata, BoardEditorWrite, BoardObjectRef
from src.schemas.board_editor_batch import BoardBatchEditorInsert
from src.services.board_editor_batch import metadata_with_inserted_objects
from tests.integration.repositories.test_board_editor import seed, service


def fixture():
    path = (
        Path(__file__).resolve().parents[4] / "frontend/src/lib/board/editorTemplate.fixture.json"
    )
    return json.loads(path.read_text())


async def test_template_wire_groups_locks_and_mixed_refs_reload_from_postgres(db_session):
    env = await seed(db_session)
    template = fixture()
    state = BoardEditorMetadata(templates=[template])
    saved = await service(db_session).put_metadata(
        project_id=None,
        board_id=env.board_id,
        user_id=env.owner_id,
        data=BoardEditorWrite(expected_revision=1, metadata=state),
    )
    db_session.expire_all()
    loaded = await service(db_session).get_metadata(
        project_id=None, board_id=env.board_id, user_id=env.owner_id
    )
    assert loaded.revision == saved.revision == 2
    restored = loaded.metadata.templates[0]
    assert restored.groups[0].members == ["t0", "t1"]
    assert restored.locked == ["t1"]
    assert restored.objects[2].data.end_ref.object_id == "t1"
    assert restored.objects[1].data.task_ids == []


async def test_inserted_objects_and_metadata_share_one_rollback(db_session):
    env = await seed(db_session)
    repository = BoardEditorRepository(db_session)
    board = await repository.get_board(env.board_id, lock=True)
    created = BoardShape(
        board_id=env.board_id,
        kind=BoardShapeKind.RECT,
        x=500,
        y=0,
        w=100,
        h=80,
        style={},
        label="Новый узел",
    )
    db_session.add(created)
    await db_session.flush()
    created_id = created.id
    insertion = BoardBatchEditorInsert(expected_revision=1, locked=["new"])
    updated = metadata_with_inserted_objects(
        BoardEditorMetadata.model_validate(board.editor_metadata),
        insertion,
        {"new": BoardObjectRef(type="shape", id=created_id)},
    )
    await repository.save_metadata(board, updated)
    await UnitOfWork(db_session).rollback()
    db_session.expire_all()
    restored = await db_session.get(Board, env.board_id)
    assert restored.revision == 1 and restored.editor_metadata["locked"] == []
    assert await db_session.scalar(select(BoardShape.id).where(BoardShape.id == created_id)) is None


@pytest.mark.parametrize(
    "patch",
    [
        {"locked": ["t1", "t1"]},
        {"locked": ["outside"]},
        {"groups": [{"id": "g", "name": "Группа", "members": ["t0", "outside"]}]},
        {"groups": [{"id": "g", "name": "Группа", "members": ["t0", "t0"]}]},
    ],
)
def test_template_rejects_bad_group_and_lock_mapping(patch):
    with pytest.raises(ValidationError):
        BoardEditorMetadata(templates=[{**fixture(), **patch}])


def test_template_rejects_refs_to_connectors_and_hidden_source_fields():
    wire = fixture()
    wire["objects"][2]["data"]["end_ref"]["object_id"] = "t2"
    with pytest.raises(ValidationError):
        BoardEditorMetadata(templates=[wire])
    wire = fixture()
    wire["objects"][1]["data"]["created_by_user_id"] = 1
    with pytest.raises(ValidationError):
        BoardEditorMetadata(templates=[wire])
