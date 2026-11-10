"""HTTP-контур свободных досок: владелец из сессии, project_id=None, коды ответов."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

from httpx import AsyncClient

from main import app
from src.dependencies.services import (
    get_board_shapes_service,
    get_board_stickers_service,
    get_boards_service,
)
from src.exceptions.board_stickers import FreeBoardTaskLinksError
from src.exceptions.boards import BoardNotFoundError
from src.schemas.boards import BoardSchema
from src.services.board_shapes import BoardShapesService
from src.services.board_stickers import BoardStickersService
from src.services.boards import BoardsService


def free_board_schema() -> BoardSchema:
    now = datetime.now(UTC)
    return BoardSchema(
        id=7,
        project_id=None,
        title="Набросок",
        position=0,
        revision=1,
        created_by_user_id=1,
        created_at=now,
        updated_at=now,
    )


async def test_free_board_routes_take_owner_from_session(api_client: AsyncClient) -> None:
    service = AsyncMock(spec=BoardsService)
    service.list_free_boards.return_value = [free_board_schema()]
    service.create_free_board.return_value = free_board_schema()
    service.get_free_content.side_effect = BoardNotFoundError(7)
    app.dependency_overrides[get_boards_service] = lambda: service

    listed = await api_client.get("/api/v1/boards")
    created = await api_client.post("/api/v1/boards", json={"title": "Набросок"})
    empty_title = await api_client.post("/api/v1/boards", json={"title": "  "})
    missing = await api_client.get("/api/v1/boards/7/content")
    deleted = await api_client.delete("/api/v1/boards/7?revision=1")
    no_revision = await api_client.delete("/api/v1/boards/7")

    assert listed.status_code == 200
    assert listed.json()[0]["project_id"] is None
    service.list_free_boards.assert_awaited_once_with(user_id=1)
    assert created.status_code == 201
    assert service.create_free_board.await_args.kwargs["user_id"] == 1
    assert empty_title.status_code == 422
    assert missing.status_code == 404
    assert deleted.status_code == 204
    service.delete_free_board.assert_awaited_once_with(board_id=7, revision=1, user_id=1)
    assert no_revision.status_code == 422


async def test_free_board_stickers_and_shapes_pass_no_project(api_client: AsyncClient) -> None:
    stickers = AsyncMock(spec=BoardStickersService)
    stickers.create_sticker.side_effect = FreeBoardTaskLinksError()
    shapes = AsyncMock(spec=BoardShapesService)
    app.dependency_overrides[get_board_stickers_service] = lambda: stickers
    app.dependency_overrides[get_board_shapes_service] = lambda: shapes

    linked = await api_client.post(
        "/api/v1/boards/7/stickers", json={"body": "Идея", "task_ids": [5]}
    )
    removed = await api_client.delete("/api/v1/boards/7/stickers/3?revision=2")
    shape_deleted = await api_client.delete("/api/v1/boards/7/shapes/9")

    assert linked.status_code == 422
    assert linked.json() == {"detail": "Привязка к задачам доступна только на доске проекта."}
    assert stickers.create_sticker.await_args.kwargs["project_id"] is None
    assert stickers.create_sticker.await_args.kwargs["author_id"] == 1
    assert removed.status_code == 204
    stickers.delete_sticker.assert_awaited_once_with(
        project_id=None, board_id=7, sticker_id=3, revision=2, user_id=1
    )
    assert shape_deleted.status_code == 204
    shapes.delete_shape.assert_awaited_once_with(
        project_id=None, board_id=7, shape_id=9, revision=None, user_id=1
    )
