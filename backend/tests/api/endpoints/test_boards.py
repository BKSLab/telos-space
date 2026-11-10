"""HTTP-контур досок и фигур: пути, коды ответов, форма 409 пакета."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

from httpx import AsyncClient

from main import app
from src.dependencies.services import get_board_shapes_service, get_boards_service
from src.exceptions.board_shapes import BoardBatchConflictError, BoardShapeInvalidError
from src.exceptions.boards import BoardNotFoundError, ProjectLastBoardError
from src.schemas.board_shapes import BoardBatchResultSchema
from src.schemas.boards import BoardSchema
from src.services.board_shapes import BoardShapesService
from src.services.boards import BoardsService


def board_schema() -> BoardSchema:
    now = datetime.now(UTC)
    return BoardSchema(
        id=3,
        project_id=1,
        title="Основная",
        position=0,
        revision=1,
        created_by_user_id=1,
        created_at=now,
        updated_at=now,
    )


async def test_board_routes_pass_path_and_map_domain_errors(api_client: AsyncClient) -> None:
    service = AsyncMock(spec=BoardsService)
    service.list_boards.return_value = [board_schema()]
    service.create_board.return_value = board_schema()
    service.delete_board.side_effect = ProjectLastBoardError(3)
    service.get_content.side_effect = BoardNotFoundError(3)
    app.dependency_overrides[get_boards_service] = lambda: service

    listed = await api_client.get("/api/v1/projects/1/boards")
    created = await api_client.post("/api/v1/projects/1/boards", json={"title": "Схема"})
    empty_title = await api_client.post("/api/v1/projects/1/boards", json={"title": "   "})
    last = await api_client.delete("/api/v1/projects/1/boards/3?revision=1")
    missing = await api_client.get("/api/v1/projects/1/boards/3/content")

    assert listed.status_code == 200 and listed.json()[0]["title"] == "Основная"
    service.list_boards.assert_awaited_once_with(1, user_id=1)
    assert created.status_code == 201
    assert service.create_board.await_args.kwargs["data"].title == "Схема"
    assert empty_title.status_code == 422
    assert last.status_code == 409
    assert last.json() == {"detail": "Нельзя удалить последнюю доску проекта."}
    assert missing.status_code == 404


async def test_shape_routes_validate_and_report_batch_conflicts(api_client: AsyncClient) -> None:
    service = AsyncMock(spec=BoardShapesService)
    service.apply_batch.side_effect = BoardBatchConflictError([{"kind": "shape", "id": 12}])
    service.create_shape.side_effect = BoardShapeInvalidError(
        "Привязка указывает на объект не с этой доски."
    )
    app.dependency_overrides[get_board_shapes_service] = lambda: service

    conflict = await api_client.post(
        "/api/v1/projects/1/boards/3/batch",
        json={"ops": [{"op": "update_shape", "id": 12, "revision": 3, "data": {"x": 1}}]},
    )
    invalid_ref = await api_client.post(
        "/api/v1/projects/1/boards/3/shapes",
        json={"kind": "arrow", "points": [[0, 0], [1, 1]], "end_ref": {"type": "sticker", "id": 9}},
    )
    no_box = await api_client.post("/api/v1/projects/1/boards/3/shapes", json={"kind": "rect"})
    unknown_op = await api_client.post(
        "/api/v1/projects/1/boards/3/batch", json={"ops": [{"op": "drop_board"}]}
    )

    assert conflict.status_code == 409
    assert conflict.json() == {
        "detail": {
            "message": BoardBatchConflictError.message,
            "conflicts": [{"kind": "shape", "id": 12}],
        }
    }
    assert invalid_ref.status_code == 422
    assert invalid_ref.json() == {"detail": "Привязка указывает на объект не с этой доски."}
    assert no_box.status_code == 422 and unknown_op.status_code == 422
    assert service.create_shape.await_count == 1

    service = AsyncMock(spec=BoardShapesService)
    service.apply_batch.return_value = BoardBatchResultSchema(
        shapes=[], deleted_shape_ids=[5], stickers=[], id_map={"tmp-1": 55}
    )
    app.dependency_overrides[get_board_shapes_service] = lambda: service
    deleted = await api_client.delete("/api/v1/projects/1/boards/3/shapes/5")
    applied = await api_client.post(
        "/api/v1/projects/1/boards/3/batch",
        json={
            "ops": [
                {
                    "op": "create_shape",
                    "client_id": "tmp-1",
                    "data": {"kind": "text", "x": 0, "y": 0, "w": 50, "h": 20},
                }
            ]
        },
    )

    assert deleted.status_code == 204
    service.delete_shape.assert_awaited_once_with(
        project_id=1, board_id=3, shape_id=5, revision=None
    )
    assert applied.status_code == 200 and applied.json()["id_map"] == {"tmp-1": 55}
